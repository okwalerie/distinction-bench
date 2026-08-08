"""Isolated subscription CLI adapters for the agent execution surface."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from dbench.provider_evidence import AGENT_CLI_SOURCE
from lofbench.provider_evidence import ProviderEvidenceEnvelope, ProviderEvidenceSource
from lofbench.run_models import ExecutionRequest, ExecutionResult, TrialExecutor

_AGENT_ENV_ALLOWLIST = frozenset(
    {
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "COLORTERM",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TERM",
        "USER",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def agent_subprocess_env(
    *,
    secret_names: Iterable[str] = (),
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the only environment an agent CLI subprocess may receive."""
    values = os.environ if source is None else source
    denied = {name.upper() for name in secret_names}

    def safe(name: str) -> bool:
        upper = name.upper()
        return not (
            upper in denied
            or "OPENROUTER" in upper
            or "SECRET" in upper
            or "PASSWORD" in upper
            or upper.endswith("_API_KEY")
            or upper.endswith("_TOKEN")
        )

    return {
        name: value for name, value in values.items() if name in _AGENT_ENV_ALLOWLIST and safe(name)
    }


def cli_version(command: str, *, environment: Mapping[str, str]) -> str:
    completed = subprocess.run(
        [command, "--version"],
        env=dict(environment),
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

    def __init__(self, *, environment: Mapping[str, str]) -> None:
        self.environment = dict(environment)

    def argv(self, request: ExecutionRequest, schema_path: Path, output_path: Path) -> list[str]:
        raise NotImplementedError

    def _result(
        self,
        *,
        request: ExecutionRequest,
        started_at: str,
        returncode: int,
        output_text: str = "",
        stdout: str = "",
        stderr: str = "",
        process_error: str = "",
        process_error_message: str = "",
    ) -> ExecutionResult:
        payload = {
            "command": self.command,
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output_text": output_text,
            "resolved_model_id": request.run.resolved_model_id,
            "request_id": (
                f"subscription:{self.command}:{request.run.run_id}:"
                f"{request.sample.id}:{request.attempt}"
            ),
            "process_error": process_error,
            "process_error_message": process_error_message,
        }
        return ExecutionResult(
            provider_evidence=ProviderEvidenceEnvelope(
                schema_version=2,
                adapter_id="agent-cli-v2",
                sources=(
                    ProviderEvidenceSource.capture_event(
                        label=AGENT_CLI_SOURCE,
                        request_started_at=started_at,
                        response_finished_at=_now(),
                        operation=self.command,
                        payload=payload,
                    ),
                ),
            )
        )

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        started_at = _now()
        if not isinstance(request.sample.input, str):
            return self._result(
                request=request,
                started_at=started_at,
                returncode=-2,
                stderr="agent_cli_text_only",
            )
        try:
            with tempfile.TemporaryDirectory(prefix="lofbench-agent-") as directory:
                root = Path(directory)
                schema_path = root / "schema.json"
                output_path = root / "output.json"
                protocol = request.task.config.response_schema
                schema = protocol.json_schema if protocol else {}
                if hasattr(schema, "model_dump"):
                    schema = schema.model_dump(mode="json", exclude_none=True)
                schema_path.write_text(json.dumps(schema))
                completed = subprocess.run(
                    self.argv(request, schema_path, output_path),
                    cwd=root,
                    env=self.environment,
                    text=True,
                    capture_output=True,
                    timeout=300,
                    check=False,
                )
                if self.command == "claude" and completed.returncode == 0:
                    output_path.write_text(completed.stdout)
                if completed.returncode != 0 or not output_path.exists():
                    return self._result(
                        request=request,
                        started_at=started_at,
                        returncode=completed.returncode,
                        stdout=completed.stdout,
                        stderr=completed.stderr,
                    )
                return self._result(
                    request=request,
                    started_at=started_at,
                    returncode=0,
                    output_text=output_path.read_text(),
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
        except Exception as exc:
            stdout = getattr(exc, "stdout", "") or ""
            stderr = getattr(exc, "stderr", "") or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            return self._result(
                request=request,
                started_at=started_at,
                returncode=-3,
                stdout=stdout,
                stderr=stderr,
                process_error=type(exc).__name__,
                process_error_message=str(exc),
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
