from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset

from dbench.config import load_env_file
from dbench.provider_evidence import (
    OPENROUTER_CHAT_SOURCE,
    OPENROUTER_GENERATION_SOURCE,
    project_openrouter_evidence,
)
from lofbench.accounting import SpendLedger
from lofbench.orchestration import RunAlreadyRunningError, RunOrchestrator
from lofbench.protocols import DEFAULT_PROTOCOL_REGISTRY
from lofbench.provider_evidence import ProviderEvidenceEnvelope, ProviderEvidenceSource
from lofbench.run_models import (
    ExecutionResult,
    ExecutionSpec,
    InMemoryExecutor,
    TrialExecutor,
    plan_run,
)
from lofbench.state_io import read_jsonl
from lofbench.suites import DEFAULT_SUITE_REGISTRY
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
        pricing={"prompt": 0.000001, "completion": 0.000002, "image": 0.000001},
        catalog_retrieved_at="2026-08-08T00:00:00+00:00",
        catalog_row={"selected_endpoint": endpoint},
    )


def _task_and_run(protocol: str = "reduce-infer-v1"):
    task = single_lof_task(form_set="probe", protocol=protocol)
    run = plan_run(
        task,
        suite_version="v1",
        form_set="probe",
        dialect_id="parens.reference-v1",
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
        "transport_error": False,
        "provider_error": False,
        "error_type": "",
    }
    values.update(overrides)
    started = datetime(2026, 8, 8, tzinfo=UTC)
    finished = started + timedelta(milliseconds=values["latency_ms"])
    if values["transport_error"]:
        sources = (
            ProviderEvidenceSource.capture_http(
                label=OPENROUTER_CHAT_SOURCE,
                sequence=1,
                request_started_at=started.isoformat(),
                response_finished_at=finished.isoformat(),
                request_method="POST",
                request_url="https://openrouter.ai/api/v1/chat/completions",
                http_status=None,
                response_headers=(),
                raw_body=b"",
                transport_error=values["error_type"] or "TransportError",
            ),
        )
    else:
        provider_name = (
            "Example Provider" if values["endpoint"] == "example-provider" else values["endpoint"]
        )
        finish_reason = "error" if values["provider_error"] else "stop"
        generation = {
            "id": values["provider_request_id"],
            "model": values["resolved_model_id"],
            "provider_name": provider_name,
            "native_tokens_prompt": values["input_tokens"],
            "native_tokens_completion": values["output_tokens"],
            "native_tokens_reasoning": values["reasoning_tokens"],
            "latency": values["latency_ms"],
            "finish_reason": finish_reason,
        }
        if values["provider_error"]:
            generation["error"] = {"type": values["error_type"] or "busy"}
        if values["observed_cost_usd"] is not None:
            generation["total_cost"] = values["observed_cost_usd"]
        chat = {
            "id": values["provider_request_id"],
            "model": values["resolved_model_id"],
            "provider": provider_name,
            "choices": [
                {
                    "message": {"content": values["response_text"]},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": values["input_tokens"],
                "completion_tokens": values["output_tokens"],
                "completion_tokens_details": {"reasoning_tokens": values["reasoning_tokens"]},
            },
        }
        if values["provider_error"]:
            chat["error"] = {"type": values["error_type"] or "busy"}
        sources = (
            ProviderEvidenceSource.capture_http(
                label=OPENROUTER_CHAT_SOURCE,
                sequence=1,
                request_started_at=started.isoformat(),
                response_finished_at=finished.isoformat(),
                request_method="POST",
                request_url="https://openrouter.ai/api/v1/chat/completions",
                http_status=200,
                response_headers=(),
                raw_body=json.dumps(chat).encode(),
            ),
            ProviderEvidenceSource.capture_http(
                label=OPENROUTER_GENERATION_SOURCE,
                sequence=1,
                request_started_at=finished.isoformat(),
                response_finished_at=finished.isoformat(),
                request_method="GET",
                request_url="https://openrouter.ai/api/v1/generation?id=request-test",
                http_status=200,
                response_headers=(),
                raw_body=json.dumps({"data": generation}).encode(),
            ),
        )
    return ExecutionResult(
        provider_evidence=ProviderEvidenceEnvelope(
            schema_version=2,
            adapter_id="openrouter-direct-v2",
            sources=sources,
        )
    )


def _ledger(tmp_path, *, global_cap: float = 30.0, cohort_cap: float = 30.0):
    return SpendLedger(
        tmp_path / "shared-ledger.jsonl",
        global_cap=global_cap,
        cohort_caps={"sample": cohort_cap},
    )


def _orchestrator(
    tmp_path,
    executor,
    *,
    accounting_recoverer=None,
    transition_observer=None,
    **ledger_caps,
):
    return RunOrchestrator(
        tmp_path / "run-state",
        executor,
        ledger=_ledger(tmp_path, **ledger_caps),
        evidence_projector=project_openrouter_evidence,
        accounting_recoverer=accounting_recoverer,
        transition_observer=transition_observer,
    )


class _SimulatedCrash(RuntimeError):
    pass


class _CrashOnceAfter:
    def __init__(self, transition: str) -> None:
        self.transition = transition
        self.crashed = False

    def __call__(self, transition: str, _call_id: str) -> None:
        if transition == self.transition and not self.crashed:
            self.crashed = True
            raise _SimulatedCrash(transition)


class _BlockingExecutor(TrialExecutor):
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls = []
        self.started = Event()
        self.release = Event()

    def execute(self, request):
        self.calls.append(request)
        self.started.set()
        if not self.release.wait(timeout=10):
            raise RuntimeError("blocking executor was not released")
        return self.result


def _one_sample(task, run):
    sample = list(task.dataset.samples)[0]
    return (
        Task(
            dataset=MemoryDataset(samples=[sample]),
            solver=task.solver,
            scorer=task.scorer,
            config=task.config,
            metadata=task.metadata,
        ),
        replace(run, expected_trial_ids=run.expected_trial_ids[:1]),
    )


def test_plain_run_is_a_dry_run(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success()] * 5)
    result = _orchestrator(tmp_path, executor).execute(run, task)
    assert result == run
    assert executor.calls == []
    assert not (tmp_path / "shared-ledger.jsonl").exists()


def test_reconcile_validates_planned_state_without_scheduling_inference(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success()])
    reconciled = _orchestrator(tmp_path, executor).reconcile(run, task)
    assert reconciled.status == "planned"
    assert reconciled.attempts == 0
    assert executor.calls == []
    assert read_jsonl(tmp_path / "shared-ledger.jsonl") == []


def test_reconcile_validates_closed_partial_state_without_a_next_call(tmp_path):
    task, run = _one_sample(*_task_and_run())
    completed = _orchestrator(tmp_path, InMemoryExecutor([_success()])).execute(
        run,
        task,
        approve_paid_run=True,
        max_spend_usd=30.0,
    )
    full_task, full_run = _task_and_run()
    partial = replace(
        full_run,
        status="probed",
        attempts=completed.attempts,
        token_usage=completed.token_usage,
        latency_ms=completed.latency_ms,
        cost_usd=completed.cost_usd,
    )
    executor = InMemoryExecutor([_success()])
    reconciled = _orchestrator(tmp_path, executor).reconcile(partial, full_task)
    assert reconciled.status == "probed"
    assert reconciled.attempts == 1
    assert executor.calls == []
    assert len(read_jsonl(tmp_path / "shared-ledger.jsonl")) == 2


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
    requests = read_jsonl(tmp_path / "run-state/request-started.jsonl")
    assert len(requests) == 5
    assert {row["call_id"] for row in requests} == {request.call_id for request in executor.calls}


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


def test_paid_transport_uncertainty_persists_and_keeps_reservation(tmp_path):
    task, run = _task_and_run()
    transient = _success(
        transport_error=True,
        error_type="TimeoutError",
        response_text="",
        observed_cost_usd=0.0,
    )
    executor = InMemoryExecutor([transient])
    with pytest.raises(RuntimeError, match="accounting is unknown"):
        _orchestrator(tmp_path, executor).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    calls = [
        json.loads(line) for line in (tmp_path / "run-state/calls.jsonl").read_text().splitlines()
    ]
    assert len(calls) == 1
    assert calls[0]["status"] == "accounting_unknown"
    assert len((tmp_path / "run-state/transcripts.jsonl").read_text().splitlines()) == 1
    observed, reserved = _ledger(tmp_path).totals()
    assert observed == 0.0
    assert reserved > 0.0


def test_three_provider_errors_leave_trial_missing_and_never_score(tmp_path):
    task, run = _task_and_run()
    transient = _success(provider_error=True, observed_cost_usd=0.0)
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


def test_concurrent_execution_of_one_run_allows_exactly_one_provider_post(tmp_path):
    full_task, full_run = _task_and_run()
    task, run = _one_sample(full_task, full_run)
    first_executor = _BlockingExecutor(_success())
    first = _orchestrator(tmp_path, first_executor)
    second_executor = InMemoryExecutor([_success()])
    second = _orchestrator(tmp_path, second_executor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(
            first.execute,
            run,
            task,
            approve_paid_run=True,
            max_spend_usd=30.0,
        )
        assert first_executor.started.wait(timeout=5)
        try:
            with pytest.raises(RunAlreadyRunningError, match="already running") as raised:
                second.execute(
                    run,
                    task,
                    approve_paid_run=True,
                    max_spend_usd=30.0,
                )
            assert raised.value.status == "already_running"
        finally:
            first_executor.release.set()
        completed = running.result(timeout=10)

    assert completed.status == "complete"
    assert len(first_executor.calls) == 1
    assert second_executor.calls == []
    state = tmp_path / "run-state"
    for name in (
        "request-started.jsonl",
        "transcripts.jsonl",
        "calls.jsonl",
        "trials.jsonl",
    ):
        assert len(read_jsonl(state / name)) == 1


def test_different_run_locks_still_share_one_atomic_global_ledger(tmp_path):
    first_task, first_run = _one_sample(*_task_and_run("reduce-infer-v1"))
    second_task, second_run = _one_sample(*_task_and_run("reduce-taught-v1"))
    ledger_path = tmp_path / "shared-ledger.jsonl"
    first_executor = _BlockingExecutor(_success())
    first = RunOrchestrator(
        tmp_path / "first-run",
        first_executor,
        ledger=SpendLedger(
            ledger_path,
            global_cap=0.015,
            cohort_caps={"sample": 0.015},
        ),
        evidence_projector=project_openrouter_evidence,
    )
    second_executor = InMemoryExecutor([_success()])
    second = RunOrchestrator(
        tmp_path / "second-run",
        second_executor,
        ledger=SpendLedger(
            ledger_path,
            global_cap=0.015,
            cohort_caps={"sample": 0.015},
        ),
        evidence_projector=project_openrouter_evidence,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(
            first.execute,
            first_run,
            first_task,
            approve_paid_run=True,
            max_spend_usd=30.0,
        )
        assert first_executor.started.wait(timeout=5)
        with pytest.raises(RuntimeError, match="global cap"):
            second.execute(
                second_run,
                second_task,
                approve_paid_run=True,
                max_spend_usd=30.0,
            )
        first_executor.release.set()
        assert running.result(timeout=10).status == "complete"

    assert second_executor.calls == []
    rows = read_jsonl(ledger_path)
    assert [row["event_type"] for row in rows] == ["reserved", "settled"]
    assert len({row["call_id"] for row in rows}) == 1


@pytest.mark.parametrize(
    "transition",
    ["reserved", "evidence", "call", "settled", "trial", "run_manifest"],
)
def test_resume_converges_after_every_durable_append_boundary(tmp_path, transition):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success()] * 5)
    crash = _CrashOnceAfter(transition)
    orchestrator = _orchestrator(
        tmp_path,
        executor,
        transition_observer=crash,
    )
    with pytest.raises(_SimulatedCrash, match=transition):
        orchestrator.execute(run, task, approve_paid_run=True, max_spend_usd=30.0)
    completed = orchestrator.execute(run, task, approve_paid_run=True, max_spend_usd=30.0)
    assert completed.status == "complete"
    assert len(executor.calls) == 5
    assert len({request.call_id for request in executor.calls}) == 5
    assert _ledger(tmp_path).totals() == pytest.approx((0.005, 0.0))


@pytest.mark.parametrize(
    ("transition", "initial_executor_calls"),
    [("request_started", 0), ("provider_returned", 1)],
)
def test_resume_quarantines_a_started_request_without_evidence(
    tmp_path, transition, initial_executor_calls
):
    full_task, full_run = _task_and_run()
    sample = list(full_task.dataset.samples)[0]
    task = Task(
        dataset=MemoryDataset(samples=[sample]),
        solver=full_task.solver,
        scorer=full_task.scorer,
        config=full_task.config,
        metadata=full_task.metadata,
    )
    run = replace(full_run, expected_trial_ids=full_run.expected_trial_ids[:1])
    first = InMemoryExecutor([_success()])
    with pytest.raises(_SimulatedCrash, match=transition):
        _orchestrator(
            tmp_path,
            first,
            transition_observer=_CrashOnceAfter(transition),
        ).execute(run, task, approve_paid_run=True, max_spend_usd=30.0)
    assert len(first.calls) == initial_executor_calls

    resume = InMemoryExecutor([_success()])
    with pytest.raises(RuntimeError, match="ambiguous.*refusing to resend"):
        _orchestrator(tmp_path, resume).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    assert resume.calls == []
    requests = read_jsonl(tmp_path / "run-state/request-started.jsonl")
    assert len(requests) == 1
    assert len(requests[0]["request_sha256"]) == 64
    assert not (tmp_path / "run-state/transcripts.jsonl").exists()
    assert not (tmp_path / "run-state/calls.jsonl").exists()
    assert json.loads((tmp_path / "run-state/run.json").read_text())["status"] == "ambiguous"
    observed, reserved = _ledger(tmp_path).totals()
    assert observed == 0.0
    assert reserved > 0.0


def test_billed_provider_error_settles_exactly_without_a_trial(tmp_path):
    full_task, _full_run = _task_and_run()
    sample = list(full_task.dataset.samples)[0]
    task = Task(
        dataset=MemoryDataset(samples=[sample]),
        solver=full_task.solver,
        scorer=full_task.scorer,
        config=full_task.config,
        metadata=full_task.metadata,
    )
    planned = plan_run(
        full_task,
        suite_version="v1",
        form_set="probe",
        dialect_id="parens.reference-v1",
        protocol_id="reduce-infer-v1",
        execution=replace(_execution(), max_transport_attempts=1),
    )
    run = replace(planned, expected_trial_ids=planned.expected_trial_ids[:1])
    executor = InMemoryExecutor(
        [
            _success(
                provider_error=True,
                error_type="provider_busy",
                observed_cost_usd=0.004,
            )
        ]
    )
    result = _orchestrator(tmp_path, executor).execute(
        run, task, approve_paid_run=True, max_spend_usd=30.0
    )
    assert result.status == "probed"
    assert result.cost_usd == pytest.approx(0.004)
    assert not (tmp_path / "run-state/trials.jsonl").exists()
    calls = read_jsonl(tmp_path / "run-state/calls.jsonl")
    assert calls[0]["status"] == "provider_error"
    assert calls[0]["observed_cost_usd"] == pytest.approx(0.004)
    assert calls[0]["input_tokens"] == 100
    assert _ledger(tmp_path).totals() == pytest.approx((0.004, 0.0))


def test_billed_provider_error_can_precede_one_scored_completion(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor(
        [_success(provider_error=True, observed_cost_usd=0.004), _success()] + [_success()] * 4
    )
    result = _orchestrator(tmp_path, executor).execute(
        run, task, approve_paid_run=True, max_spend_usd=30.0
    )
    assert result.status == "complete"
    assert result.attempts == 6
    trials = read_jsonl(tmp_path / "run-state/trials.jsonl")
    assert trials[0]["attempt_count"] == 2
    assert _ledger(tmp_path).totals() == pytest.approx((0.009, 0.0))


def test_missing_usage_or_cost_stops_immediately(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success(observed_cost_usd=None)])
    with pytest.raises(RuntimeError, match="accounting is unknown"):
        _orchestrator(tmp_path, executor).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    calls = [
        json.loads(line) for line in (tmp_path / "run-state/calls.jsonl").read_text().splitlines()
    ]
    assert calls[0]["status"] == "accounting_unknown"
    observed, reserved = _ledger(tmp_path).totals()
    assert observed == 0.0
    assert reserved > 0.0
    resume = InMemoryExecutor([_success()] * 5)
    with pytest.raises(RuntimeError, match="unresolved provider accounting"):
        _orchestrator(tmp_path, resume).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    assert resume.calls == []


def test_resume_replays_a_recovered_evidence_revision_after_crash(tmp_path):
    full_task, full_run = _task_and_run()
    sample = list(full_task.dataset.samples)[0]
    task = Task(
        dataset=MemoryDataset(samples=[sample]),
        solver=full_task.solver,
        scorer=full_task.scorer,
        config=full_task.config,
        metadata=full_task.metadata,
    )
    run = replace(full_run, expected_trial_ids=full_run.expected_trial_ids[:1])
    first = InMemoryExecutor([_success(observed_cost_usd=None)])
    with pytest.raises(RuntimeError, match="accounting is unknown"):
        _orchestrator(tmp_path, first).execute(run, task, approve_paid_run=True, max_spend_usd=30.0)

    recovered_evidence = _success().provider_evidence
    recovery_calls = 0

    def recover(_evidence, _run):
        nonlocal recovery_calls
        recovery_calls += 1
        return recovered_evidence

    crash = _CrashOnceAfter("evidence")
    resume = _orchestrator(
        tmp_path,
        InMemoryExecutor([]),
        accounting_recoverer=recover,
        transition_observer=crash,
    )
    with pytest.raises(_SimulatedCrash, match="evidence"):
        resume.execute(run, task, approve_paid_run=True, max_spend_usd=30.0)
    completed = resume.execute(run, task, approve_paid_run=True, max_spend_usd=30.0)
    assert completed.status == "complete"
    assert recovery_calls == 1
    assert resume.executor.calls == []
    assert [row["revision"] for row in read_jsonl(tmp_path / "run-state/transcripts.jsonl")] == [
        0,
        1,
    ]


def test_single_attempt_mode_never_retries_a_sample_run_call(tmp_path):
    task, _run = _task_and_run()
    transient = _success(provider_error=True, observed_cost_usd=0.0)
    executor = InMemoryExecutor([transient] + [_success()] * 4)
    run = plan_run(
        task,
        suite_version="v1",
        form_set="probe",
        dialect_id="parens.reference-v1",
        protocol_id="reduce-infer-v1",
        execution=replace(_execution(), max_transport_attempts=1),
    )
    result = _orchestrator(tmp_path, executor).execute(
        run, task, approve_paid_run=True, max_spend_usd=30.0
    )
    assert result.status == "probed"
    assert len(executor.calls) == 5
    assert executor.calls[0].sample.id != executor.calls[1].sample.id


def test_endpoint_fallback_and_model_substitution_stop(tmp_path):
    task, run = _task_and_run()
    fallback = InMemoryExecutor([_success(endpoint="other-provider")])
    with pytest.raises(RuntimeError, match="accounting is unknown"):
        _orchestrator(tmp_path / "fallback", fallback).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    substituted = InMemoryExecutor([_success(resolved_model_id="other/model")])
    with pytest.raises(RuntimeError, match="accounting is unknown"):
        _orchestrator(tmp_path / "substitute", substituted).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    with pytest.raises(RuntimeError, match="unresolved provider accounting"):
        _orchestrator(tmp_path / "substitute", InMemoryExecutor([_success()] * 5)).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )


def test_cohort_budget_exhaustion_aborts_before_next_request(tmp_path):
    task, run = _task_and_run()
    executor = InMemoryExecutor([_success()] * 5)
    with pytest.raises(RuntimeError, match="exceed"):
        _orchestrator(tmp_path, executor, cohort_cap=0.0105).execute(
            run, task, approve_paid_run=True, max_spend_usd=30.0
        )
    assert len(executor.calls) == 1


def test_unknown_catalog_pricing_stops_before_request(tmp_path):
    task, _run = _task_and_run()
    run = plan_run(
        task,
        suite_version="v1",
        form_set="probe",
        dialect_id="parens.reference-v1",
        protocol_id="reduce-infer-v1",
        execution=replace(_execution(), pricing={}),
    )
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
    completed = RunOrchestrator(
        tmp_path / "first-run",
        first,
        ledger=ledger,
        evidence_projector=project_openrouter_evidence,
    ).execute(first_run, first_task, approve_paid_run=True, max_spend_usd=30.0)
    assert completed.cost_usd == pytest.approx(0.005)
    second = InMemoryExecutor([_success()] * 5)
    with pytest.raises(RuntimeError, match="global cap"):
        RunOrchestrator(
            tmp_path / "second-run",
            second,
            ledger=ledger,
            evidence_projector=project_openrouter_evidence,
        ).execute(
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
        ("pricing", {"prompt": 1.0, "completion": 2.0, "image": 3.0}),
        ("catalog_retrieved_at", "2026-08-08T00:00:01+00:00"),
        ("catalog_row", {"selected_endpoint": {"tag": "different-catalog-row"}}),
    ],
)
def test_run_identity_changes_with_execution_defining_configuration(field, value):
    task, original = _task_and_run()
    assert original.run_id == original.authoritative_run_id()
    execution = replace(_execution(), **{field: value})
    changed = plan_run(
        task,
        suite_version="v1",
        form_set="probe",
        dialect_id="parens.reference-v1",
        protocol_id="reduce-infer-v1",
        execution=execution,
    )
    assert changed.run_id != original.run_id


def test_run_identity_changes_with_protocol_registry_bytes(tmp_path):
    task, original = _task_and_run()
    payload = json.loads(DEFAULT_PROTOCOL_REGISTRY.read_text())
    payload["protocols"]["reduce-infer-v1"]["system_text"] = "different protocol"
    changed_registry = tmp_path / "protocols.json"
    changed_registry.write_text(json.dumps(payload))
    changed = plan_run(
        task,
        suite_version="v1",
        form_set="probe",
        dialect_id="parens.reference-v1",
        protocol_id="reduce-infer-v1",
        execution=_execution(),
        protocol_registry_path=changed_registry,
    )
    assert changed.run_id != original.run_id


def test_run_identity_changes_with_suite_registry_bytes(tmp_path):
    task, original = _task_and_run()
    payload = json.loads(DEFAULT_SUITE_REGISTRY.read_text())
    payload["header"]["authority_test_marker"] = "different suite"
    changed_registry = tmp_path / "suite.json"
    changed_registry.write_text(json.dumps(payload))
    changed = plan_run(
        task,
        suite_version="v1",
        form_set="probe",
        dialect_id="parens.reference-v1",
        protocol_id="reduce-infer-v1",
        execution=_execution(),
        suite_registry_path=changed_registry,
    )
    assert changed.run_id != original.run_id


def test_run_identity_changes_with_the_exact_selected_form_cells():
    task, original = _task_and_run()
    core_task = single_lof_task(form_set="core")
    selected = list(core_task.dataset.samples)[:5]
    assert [sample.metadata["abstract_form_id"] for sample in selected] != [
        sample.metadata["abstract_form_id"] for sample in task.dataset.samples
    ]
    altered_task = Task(
        dataset=MemoryDataset(samples=selected, name="altered_probe"),
        solver=task.solver,
        scorer=task.scorer,
        config=task.config,
        metadata={
            **task.metadata,
            "form_ids": [sample.metadata["abstract_form_id"] for sample in selected],
            "cell_hashes": {
                sample.metadata["abstract_form_id"]: {
                    "symbolic_payload_hash": sample.metadata["symbolic_payload_hash"],
                    "model_payload_sha256": sample.metadata["model_payload_sha256"],
                }
                for sample in selected
            },
        },
    )
    altered = plan_run(
        altered_task,
        suite_version="v1",
        form_set="probe",
        dialect_id="parens.reference-v1",
        protocol_id="reduce-infer-v1",
        execution=_execution(),
    )
    assert altered.run_id != original.run_id


def test_run_result_fields_do_not_create_a_second_identity_definition():
    _task, run = _task_and_run()
    completed = replace(
        run,
        status="complete",
        attempts=5,
        token_usage={"input_tokens": 1, "output_tokens": 2, "reasoning_tokens": 3},
        latency_ms=4.0,
        cost_usd=5.0,
    )
    assert completed.authoritative_run_id() == run.run_id


def test_distinct_singular_dialect_ids_create_distinct_run_identities():
    reference_task, reference = _task_and_run()
    prose_task = single_lof_task(form_set="probe", dialect="prose.containment-plain-v1")
    prose = plan_run(
        prose_task,
        suite_version="v1",
        form_set="probe",
        dialect_id="prose.containment-plain-v1",
        protocol_id="reduce-infer-v1",
        execution=_execution(),
    )
    assert reference_task.metadata["form_ids"] == prose_task.metadata["form_ids"]
    assert reference.dialect_id == "parens.reference-v1"
    assert prose.dialect_id == "prose.containment-plain-v1"
    assert "dialect_set" not in reference.to_dict()
    assert reference.run_id != prose.run_id
