from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dbench.provider_evidence import (
    OPENROUTER_CHAT_SOURCE,
    OPENROUTER_GENERATION_SOURCE,
    project_openrouter_evidence,
)
from dbench.release_policy import validate_sample_release
from lofbench.authority import (
    PROTOCOL_REGISTRY_GIT_PATH,
    SUITE_REGISTRY_GIT_PATH,
    derive_run_authority,
)
from lofbench.metrics import write_release_metrics
from lofbench.protocols import DEFAULT_PROTOCOL_REGISTRY, get_protocol
from lofbench.provider_evidence import ProviderEvidenceEnvelope, ProviderEvidenceSource
from lofbench.records import (
    AttemptEvidence,
    CallRecord,
    LedgerEvent,
    RequestStartedRecord,
    RunManifest,
    TrialRecord,
    execution_spec_identity,
)
from lofbench.release_bundle import ReleaseBundle
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
    run: RunManifest, trials: list[TrialRecord]
) -> tuple[list[TrialRecord], list[CallRecord], list[LedgerEvent], list[AttemptEvidence]]:
    calls = []
    events = []
    evidence = []
    linked_trials = []
    trials_by_id = {trial.trial_id: trial for trial in trials}
    for index, trial_id in enumerate(run.expected_trial_ids, start=1):
        trial = trials_by_id[trial_id]
        call_id = call_id_for(trial_id, 1)
        started_at = f"2026-08-08T00:00:0{index}.000000+00:00"
        finished_at = f"2026-08-08T00:00:0{index}.010000+00:00"
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
            latency_ms=10.0,
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
        linked_trials.append(replace(trial, completion_evidence_sha256=record.evidence_sha256))
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


def _sample_records(run: RunManifest, form_ids: tuple[str, ...]):
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
    for index, (trial_id, form_id) in enumerate(
        zip(run.expected_trial_ids, form_ids, strict=True), start=1
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
                observed_cost_usd=0.001,
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
            observed_cost_usd=0.001,
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
    return trials, calls, events, evidence


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
