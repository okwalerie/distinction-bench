from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset

from dbench.config import load_env_file
from lofbench.accounting import SpendLedger
from lofbench.orchestration import RunOrchestrator
from lofbench.run_models import (
    ExecutionResult,
    ExecutionSpec,
    InMemoryExecutor,
    plan_run,
)
from lofbench.tasks.single import single_lof_task


def _execution() -> ExecutionSpec:
    endpoint = {
        "tag": "example-provider",
        "provider_name": "Example Provider",
        "pricing": {"prompt": "0.000001", "completion": "0.000002"},
    }
    return ExecutionSpec(
        requested_model_id="example/model",
        resolved_model_id="example/model",
        execution_surface="direct_api",
        provider="openrouter",
        endpoint=endpoint["tag"],
        routing_policy={"order": [endpoint["tag"]], "allow_fallbacks": False},
        privacy_policy={"data_collection": "deny", "zdr": True},
        sdk_version="test",
        reasoning={"effort": "default"},
        generation={"temperature": 0, "max_tokens": 512, "max_retries": 0},
        billing_channel="test-paid",
        cohort="sample",
        max_transport_attempts=3,
        catalog_retrieved_at="2026-08-08T00:00:00+00:00",
        catalog_row={"selected_endpoint": endpoint},
    )


def _task_and_run(protocol: str = "reduce-infer-v1"):
    task = single_lof_task(form_set="probe", protocol=protocol)
    run = plan_run(
        task,
        suite_version="v1",
        form_set="probe",
        dialect_set="parens.reference-v1",
        protocol_id=protocol,
        execution=_execution(),
    )
    return task, run


def _success(value: str = "marked", **overrides) -> ExecutionResult:
    values = {
        "response_text": json.dumps({"value": value}),
        "resolved_model_id": "example/model",
        "endpoint": "example-provider",
        "input_tokens": 100,
        "output_tokens": 5,
        "reasoning_tokens": 0,
        "observed_cost_usd": 0.001,
        "latency_ms": 20.0,
        "provider_request_id": "request-test",
    }
    values.update(overrides)
    return ExecutionResult(**values)


def _ledger(tmp_path, *, global_cap: float = 30.0, cohort_cap: float = 30.0):
    return SpendLedger(
        tmp_path / "shared-ledger.jsonl",
        global_cap=global_cap,
        cohort_caps={"sample": cohort_cap},
    )


def _orchestrator(tmp_path, executor, **ledger_caps):
    return RunOrchestrator(
        tmp_path / "run-state",
        executor,
        ledger=_ledger(tmp_path, **ledger_caps),
    )


def test_plain_run_is_a_dry_run(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success()] * 5)
    result = _orchestrator(tmp_path, executor).execute(run, task)
    assert result == run
    assert executor.calls == []
    assert not (tmp_path / "shared-ledger.jsonl").exists()


def test_typed_approval_requires_exact_30_dollar_cap(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success()] * 5)
    result = _orchestrator(tmp_path, executor).execute(
        run,
        task,
        approve_paid_run=True,
        max_spend_usd=29.0,
    )
    assert result == run
    assert executor.calls == []


def test_complete_run_records_every_trial_and_spend(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success()] * 5)
    result = _orchestrator(tmp_path, executor).execute(
        run,
        task,
        approve_paid_run=True,
        max_spend_usd=30.0,
    )
    assert result.status == "complete"
    assert result.attempts == 5
    assert result.cost_usd == pytest.approx(0.005)
    trials = [
        json.loads(line) for line in (tmp_path / "run-state/trials.jsonl").read_text().splitlines()
    ]
    assert len(trials) == 5
    assert {row["trial_id"] for row in trials} == set(run.expected_trial_ids)


def test_invalid_answer_is_complete_incorrect_and_not_retried(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success(response_text="i refuse")] + [_success()] * 4)
    result = _orchestrator(tmp_path, executor).execute(
        run, task, approve_paid_run=True, max_spend_usd=30.0
    )
    assert result.status == "complete"
    trials = [
        json.loads(line) for line in (tmp_path / "run-state/trials.jsonl").read_text().splitlines()
    ]
    assert trials[0]["parse_status"] == "invalid"
    assert trials[0]["correct"] is False
    assert len(executor.calls) == 5


def test_transport_failure_retries_then_completes(tmp_path):
    task, run = _task_and_run()
    transient = _success(
        transport_error=True,
        error_type="TimeoutError",
        response_text="",
        observed_cost_usd=0.0,
    )
    executor = InMemoryExecutor([transient, _success()] + [_success()] * 4)
    result = _orchestrator(tmp_path, executor).execute(
        run, task, approve_paid_run=True, max_spend_usd=30.0
    )
    assert result.status == "complete"
    assert len(executor.calls) == 6
    calls = [
        json.loads(line) for line in (tmp_path / "run-state/calls.jsonl").read_text().splitlines()
    ]
    assert calls[0]["status"] == "transport_error"


def test_three_transport_failures_leave_trial_missing(tmp_path):
    task, run = _task_and_run()
    transient = _success(transport_error=True, observed_cost_usd=0.0)
    executor = InMemoryExecutor([transient] * 3 + [_success()] * 4)
    result = _orchestrator(tmp_path, executor).execute(
        run, task, approve_paid_run=True, max_spend_usd=30.0
    )
    assert result.status == "probed"
    assert len(executor.calls) == 7
    assert len((tmp_path / "run-state/trials.jsonl").read_text().splitlines()) == 4


def test_resume_schedules_only_missing_trial_ids(tmp_path):
    task, full_run = _task_and_run()
    first_two_task = Task(
        dataset=MemoryDataset(samples=list(task.dataset.samples)[:2]),
        solver=task.solver,
        scorer=task.scorer,
        config=task.config,
        metadata=task.metadata,
    )
    partial_run = replace(full_run, expected_trial_ids=full_run.expected_trial_ids[:2])
    first = InMemoryExecutor([_success()] * 2)
    _orchestrator(tmp_path, first).execute(
        partial_run, first_two_task, approve_paid_run=True, max_spend_usd=30.0
    )
    resume = InMemoryExecutor([_success()] * 3)
    completed = _orchestrator(tmp_path, resume).execute(
        full_run, task, approve_paid_run=True, max_spend_usd=30.0
    )
    assert completed.status == "complete"
    assert len(resume.calls) == 3
    assert len((tmp_path / "run-state/trials.jsonl").read_text().splitlines()) == 5


def test_complete_resume_does_not_duplicate_paid_calls(tmp_path):
    task, run = _task_and_run()
    first = InMemoryExecutor([_success()] * 5)
    complete = _orchestrator(tmp_path, first).execute(
        run, task, approve_paid_run=True, max_spend_usd=30.0
    )
    resume = InMemoryExecutor([])
    again = _orchestrator(tmp_path, resume).execute(
        complete, task, approve_paid_run=True, max_spend_usd=30.0
    )
    assert again.status == "complete"
    assert resume.calls == []


def test_missing_usage_or_cost_stops_immediately(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success(observed_cost_usd=None)])
    with pytest.raises(RuntimeError, match="omitted price or usage"):
        _orchestrator(tmp_path, executor).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    calls = [
        json.loads(line) for line in (tmp_path / "run-state/calls.jsonl").read_text().splitlines()
    ]
    assert calls[0]["status"] == "accounting_unknown"
    resume = InMemoryExecutor([_success()] * 5)
    with pytest.raises(RuntimeError, match="unresolved provider-accounting"):
        _orchestrator(tmp_path, resume).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    assert resume.calls == []


def test_single_attempt_mode_never_retries_a_sample_run_call(tmp_path):
    task, run = _task_and_run()
    transient = _success(transport_error=True, observed_cost_usd=0.0)
    executor = InMemoryExecutor([transient] + [_success()] * 4)
    run = replace(run, max_transport_attempts=1)
    result = _orchestrator(tmp_path, executor).execute(
        run, task, approve_paid_run=True, max_spend_usd=30.0
    )
    assert result.status == "probed"
    assert len(executor.calls) == 5
    assert executor.calls[0].sample.id != executor.calls[1].sample.id


def test_endpoint_fallback_and_model_substitution_stop(tmp_path):
    task, run = _task_and_run()
    fallback = InMemoryExecutor([_success(endpoint="other-provider")])
    with pytest.raises(RuntimeError, match="fallback"):
        _orchestrator(tmp_path / "fallback", fallback).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    substituted = InMemoryExecutor([_success(resolved_model_id="other/model")])
    with pytest.raises(RuntimeError, match="different model"):
        _orchestrator(tmp_path / "substitute", substituted).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    with pytest.raises(RuntimeError, match="identity gate"):
        _orchestrator(
            tmp_path / "substitute", InMemoryExecutor([_success()] * 5)
        ).execute(run, task, approve_paid_run=True, max_spend_usd=30.0)


def test_cohort_budget_exhaustion_aborts_before_next_request(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success()] * 5)
    with pytest.raises(RuntimeError, match="exceed"):
        _orchestrator(tmp_path, executor, cohort_cap=0.0105).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    assert len(executor.calls) == 1


def test_unknown_catalog_pricing_stops_before_request(tmp_path):
    task, run = _task_and_run()
    run = replace(run, catalog_row={"selected_endpoint": {"pricing": {}}})
    executor = InMemoryExecutor([_success()])
    with pytest.raises(RuntimeError, match="unknown endpoint pricing"):
        _orchestrator(tmp_path, executor).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    assert executor.calls == []


def test_release_ledger_enforces_aggregate_cap_across_run_directories(tmp_path):
    first_task, first_run = _task_and_run("reduce-infer-v1")
    second_task, second_run = _task_and_run("reduce-taught-v1")
    ledger = _ledger(tmp_path, global_cap=0.015, cohort_cap=0.015)
    first = InMemoryExecutor([_success()] * 5)
    completed = RunOrchestrator(tmp_path / "first-run", first, ledger=ledger).execute(
        first_run, first_task, approve_paid_run=True, max_spend_usd=30.0
    )
    assert completed.cost_usd == pytest.approx(0.005)
    second = InMemoryExecutor([_success()] * 5)
    with pytest.raises(RuntimeError, match="global cap"):
        RunOrchestrator(tmp_path / "second-run", second, ledger=ledger).execute(
            second_run,
            second_task,
            approve_paid_run=True,
            max_spend_usd=30.0,
        )
    assert len(second.calls) == 1


def test_release_ledger_serializes_concurrent_reservations(tmp_path):
    ledger = _ledger(tmp_path, global_cap=1.0, cohort_cap=1.0)

    def reserve(call_id: str) -> str:
        try:
            ledger.reserve(
                call_id=call_id,
                trial_id=f"trial-{call_id}",
                run_id=f"run-{call_id}",
                cohort="sample",
                amount=0.75,
            )
        except RuntimeError:
            return "rejected"
        return "reserved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, ("one", "two")))
    assert sorted(outcomes) == ["rejected", "reserved"]
    observed, reserved = ledger.totals()
    assert observed == 0
    assert reserved == pytest.approx(0.75)


def test_env_file_permissions_and_secret_nondisclosure(tmp_path, capsys):
    path = tmp_path / "dbench.env"
    secret = "not-a-real-secret-value"
    path.write_text(f"OPENROUTER_API_KEY={secret}\n")
    path.chmod(0o600)
    assert load_env_file(path)["OPENROUTER_API_KEY"] == secret
    assert secret not in capsys.readouterr().out
    path.chmod(0o644)
    with pytest.raises(PermissionError, match="0600"):
        load_env_file(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("resolved_model_id", "example/model-revision"),
        ("endpoint", "other-endpoint"),
        ("routing_policy", {"order": ["other-endpoint"], "allow_fallbacks": False}),
        ("privacy_policy", {"data_collection": "deny", "zdr": False}),
        ("sdk_version", "test-next"),
        ("billing_channel", "different-paid-channel"),
    ],
)
def test_run_identity_changes_with_execution_defining_configuration(field, value):
    task, original = _task_and_run()
    execution = replace(_execution(), **{field: value})
    changed = plan_run(
        task,
        suite_version="v1",
        form_set="probe",
        dialect_set="parens.reference-v1",
        protocol_id="reduce-infer-v1",
        execution=execution,
    )
    assert changed.run_id != original.run_id
