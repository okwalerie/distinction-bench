"""Isolated subscription CLI adapters for the agent execution surface."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

from lofbench.run_models import ExecutionRequest, ExecutionResult, TrialExecutor


def cli_version(command: str) -> str:
    completed = subprocess.run(
        [command, "--version"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    value = (completed.stdout or completed.stderr).strip().splitlines()
    if completed.returncode != 0 or not value:
        raise RuntimeError(f"cannot resolve {command} CLI version")
    return value[0]


class AgentCliExecutor(TrialExecutor):
    command: str

    def argv(self, request: ExecutionRequest, schema_path: Path, output_path: Path) -> list[str]:
        raise NotImplementedError

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not isinstance(request.sample.input, str):
            return ExecutionResult(error_type="agent_cli_text_only", transport_error=True)
        with tempfile.TemporaryDirectory(prefix="lofbench-agent-") as directory:
            root = Path(directory)
            schema_path = root / "schema.json"
            output_path = root / "output.json"
            protocol = request.task.config.response_schema
            schema_path.write_text(json.dumps(protocol.json_schema if protocol else {}))
            started = time.monotonic()
            completed = subprocess.run(
                self.argv(request, schema_path, output_path),
                cwd=root,
                text=True,
                capture_output=True,
                timeout=300,
                check=False,
            )
            if self.command == "claude" and completed.returncode == 0:
                output_path.write_text(completed.stdout)
            if completed.returncode != 0 or not output_path.exists():
                return ExecutionResult(
                    transport_error=True,
                    error_type=f"{self.command}_exit_{completed.returncode}",
                    observed_cost_usd=0.0,
                    input_tokens=0,
                    output_tokens=0,
                    reasoning_tokens=0,
                )
            return ExecutionResult(
                response_text=output_path.read_text(),
                resolved_model_id=request.run.resolved_model_id,
                endpoint=self.command,
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                observed_cost_usd=0.0,
                latency_ms=(time.monotonic() - started) * 1000,
                provider_request_id=f"subscription:{self.command}:{request.attempt}",
                transcript={"surface": self.command, "stderr": completed.stderr},
            )


class CodexCliExecutor(AgentCliExecutor):
    command = "codex"

    def argv(self, request: ExecutionRequest, schema_path: Path, output_path: Path) -> list[str]:
        return [
            "codex",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--model",
            request.run.requested_model_id,
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            request.sample.input,
        ]


class ClaudeCliExecutor(AgentCliExecutor):
    command = "claude"

    def argv(self, request: ExecutionRequest, schema_path: Path, output_path: Path) -> list[str]:
        return [
            "claude",
            "-p",
            request.sample.input,
            "--model",
            request.run.requested_model_id,
            "--tools",
            "",
            "--json-schema",
            schema_path.read_text(),
            "--output-format",
            "json",
        ]
