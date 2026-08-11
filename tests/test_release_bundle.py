from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

from dbench.provider_evidence import (
    OPENROUTER_CHAT_SOURCE,
    OPENROUTER_GENERATION_SOURCE,
    project_openrouter_evidence,
)
from dbench.reissue import reissue_sealed_release
from dbench.release_policy import validate_sample_release
from lofbench.authority import (
    PROTOCOL_REGISTRY_GIT_PATH,
    SUITE_REGISTRY_GIT_PATH,
    authority_from_git,
    canonical_sha256,
    derive_run_authority,
)
from lofbench.metrics import write_release_metrics
from lofbench.protocols import DEFAULT_PROTOCOL_REGISTRY, get_protocol
from lofbench.provider_evidence import ProviderEvidenceEnvelope, ProviderEvidenceSource
from lofbench.publication import archive_release
from lofbench.records import (
    AttemptEvidence,
    CallRecord,
    LedgerEvent,
    RequestStartedRecord,
    RunManifest,
    TrialRecord,
    execution_spec_identity,
)
from lofbench.release_bundle import ReleaseBundle, _matches_finite_aggregate
from lofbench.run_models import call_id_for, request_sha256_for, trial_id_for
from lofbench.suites import DEFAULT_SUITE_REGISTRY, load_suite
from lofsite.build import build_site


def _provider_envelope(
    run: RunManifest,
    *,
    response_text: str,
    request_id: str,
    started_at: str,
    finished_at: str,
    input_tokens: int = 20,
    output_tokens: int = 4,
    reasoning_tokens: int = 0,
    cost_usd: float = 0.001,
) -> ProviderEvidenceEnvelope:
    selected = run.catalog_row["selected_endpoint"]
    chat = {
        "id": request_id,
        "model": run.requested_model_id,
        "provider": selected["provider_name"],
        "choices": [{"message": {"content": response_text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    }
    generation = {
        "data": {
            "id": request_id,
            "model": run.resolved_model_id,
            "provider_name": selected["provider_name"],
            "total_cost": cost_usd,
            "native_tokens_prompt": input_tokens,
            "native_tokens_completion": output_tokens,
            "native_tokens_reasoning": reasoning_tokens,
            "latency": 10.0,
            "finish_reason": "stop",
        }
    }
    return ProviderEvidenceEnvelope(
        schema_version=2,
        adapter_id="openrouter-direct-v2",
        sources=(
            ProviderEvidenceSource.capture_http(
                label=OPENROUTER_CHAT_SOURCE,
                sequence=1,
                request_started_at=started_at,
                response_finished_at=finished_at,
                request_method="POST",
                request_url="https://openrouter.ai/api/v1/chat/completions",
                http_status=200,
                response_headers=(),
                raw_body=json.dumps(chat).encode(),
            ),
            ProviderEvidenceSource.capture_http(
                label=OPENROUTER_GENERATION_SOURCE,
                sequence=1,
                request_started_at=finished_at,
                response_finished_at=finished_at,
                request_method="GET",
                request_url=f"https://openrouter.ai/api/v1/generation?id={request_id}",
                http_status=200,
                response_headers=(),
                raw_body=json.dumps(generation).encode(),
            ),
        ),
    )


def _replace_evidence_source(
    envelope: ProviderEvidenceEnvelope,
    label: str,
    payload: dict,
) -> ProviderEvidenceEnvelope:
    return replace(
        envelope,
        sources=tuple(
            ProviderEvidenceSource.capture_http(
                label=source.label,
                sequence=source.sequence,
                request_started_at=source.request_started_at,
                response_finished_at=source.response_finished_at,
                request_method=source.request_method,
                request_url=source.request_url,
                http_status=source.http_status,
                response_headers=source.response_headers,
                raw_body=json.dumps(payload).encode(),
                transport_error=source.transport_error,
                transport_error_message=source.transport_error_message,
            )
            if source.label == label
            else source
            for source in envelope.sources
        ),
    )


def _clean_repository(path: Path) -> Path:
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "anchor").write_text("test\n")
    suite_target = path / SUITE_REGISTRY_GIT_PATH
    suite_target.parent.mkdir(parents=True)
    protocol_target = path / PROTOCOL_REGISTRY_GIT_PATH
    protocol_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_SUITE_REGISTRY, suite_target)
    shutil.copyfile(DEFAULT_PROTOCOL_REGISTRY, protocol_target)
    subprocess.run(["git", "add", "anchor", "src"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "test"], cwd=path, check=True)
    return path


def _run(
    *,
    status: str = "complete",
    suite_version: str = "v1",
    max_transport_attempts: int = 1,
) -> RunManifest:
    values = dict(
        suite_version=suite_version,
        form_set="probe",
        dialect_id="parens.reference-v1",
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
        max_transport_attempts=max_transport_attempts,
        expected_trial_ids=(),
        pricing={"prompt": 0.000001, "completion": 0.000002, "image": 0.000001},
        authority={},
        status=status,
        attempts=5,
        token_usage={"input_tokens": 100, "output_tokens": 20, "reasoning_tokens": 0},
        latency_ms=50.0,
        cost_usd=0.005,
        catalog_row={
            "selected_endpoint": {
                "tag": "provider/example",
                "provider_name": "Example Provider",
            }
        },
    )
    form_ids = load_suite().form_sets["probe"]
    values["authority"] = derive_run_authority(
        suite_bytes=DEFAULT_SUITE_REGISTRY.read_bytes(),
        protocol_bytes=DEFAULT_PROTOCOL_REGISTRY.read_bytes(),
        form_ids=form_ids,
        dialect_id=values["dialect_id"],
        protocol_id=values["protocol_id"],
        execution_spec=execution_spec_identity(values),
        catalog_retrieved_at="",
        catalog_row=values["catalog_row"],
    ).to_dict()
    run = RunManifest.plan(**values)
    return replace(
        run,
        expected_trial_ids=tuple(
            trial_id_for(run.run_id, form_id, run.dialect_id) for form_id in form_ids
        ),
    )


def _trial(run: RunManifest, form_index: int = 0, **overrides) -> TrialRecord:
    suite = load_suite()
    form_id = suite.form_sets["probe"][form_index]
    form = next(item for item in suite.forms if item["abstract_form_id"] == form_id)
    cell = next(
        item
        for item in suite.cells
        if item["abstract_form_id"] == form_id and item["dialect_id"] == run.dialect_id
    )
    protocol = get_protocol(run.protocol_id)
    response_text = (
        json.dumps({"value": form["normal_value"]})
        if protocol.answer_kind == "normal_value"
        else json.dumps({"tree": form["abstract_form"]})
    )
    parse_status, prediction, correct = protocol.parse_answer(
        response_text,
        expected_normal_value=form["normal_value"],
        expected_tree=form["abstract_form"],
    )
    values = {
        "trial_id": trial_id_for(run.run_id, form_id, run.dialect_id),
        "run_id": run.run_id,
        "suite_version": run.suite_version,
        "abstract_form_id": form_id,
        "dialect_id": run.dialect_id,
        "protocol_id": run.protocol_id,
        "execution_surface": run.execution_surface,
        "requested_model_id": run.requested_model_id,
        "resolved_model_id": run.resolved_model_id,
        "provider": run.provider,
        "endpoint": run.endpoint,
        "prompt_hash": protocol.prompt_hash(
            reading_rule=suite.specs[run.dialect_id].reading_rule,
            model_payload_sha256=cell["model_payload_sha256"],
        ),
        "symbolic_payload_hash": cell["symbolic_payload_hash"],
        "model_payload_sha256": cell["model_payload_sha256"],
        "parse_status": parse_status,
        "attempt_count": 1,
        "latency_ms": 10.0,
        "provider_latency_ms": 10.0,
        "input_tokens": 20,
        "output_tokens": 4,
        "reasoning_tokens": 0,
        "observed_cost_usd": 0.001,
        "prediction": prediction,
        "normal_value": form["normal_value"],
        "correct": correct,
        "response_text": response_text,
    }
    values.update(overrides)
    return TrialRecord(**values)


def _trials(run: RunManifest, **first_overrides) -> list[TrialRecord]:
    return [
        _trial(run, index, **(first_overrides if index == 0 else {}))
        for index in range(len(load_suite().form_sets["probe"]))
    ]


def _accounting(
    run: RunManifest,
    trials: list[TrialRecord],
    *,
    latencies_ms: tuple[float, ...] | None = None,
) -> tuple[list[TrialRecord], list[CallRecord], list[LedgerEvent], list[AttemptEvidence]]:
    calls = []
    events = []
    evidence = []
    linked_trials = []
    trials_by_id = {trial.trial_id: trial for trial in trials}
    for index, trial_id in enumerate(run.expected_trial_ids, start=1):
        trial = trials_by_id[trial_id]
        latency_ms = latencies_ms[index - 1] if latencies_ms is not None else 10.0
        call_id = call_id_for(trial_id, 1)
        started = datetime(2026, 8, 8, 0, 0, index, tzinfo=UTC)
        started_at = started.isoformat(timespec="microseconds")
        finished_at = (started + timedelta(milliseconds=latency_ms)).isoformat(
            timespec="microseconds"
        )
        record = AttemptEvidence.capture(
            call_id=call_id,
            trial_id=trial_id,
            run_id=run.run_id,
            attempt=1,
            provider_evidence=_provider_envelope(
                run,
                response_text=trial.response_text,
                request_id=f"request_{index}",
                started_at=started_at,
                finished_at=finished_at,
            ),
        )
        call = CallRecord(
            call_id=call_id,
            trial_id=trial_id,
            run_id=run.run_id,
            attempt=1,
            started_at=started_at,
            finished_at=finished_at,
            status="complete",
            reserved_cost_usd=0.01,
            observed_cost_usd=0.001,
            resolved_model_id=run.resolved_model_id,
            provider=run.provider,
            endpoint=run.endpoint,
            latency_ms=latency_ms,
            provider_latency_ms=10.0,
            input_tokens=20,
            output_tokens=4,
            reasoning_tokens=0,
            provider_request_id=f"request_{index}",
            response_sha256=sha256(trial.response_text.encode()).hexdigest(),
            evidence_sha256=record.evidence_sha256,
        )
        calls.append(call)
        evidence.append(record)
        linked_trials.append(
            replace(
                trial,
                latency_ms=latency_ms if latencies_ms is not None else trial.latency_ms,
                completion_evidence_sha256=record.evidence_sha256,
            )
        )
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
    return linked_trials, calls, events, evidence


def _request_records(
    run: RunManifest,
    trials: list[TrialRecord],
    calls: list[CallRecord],
) -> list[RequestStartedRecord]:
    by_id = {trial.trial_id: trial for trial in trials}
    return [
        RequestStartedRecord(
            call_id=call.call_id,
            trial_id=call.trial_id,
            run_id=run.run_id,
            attempt=call.attempt,
            request_sha256=request_sha256_for(
                call_id=call.call_id,
                trial_id=call.trial_id,
                run_id=run.run_id,
                attempt=call.attempt,
                prompt_hash=by_id[call.trial_id].prompt_hash,
                model_payload_sha256=by_id[call.trial_id].model_payload_sha256,
            ),
            started_at=call.started_at,
        )
        for call in calls
    ]


def _admit(bundle: ReleaseBundle, run: RunManifest, trials: list[TrialRecord]) -> None:
    if {trial.trial_id for trial in trials} != set(run.expected_trial_ids):
        bundle.admit_run(run, trials)
        return
    linked_trials, calls, events, evidence = _accounting(run, trials)
    bundle.admit_run(
        run,
        linked_trials,
        calls=calls,
        request_starts=_request_records(run, linked_trials, calls),
        ledger_events=events,
        evidence=evidence,
    )


def _derive(bundle: ReleaseBundle) -> None:
    write_release_metrics(bundle.root, suite=load_suite(), runs=bundle.runs())


@pytest.fixture(scope="session")
def authority_repository(tmp_path_factory):
    return _clean_repository(tmp_path_factory.mktemp("authority-repo"))


@pytest.fixture
def working(tmp_path, authority_repository):
    repository = authority_repository
    run = _run()
    bundle = ReleaseBundle.create_working(
        tmp_path / "release",
        release_id="v1.0.0-test",
        repository_url="https://example.invalid/repo",
        repository_root=repository,
        expected_run_ids=(run.run_id,),
        spend_caps_usd={"global": 30.0, "cohorts": {"test-release": 30.0}},
        evidence_projector=project_openrouter_evidence,
        release_policy_validator=validate_sample_release,
    )
    return bundle, repository, run


def test_create_validate_admit_and_seal(working):
    bundle, repository, run = working
    bundle.validate()
    _admit(bundle, run, _trials(run))
    _derive(bundle)
    bundle.seal(repository_root=repository)
    reopened = ReleaseBundle.open(
        bundle.root,
        evidence_projector=project_openrouter_evidence,
        release_policy_validator=validate_sample_release,
    )
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


def test_admission_recomputes_run_id_from_execution_identity(working):
    bundle, _repository, run = working
    forged = replace(run, run_id="run_forged")
    with pytest.raises(RuntimeError, match="run_id"):
        _admit(bundle, forged, _trials(forged))


def test_planned_run_authority_is_recomputed_before_execution(working):
    bundle, _repository, run = working
    bundle.validate_planned_run(run)
    values = run.to_dict()
    values.pop("run_id")
    values["authority"] = {
        **run.authority,
        "selected_form_set_sha256": "0" * 64,
    }
    forged = RunManifest.plan(**values)
    with pytest.raises(RuntimeError, match="authority"):
        bundle.validate_planned_run(forged)


def test_admission_recomputes_parse_and_score_from_response_text(working):
    bundle, _repository, run = working
    with pytest.raises(RuntimeError, match="parse_status"):
        _admit(bundle, run, _trials(run, response_text="not-json"))


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


def test_release_accounting_rejects_orphan_29999_settlement(working):
    bundle, _repository, run = working
    _admit(bundle, run, _trials(run))
    orphan = LedgerEvent(
        event_type="settled",
        call_id="call_orphan",
        trial_id=run.expected_trial_ids[0],
        run_id=run.run_id,
        cohort=run.cohort,
        amount_usd=29.999,
        at="2026-08-08T00:00:59+00:00",
    )
    with (bundle.root / "ledger.jsonl").open("a") as handle:
        handle.write(json.dumps(orphan.to_dict()) + "\n")
    with pytest.raises(RuntimeError, match="ledger"):
        bundle.validate()


@pytest.mark.parametrize(
    ("caps", "message"),
    [
        ({"global": 0.004, "cohorts": {"test-release": 30.0}}, "global cap"),
        ({"global": 30.0, "cohorts": {"test-release": 0.004}}, "cohort"),
    ],
)
def test_admission_enforces_release_global_and_cohort_caps(working, caps, message):
    bundle, _repository, run = working
    bundle.manifest["spend_caps_usd"] = caps
    with pytest.raises(RuntimeError, match=message):
        _admit(bundle, run, _trials(run))


def test_validation_rejects_forged_profile_and_effect_rows(working):
    bundle, _repository, run = working
    _admit(bundle, run, _trials(run))
    _derive(bundle)
    profile_path = bundle.root / "profiles.parquet"
    profiles = pq.read_table(profile_path).to_pylist()
    profiles[0]["competence"] = 0.123
    pq.write_table(pa.Table.from_pylist(profiles), profile_path)
    with pytest.raises(RuntimeError, match="profiles do not recompute"):
        bundle.validate()

    _derive(bundle)
    pq.write_table(
        pa.Table.from_pylist([{"run_id": run.run_id}]),
        bundle.root / "effects.parquet",
    )
    with pytest.raises(RuntimeError, match="effects do not recompute"):
        bundle.validate()


def test_dirty_repository_blocks_seal(working):
    bundle, repository, run = working
    _admit(bundle, run, _trials(run))
    dirty = repository / "dirty"
    dirty.write_text("dirty")
    try:
        with pytest.raises(RuntimeError, match="clean worktree"):
            bundle.seal(repository_root=repository)
    finally:
        dirty.unlink()


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


def test_bundle_suite_copy_is_evidence_not_mutable_authority(working):
    bundle, _repository, _run_manifest = working
    suite_path = bundle.root / "suite.json"
    suite = json.loads(suite_path.read_text())
    suite["form_sets"]["probe"] = list(reversed(suite["form_sets"]["probe"]))
    suite_path.write_text(json.dumps(suite))
    with pytest.raises(RuntimeError, match="authority|registry"):
        bundle.validate()


def test_bundle_protocol_copy_is_evidence_not_mutable_authority(working):
    bundle, _repository, _run_manifest = working
    protocols_path = bundle.root / "protocols.json"
    protocols = json.loads(protocols_path.read_text())
    protocols["protocols"]["reduce-infer-v1"]["system_text"] = "forged"
    protocols_path.write_text(json.dumps(protocols))
    with pytest.raises(RuntimeError, match="authority|registry"):
        bundle.validate()


def test_authority_validation_reads_recorded_git_tree_not_worktree(working):
    bundle, repository, _run_manifest = working
    source_suite = repository / SUITE_REGISTRY_GIT_PATH
    original = source_suite.read_bytes()
    source_suite.write_text("forged current worktree")
    try:
        bundle.validate()
    finally:
        source_suite.write_bytes(original)


def test_admission_rejects_incomplete_contradictory_attempt_evidence(working):
    bundle, _repository, run = working
    trials, calls, events, evidence = _accounting(run, _trials(run))
    chat_source = next(
        source
        for source in evidence[0].provider_evidence.sources
        if source.label == OPENROUTER_CHAT_SOURCE
    )
    chat = chat_source.payload()
    chat["choices"][0]["message"]["content"] = '{"value":"contradictory"}'
    contradictory = replace(
        evidence[0],
        provider_evidence=_replace_evidence_source(
            evidence[0].provider_evidence, OPENROUTER_CHAT_SOURCE, chat
        ),
    )
    with pytest.raises(RuntimeError, match="evidence|transcript"):
        bundle.admit_run(
            run,
            trials,
            calls=calls,
            request_starts=_request_records(run, trials, calls),
            ledger_events=events,
            evidence=[contradictory],
        )


def test_admission_requires_exact_request_started_records(working):
    bundle, _repository, run = working
    trials, calls, events, evidence = _accounting(run, _trials(run))
    with pytest.raises(RuntimeError, match="request-started"):
        bundle.admit_run(
            run,
            trials,
            calls=calls,
            ledger_events=events,
            evidence=evidence,
        )


def test_ambiguous_run_cannot_be_admitted_or_sealed(working):
    bundle, repository, run = working
    ambiguous = replace(run, status="ambiguous", attempts=0)
    with pytest.raises(RuntimeError, match="only complete"):
        bundle.admit_run(ambiguous, [])
    with pytest.raises(RuntimeError, match="expected runs|admitted"):
        bundle.seal(repository_root=repository)


def test_admission_persists_and_revalidates_request_started_records(working):
    bundle, _repository, run = working
    trials, calls, events, evidence = _accounting(run, _trials(run))
    requests = _request_records(run, trials, calls)
    bundle.admit_run(
        run,
        trials,
        calls=calls,
        request_starts=requests,
        ledger_events=events,
        evidence=evidence,
    )
    assert len((bundle.root / "request-started.jsonl").read_text().splitlines()) == 5
    _derive(bundle)
    bundle.validate()


@pytest.mark.parametrize("reverse_calls", [False, True])
def test_release_accepts_cross_runtime_latency_aggregate_at_recorded_scale(
    working,
    reverse_calls,
):
    bundle, _repository, run = working
    latencies_ms = (
        16_400.828,
        17_015.099,
        16_374.375,
        16_631.365999999998,
        16_558.981,
    )
    trials, calls, events, evidence = _accounting(
        run,
        _trials(run),
        latencies_ms=latencies_ms,
    )
    recorded_latency_ms = pc.sum(pa.array(latencies_ms)).as_py()
    assert abs(math.fsum(latencies_ms) - recorded_latency_ms) > 1e-12
    if reverse_calls:
        calls.reverse()
    run = replace(run, latency_ms=recorded_latency_ms)

    bundle.admit_run(
        run,
        trials,
        calls=calls,
        request_starts=_request_records(run, trials, calls),
        ledger_events=events,
        evidence=evidence,
    )
    _derive(bundle)
    bundle.validate()


def test_release_aggregate_tolerance_rejects_material_and_nonfinite_drift():
    latencies_ms = (
        16_400.828,
        17_015.099,
        16_374.375,
        16_631.365999999998,
        16_558.981,
    )
    recorded_latency_ms = pc.sum(pa.array(latencies_ms)).as_py()
    assert _matches_finite_aggregate(recorded_latency_ms, latencies_ms)
    assert not _matches_finite_aggregate(recorded_latency_ms + 1e-6, latencies_ms)
    assert not _matches_finite_aggregate(float("nan"), latencies_ms)
    assert not _matches_finite_aggregate(recorded_latency_ms, (*latencies_ms, float("inf")))


def test_validation_rejects_a_tampered_request_started_record(working):
    bundle, _repository, run = working
    trials, calls, events, evidence = _accounting(run, _trials(run))
    bundle.admit_run(
        run,
        trials,
        calls=calls,
        request_starts=_request_records(run, trials, calls),
        ledger_events=events,
        evidence=evidence,
    )
    path = bundle.root / "request-started.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["request_sha256"] = "0" * 64
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(RuntimeError, match="request-started hash"):
        bundle.validate()


def test_admission_rejects_a_forged_request_started_hash(working):
    bundle, _repository, run = working
    trials, calls, events, evidence = _accounting(run, _trials(run))
    requests = _request_records(run, trials, calls)
    requests[0] = replace(requests[0], request_sha256="0" * 64)
    with pytest.raises(RuntimeError, match="request-started hash"):
        bundle.admit_run(
            run,
            trials,
            calls=calls,
            request_starts=requests,
            ledger_events=events,
            evidence=evidence,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "completion",
        "request_id",
        "model",
        "provider",
        "endpoint",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cost",
        "latency",
        "error",
    ],
)
def test_admission_rejects_raw_provider_evidence_contradictions(working, mutation):
    bundle, _repository, run = working
    trials, calls, events, evidence = _accounting(run, _trials(run))
    envelope = evidence[0].provider_evidence
    chat_source = next(
        source for source in envelope.sources if source.label == OPENROUTER_CHAT_SOURCE
    )
    generation_source = next(
        source for source in envelope.sources if source.label == OPENROUTER_GENERATION_SOURCE
    )
    chat = chat_source.payload()
    generation = generation_source.payload()
    if mutation == "completion":
        chat["choices"][0]["message"]["content"] = '{"value":"forged"}'
    elif mutation == "request_id":
        chat["id"] = generation["data"]["id"] = "request_forged"
    elif mutation == "model":
        chat["model"] = generation["data"]["model"] = "forged/model"
    elif mutation in {"provider", "endpoint"}:
        generation["data"]["provider_name"] = "forged-provider"
    elif mutation == "input_tokens":
        chat["usage"]["prompt_tokens"] = 999
    elif mutation == "output_tokens":
        chat["usage"]["completion_tokens"] = 999
    elif mutation == "reasoning_tokens":
        chat["usage"]["completion_tokens_details"]["reasoning_tokens"] = 999
    elif mutation == "cost":
        generation["data"]["total_cost"] = 0.123
    elif mutation == "latency":
        envelope = replace(
            envelope,
            sources=tuple(
                replace(source, response_finished_at="2026-08-08T00:00:01+00:00")
                if source.label == OPENROUTER_CHAT_SOURCE
                else source
                for source in envelope.sources
            ),
        )
    else:
        chat = {"error": {"type": "forged_error"}}
    envelope = _replace_evidence_source(envelope, OPENROUTER_CHAT_SOURCE, chat)
    envelope = _replace_evidence_source(envelope, OPENROUTER_GENERATION_SOURCE, generation)
    material = evidence[0].digest_material()
    material["provider_evidence"] = envelope.to_dict()
    evidence[0] = AttemptEvidence.capture(**material)
    calls[0] = replace(calls[0], evidence_sha256=evidence[0].evidence_sha256)
    trials[0] = replace(trials[0], completion_evidence_sha256=evidence[0].evidence_sha256)

    with pytest.raises(RuntimeError, match="evidence"):
        bundle.admit_run(
            run,
            trials,
            calls=calls,
            request_starts=_request_records(run, trials, calls),
            ledger_events=events,
            evidence=evidence,
        )


def test_admission_fails_closed_without_generation_evidence(working):
    bundle, _repository, run = working
    trials, calls, events, evidence = _accounting(run, _trials(run))
    envelope = replace(
        evidence[0].provider_evidence,
        sources=tuple(
            source
            for source in evidence[0].provider_evidence.sources
            if source.label != OPENROUTER_GENERATION_SOURCE
        ),
    )
    material = evidence[0].digest_material()
    material["provider_evidence"] = envelope.to_dict()
    evidence[0] = AttemptEvidence.capture(**material)
    calls[0] = replace(calls[0], evidence_sha256=evidence[0].evidence_sha256)
    trials[0] = replace(trials[0], completion_evidence_sha256=evidence[0].evidence_sha256)
    with pytest.raises(RuntimeError, match="evidence"):
        bundle.admit_run(
            run,
            trials,
            calls=calls,
            request_starts=_request_records(run, trials, calls),
            ledger_events=events,
            evidence=evidence,
        )


def test_completed_trial_error_type_is_derived_from_final_attempt_evidence(working):
    bundle, _repository, run = working
    with pytest.raises(RuntimeError, match="completion evidence"):
        _admit(bundle, run, _trials(run, error_type="forged_error"))


def test_admission_rejects_raw_error_contradicting_failed_call(tmp_path, authority_repository):
    run = _run(max_transport_attempts=2)
    bundle = ReleaseBundle.create_working(
        tmp_path / "release",
        release_id="v1.0.0-test",
        repository_url="https://example.invalid/repo",
        repository_root=authority_repository,
        expected_run_ids=(run.run_id,),
        spend_caps_usd={"global": 30.0, "cohorts": {"test-release": 30.0}},
        evidence_projector=project_openrouter_evidence,
        release_policy_validator=validate_sample_release,
    )
    trials, calls, events, evidence = _accounting(run, _trials(run))
    original_call = calls[0]
    original_evidence = evidence[0]
    completed_call_id = call_id_for(original_call.trial_id, 2)
    completed_material = original_evidence.digest_material()
    completed_material.update(call_id=completed_call_id, attempt=2)
    completed_evidence = AttemptEvidence.capture(**completed_material)
    calls[0] = replace(
        original_call,
        call_id=completed_call_id,
        attempt=2,
        evidence_sha256=completed_evidence.evidence_sha256,
    )
    evidence[0] = completed_evidence
    trials[0] = replace(
        trials[0],
        attempt_count=2,
        completion_evidence_sha256=completed_evidence.evidence_sha256,
    )
    events[:2] = [replace(event, call_id=completed_call_id) for event in events[:2]]

    failed_envelope = ProviderEvidenceEnvelope(
        schema_version=2,
        adapter_id="openrouter-direct-v2",
        sources=(
            ProviderEvidenceSource.capture_http(
                label=OPENROUTER_CHAT_SOURCE,
                sequence=1,
                request_started_at="2026-08-08T00:00:00.000000+00:00",
                response_finished_at="2026-08-08T00:00:00.005000+00:00",
                request_method="POST",
                request_url="https://openrouter.ai/api/v1/chat/completions",
                http_status=503,
                response_headers=(),
                raw_body=json.dumps({"id": "failed-request", "error": {"type": "busy"}}).encode(),
            ),
            ProviderEvidenceSource.capture_http(
                label=OPENROUTER_GENERATION_SOURCE,
                sequence=1,
                request_started_at="2026-08-08T00:00:00.005000+00:00",
                response_finished_at="2026-08-08T00:00:00.005000+00:00",
                request_method="GET",
                request_url=("https://openrouter.ai/api/v1/generation?id=failed-request"),
                http_status=200,
                response_headers=(),
                raw_body=json.dumps(
                    {
                        "data": {
                            "id": "failed-request",
                            "model": run.resolved_model_id,
                            "provider_name": run.catalog_row["selected_endpoint"]["provider_name"],
                            "native_tokens_prompt": 0,
                            "native_tokens_completion": 0,
                            "native_tokens_reasoning": 0,
                            "total_cost": 0.0,
                            "latency": 5.0,
                            "finish_reason": "error",
                        }
                    }
                ).encode(),
            ),
        ),
    )
    failed_evidence = AttemptEvidence.capture(
        call_id=original_call.call_id,
        trial_id=original_call.trial_id,
        run_id=run.run_id,
        attempt=1,
        provider_evidence=failed_envelope,
    )
    failed_call = replace(
        original_call,
        started_at=failed_envelope.sources[0].request_started_at,
        finished_at=failed_envelope.sources[0].response_finished_at,
        status="provider_error",
        observed_cost_usd=0.0,
        resolved_model_id=run.resolved_model_id,
        latency_ms=5.0,
        provider_latency_ms=5.0,
        input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        provider_request_id="failed-request",
        error_type="provider_busy",
        response_sha256=sha256(b"").hexdigest(),
        evidence_sha256=failed_evidence.evidence_sha256,
    )
    calls.insert(0, failed_call)
    evidence.insert(0, failed_evidence)
    events[:0] = [
        LedgerEvent(
            event_type="reserved",
            call_id=failed_call.call_id,
            trial_id=failed_call.trial_id,
            run_id=run.run_id,
            cohort=run.cohort,
            amount_usd=failed_call.reserved_cost_usd,
            at=failed_call.started_at,
        ),
        LedgerEvent(
            event_type="settled",
            call_id=failed_call.call_id,
            trial_id=failed_call.trial_id,
            run_id=run.run_id,
            cohort=run.cohort,
            amount_usd=0.0,
            at=failed_call.finished_at,
        ),
    ]
    run = replace(run, attempts=6, latency_ms=55.0)

    forged_envelope = _replace_evidence_source(
        failed_envelope,
        OPENROUTER_CHAT_SOURCE,
        {"id": "failed-request", "error": {"type": "forged"}},
    )
    forged_material = failed_evidence.digest_material()
    forged_material["provider_evidence"] = forged_envelope.to_dict()
    evidence[0] = AttemptEvidence.capture(**forged_material)
    calls[0] = replace(calls[0], evidence_sha256=evidence[0].evidence_sha256)
    with pytest.raises(RuntimeError, match="evidence contradicts.*error_type"):
        bundle.admit_run(
            run,
            trials,
            calls=calls,
            request_starts=_request_records(run, trials, calls),
            ledger_events=events,
            evidence=evidence,
        )


def test_admission_accepts_a_complete_accounting_recovery_revision_chain(working):
    bundle, _repository, run = working
    trials, calls, events, evidence = _accounting(run, _trials(run))
    final = evidence[0]
    unknown_envelope = replace(
        final.provider_evidence,
        sources=tuple(
            source
            for source in final.provider_evidence.sources
            if source.label != OPENROUTER_GENERATION_SOURCE
        ),
    )
    initial = AttemptEvidence.capture(
        call_id=final.call_id,
        trial_id=final.trial_id,
        run_id=final.run_id,
        attempt=final.attempt,
        provider_evidence=unknown_envelope,
    )
    recovered = initial.revise(final.provider_evidence)
    evidence[:1] = [initial, recovered]
    calls[0] = replace(calls[0], evidence_sha256=recovered.evidence_sha256)
    trials[0] = replace(trials[0], completion_evidence_sha256=recovered.evidence_sha256)
    bundle.admit_run(
        run,
        trials,
        calls=calls,
        request_starts=_request_records(run, trials, calls),
        ledger_events=events,
        evidence=evidence,
    )
    rows = [
        json.loads(line) for line in (bundle.root / "transcripts.jsonl").read_text().splitlines()
    ]
    assert [row["revision"] for row in rows[:2]] == [0, 1]


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_admission_requires_exact_evidence_revision_chains(working, mutation):
    bundle, _repository, run = working
    trials, calls, events, evidence = _accounting(run, _trials(run))
    if mutation == "missing":
        evidence.pop()
    elif mutation == "extra":
        material = evidence[0].digest_material()
        material["call_id"] = "call_extra"
        evidence.append(AttemptEvidence.capture(**material))
    else:
        evidence.append(evidence[0])
    with pytest.raises(RuntimeError, match="evidence"):
        bundle.admit_run(
            run,
            trials,
            calls=calls,
            request_starts=_request_records(run, trials, calls),
            ledger_events=events,
            evidence=evidence,
        )


@pytest.mark.parametrize(
    ("trial_overrides", "run_overrides"),
    [
        ({"latency_ms": float("nan")}, {}),
        (
            {"reasoning_tokens": -1},
            {
                "token_usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "reasoning_tokens": -1,
                }
            },
        ),
        ({}, {"latency_ms": -1.0}),
        ({}, {"latency_ms": 49.0}),
    ],
)
def test_admission_rejects_nonfinite_or_negative_resources(working, trial_overrides, run_overrides):
    bundle, _repository, run = working
    trials, calls, events, evidence = _accounting(run, _trials(run, **trial_overrides))
    if "reasoning_tokens" in trial_overrides:
        calls[0] = replace(calls[0], reasoning_tokens=-1)
    forged_run = replace(run, **run_overrides)
    with pytest.raises(RuntimeError, match="finite|nonnegative|latency|usage|reasoning"):
        bundle.admit_run(
            forged_run,
            trials,
            calls=calls,
            request_starts=_request_records(forged_run, trials, calls),
            ledger_events=events,
            evidence=evidence,
        )


def test_checksum_drift_is_rejected(working):
    bundle, repository, run = working
    _admit(bundle, run, _trials(run))
    _derive(bundle)
    bundle.seal(repository_root=repository)
    with (bundle.root / "transcripts.jsonl").open("a") as handle:
        handle.write("{}\n")
    with pytest.raises(RuntimeError, match="attempt evidence|checksum drift"):
        bundle.validate()


def test_sealed_bundle_refuses_mutation(working):
    bundle, repository, run = working
    _admit(bundle, run, _trials(run))
    _derive(bundle)
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
    values = dict(
        suite_version="v1",
        form_set="probe",
        dialect_id="enclosure.plain-v1",
        protocol_id=protocol_id,
        requested_model_id="example/vision-model",
        resolved_model_id="example/vision-model-20260808",
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
        pricing={"prompt": 0.000001, "completion": 0.000002, "image": 0.000001},
        authority={},
        status="complete",
        attempts=5,
        token_usage={"input_tokens": 100, "output_tokens": 20, "reasoning_tokens": 0},
        latency_ms=50.0,
        cost_usd=0.005,
        catalog_row={
            "authenticated": True,
            "selected_endpoint": {
                "model_id": "example/vision-model",
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
            "authenticated_user_model": {
                "id": "example/vision-model",
                "canonical_slug": "example/vision-model-20260808",
                "name": "Example Vision Model",
            },
            "authenticated_user_models_retrieval": {
                "label": "openrouter.catalog.response.v1",
                "request_started_at": "2026-08-08T00:00:00+00:00",
                "response_finished_at": "2026-08-08T00:00:01+00:00",
                "request_method": "GET",
                "request_url": "https://openrouter.ai/api/v1/models/user",
                "http_status": 200,
                "response_headers": [["content-type", "application/json"]],
                "raw_body_sha256": "0" * 64,
                "raw_body_bytes": 100,
                "text_decoding": "utf-8",
                "json_parse_outcome": "parsed",
            },
        },
    )
    values["authority"] = derive_run_authority(
        suite_bytes=DEFAULT_SUITE_REGISTRY.read_bytes(),
        protocol_bytes=DEFAULT_PROTOCOL_REGISTRY.read_bytes(),
        form_ids=form_ids,
        dialect_id=values["dialect_id"],
        protocol_id=protocol_id,
        execution_spec=execution_spec_identity(values),
        catalog_retrieved_at="",
        catalog_row=values["catalog_row"],
    ).to_dict()
    run = RunManifest.plan(**values)
    return replace(
        run,
        expected_trial_ids=tuple(
            trial_id_for(run.run_id, form_id, run.dialect_id) for form_id in form_ids
        ),
    )


def _sample_records(
    run: RunManifest,
    form_ids: tuple[str, ...],
    *,
    costs: tuple[float, ...] | None = None,
):
    suite = load_suite()
    forms = {form["abstract_form_id"]: form for form in suite.forms}
    cells = {
        cell["abstract_form_id"]: cell
        for cell in suite.cells
        if cell["dialect_id"] == run.dialect_id
    }
    protocol = get_protocol(run.protocol_id)
    trials = []
    calls = []
    events = []
    evidence = []
    cost_values = costs or (0.001,) * len(form_ids)
    for index, (trial_id, form_id, cost_usd) in enumerate(
        zip(run.expected_trial_ids, form_ids, cost_values, strict=True), start=1
    ):
        form = forms[form_id]
        cell = cells[form_id]
        response_text = (
            json.dumps({"value": form["normal_value"]})
            if protocol.answer_kind == "normal_value"
            else json.dumps({"tree": form["abstract_form"]})
        )
        parse_status, prediction, correct = protocol.parse_answer(
            response_text,
            expected_normal_value=form["normal_value"],
            expected_tree=form["abstract_form"],
        )
        call_id = call_id_for(trial_id, 1)
        started_at = f"2026-08-08T00:00:0{index}.000000+00:00"
        finished_at = f"2026-08-08T00:00:0{index}.010000+00:00"
        provider_request_id = f"request:{run.protocol_id}:{index}"
        record = AttemptEvidence.capture(
            call_id=call_id,
            trial_id=trial_id,
            run_id=run.run_id,
            attempt=1,
            provider_evidence=_provider_envelope(
                run,
                response_text=response_text,
                request_id=provider_request_id,
                started_at=started_at,
                finished_at=finished_at,
                cost_usd=cost_usd,
            ),
        )
        trials.append(
            TrialRecord(
                trial_id=trial_id,
                run_id=run.run_id,
                suite_version=run.suite_version,
                abstract_form_id=form_id,
                dialect_id=run.dialect_id,
                protocol_id=run.protocol_id,
                execution_surface=run.execution_surface,
                requested_model_id=run.requested_model_id,
                resolved_model_id=run.resolved_model_id,
                provider=run.provider,
                endpoint=run.endpoint,
                prompt_hash=protocol.prompt_hash(
                    reading_rule=suite.specs[run.dialect_id].reading_rule,
                    model_payload_sha256=cell["model_payload_sha256"],
                ),
                symbolic_payload_hash=cell["symbolic_payload_hash"],
                model_payload_sha256=cell["model_payload_sha256"],
                parse_status=parse_status,
                attempt_count=1,
                latency_ms=10.0,
                provider_latency_ms=10.0,
                input_tokens=20,
                output_tokens=4,
                reasoning_tokens=0,
                observed_cost_usd=cost_usd,
                prediction=prediction,
                normal_value=form["normal_value"],
                correct=correct,
                response_text=response_text,
                completion_evidence_sha256=record.evidence_sha256,
            )
        )
        call = CallRecord(
            call_id=call_id,
            trial_id=trial_id,
            run_id=run.run_id,
            attempt=1,
            started_at=started_at,
            finished_at=finished_at,
            status="complete",
            reserved_cost_usd=0.01,
            observed_cost_usd=cost_usd,
            resolved_model_id=run.resolved_model_id,
            provider=run.provider,
            endpoint=run.endpoint,
            latency_ms=10.0,
            provider_latency_ms=10.0,
            input_tokens=20,
            output_tokens=4,
            reasoning_tokens=0,
            provider_request_id=provider_request_id,
            response_sha256=sha256(response_text.encode()).hexdigest(),
            evidence_sha256=record.evidence_sha256,
        )
        calls.append(call)
        evidence.append(record)
        for event_type, amount, at in (
            ("reserved", 0.01, call.started_at),
            ("settled", cost_usd, call.finished_at),
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
    return trials, calls, events, evidence


@pytest.mark.requires_visual_runtime
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
        spend_caps_usd={"global": 30.0, "cohorts": {"sample": 30.0}},
        materialize_stimuli=True,
        evidence_projector=project_openrouter_evidence,
        release_policy_validator=validate_sample_release,
    )
    for run in runs:
        trials, calls, events, evidence = _sample_records(run, form_ids)
        bundle.admit_run(
            run,
            trials,
            calls=calls,
            request_starts=_request_records(run, trials, calls),
            ledger_events=events,
            evidence=evidence,
        )
    write_release_metrics(bundle.root, suite=load_suite(), runs=bundle.runs())
    build_site(bundle.publication(), bundle.root / "site")
    runs_page = (bundle.root / "site" / "runs.html").read_text()
    for marker in (
        "four-protocol by five-form smoke test",
        "how these rows are derived",
        "competence + invariance profiles",
        "aggregate resource use",
        "controlled dialect effects",
        "dialect matrix",
        "family matrix",
        "reasoning contrasts",
        "not run",
        "input/output/reasoning tokens",
    ):
        assert marker in runs_page
    for value in (
        *(run.run_id for run in runs),
        *(run.endpoint for run in runs),
        "catalog_row",
        "routing_policy",
        "response_text",
        "prompt_hash",
        "call_id",
        "trial_id",
        "run_id",
    ):
        assert value not in runs_page
    human_page = (bundle.root / "site" / "human.html").read_text()
    assert "HumanTrialRecord" in human_page
    assert "familiarity_band" in human_page
    assert "records:rows" in human_page
    assert (bundle.root / "site" / "downloads" / "human-trial.schema.json").is_file()
    assert {path.name for path in (bundle.root / "site" / "downloads").iterdir()} == {
        "suite.json",
        "protocols.json",
        "human-trial.schema.json",
        "profiles.parquet",
        "effects.parquet",
    }
    unexpected = bundle.root / "site" / "downloads" / "calls.parquet"
    unexpected.write_bytes((bundle.root / "calls.parquet").read_bytes())
    with pytest.raises(RuntimeError, match="file set is not exact"):
        bundle.seal(repository_root=repository)
    unexpected.unlink()
    original_runs_page = runs_page
    (bundle.root / "site" / "runs.html").write_text(
        original_runs_page + "<p>request-started.jsonl</p>"
    )
    with pytest.raises(RuntimeError, match="forbidden public output marker"):
        bundle.seal(repository_root=repository)
    (bundle.root / "site" / "runs.html").write_text(original_runs_page)
    bundle.seal(repository_root=repository)
    assert len(bundle.runs()) == 4
    assert pq.read_table(bundle.root / "calls.parquet").num_rows == 20
    assert len((bundle.root / "transcripts.jsonl").read_text().splitlines()) == 20
    assert len((bundle.root / "request-started.jsonl").read_text().splitlines()) == 20
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
        spend_caps_usd={"global": 30.0, "cohorts": {"sample": 30.0}},
        evidence_projector=project_openrouter_evidence,
        release_policy_validator=validate_sample_release,
    )
    trials, _calls, _events, _evidence = _sample_records(run, form_ids)
    with pytest.raises(RuntimeError, match="attempt count"):
        bundle.admit_run(run, trials)


@pytest.fixture(scope="module")
def sealed_reissue_source(tmp_path_factory, visual_runtime):
    root = tmp_path_factory.mktemp("sealed-reissue")
    repository = root / "repository"
    subprocess.run(["git", "clone", "-q", "--shared", str(Path.cwd()), str(repository)], check=True)
    candidate_authority, _, _ = authority_from_git(repository)
    predecessor_commit = subprocess.run(
        ["git", "rev-parse", "HEAD^"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    predecessor_authority, _, _ = authority_from_git(repository, commit=predecessor_commit)
    protocols = (
        "reduce-infer-v1",
        "reduce-taught-v1",
        "transcribe-infer-v1",
        "transcribe-taught-v1",
    )
    form_ids = tuple(load_suite().form_sets["probe"])
    cost_values = (0.002320875, *(0.0035 for _ in range(18)), 0.004186)
    runs = []
    offset = 0
    for protocol in protocols:
        costs = tuple(cost_values[offset : offset + 5])
        offset += 5
        runs.append(replace(_sample_run(protocol, form_ids), cost_usd=math.fsum(costs)))
    source = root / "source"
    state = root / "state"
    records_by_run = {}
    all_ledger = []
    for index, run in enumerate(runs):
        costs = tuple(cost_values[index * 5 : index * 5 + 5])
        trials, calls, events, evidence = _sample_records(run, form_ids, costs=costs)
        if index == 0:
            recovered = evidence[0].revise(evidence[0].provider_evidence)
            evidence[0:1] = [evidence[0], recovered]
            calls[0] = replace(calls[0], evidence_sha256=recovered.evidence_sha256)
            trials[0] = replace(trials[0], completion_evidence_sha256=recovered.evidence_sha256)
        requests = _request_records(run, trials, calls)
        records_by_run[run.run_id] = (trials, calls, events, evidence, requests)
        all_ledger.extend(row.to_dict() for row in events)

    old_runs = []
    for index, run in enumerate(runs):
        values = run.to_dict()
        values.pop("run_id")
        values.update(
            resolved_model_id=run.requested_model_id,
            expected_trial_ids=(),
            status="planned",
            attempts=0,
            token_usage={},
            latency_ms=0.0,
            cost_usd=0.0,
        )
        legacy_catalog = json.loads(json.dumps(run.catalog_row))
        legacy_catalog.pop("authenticated_user_model")
        legacy_catalog.pop("authenticated_user_models_retrieval")
        values["catalog_row"] = legacy_catalog
        values["authority"] = derive_run_authority(
            suite_bytes=DEFAULT_SUITE_REGISTRY.read_bytes(),
            protocol_bytes=DEFAULT_PROTOCOL_REGISTRY.read_bytes(),
            form_ids=form_ids,
            dialect_id=run.dialect_id,
            protocol_id=run.protocol_id,
            execution_spec=execution_spec_identity(values),
            catalog_retrieved_at=run.catalog_retrieved_at,
            catalog_row=legacy_catalog,
        ).to_dict()
        old = RunManifest.plan(**values)
        old_runs.append(
            replace(
                old,
                expected_trial_ids=tuple(
                    trial_id_for(old.run_id, form_id, old.dialect_id) for form_id in form_ids
                ),
                status="probed" if index == 0 else "planned",
                attempts=1 if index == 0 else 0,
            )
        )
    run_map = {old.run_id: new.run_id for old, new in zip(old_runs, runs, strict=True)}
    trial_map = {
        old_trial: new_trial
        for old, new in zip(old_runs, runs, strict=True)
        for old_trial, new_trial in zip(old.expected_trial_ids, new.expected_trial_ids, strict=True)
    }
    predecessor_state = root / "predecessor-state"
    predecessor_state.mkdir()
    for old in old_runs:
        run_dir = predecessor_state / old.run_id
        run_dir.mkdir()
        (run_dir / "run.json").write_text(json.dumps(old.to_dict()) + "\n")
    active_old = old_runs[0]
    active_new = runs[0]
    new_trials, new_calls, _new_events, new_evidence, new_requests = records_by_run[
        active_new.run_id
    ]
    old_trial_id = active_old.expected_trial_ids[0]
    old_call_id = call_id_for(old_trial_id, 1)
    old_evidence_first = AttemptEvidence.capture(
        call_id=old_call_id,
        trial_id=old_trial_id,
        run_id=active_old.run_id,
        attempt=1,
        provider_evidence=new_evidence[0].provider_evidence,
    )
    old_evidence = [
        old_evidence_first,
        old_evidence_first.revise(new_evidence[0].provider_evidence),
    ]
    old_calls = []
    for record in old_evidence:
        row = new_calls[0].to_dict()
        row.update(
            call_id=old_call_id,
            trial_id=old_trial_id,
            run_id=active_old.run_id,
            status="accounting_unknown",
            evidence_sha256=record.evidence_sha256,
        )
        old_calls.append(row)
    old_request = new_requests[0].to_dict()
    old_request.update(call_id=old_call_id, trial_id=old_trial_id, run_id=active_old.run_id)
    active_dir = predecessor_state / active_old.run_id
    for name, rows in (
        ("calls.jsonl", old_calls),
        ("request-started.jsonl", [old_request]),
        ("transcripts.jsonl", [row.to_dict() for row in old_evidence]),
    ):
        (active_dir / name).write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        )
    predecessor_reservation = LedgerEvent(
        event_type="reserved",
        call_id=old_call_id,
        trial_id=old_trial_id,
        run_id=active_old.run_id,
        cohort=active_old.cohort,
        amount_usd=0.01,
        at=old_calls[0]["started_at"],
    )
    (predecessor_state / "spend-ledger.jsonl").write_text(
        json.dumps(predecessor_reservation.to_dict(), sort_keys=True) + "\n"
    )
    (predecessor_state / "cost-sheet.json").write_text("{}\n")
    predecessor_release = {
        "release_id": "v1.0.0-sample.1",
        "status": "working",
        "repository_commit": predecessor_commit,
        "authority": predecessor_authority.to_dict(),
        "expected_run_ids": [run.run_id for run in old_runs],
    }
    predecessor_release_bytes = (json.dumps(predecessor_release, indent=2) + "\n").encode()
    from dbench.migration import _state_digest

    predecessor_state_sha256 = _state_digest(predecessor_state)
    active_attempt = {
        "old_run_id": active_old.run_id,
        "new_run_id": active_new.run_id,
        "old_trial_id": old_trial_id,
        "new_trial_id": new_trials[0].trial_id,
        "old_call_id": old_call_id,
        "new_call_id": new_calls[0].call_id,
        "old_request_sha256": old_request["request_sha256"],
        "new_request_sha256": new_requests[0].request_sha256,
        "old_evidence_sha256": [row.evidence_sha256 for row in old_evidence],
        "new_evidence_sha256": [
            row.evidence_sha256 for row in new_evidence if row.call_id == new_calls[0].call_id
        ],
        "new_call_record_sha256": canonical_sha256(new_calls[0].to_dict()),
        "provider_evidence_sha256": [
            canonical_sha256(row.provider_evidence.to_dict()) for row in old_evidence
        ],
        "observed_cost_usd": new_calls[0].observed_cost_usd,
    }
    audit = {
        "schema_version": 1,
        "kind": "openrouter-canonical-model-identity-v1",
        "identity_event_id": "ev_01KZHVJWTQAXJEACS9BTVN34Z9",
        "identity_event_authority": {
            "actor": "agent:codex-root",
            "body_sha256": "908c7d1a5e0b42765fc807402051ab43900c03f8985ec6e86d7ee4aff3cc8103",
            "type": "comment_added",
        },
        "migrated_at": "2026-08-08T00:00:00+00:00",
        "predecessor_repository_commit": predecessor_commit,
        "repository_commit": candidate_authority.source_commit,
        "predecessor_authority": predecessor_authority.to_dict(),
        "authority": candidate_authority.to_dict(),
        "predecessor_authority_sha256": canonical_sha256(predecessor_authority.to_dict()),
        "authority_sha256": canonical_sha256(candidate_authority.to_dict()),
        "predecessor_release_manifest_sha256": sha256(predecessor_release_bytes).hexdigest(),
        "predecessor_state_sha256": predecessor_state_sha256,
        "predecessor_run_manifest_sha256": {
            run.run_id: canonical_sha256(run.to_dict()) for run in old_runs
        },
        "authenticated_catalog_sha256": canonical_sha256(runs[0].catalog_row),
        "predecessor_call_record_sha256": [canonical_sha256(row) for row in old_calls],
        "requested_model_id": runs[0].requested_model_id,
        "resolved_model_id": runs[0].resolved_model_id,
        "endpoint": runs[0].endpoint,
        "provider": "Exact Provider",
        "run_id_map": run_map,
        "trial_id_map": trial_map,
        "attempts_retained": 1,
        "remaining_attempts": 19,
        "active_attempt": active_attempt,
    }
    bundle = ReleaseBundle.create_working(
        source,
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
            "model_id": runs[0].requested_model_id,
            "endpoint": runs[0].endpoint,
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
        spend_caps_usd={"global": 30.0, "cohorts": {"sample": 30.0}},
        materialize_stimuli=True,
        evidence_projector=project_openrouter_evidence,
        release_policy_validator=validate_sample_release,
        working_state_migrations=[audit],
    )
    state.mkdir()
    for run in runs:
        trials, calls, events, evidence, requests = records_by_run[run.run_id]
        bundle.admit_run(
            run,
            trials,
            calls=calls,
            request_starts=requests,
            ledger_events=events,
            evidence=evidence,
        )
        run_dir = state / run.run_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps(run.to_dict()) + "\n")
        for name, rows in (
            ("trials.jsonl", [row.to_dict() for row in trials]),
            ("calls.jsonl", [row.to_dict() for row in calls]),
            ("request-started.jsonl", [row.to_dict() for row in requests]),
            ("transcripts.jsonl", [row.to_dict() for row in evidence]),
        ):
            (run_dir / name).write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
            )
    (state / "model-identity-migration.json").write_text(json.dumps(audit, indent=2) + "\n")
    (state / "spend-ledger.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in all_ledger)
    )
    backup = root / f"state.pre-canonical-model-{predecessor_state_sha256[:12]}"
    backup.mkdir()
    shutil.move(predecessor_state, backup / "state")
    (backup / "migration-audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    (backup / "predecessor-release.json").write_bytes(predecessor_release_bytes)
    write_release_metrics(bundle.root, suite=load_suite(), runs=bundle.runs())
    build_site(bundle.publication(), bundle.root / "site")
    bundle.seal(repository_root=repository)
    sealed_manifest = json.loads((bundle.root / "release.json").read_text())
    legacy_runs_path = bundle.root / "site" / "runs.html"
    legacy_runs_path.write_text(
        legacy_runs_path.read_text()
        + "\n<!-- containment tree and frozen form; actual rendered stimulus; "
        "reading rule, protocol, and exact prompt; target and recorded response; "
        "parse and scorer identity; profile contribution; exact endpoint + routing "
        "provenance -->\n"
    )
    sealed_manifest["files"]["site/runs.html"] = {
        "sha256": sha256(legacy_runs_path.read_bytes()).hexdigest(),
        "bytes": legacy_runs_path.stat().st_size,
    }
    (bundle.root / "release.json").write_text(json.dumps(sealed_manifest, indent=2) + "\n")
    archive = archive_release(source, root / "source.tar.gz")
    from dbench import release_policy
    from dbench.reissue import _source_bundle_digest

    source_manifest = json.loads((source / "release.json").read_text())
    source_bundle_sha256, source_file_count, source_tree_entries = _source_bundle_digest(source)
    original_contract = release_policy._SAMPLE_REISSUE_SOURCE_CONTRACT
    original_run_ids = release_policy._SAMPLE_REISSUE_RUN_IDS
    release_policy._SAMPLE_REISSUE_SOURCE_CONTRACT = {
        "source_release_id": source_manifest["release_id"],
        "source_repository_commit": source_manifest["repository_commit"],
        "source_release_manifest_sha256": sha256(
            (source / "release.json").read_bytes()
        ).hexdigest(),
        "source_bundle_sha256": source_bundle_sha256,
        "source_archive_sha256": sha256(archive.read_bytes()).hexdigest(),
        "source_archive_bytes": archive.stat().st_size,
        "source_file_count": source_file_count,
        "source_tree_entries": source_tree_entries,
        "migration_audit_sha256": canonical_sha256(audit),
    }
    release_policy._SAMPLE_REISSUE_RUN_IDS = frozenset(run.run_id for run in runs)
    try:
        yield repository, source, state, archive
    finally:
        release_policy._SAMPLE_REISSUE_SOURCE_CONTRACT = original_contract
        release_policy._SAMPLE_REISSUE_RUN_IDS = original_run_ids


def _copy_reissue_source(sealed_reissue_source, root: Path):
    repository, original_source, original_state, original_archive = sealed_reissue_source
    source = shutil.copytree(original_source, root / "source")
    state = shutil.copytree(original_state, root / "state")
    backups = list(original_state.parent.glob(f"{original_state.name}.pre-canonical-model-*"))
    assert len(backups) == 1
    shutil.copytree(
        backups[0],
        root / backups[0].name.replace(original_state.name, state.name, 1),
    )
    archive = shutil.copyfile(original_archive, root / "source.tar.gz")
    return repository, source, state, Path(archive)


@pytest.mark.requires_visual_runtime
def test_reissue_accepts_only_stale_site_and_preserves_exact_core(tmp_path, sealed_reissue_source):
    repository, source, state, archive = _copy_reissue_source(sealed_reissue_source, tmp_path)
    with pytest.raises(RuntimeError, match="reissue origin"):
        ReleaseBundle.open(
            source,
            repository_root=repository,
            evidence_projector=project_openrouter_evidence,
            release_policy_validator=validate_sample_release,
        ).validate()
    target = tmp_path / "target"
    result = reissue_sealed_release(
        source_root=source,
        source_archive=archive,
        target_root=target,
        state_root=state,
        repository_root=repository,
    )
    assert result.attempts == 20
    assert result.observed_cost_usd == 0.069506875
    reissued = ReleaseBundle.open(
        target,
        repository_root=repository,
        evidence_projector=project_openrouter_evidence,
        release_policy_validator=validate_sample_release,
    )
    reissued.validate()
    assert reissued.manifest["status"] == "working"
    assert (
        reissued.manifest["working_state_migrations"]
        == json.loads((source / "release.json").read_text())["working_state_migrations"]
    )
    assert (
        reissued.manifest["reissued_from"]["source_archive_sha256"]
        == sha256(archive.read_bytes()).hexdigest()
    )
    calls = pq.read_table(target / "calls.parquet").to_pylist()
    assert len(calls) == 20
    assert math.fsum(row["observed_cost_usd"] for row in calls) == 0.069506875


@pytest.mark.requires_visual_runtime
@pytest.mark.parametrize("mutation", ["drop", "swap"])
def test_reissue_rejects_dropped_or_tampered_migration_audit(
    tmp_path, sealed_reissue_source, mutation
):
    repository, source, state, archive = _copy_reissue_source(sealed_reissue_source, tmp_path)
    manifest_path = source / "release.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "drop":
        manifest.pop("working_state_migrations")
    else:
        mapping = manifest["working_state_migrations"][0]["trial_id_map"]
        first, second = list(mapping)[:2]
        mapping[first], mapping[second] = mapping[second], mapping[first]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    target = tmp_path / "target"
    with pytest.raises(RuntimeError, match="migration"):
        reissue_sealed_release(
            source_root=source,
            source_archive=archive,
            target_root=target,
            state_root=state,
            repository_root=repository,
        )
    assert not target.exists()


@pytest.mark.requires_visual_runtime
def test_reissue_rejects_core_tamper_and_target_collisions(tmp_path, sealed_reissue_source):
    repository, source, state, archive = _copy_reissue_source(sealed_reissue_source, tmp_path)
    with (source / "calls.parquet").open("ab") as handle:
        handle.write(b"tamper")
    target = tmp_path / "target"
    with pytest.raises(RuntimeError):
        reissue_sealed_release(
            source_root=source,
            source_archive=archive,
            target_root=target,
            state_root=state,
            repository_root=repository,
        )
    assert not target.exists()

    with pytest.raises(RuntimeError, match="distinct sibling"):
        reissue_sealed_release(
            source_root=source,
            source_archive=archive,
            target_root=source,
            state_root=state,
            repository_root=repository,
        )
    target.mkdir()
    with pytest.raises(FileExistsError, match="absent"):
        reissue_sealed_release(
            source_root=source,
            source_archive=archive,
            target_root=target,
            state_root=state,
            repository_root=repository,
        )
    (target / "unexpected").write_text("occupied\n")
    with pytest.raises(FileExistsError, match="absent"):
        reissue_sealed_release(
            source_root=source,
            source_archive=archive,
            target_root=target,
            state_root=state,
            repository_root=repository,
        )


@pytest.mark.requires_visual_runtime
@pytest.mark.parametrize("copy", ["state", "backup"])
def test_reissue_requires_identical_state_and_backup_audits(tmp_path, sealed_reissue_source, copy):
    repository, source, state, archive = _copy_reissue_source(sealed_reissue_source, tmp_path)
    audit = json.loads((state / "model-identity-migration.json").read_text())
    if copy == "state":
        path = state / "model-identity-migration.json"
    else:
        backup = next(state.parent.glob(f"{state.name}.pre-canonical-model-*"))
        path = backup / "migration-audit.json"
    audit["remaining_attempts"] = 18
    path.write_text(json.dumps(audit, indent=2) + "\n")
    with pytest.raises(RuntimeError, match="migration audit"):
        reissue_sealed_release(
            source_root=source,
            source_archive=archive,
            target_root=tmp_path / "target",
            state_root=state,
            repository_root=repository,
        )


@pytest.mark.requires_visual_runtime
def test_reissue_publish_is_atomic_noreplace(tmp_path, sealed_reissue_source, monkeypatch):
    from dbench import reissue

    repository, source, state, archive = _copy_reissue_source(sealed_reissue_source, tmp_path)
    target = tmp_path / "target"
    rename = reissue.rename_path_noreplace_durable

    def collide(stage, destination):
        destination.mkdir()
        rename(stage, destination)

    monkeypatch.setattr(reissue, "rename_path_noreplace_durable", collide)
    with pytest.raises(FileExistsError):
        reissue_sealed_release(
            source_root=source,
            source_archive=archive,
            target_root=target,
            state_root=state,
            repository_root=repository,
        )
    assert target.is_dir() and not any(target.iterdir())


@pytest.mark.requires_visual_runtime
def test_reissue_origin_and_audit_remain_validation_invariants(tmp_path, sealed_reissue_source):
    repository, source, state, archive = _copy_reissue_source(sealed_reissue_source, tmp_path)
    target = tmp_path / "target"
    reissue_sealed_release(
        source_root=source,
        source_archive=archive,
        target_root=target,
        state_root=state,
        repository_root=repository,
    )
    release_path = target / "release.json"
    original = json.loads(release_path.read_text())
    removed = json.loads(json.dumps(original))
    removed.pop("reissued_from")
    release_path.write_text(json.dumps(removed, indent=2) + "\n")
    with pytest.raises(RuntimeError, match="reissue provenance"):
        ReleaseBundle.open(
            target,
            repository_root=repository,
            evidence_projector=project_openrouter_evidence,
            release_policy_validator=validate_sample_release,
        ).validate()

    forged = json.loads(json.dumps(original))
    forged["working_state_migrations"][0]["remaining_attempts"] = 18
    forged["origin"]["working_state_migrations_sha256"] = canonical_sha256(
        forged["working_state_migrations"]
    )
    release_path.write_text(json.dumps(forged, indent=2) + "\n")
    with pytest.raises(RuntimeError, match="migration provenance"):
        ReleaseBundle.open(
            target,
            repository_root=repository,
            evidence_projector=project_openrouter_evidence,
            release_policy_validator=validate_sample_release,
        ).validate()

    forged = json.loads(json.dumps(original))
    forged["reissued_from"]["source_archive_sha256"] = "0" * 64
    forged["origin"]["reissued_from_sha256"] = canonical_sha256(forged["reissued_from"])
    release_path.write_text(json.dumps(forged, indent=2) + "\n")
    with pytest.raises(RuntimeError, match="migration provenance"):
        ReleaseBundle.open(
            target,
            repository_root=repository,
            evidence_projector=project_openrouter_evidence,
            release_policy_validator=validate_sample_release,
        ).validate()


@pytest.mark.requires_visual_runtime
def test_reissue_cannot_downgrade_known_lineage_to_fresh(
    tmp_path, sealed_reissue_source, monkeypatch
):
    from dbench import cli

    repository, source, state, archive = _copy_reissue_source(sealed_reissue_source, tmp_path)
    target = tmp_path / "target"
    reissue_sealed_release(
        source_root=source,
        source_archive=archive,
        target_root=target,
        state_root=state,
        repository_root=repository,
    )
    release_path = target / "release.json"
    downgraded = json.loads(release_path.read_text())
    downgraded.pop("sample_contract")
    downgraded.pop("reissued_from")
    downgraded.pop("working_state_migrations")
    downgraded["origin"] = {"schema_version": 1, "kind": "fresh"}
    release_path.write_text(json.dumps(downgraded, indent=2) + "\n")

    def reopened() -> ReleaseBundle:
        return ReleaseBundle.open(
            target,
            repository_root=repository,
            evidence_projector=project_openrouter_evidence,
            release_policy_validator=validate_sample_release,
        )

    with pytest.raises(RuntimeError, match="sample contract"):
        reopened().validate()
    with pytest.raises(RuntimeError, match="sample contract"):
        reopened().seal(repository_root=repository)
    monkeypatch.chdir(repository)
    with pytest.raises(RuntimeError, match="sample contract"):
        cli.main(["prepare", "--release", str(target)])


@pytest.mark.requires_visual_runtime
def test_reissue_cli_has_no_secret_or_executor_path(
    tmp_path, sealed_reissue_source, monkeypatch, capsys
):
    from dbench import cli

    repository, source, state, archive = _copy_reissue_source(sealed_reissue_source, tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("reissue entered a secret, catalog, or executor path")

    monkeypatch.setattr(cli, "_load_secret_env", forbidden)
    monkeypatch.setattr(cli, "_executor", forbidden)
    monkeypatch.setattr(cli, "fetch_openrouter_endpoint", forbidden)
    monkeypatch.chdir(repository)
    target = tmp_path / "target"
    assert (
        cli.main(
            [
                "reissue-sealed-release",
                "--source-release",
                str(source),
                "--source-archive",
                str(archive),
                "--target-release",
                str(target),
                "--state-root",
                str(state),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["attempts"] == 20
    assert output["observed_cost_usd"] == 0.069506875
