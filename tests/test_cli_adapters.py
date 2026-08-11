from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_runner import _task_and_run

from dbench.agent_cli import ClaudeCliExecutor, CodexCliExecutor, agent_subprocess_env
from dbench.cli import _executor, _load_secret_env, _parser, _validate_probe_run, main
from dbench.migration import IDENTITY_EVENT_ID
from dbench.provider_evidence import project_agent_cli_evidence
from lofbench.run_models import ExecutionRequest
from lofbench.state_io import FileLockUnavailableError, state_lifecycle_lock


def test_application_dispatches_both_subscription_cli_adapters():
    _task, run = _task_and_run()
    assert isinstance(
        _executor(replace(run, execution_surface="codex_cli"), secrets={}), CodexCliExecutor
    )
    assert isinstance(
        _executor(replace(run, execution_surface="claude_cli"), secrets={}), ClaudeCliExecutor
    )


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

    codex = CodexCliExecutor(environment={}).argv(request, schema, output)
    assert codex[:2] == ["codex", "exec"]
    assert ["--model", run.requested_model_id] == codex[codex.index("--model") :][:2]
    assert ["--sandbox", "read-only"] == codex[codex.index("--sandbox") :][:2]
    assert ["--output-schema", str(schema)] == codex[codex.index("--output-schema") :][:2]

    claude = ClaudeCliExecutor(environment={}).argv(request, schema, output)
    assert claude[:2] == ["claude", "-p"]
    assert ["--model", run.requested_model_id] == claude[claude.index("--model") :][:2]
    assert ["--tools", ""] == claude[claude.index("--tools") :][:2]
    assert ["--json-schema", schema.read_text()] == claude[claude.index("--json-schema") :][:2]


def test_agent_subprocess_receives_explicit_environment_without_sentinels(tmp_path, monkeypatch):
    task, run = _task_and_run()
    sample = list(task.dataset.samples)[0]
    request = ExecutionRequest(
        run=run,
        task=task,
        sample=sample,
        attempt=1,
        log_dir=tmp_path / "logs",
    )
    openrouter_sentinel = "sentinel-openrouter-secret"
    dbench_sentinel = "sentinel-dbench-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", openrouter_sentinel)
    monkeypatch.setenv("DBENCH_SECRET", dbench_sentinel)
    captured = {}

    def fake_run(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=1, stdout="", stderr="failed safely")

    monkeypatch.setattr("dbench.agent_cli.subprocess.run", fake_run)
    environment = agent_subprocess_env(secret_names={"DBENCH_SECRET"})
    CodexCliExecutor(environment=environment).execute(request)

    assert "env" in captured
    child_environment = captured["env"]
    assert "OPENROUTER_API_KEY" not in child_environment
    assert "DBENCH_SECRET" not in child_environment
    assert openrouter_sentinel not in child_environment.values()
    assert dbench_sentinel not in child_environment.values()


def test_agent_timeout_returns_durable_transport_evidence(tmp_path, monkeypatch):
    task, run = _task_and_run()
    request = ExecutionRequest(
        run=run,
        task=task,
        sample=list(task.dataset.samples)[0],
        attempt=1,
        log_dir=tmp_path / "logs",
    )

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("codex", 300, output="partial", stderr="timed out")

    monkeypatch.setattr("dbench.agent_cli.subprocess.run", timeout)
    result = CodexCliExecutor(environment={}).execute(request)
    source = result.provider_evidence.sources[0]
    payload = source.payload()
    assert payload["process_error"] == "TimeoutExpired"
    assert payload["stdout"] == "partial"
    assert payload["stderr"] == "timed out"
    projection = project_agent_cli_evidence(result.provider_evidence, run.to_dict())
    assert projection.status == "transport_error"
    assert projection.error_type == "TimeoutExpired"


def test_env_file_loading_does_not_mutate_process_environment(tmp_path, monkeypatch):
    env_file = tmp_path / "dbench.env"
    env_file.write_text("OPENROUTER_API_KEY=local-only-sentinel\n")
    env_file.chmod(0o600)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert _load_secret_env(env_file) == {"OPENROUTER_API_KEY": "local-only-sentinel"}
    assert "OPENROUTER_API_KEY" not in os.environ


def test_probe_is_a_real_guarded_execution_command():
    _task, run = _task_and_run()
    contract = {
        "protocol_ids": [run.protocol_id],
        "trials_per_run": 5,
        "max_transport_attempts": run.max_transport_attempts,
    }
    _validate_probe_run(run, contract)
    with pytest.raises(RuntimeError, match="probe form set"):
        _validate_probe_run(replace(run, form_set="core"), contract)
    with pytest.raises(RuntimeError, match="4 x 5"):
        _validate_probe_run(replace(run, max_transport_attempts=1), contract)
    parsed = _parser().parse_args(
        [
            "probe",
            "--release",
            "/tmp/release",
            "--state-root",
            "/tmp/state",
            "--run-id",
            run.run_id,
        ]
    )
    assert parsed.command == "probe"


def test_salvage_holds_lifecycle_lock_during_catalog_selection(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    env_file = tmp_path / "dbench.env"
    env_file.write_text("OPENROUTER_API_KEY=local-only-sentinel\n")
    env_file.chmod(0o600)
    outcome = []

    def fake_fetch(*_args, **_kwargs):
        def contend():
            try:
                with state_lifecycle_lock(state_root, blocking=False):
                    outcome.append("acquired")
            except FileLockUnavailableError:
                outcome.append("blocked")

        worker = threading.Thread(target=contend)
        worker.start()
        worker.join(timeout=5)
        raise RuntimeError("catalog selection stopped after lock check")

    monkeypatch.setattr("dbench.cli.fetch_openrouter_endpoint", fake_fetch)
    with pytest.raises(RuntimeError, match="stopped after lock check"):
        main(
            [
                "salvage-working-model-identity",
                "--release",
                str(tmp_path / "release"),
                "--state-root",
                str(state_root),
                "--env-file",
                str(env_file),
                "--expected-source-commit",
                "0" * 40,
                "--requested-model",
                "example/model",
                "--resolved-model",
                "example/model-1",
                "--endpoint",
                "provider/flex",
                "--provider",
                "provider",
                "--expected-observed-cost-usd",
                "0.01",
                "--identity-event",
                IDENTITY_EVENT_ID,
            ]
        )
    assert outcome == ["blocked"]
