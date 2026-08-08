from __future__ import annotations

from dataclasses import replace

from test_runner import _task_and_run

from dbench.agent_cli import ClaudeCliExecutor, CodexCliExecutor
from dbench.cli import _executor
from lofbench.run_models import ExecutionRequest


def test_application_dispatches_both_subscription_cli_adapters():
    _task, run = _task_and_run()
    assert isinstance(_executor(replace(run, execution_surface="codex_cli")), CodexCliExecutor)
    assert isinstance(_executor(replace(run, execution_surface="claude_cli")), ClaudeCliExecutor)


def test_cli_adapter_commands_pin_model_schema_and_tool_policy(tmp_path):
    task, run = _task_and_run()
    sample = list(task.dataset.samples)[0]
    request = ExecutionRequest(
        run=run,
        task=task,
        sample=sample,
        attempt=1,
        log_dir=tmp_path / "logs",
    )
    schema = tmp_path / "schema.json"
    output = tmp_path / "output.json"
    schema.write_text('{"type":"object"}')

    codex = CodexCliExecutor().argv(request, schema, output)
    assert codex[:2] == ["codex", "exec"]
    assert ["--model", run.requested_model_id] == codex[codex.index("--model") :][:2]
    assert ["--sandbox", "read-only"] == codex[codex.index("--sandbox") :][:2]
    assert ["--output-schema", str(schema)] == codex[codex.index("--output-schema") :][:2]

    claude = ClaudeCliExecutor().argv(request, schema, output)
    assert claude[:2] == ["claude", "-p"]
    assert ["--model", run.requested_model_id] == claude[claude.index("--model") :][:2]
    assert ["--tools", ""] == claude[claude.index("--tools") :][:2]
    assert ["--json-schema", schema.read_text()] == claude[claude.index("--json-schema") :][:2]
