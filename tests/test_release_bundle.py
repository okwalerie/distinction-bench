from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lofbench.metrics import write_release_metrics
from lofbench.protocols import get_protocol
from lofbench.records import CallRecord, LedgerEvent, RunManifest, TrialRecord
from lofbench.release_bundle import ReleaseBundle
from lofbench.run_models import trial_id_for
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
    run = RunManifest.plan(
        suite_version=suite_version,
        form_set="probe",
        dialect_set="parens.reference-v1",
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
        expected_trial_ids=(),
        status=status,
        attempts=5,
        token_usage={"input_tokens": 100, "output_tokens": 20, "reasoning_tokens": 0},
        latency_ms=50.0,
        cost_usd=0.005,
    )
    form_ids = load_suite().form_sets["probe"]
    return replace(
        run,
        expected_trial_ids=tuple(
            trial_id_for(run.run_id, form_id, run.dialect_set) for form_id in form_ids
        ),
    )


def _trial(run: RunManifest, form_index: int = 0, **overrides) -> TrialRecord:
    suite = load_suite()
    form_id = suite.form_sets["probe"][form_index]
    form = next(item for item in suite.forms if item["abstract_form_id"] == form_id)
    cell = next(
        item
        for item in suite.cells
        if item["abstract_form_id"] == form_id and item["dialect_id"] == run.dialect_set
    )
    protocol = get_protocol(run.protocol_id)
    values = {
        "trial_id": trial_id_for(run.run_id, form_id, run.dialect_set),
        "run_id": run.run_id,
        "suite_version": run.suite_version,
        "abstract_form_id": form_id,
        "dialect_id": run.dialect_set,
        "protocol_id": run.protocol_id,
        "execution_surface": run.execution_surface,
        "requested_model_id": run.requested_model_id,
        "resolved_model_id": run.resolved_model_id,
        "provider": run.provider,
        "endpoint": run.endpoint,
        "prompt_hash": protocol.prompt_hash(
            reading_rule=suite.specs[run.dialect_set].reading_rule,
            model_payload_sha256=cell["model_payload_sha256"],
        ),
        "symbolic_payload_hash": cell["symbolic_payload_hash"],
        "model_payload_sha256": cell["model_payload_sha256"],
        "parse_status": "valid",
        "attempt_count": 1,
        "latency_ms": 10.0,
        "input_tokens": 20,
        "output_tokens": 4,
        "reasoning_tokens": 0,
        "observed_cost_usd": 0.001,
        "prediction": form["normal_value"],
        "normal_value": form["normal_value"],
        "correct": True,
    }
    values.update(overrides)
    return TrialRecord(**values)


def _trials(run: RunManifest, **first_overrides) -> list[TrialRecord]:
    return [
        _trial(run, index, **(first_overrides if index == 0 else {}))
        for index in range(len(load_suite().form_sets["probe"]))
    ]


def _accounting(run: RunManifest) -> tuple[list[CallRecord], list[LedgerEvent]]:
    calls = []
    events = []
    for index, trial_id in enumerate(run.expected_trial_ids, start=1):
        call = CallRecord(
            call_id=f"call_{index}",
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
            provider_request_id=f"request_{index}",
        )
        calls.append(call)
        events.extend(
            [
                LedgerEvent(
                    event_type="reserved",
                    call_id=call.call_id,
                    trial_id=call.trial_id,
                    run_id=run.run_id,
                    cohort=run.cohort,
                    amount_usd=call.reserved_cost_usd,
                    at=call.started_at,
                ),
                LedgerEvent(
                    event_type="settled",
                    call_id=call.call_id,
                    trial_id=call.trial_id,
                    run_id=run.run_id,
                    cohort=run.cohort,
                    amount_usd=call.observed_cost_usd,
                    at=call.finished_at,
                ),
            ]
        )
    return calls, events


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
    _admit(bundle, run, _trials(run))
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
        _admit(bundle, replace(run, status="probed"), _trials(run))
    pilot = _run(suite_version="pilot-v0")
    with pytest.raises(RuntimeError, match="does not match"):
        _admit(bundle, pilot, _trials(pilot))


def test_resolved_model_drift_is_rejected(working):
    bundle, _repository, run = working
    with pytest.raises(RuntimeError, match="resolved_model_id"):
        _admit(bundle, run, _trials(run, resolved_model_id="substituted/model"))


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("trial_id", "trial_forged"),
        ("run_id", "run_forged"),
        ("suite_version", "forged-suite"),
        ("abstract_form_id", "lof_not_in_probe"),
        ("dialect_id", "pattern.plain-v1"),
        ("protocol_id", "reduce-taught-v1"),
        ("execution_surface", "agent"),
        ("requested_model_id", "forged/requested"),
        ("resolved_model_id", "forged/resolved"),
        ("provider", "forged-provider"),
        ("endpoint", "forged-endpoint"),
        ("prompt_hash", "0" * 64),
        ("symbolic_payload_hash", "0" * 32),
        ("model_payload_sha256", "0" * 64),
        ("normal_value", "forged-normal-value"),
    ],
)
def test_admission_recomputes_every_trial_provenance_field(working, field, bad_value):
    bundle, _repository, run = working
    with pytest.raises(RuntimeError, match="trial|frozen form"):
        _admit(bundle, run, _trials(run, **{field: bad_value}))


def test_admission_recomputes_run_expected_trial_ids(working):
    bundle, _repository, run = working
    forged = replace(run, expected_trial_ids=tuple(reversed(run.expected_trial_ids)))
    with pytest.raises(RuntimeError, match="expected trial ids"):
        _admit(bundle, forged, _trials(run))


def test_validation_rechecks_provenance_after_admission(working):
    bundle, _repository, run = working
    _admit(bundle, run, _trials(run))
    path = bundle.root / "trials.parquet"
    rows = pq.read_table(path).to_pylist()
    rows[0]["prompt_hash"] = "0" * 64
    pq.write_table(pa.Table.from_pylist(rows), path)
    with pytest.raises(RuntimeError, match="prompt_hash"):
        bundle.validate()


def test_dirty_repository_blocks_seal(working):
    bundle, repository, run = working
    _admit(bundle, run, _trials(run))
    (repository / "dirty").write_text("dirty")
    with pytest.raises(RuntimeError, match="clean worktree"):
        bundle.seal(repository_root=repository)


def test_unlisted_root_file_is_rejected(working):
    bundle, _repository, _run_manifest = working
    (bundle.root / "surprise.txt").write_text("nope")
    with pytest.raises(RuntimeError, match="root files"):
        bundle.validate()


def test_changed_human_trial_schema_is_rejected(working):
    bundle, _repository, _run_manifest = working
    schema_path = bundle.root / "human-trial.schema.json"
    schema = json.loads(schema_path.read_text())
    schema["title"] = "forged"
    schema_path.write_text(json.dumps(schema))
    with pytest.raises(RuntimeError, match="human trial schema"):
        bundle.validate()


def test_checksum_drift_is_rejected(working):
    bundle, repository, run = working
    _admit(bundle, run, _trials(run))
    bundle.seal(repository_root=repository)
    with (bundle.root / "transcripts.jsonl").open("a") as handle:
        handle.write("{}\n")
    with pytest.raises(RuntimeError, match="checksum drift"):
        bundle.validate()


def test_sealed_bundle_refuses_mutation(working):
    bundle, repository, run = working
    _admit(bundle, run, _trials(run))
    bundle.seal(repository_root=repository)
    with pytest.raises(RuntimeError, match="immutable"):
        _admit(bundle, run, _trials(run))


def test_schema_mismatch_is_rejected(working):
    bundle, _repository, _run_manifest = working
    release_path = bundle.root / "release.json"
    value = json.loads(release_path.read_text())
    value["bundle_schema_version"] = 999
    release_path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="unsupported"):
        ReleaseBundle.open(bundle.root)


def _sample_run(protocol_id: str, form_ids: tuple[str, ...]) -> RunManifest:
    run = RunManifest.plan(
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
        expected_trial_ids=(),
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
    return replace(
        run,
        expected_trial_ids=tuple(
            trial_id_for(run.run_id, form_id, run.dialect_set) for form_id in form_ids
        ),
    )


def _sample_records(run: RunManifest, form_ids: tuple[str, ...]):
    suite = load_suite()
    forms = {form["abstract_form_id"]: form for form in suite.forms}
    cells = {
        cell["abstract_form_id"]: cell
        for cell in suite.cells
        if cell["dialect_id"] == run.dialect_set
    }
    protocol = get_protocol(run.protocol_id)
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
                prompt_hash=protocol.prompt_hash(
                    reading_rule=suite.specs[run.dialect_set].reading_rule,
                    model_payload_sha256=cell["model_payload_sha256"],
                ),
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
    runs_page = (bundle.root / "site" / "runs.html").read_text()
    for marker in (
        "containment tree and frozen form",
        "actual rendered stimulus",
        "reading rule, protocol, and exact prompt",
        "target and recorded response",
        "parse and scorer identity",
        "profile contribution",
        "dialect matrix",
        "family matrix",
        "reasoning contrasts",
        "not run",
        "exact endpoint + routing provenance",
        "allow_fallbacks",
        "input/output/reasoning tokens",
    ):
        assert marker in runs_page
    human_page = (bundle.root / "site" / "human.html").read_text()
    assert "HumanTrialRecord" in human_page
    assert "familiarity_band" in human_page
    assert "records:rows" in human_page
    assert (bundle.root / "site" / "downloads" / "human-trial.schema.json").is_file()
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
