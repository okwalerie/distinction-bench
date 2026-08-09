from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import dbench.migration as migration
from dbench.migration import (
    SAMPLE_PROTOCOLS,
    _frozen_identity_task,
    salvage_working_model_identity,
)
from dbench.openrouter import EndpointSelection
from dbench.provider_evidence import (
    OPENROUTER_CHAT_SOURCE,
    OPENROUTER_GENERATION_SOURCE,
    project_openrouter_evidence,
)
from dbench.publication import open_release
from lofbench.authority import PROTOCOL_REGISTRY_GIT_PATH, SUITE_REGISTRY_GIT_PATH
from lofbench.orchestration import call_record_from_projection
from lofbench.protocols import DEFAULT_PROTOCOL_REGISTRY
from lofbench.provider_evidence import ProviderEvidenceEnvelope, ProviderEvidenceSource
from lofbench.records import AttemptEvidence, LedgerEvent, RequestStartedRecord
from lofbench.release_bundle import ReleaseBundle
from lofbench.run_models import ExecutionSpec, call_id_for, plan_run, request_sha256_for
from lofbench.state_io import append_jsonl_fsynced, read_jsonl, write_json_atomic
from lofbench.suites import DEFAULT_SUITE_REGISTRY

REQUESTED = "example/vision-model"
RESOLVED = "example/vision-model-20260808"
ENDPOINT = "provider/example/flex"
PROVIDER = "Example Provider"
COST = 0.002320875
EVENT = "ev_test_live_identity"


def _repository(path: Path) -> tuple[Path, str]:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    suite = path / SUITE_REGISTRY_GIT_PATH
    suite.parent.mkdir(parents=True)
    protocols = path / PROTOCOL_REGISTRY_GIT_PATH
    protocols.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_SUITE_REGISTRY, suite)
    shutil.copyfile(DEFAULT_PROTOCOL_REGISTRY, protocols)
    subprocess.run(["git", "add", "src"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "predecessor"], cwd=path, check=True)
    return path, subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _catalog(*, canonical: bool) -> dict:
    pricing = {"prompt": "0.000000125", "completion": "0.00000075", "image": "0.000000125"}
    endpoint = {
        "model_id": REQUESTED,
        "tag": ENDPOINT,
        "provider_name": PROVIDER,
        "pricing": pricing,
        "status": 0,
        "supported_parameters": ["response_format"],
    }
    catalog = {
        "authenticated": True,
        "retrieved_at": "2026-08-08T00:00:00+00:00",
        "model": {
            "id": REQUESTED,
            "architecture": {"input_modalities": ["text", "image"]},
            "endpoints": [endpoint],
        },
        "selected_endpoint": endpoint,
        "zdr_selected_endpoint": endpoint,
    }
    if canonical:
        catalog.update(
            {
                "authenticated_user_model": {
                    "id": REQUESTED,
                    "canonical_slug": RESOLVED,
                    "name": "Exact Entire User Row",
                    "context_length": 1048576,
                },
                "authenticated_user_models_retrieval": {
                    "label": "openrouter.catalog.response.v1",
                    "request_started_at": "2026-08-08T00:00:00+00:00",
                    "response_finished_at": "2026-08-08T00:00:01+00:00",
                    "request_method": "GET",
                    "request_url": "https://openrouter.ai/api/v1/models/user",
                    "http_status": 200,
                    "response_headers": [["content-type", "application/json"]],
                    "raw_body_sha256": "a" * 64,
                    "raw_body_bytes": 1234,
                    "text_decoding": "utf-8",
                    "json_parse_outcome": "parsed",
                },
            }
        )
    return catalog


def _selection() -> EndpointSelection:
    catalog = _catalog(canonical=True)
    selected = catalog["selected_endpoint"]
    return EndpointSelection(
        model_id=REQUESTED,
        resolved_model_id=RESOLVED,
        endpoint_tag=ENDPOINT,
        provider_name=PROVIDER,
        pricing=selected["pricing"],
        supported_parameters=("response_format",),
        architecture={"input_modalities": ["text", "image"]},
        retrieved_at="2026-08-08T00:00:00+00:00",
        catalog_row=catalog,
    )


def _legacy_execution() -> ExecutionSpec:
    selection = _selection().execution_spec(cohort="sample", max_transport_attempts=1)
    return replace(
        selection,
        resolved_model_id=REQUESTED,
        catalog_row=_catalog(canonical=False),
    )


def _source(
    *,
    label: str,
    sequence: int,
    payload: dict,
    status: int,
) -> ProviderEvidenceSource:
    return ProviderEvidenceSource.capture_http(
        label=label,
        sequence=sequence,
        request_started_at="2026-08-08T00:00:00+00:00",
        response_finished_at="2026-08-08T00:00:01+00:00",
        request_method="POST" if label == OPENROUTER_CHAT_SOURCE else "GET",
        request_url=(
            "https://openrouter.ai/api/v1/chat/completions"
            if label == OPENROUTER_CHAT_SOURCE
            else "https://openrouter.ai/api/v1/generation?id=request-1"
        ),
        http_status=status,
        response_headers=(("content-type", "application/json"),),
        raw_body=json.dumps(payload).encode(),
    )


def _evidence() -> tuple[ProviderEvidenceEnvelope, ProviderEvidenceEnvelope]:
    chat = {
        "id": "request-1",
        "model": REQUESTED,
        "provider": PROVIDER,
        "choices": [
            {
                "message": {"content": '{"value":"unmarked"}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 18495,
            "completion_tokens": 12,
            "completion_tokens_details": {"reasoning_tokens": 0},
            "cost": COST,
        },
    }
    initial_sources = (
        _source(label=OPENROUTER_CHAT_SOURCE, sequence=1, payload=chat, status=200),
        *(
            _source(
                label=OPENROUTER_GENERATION_SOURCE,
                sequence=sequence,
                payload={"error": {"code": 404}},
                status=404,
            )
            for sequence in (1, 2, 3)
        ),
    )
    generation = {
        "data": {
            "id": "request-1",
            "model": RESOLVED,
            "provider_name": PROVIDER,
            "native_tokens_prompt": 18495,
            "native_tokens_completion": 12,
            "native_tokens_reasoning": 0,
            "total_cost": COST,
            "latency": 10.0,
            "finish_reason": "stop",
            "cancelled": False,
        }
    }
    recovered_sources = (
        *initial_sources,
        _source(
            label=OPENROUTER_GENERATION_SOURCE,
            sequence=4,
            payload=generation,
            status=200,
        ),
    )
    return (
        ProviderEvidenceEnvelope(
            schema_version=2,
            adapter_id="openrouter-direct-v2",
            sources=initial_sources,
        ),
        ProviderEvidenceEnvelope(
            schema_version=2,
            adapter_id="openrouter-direct-v2",
            sources=recovered_sources,
        ),
    )


def _fixture(tmp_path: Path):
    repository, predecessor = _repository(tmp_path / "repository")
    tasks = {
        protocol: _frozen_identity_task(
            suite_path=repository / SUITE_REGISTRY_GIT_PATH,
            form_set="probe",
            dialect_id="enclosure.plain-v1",
            protocol_id=protocol,
        )
        for protocol in SAMPLE_PROTOCOLS
    }
    runs = {
        protocol: plan_run(
            tasks[protocol],
            suite_version="v1",
            form_set="probe",
            dialect_id="enclosure.plain-v1",
            protocol_id=protocol,
            execution=_legacy_execution(),
            suite_registry_path=repository / SUITE_REGISTRY_GIT_PATH,
            protocol_registry_path=repository / PROTOCOL_REGISTRY_GIT_PATH,
        )
        for protocol in SAMPLE_PROTOCOLS
    }
    release = tmp_path / "release"
    state = tmp_path / "state"
    approval = {
        "approved_by": "human:test",
        "approved_at": "2026-08-08T00:00:00+00:00",
        "scope": "exact test sample",
        "max_spend_usd": 30.0,
        "release_id": "v1.0.0-sample.1",
        "run_ids": [runs[protocol].run_id for protocol in SAMPLE_PROTOCOLS],
        "model_id": REQUESTED,
        "endpoint": ENDPOINT,
        "provider": PROVIDER,
        "paid_calls": 20,
    }
    ReleaseBundle.create_working(
        release,
        release_id="v1.0.0-sample.1",
        repository_url="https://example.invalid/repository",
        repository_root=repository,
        expected_run_ids=[run.run_id for run in runs.values()],
        paid_run_approval=approval,
        sample_contract={
            "protocol_ids": list(SAMPLE_PROTOCOLS),
            "form_set": "probe",
            "dialect_id": "enclosure.plain-v1",
            "execution_surface": "direct_api",
            "max_transport_attempts": 1,
            "trials_per_run": 5,
            "total_attempts": 20,
        },
        spend_caps_usd={"global": 30.0, "cohorts": {"sample": 30.0}},
    )
    state.mkdir()
    active = runs[SAMPLE_PROTOCOLS[0]]
    trial_id = active.expected_trial_ids[0]
    call_id = call_id_for(trial_id, 1)
    request = RequestStartedRecord(
        call_id=call_id,
        trial_id=trial_id,
        run_id=active.run_id,
        attempt=1,
        request_sha256=request_sha256_for(
            call_id=call_id,
            trial_id=trial_id,
            run_id=active.run_id,
            attempt=1,
            prompt_hash=list(tasks[SAMPLE_PROTOCOLS[0]].dataset.samples)[0].metadata["prompt_hash"],
            model_payload_sha256=list(tasks[SAMPLE_PROTOCOLS[0]].dataset.samples)[0].metadata[
                "model_payload_sha256"
            ],
        ),
        started_at="2026-08-08T00:00:00+00:00",
    )
    initial, recovered = _evidence()
    first = AttemptEvidence.capture(
        call_id=call_id,
        trial_id=trial_id,
        run_id=active.run_id,
        attempt=1,
        provider_evidence=initial,
    )
    second = first.revise(recovered)
    active = replace(
        active,
        status="probed",
        attempts=1,
        token_usage={
            "input_tokens": 18_495,
            "output_tokens": 12,
            "reasoning_tokens": 0,
        },
        latency_ms=1_000.0,
    )
    runs[SAMPLE_PROTOCOLS[0]] = active
    for protocol, run in runs.items():
        write_json_atomic(state / run.run_id / "run.json", run.to_dict())
        if protocol != SAMPLE_PROTOCOLS[0]:
            continue
        (state / run.run_id / ".run.lock").touch(mode=0o600)
        append_jsonl_fsynced(state / run.run_id / "request-started.jsonl", request.to_dict())
        append_jsonl_fsynced(state / run.run_id / "transcripts.jsonl", first.to_dict())
        append_jsonl_fsynced(state / run.run_id / "transcripts.jsonl", second.to_dict())
        for evidence, error_type in (
            (first, "missing_generation_accounting"),
            (second, "contradictory_model"),
        ):
            projection = project_openrouter_evidence(
                evidence.provider_evidence,
                run.to_dict(),
            )
            projection = replace(projection, error_type=error_type)
            append_jsonl_fsynced(
                state / run.run_id / "calls.jsonl",
                call_record_from_projection(
                    call_id=call_id,
                    trial_id=trial_id,
                    run=run,
                    attempt=1,
                    reservation=0.01,
                    projection=projection,
                    evidence=evidence,
                ).to_dict(),
            )
    append_jsonl_fsynced(
        state / "spend-ledger.jsonl",
        LedgerEvent(
            event_type="reserved",
            call_id=call_id,
            trial_id=trial_id,
            run_id=active.run_id,
            cohort="sample",
            amount_usd=0.01,
            at="2026-08-08T00:00:00+00:00",
        ).to_dict(),
    )
    (state / "spend-ledger.jsonl.lock").touch(mode=0o600)
    write_json_atomic(
        state / "cost-sheet.json",
        {
            "model_id": REQUESTED,
            "endpoint": ENDPOINT,
            "provider": PROVIDER,
            "runs": [
                {
                    "run_id": runs[protocol].run_id,
                    "protocol_id": protocol,
                    "trials": 5,
                    "conservative_reservation_usd": 0.05,
                }
                for protocol in SAMPLE_PROTOCOLS
            ],
        },
    )
    marker = repository / "src/dbench/canonical-model-migration.py"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("# migration implementation\n")
    subprocess.run(["git", "add", "src/dbench"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "canonical migration"], cwd=repository, check=True)
    return repository, predecessor, release, state, runs, second.provider_evidence


def _salvage(repository, predecessor, release, state):
    return salvage_working_model_identity(
        release_root=release,
        state_root=state,
        repository_root=repository,
        selection=_selection(),
        expected_source_commit=predecessor,
        expected_requested_model_id=REQUESTED,
        expected_resolved_model_id=RESOLVED,
        expected_endpoint=ENDPOINT,
        expected_provider=PROVIDER,
        expected_observed_cost_usd=COST,
        identity_event_id=EVENT,
    )


def test_salvage_rekeys_retained_attempt_and_leaves_exactly_nineteen_calls(tmp_path):
    repository, predecessor, release, state, old_runs, old_provider_evidence = _fixture(tmp_path)
    result = _salvage(repository, predecessor, release, state)
    assert result["retained_attempts"] == 1
    assert result["remaining_attempts"] == 19
    assert result["observed_cost_usd"] == COST

    bundle = open_release(release, repository_root=repository)
    bundle.validate()
    assert set(bundle.manifest["expected_run_ids"]) == set(result["run_ids"])
    assert bundle.manifest["repository_commit"] != predecessor
    audit = bundle.manifest["working_state_migrations"][0]
    assert audit["identity_event_id"] == EVENT
    assert set(audit["run_id_map"]) == {run.run_id for run in old_runs.values()}
    assert audit["attempts_retained"] == 1
    assert audit["remaining_attempts"] == 19
    assert audit["predecessor_authority"] != audit["authority"]
    assert len(audit["predecessor_run_manifest_sha256"]) == 4
    assert len(audit["predecessor_call_record_sha256"]) == 2

    attempts = 0
    trials = 0
    active = None
    for run_id in result["run_ids"]:
        run_dir = state / run_id
        run = json.loads((run_dir / "run.json").read_text())
        bundle.validate_planned_run(type(next(iter(old_runs.values()))).from_dict(run))
        attempts += run["attempts"]
        run_trials = read_jsonl(run_dir / "trials.jsonl")
        trials += len(run_trials)
        if run["attempts"]:
            active = (run, run_dir)
    assert attempts == 1
    assert trials == 1
    assert active is not None
    active_run, active_dir = active
    assert active_run["status"] == "probed"
    assert active_run["cost_usd"] == COST
    assert len(read_jsonl(active_dir / "calls.jsonl")) == 1
    migrated_evidence = [
        AttemptEvidence.from_dict(row) for row in read_jsonl(active_dir / "transcripts.jsonl")
    ]
    assert migrated_evidence[-1].provider_evidence == old_provider_evidence
    projection = project_openrouter_evidence(
        migrated_evidence[-1].provider_evidence,
        active_run,
    )
    assert projection.status == "complete"
    assert projection.resolved_model_id == RESOLVED
    ledger = read_jsonl(state / "spend-ledger.jsonl")
    assert [row["event_type"] for row in ledger] == ["reserved", "settled"]
    assert ledger[-1]["amount_usd"] == COST
    assert (
        sum(
            5 - run["attempts"]
            for run in (
                json.loads((state / run_id / "run.json").read_text())
                for run_id in result["run_ids"]
            )
        )
        == 19
    )
    assert Path(result["predecessor_backup"]).is_dir()
    assert (Path(result["predecessor_backup"]) / "predecessor-release.json").is_file()


def test_salvage_rejects_forged_generation_without_mutating_predecessor(tmp_path):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    before_release = (release / "release.json").read_bytes()
    transcript = next(state.glob("run_*/transcripts.jsonl"))
    rows = read_jsonl(transcript)
    rows[-1]["provider_evidence"]["sources"][-1]["body_text"] = json.dumps(
        {"data": {"model": "forged/model"}}
    )
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    forged_state = sorted(
        (path.relative_to(state), path.read_bytes()) for path in state.rglob("*") if path.is_file()
    )
    with pytest.raises(RuntimeError, match="raw body contradicts|evidence revision"):
        _salvage(repository, predecessor, release, state)
    assert (release / "release.json").read_bytes() == before_release
    assert not list(state.parent.glob("state.pre-canonical-model-*"))
    assert forged_state == sorted(
        (path.relative_to(state), path.read_bytes()) for path in state.rglob("*") if path.is_file()
    )


def test_salvage_is_one_time_and_rejects_the_migrated_state(tmp_path):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    _salvage(repository, predecessor, release, state)
    with pytest.raises(RuntimeError, match="predecessor|one-time"):
        _salvage(repository, predecessor, release, state)


def test_salvage_restores_exact_predecessor_if_state_install_fails(tmp_path, monkeypatch):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    before_release = (release / "release.json").read_bytes()
    before_state = sorted(
        (path.relative_to(state), path.read_bytes()) for path in state.rglob("*") if path.is_file()
    )
    replace = migration.os.replace

    def fail_stage_install(source, destination):
        if Path(destination) == state and ".canonical-model-stage-" in Path(source).name:
            raise OSError("simulated state installation failure")
        replace(source, destination)

    monkeypatch.setattr(migration.os, "replace", fail_stage_install)
    with pytest.raises(OSError, match="simulated state installation failure"):
        _salvage(repository, predecessor, release, state)
    assert (release / "release.json").read_bytes() == before_release
    assert before_state == sorted(
        (path.relative_to(state), path.read_bytes()) for path in state.rglob("*") if path.is_file()
    )
    assert not list(state.parent.glob("state.pre-canonical-model-*"))
    assert not list(state.parent.glob(".state.canonical-model-stage-*"))


def test_salvage_restores_exact_predecessor_if_release_update_fails(tmp_path, monkeypatch):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    before_release = (release / "release.json").read_bytes()
    before_state = sorted(
        (path.relative_to(state), path.read_bytes()) for path in state.rglob("*") if path.is_file()
    )
    write_json = migration.write_json_atomic

    def fail_release_update(path, value):
        if Path(path) == release / "release.json":
            raise OSError("simulated release update failure")
        write_json(path, value)

    monkeypatch.setattr(migration, "write_json_atomic", fail_release_update)
    with pytest.raises(OSError, match="simulated release update failure"):
        _salvage(repository, predecessor, release, state)
    assert (release / "release.json").read_bytes() == before_release
    assert before_state == sorted(
        (path.relative_to(state), path.read_bytes()) for path in state.rglob("*") if path.is_file()
    )
    assert not list(state.parent.glob("state.pre-canonical-model-*"))
    assert not list(state.parent.glob(".state.failed-canonical-model-*"))
