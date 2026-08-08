from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from lofbench.metrics import write_release_metrics
from lofbench.records import CallRecord, LedgerEvent, RunManifest, TrialRecord
from lofbench.release_bundle import ReleaseBundle
from lofbench.suites import load_suite
from lofsite.build import build_site


def _clean_repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "anchor").write_text("test\n")
    subprocess.run(["git", "add", "anchor"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "test"], cwd=path, check=True)
    return path


def _run(*, status: str = "complete", suite_version: str = "v1") -> RunManifest:
    return RunManifest.plan(
        suite_version=suite_version,
        form_set="probe",
        dialect_set="probe_text",
        protocol_id="reduce-infer-v1",
        requested_model_id="example/model",
        resolved_model_id="example/model-20260808",
        execution_surface="direct_api",
        provider="openrouter",
        endpoint="provider/example",
        routing_policy={"allow_fallbacks": False},
        privacy_policy={"data_collection": "deny", "zdr": True},
        sdk_version="test",
        reasoning={"effort": "default"},
        generation={"temperature": 0, "max_tokens": 512, "max_retries": 0},
        billing_channel="test",
        cohort="test-release",
        max_transport_attempts=1,
        expected_trial_ids=("trial_one",),
        status=status,
        attempts=1,
        token_usage={"input_tokens": 20, "output_tokens": 4, "reasoning_tokens": 0},
        latency_ms=10.0,
        cost_usd=0.001,
    )


def _trial(run: RunManifest, **overrides) -> TrialRecord:
    values = {
        "trial_id": "trial_one",
        "run_id": run.run_id,
        "suite_version": run.suite_version,
        "abstract_form_id": "lof_example",
        "dialect_id": "parens.reference-v1",
        "protocol_id": run.protocol_id,
        "execution_surface": run.execution_surface,
        "requested_model_id": run.requested_model_id,
        "resolved_model_id": run.resolved_model_id,
        "provider": run.provider,
        "endpoint": run.endpoint,
        "prompt_hash": "a" * 64,
        "symbolic_payload_hash": "b" * 32,
        "model_payload_sha256": "c" * 64,
        "parse_status": "valid",
        "attempt_count": 1,
        "latency_ms": 10.0,
        "input_tokens": 20,
        "output_tokens": 4,
        "reasoning_tokens": 0,
        "observed_cost_usd": 0.001,
        "prediction": "marked",
        "normal_value": "marked",
        "correct": True,
    }
    values.update(overrides)
    return TrialRecord(**values)


def _accounting(run: RunManifest) -> tuple[list[CallRecord], list[LedgerEvent]]:
    call = CallRecord(
        call_id="call_one",
        trial_id="trial_one",
        run_id=run.run_id,
        attempt=1,
        started_at="2026-08-08T00:00:00+00:00",
        finished_at="2026-08-08T00:00:01+00:00",
        status="complete",
        reserved_cost_usd=0.01,
        observed_cost_usd=0.001,
        resolved_model_id=run.resolved_model_id,
        endpoint=run.endpoint,
        input_tokens=20,
        output_tokens=4,
        reasoning_tokens=0,
        provider_request_id="request_one",
    )
    events = [
        LedgerEvent(
            event_type="reserved",
            call_id=call.call_id,
            trial_id=call.trial_id,
            run_id=run.run_id,
            cohort=run.cohort,
            amount_usd=call.reserved_cost_usd,
            at="2026-08-08T00:00:00+00:00",
        ),
        LedgerEvent(
            event_type="settled",
            call_id=call.call_id,
            trial_id=call.trial_id,
            run_id=run.run_id,
            cohort=run.cohort,
            amount_usd=call.observed_cost_usd,
            at="2026-08-08T00:00:01+00:00",
        ),
    ]
    return [call], events


def _admit(bundle: ReleaseBundle, run: RunManifest, trials: list[TrialRecord]) -> None:
    calls, events = _accounting(run)
    bundle.admit_run(run, trials, calls=calls, ledger_events=events)


@pytest.fixture
def working(tmp_path):
    repository = _clean_repository(tmp_path / "repo")
    run = _run()
    bundle = ReleaseBundle.create_working(
        tmp_path / "release",
        release_id="v1.0.0-test",
        repository_url="https://example.invalid/repo",
        repository_root=repository,
        expected_run_ids=(run.run_id,),
    )
    return bundle, repository, run


def test_create_validate_admit_and_seal(working):
    bundle, repository, run = working
    bundle.validate()
    _admit(bundle, run, [_trial(run)])
    bundle.seal(repository_root=repository)
    reopened = ReleaseBundle.open(bundle.root)
    assert reopened.manifest["status"] == "sealed"
    assert reopened.manifest["admitted_run_ids"] == [run.run_id]
    assert reopened.manifest["files"]
    reopened.validate()


def test_incomplete_run_is_rejected(working):
    bundle, _repository, run = working
    with pytest.raises(RuntimeError, match="incomplete"):
        _admit(bundle, run, [])


def test_noncomplete_and_pilot_runs_are_rejected(working):
    bundle, _repository, run = working
    with pytest.raises(RuntimeError, match="only complete"):
        _admit(bundle, replace(run, status="probed"), [_trial(run)])
    pilot = _run(suite_version="pilot-v0")
    with pytest.raises(RuntimeError, match="does not match"):
        _admit(bundle, pilot, [_trial(pilot)])


def test_resolved_model_drift_is_rejected(working):
    bundle, _repository, run = working
    with pytest.raises(RuntimeError, match="resolved model drift"):
        _admit(bundle, run, [_trial(run, resolved_model_id="substituted/model")])


def test_dirty_repository_blocks_seal(working):
    bundle, repository, run = working
    _admit(bundle, run, [_trial(run)])
    (repository / "dirty").write_text("dirty")
    with pytest.raises(RuntimeError, match="clean worktree"):
        bundle.seal(repository_root=repository)


def test_unlisted_root_file_is_rejected(working):
    bundle, _repository, _run_manifest = working
    (bundle.root / "surprise.txt").write_text("nope")
    with pytest.raises(RuntimeError, match="root files"):
        bundle.validate()


def test_checksum_drift_is_rejected(working):
    bundle, repository, run = working
    _admit(bundle, run, [_trial(run)])
    bundle.seal(repository_root=repository)
    with (bundle.root / "transcripts.jsonl").open("a") as handle:
        handle.write("{}\n")
    with pytest.raises(RuntimeError, match="checksum drift"):
        bundle.validate()


def test_sealed_bundle_refuses_mutation(working):
    bundle, repository, run = working
    _admit(bundle, run, [_trial(run)])
    bundle.seal(repository_root=repository)
    with pytest.raises(RuntimeError, match="immutable"):
        _admit(bundle, run, [_trial(run)])


def test_schema_mismatch_is_rejected(working):
    bundle, _repository, _run_manifest = working
    release_path = bundle.root / "release.json"
    value = json.loads(release_path.read_text())
    value["bundle_schema_version"] = 999
    release_path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="unsupported"):
        ReleaseBundle.open(bundle.root)


def _sample_run(protocol_id: str, form_ids: tuple[str, ...]) -> RunManifest:
    return RunManifest.plan(
        suite_version="v1",
        form_set="probe",
        dialect_set="enclosure.plain-v1",
        protocol_id=protocol_id,
        requested_model_id="example/vision-model",
        resolved_model_id="example/vision-model",
        execution_surface="direct_api",
        provider="openrouter",
        endpoint="eligible-endpoint",
        routing_policy={
            "order": ["eligible-endpoint"],
            "allow_fallbacks": False,
            "data_collection": "deny",
            "zdr": True,
        },
        privacy_policy={
            "data_collection": "deny",
            "zdr": True,
            "authenticated_zdr_catalog": True,
        },
        sdk_version="test",
        reasoning={"effort": "default"},
        generation={"temperature": 0, "max_tokens": 512, "max_retries": 0},
        billing_channel="test-paid",
        cohort="sample",
        max_transport_attempts=1,
        expected_trial_ids=tuple(f"{protocol_id}:{form_id}" for form_id in form_ids),
        status="complete",
        attempts=5,
        token_usage={"input_tokens": 100, "output_tokens": 20, "reasoning_tokens": 0},
        latency_ms=50.0,
        cost_usd=0.005,
        catalog_row={
            "authenticated": True,
            "selected_endpoint": {
                "tag": "eligible-endpoint",
                "provider_name": "Exact Provider",
                "pricing": {"prompt": "0.001", "completion": "0.002"},
            },
            "zdr_selected_endpoint": {
                "model_id": "example/vision-model",
                "tag": "eligible-endpoint",
                "provider_name": "Exact Provider",
                "pricing": {"prompt": "0.001", "completion": "0.002"},
            },
        },
    )


def _sample_records(run: RunManifest, form_ids: tuple[str, ...]):
    suite = load_suite()
    forms = {form["abstract_form_id"]: form for form in suite.forms}
    cells = {
        cell["abstract_form_id"]: cell
        for cell in suite.cells
        if cell["dialect_id"] == run.dialect_set
    }
    trials = []
    calls = []
    events = []
    for index, (trial_id, form_id) in enumerate(
        zip(run.expected_trial_ids, form_ids, strict=True), start=1
    ):
        form = forms[form_id]
        cell = cells[form_id]
        trials.append(
            TrialRecord(
                trial_id=trial_id,
                run_id=run.run_id,
                suite_version=run.suite_version,
                abstract_form_id=form_id,
                dialect_id=run.dialect_set,
                protocol_id=run.protocol_id,
                execution_surface=run.execution_surface,
                requested_model_id=run.requested_model_id,
                resolved_model_id=run.resolved_model_id,
                provider=run.provider,
                endpoint=run.endpoint,
                prompt_hash="a" * 64,
                symbolic_payload_hash=cell["symbolic_payload_hash"],
                model_payload_sha256=cell["model_payload_sha256"],
                parse_status="valid",
                attempt_count=1,
                latency_ms=10.0,
                input_tokens=20,
                output_tokens=4,
                reasoning_tokens=0,
                observed_cost_usd=0.001,
                prediction=form["normal_value"],
                normal_value=form["normal_value"],
                correct=True,
                response_text=json.dumps({"value": form["normal_value"]}),
            )
        )
        call = CallRecord(
            call_id=f"{run.run_id}:call:{index}",
            trial_id=trial_id,
            run_id=run.run_id,
            attempt=1,
            started_at=f"2026-08-08T00:00:0{index}+00:00",
            finished_at=f"2026-08-08T00:00:1{index}+00:00",
            status="complete",
            reserved_cost_usd=0.01,
            observed_cost_usd=0.001,
            resolved_model_id=run.resolved_model_id,
            endpoint=run.endpoint,
            input_tokens=20,
            output_tokens=4,
            reasoning_tokens=0,
            provider_request_id=f"request:{run.protocol_id}:{index}",
        )
        calls.append(call)
        for event_type, amount, at in (
            ("reserved", 0.01, call.started_at),
            ("settled", 0.001, call.finished_at),
        ):
            events.append(
                LedgerEvent(
                    event_type=event_type,
                    call_id=call.call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    cohort=run.cohort,
                    amount_usd=amount,
                    at=at,
                )
            )
    return trials, calls, events


def test_sample_contract_seals_only_four_runs_five_shared_forms_and_twenty_calls(tmp_path):
    repository = _clean_repository(tmp_path / "repo")
    protocols = (
        "reduce-infer-v1",
        "reduce-taught-v1",
        "transcribe-infer-v1",
        "transcribe-taught-v1",
    )
    form_ids = tuple(load_suite().form_sets["probe"])
    runs = [_sample_run(protocol, form_ids) for protocol in protocols]
    bundle = ReleaseBundle.create_working(
        tmp_path / "sample",
        release_id="v1.0.0-sample.1",
        repository_url="https://example.invalid/repo",
        repository_root=repository,
        expected_run_ids=[run.run_id for run in runs],
        paid_run_approval={
            "approved_by": "human:test",
            "approved_at": "2026-08-08T00:00:00+00:00",
            "scope": "test sample only",
            "max_spend_usd": 30.0,
            "release_id": "v1.0.0-sample.1",
            "run_ids": [run.run_id for run in runs],
            "model_id": "example/vision-model",
            "endpoint": "eligible-endpoint",
            "provider": "Exact Provider",
            "paid_calls": 20,
        },
        sample_contract={
            "protocol_ids": list(protocols),
            "form_set": "probe",
            "dialect_id": "enclosure.plain-v1",
            "execution_surface": "direct_api",
            "max_transport_attempts": 1,
            "trials_per_run": 5,
            "total_attempts": 20,
        },
    )
    for run in runs:
        trials, calls, events = _sample_records(run, form_ids)
        bundle.admit_run(run, trials, calls=calls, ledger_events=events)
    write_release_metrics(bundle.root, suite=load_suite(), runs=bundle.runs())
    build_site(bundle.root, bundle.root / "site")
    bundle.seal(repository_root=repository)
    assert len(bundle.runs()) == 4
    assert pq.read_table(bundle.root / "calls.parquet").num_rows == 20
    assert len((bundle.root / "ledger.jsonl").read_text().splitlines()) == 40


def test_sample_contract_rejects_empty_call_evidence(tmp_path):
    repository = _clean_repository(tmp_path / "repo")
    form_ids = tuple(load_suite().form_sets["probe"])
    run = _sample_run("reduce-infer-v1", form_ids)
    bundle = ReleaseBundle.create_working(
        tmp_path / "sample",
        release_id="v1.0.0-sample.1",
        repository_url="https://example.invalid/repo",
        repository_root=repository,
        sample_contract={
            "protocol_ids": [
                "reduce-infer-v1",
                "reduce-taught-v1",
                "transcribe-infer-v1",
                "transcribe-taught-v1",
            ],
            "form_set": "probe",
            "dialect_id": "enclosure.plain-v1",
            "execution_surface": "direct_api",
            "max_transport_attempts": 1,
            "trials_per_run": 5,
            "total_attempts": 20,
        },
    )
    trials, _calls, _events = _sample_records(run, form_ids)
    with pytest.raises(RuntimeError, match="attempt count"):
        bundle.admit_run(run, trials)
