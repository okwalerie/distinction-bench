from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

import dbench.migration as migration
import lofbench.protocols as runtime_protocols
from dbench.cli import main as cli_main
from dbench.migration import (
    IDENTITY_EVENT_ACTOR,
    IDENTITY_EVENT_BODY,
    IDENTITY_EVENT_ID,
    IDENTITY_EVENT_TYPE,
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
from lofbench.authority import (
    PROTOCOL_REGISTRY_GIT_PATH,
    SUITE_REGISTRY_GIT_PATH,
    canonical_sha256,
    derive_run_authority,
)
from lofbench.orchestration import RunAlreadyRunningError, call_record_from_projection
from lofbench.protocols import DEFAULT_PROTOCOL_REGISTRY, protocol_registry_from_bytes
from lofbench.provider_evidence import ProviderEvidenceEnvelope, ProviderEvidenceSource
from lofbench.records import (
    AttemptEvidence,
    LedgerEvent,
    RequestStartedRecord,
    execution_spec_identity,
    run_id_for,
)
from lofbench.release_bundle import ReleaseBundle
from lofbench.run_models import (
    ExecutionSpec,
    call_id_for,
    plan_run,
    request_sha256_for,
    trial_id_for,
)
from lofbench.state_io import append_jsonl_fsynced, read_jsonl, write_json_atomic
from lofbench.suites import DEFAULT_SUITE_REGISTRY

REQUESTED = "example/vision-model"
RESOLVED = "example/vision-model-20260808"
ENDPOINT = "provider/example/flex"
PROVIDER = "Example Provider"
COST = 0.002320875
EVENT = IDENTITY_EVENT_ID


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
    events = path / ".lattice/events/task_01KZGZ7YTE0GCWF9E29XZVJP17.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": IDENTITY_EVENT_ID,
                "task_id": "task_01KZGZ7YTE0GCWF9E29XZVJP17",
                "actor": IDENTITY_EVENT_ACTOR,
                "type": IDENTITY_EVENT_TYPE,
                "data": {"body": IDENTITY_EVENT_BODY},
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    subprocess.run(["git", "add", "src", ".lattice"], cwd=path, check=True)
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
    protocols = protocol_registry_from_bytes((repository / PROTOCOL_REGISTRY_GIT_PATH).read_bytes())
    tasks = {
        protocol: _frozen_identity_task(
            suite_path=repository / SUITE_REGISTRY_GIT_PATH,
            form_set="probe",
            dialect_id="enclosure.plain-v1",
            protocol=protocols[protocol],
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
    marker = repository / "src/dbench/migration.py"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("# migration implementation\n")
    subprocess.run(["git", "add", "src/dbench"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "canonical migration"], cwd=repository, check=True)
    review_fix = repository / "src/lofbench/state_io.py"
    review_fix.parent.mkdir(parents=True, exist_ok=True)
    review_fix.write_text("# lifecycle-lock review repair\n")
    subprocess.run(["git", "add", str(review_fix)], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "migration review repair"], cwd=repository, check=True)
    return repository, predecessor, release, state, runs, second.provider_evidence


def _salvage(repository, predecessor, release, state, **changes):
    values = {
        "identity_event_id": EVENT,
        "transition_observer": None,
    }
    values.update(changes)
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
        **values,
    )


def _tree_snapshot(root: Path):
    return sorted(
        (path.relative_to(root), path.read_bytes()) for path in root.rglob("*") if path.is_file()
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
    assert (Path(result["predecessor_backup"]) / "migration-audit.json").is_file()
    assert (Path(result["predecessor_backup"]) / "state").is_dir()


def test_salvage_rejects_forged_generation_without_mutating_predecessor(tmp_path):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    before_release = (release / "release.json").read_bytes()
    transcript = next(state.glob("run_*/transcripts.jsonl"))
    rows = read_jsonl(transcript)
    rows[-1]["provider_evidence"]["sources"][-1]["body_text"] = json.dumps(
        {"data": {"model": "forged/model"}}
    )
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    forged_state = _tree_snapshot(state)
    with pytest.raises(RuntimeError, match="raw body contradicts|evidence revision"):
        _salvage(repository, predecessor, release, state)
    assert (release / "release.json").read_bytes() == before_release
    assert not list(state.parent.glob("state.pre-canonical-model-*"))
    assert forged_state == _tree_snapshot(state)


def test_salvage_is_one_time_and_rejects_the_migrated_state(tmp_path):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    _salvage(repository, predecessor, release, state)
    with pytest.raises(RuntimeError, match="predecessor|one-time"):
        _salvage(repository, predecessor, release, state)


def test_salvage_restores_exact_predecessor_if_state_install_fails(tmp_path, monkeypatch):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    before_release = (release / "release.json").read_bytes()
    before_state = _tree_snapshot(state)
    replace = migration.os.replace

    def fail_stage_install(source, destination):
        if Path(destination) == state and ".canonical-model-stage-" in Path(source).name:
            raise OSError("simulated state installation failure")
        replace(source, destination)

    monkeypatch.setattr(migration.os, "replace", fail_stage_install)
    with pytest.raises(OSError, match="simulated state installation failure"):
        _salvage(repository, predecessor, release, state)
    assert (release / "release.json").read_bytes() == before_release
    assert before_state == _tree_snapshot(state)
    assert not list(state.parent.glob("state.pre-canonical-model-*"))
    assert not list(state.parent.glob(".state.canonical-model-stage-*"))


def test_salvage_restores_exact_predecessor_if_release_update_fails(tmp_path, monkeypatch):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    before_release = (release / "release.json").read_bytes()
    before_state = _tree_snapshot(state)
    write_json = migration.write_json_atomic

    def fail_release_update(path, value):
        if Path(path) == release / "release.json":
            raise OSError("simulated release update failure")
        write_json(path, value)

    monkeypatch.setattr(migration, "write_json_atomic", fail_release_update)
    with pytest.raises(OSError, match="simulated release update failure"):
        _salvage(repository, predecessor, release, state)
    assert (release / "release.json").read_bytes() == before_release
    assert before_state == _tree_snapshot(state)
    assert not list(state.parent.glob("state.pre-canonical-model-*"))
    assert not list(state.parent.glob(".state.failed-canonical-model-*"))


def test_salvage_rejects_arbitrary_or_missing_identity_event(tmp_path):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    before_release = (release / "release.json").read_bytes()
    before_state = _tree_snapshot(state)
    with pytest.raises(RuntimeError, match="migration scope"):
        _salvage(
            repository,
            predecessor,
            release,
            state,
            identity_event_id="ev_nonexistent",
        )
    assert (release / "release.json").read_bytes() == before_release
    assert _tree_snapshot(state) == before_state


def test_salvage_rejects_tampered_tracked_identity_event(tmp_path):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    events = repository / ".lattice/events/task_01KZGZ7YTE0GCWF9E29XZVJP17.jsonl"
    row = json.loads(events.read_text())
    row["data"]["body"] = "forged live identity event"
    events.write_text(json.dumps(row) + "\n")
    subprocess.run(["git", "add", ".lattice"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--amend", "--no-edit", "-q"], cwd=repository, check=True)
    with pytest.raises(RuntimeError, match="tracked identity event"):
        _salvage(repository, predecessor, release, state)


def test_salvage_rejects_unrelated_source_changes_in_repair_lineage(tmp_path):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    unrelated = repository / "src/dbench/unrelated.py"
    unrelated.write_text("raise RuntimeError('unrelated')\n")
    subprocess.run(["git", "add", str(unrelated)], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "unrelated source"], cwd=repository, check=True)
    with pytest.raises(RuntimeError, match="bounded canonical-identity repair lineage"):
        _salvage(repository, predecessor, release, state)


class _ProcessDeath(BaseException):
    pass


def _committed_recovery_state(repository, predecessor, release, state):
    def die(candidate):
        if candidate == "journal:committed":
            raise _ProcessDeath(candidate)

    with pytest.raises(_ProcessDeath, match="journal:committed"):
        _salvage(
            repository,
            predecessor,
            release,
            state,
            transition_observer=die,
        )
    journal_path = state.parent / ".state.canonical-model-migration.json"
    journal = json.loads(journal_path.read_text())
    audit_path = Path(journal["backup"]) / "migration-audit.json"
    return journal_path, journal, audit_path, json.loads(audit_path.read_text())


def _republish_forged_committed_authority(
    *,
    release,
    state,
    journal_path,
    journal,
    audit_path,
    audit,
):
    write_json_atomic(audit_path, audit)
    write_json_atomic(state / "model-identity-migration.json", audit)
    manifest = json.loads((release / "release.json").read_text())
    manifest["working_state_migrations"][-1] = audit
    write_json_atomic(release / "release.json", manifest)
    journal["migration_audit_sha256"] = sha256(audit_path.read_bytes()).hexdigest()
    journal["candidate_state_sha256"] = migration._state_digest(state)
    journal["candidate_release_sha256"] = sha256(
        (release / "release.json").read_bytes()
    ).hexdigest()
    write_json_atomic(journal_path, journal)


@pytest.mark.parametrize(
    "transition",
    [
        "journal:initializing",
        "workspace:created",
        "backup:release",
        "backup:audit",
        "journal:prepared",
        "rename:predecessor",
        "journal:predecessor_moved",
        "rename:candidate",
        "journal:candidate_installed",
        "release:installed",
        "journal:release_installed",
    ],
)
def test_process_death_phase_rolls_back_exactly_on_next_invocation(tmp_path, transition):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    before_release = (release / "release.json").read_bytes()
    before_state = _tree_snapshot(state)

    def die(candidate):
        if candidate == transition:
            raise _ProcessDeath(candidate)

    with pytest.raises(_ProcessDeath, match=transition):
        _salvage(
            repository,
            predecessor,
            release,
            state,
            transition_observer=die,
        )
    assert list(state.parent.glob(".state.canonical-model-migration.json"))
    with pytest.raises(RuntimeError, match="rolled back interrupted migration"):
        _salvage(repository, predecessor, release, state)
    assert (release / "release.json").read_bytes() == before_release
    assert _tree_snapshot(state) == before_state
    assert not list(state.parent.glob(".state.canonical-model-migration.json"))
    assert not list(state.parent.glob("state.pre-canonical-model-*"))
    assert not list(state.parent.glob(".state.canonical-model-stage-*"))


def test_process_death_after_commit_is_finalized_without_rollback(tmp_path, monkeypatch):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)

    def die(candidate):
        if candidate == "journal:committed":
            raise _ProcessDeath(candidate)

    with pytest.raises(_ProcessDeath, match="journal:committed"):
        _salvage(
            repository,
            predecessor,
            release,
            state,
            transition_observer=die,
        )
    verified_authorities = []
    verify_authority_copies = migration.verify_authority_copies

    def observe_authority(manifest, **kwargs):
        verified_authorities.append((manifest.source_commit, kwargs["repository_root"]))
        return verify_authority_copies(manifest, **kwargs)

    monkeypatch.setattr(migration, "verify_authority_copies", observe_authority)
    with pytest.raises(RuntimeError, match="finalized interrupted migration"):
        _salvage(repository, predecessor, release, state)
    assert verified_authorities == [
        (predecessor, repository),
        (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            repository,
        ),
    ]
    runs = [json.loads(path.read_text()) for path in state.glob("run_*/run.json")]
    assert sum(run["attempts"] for run in runs) == 1
    assert sum(5 - run["attempts"] for run in runs) == 19
    assert not list(state.parent.glob(".state.canonical-model-migration.json"))


@pytest.mark.parametrize("match_corrupt_digest", [False, True])
def test_committed_recovery_rejects_corrupt_persisted_audit(
    tmp_path,
    match_corrupt_digest,
):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)

    def die(candidate):
        if candidate == "journal:committed":
            raise _ProcessDeath(candidate)

    with pytest.raises(_ProcessDeath):
        _salvage(
            repository,
            predecessor,
            release,
            state,
            transition_observer=die,
        )
    journal_path = state.parent / ".state.canonical-model-migration.json"
    journal = json.loads(journal_path.read_text())
    audit_path = Path(journal["backup"]) / "migration-audit.json"
    audit = json.loads(audit_path.read_text())
    audit["remaining_attempts"] = 18
    write_json_atomic(audit_path, audit)
    if match_corrupt_digest:
        journal["migration_audit_sha256"] = sha256(audit_path.read_bytes()).hexdigest()
        write_json_atomic(journal_path, journal)

    with pytest.raises(RuntimeError, match="committed migration journal is inconsistent"):
        _salvage(repository, predecessor, release, state)
    assert journal_path.is_file()
    assert audit_path.is_file()


@pytest.mark.parametrize("forgery", ["inactive_trial_map_swap", "attempt_closure"])
def test_committed_recovery_rejects_coordinated_semantic_forgery(tmp_path, forgery):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    journal_path, journal, audit_path, audit = _committed_recovery_state(
        repository,
        predecessor,
        release,
        state,
    )

    if forgery == "inactive_trial_map_swap":
        old_active_id = audit["active_attempt"]["old_run_id"]
        inactive_old_ids = [run_id for run_id in audit["run_id_map"] if run_id != old_active_id]
        first, second = (
            next(
                trial_id
                for trial_id in audit["trial_id_map"]
                if trial_id.startswith("trial_")
                and trial_id
                in json.loads(
                    (Path(journal["backup"]) / "state" / run_id / "run.json").read_text()
                )["expected_trial_ids"]
            )
            for run_id in inactive_old_ids[:2]
        )
        audit["trial_id_map"][first], audit["trial_id_map"][second] = (
            audit["trial_id_map"][second],
            audit["trial_id_map"][first],
        )
    else:
        active = audit["active_attempt"]
        run_dir = state / active["new_run_id"]
        call_path = run_dir / "calls.jsonl"
        trial_path = run_dir / "trials.jsonl"
        ledger_path = state / "spend-ledger.jsonl"
        run_path = run_dir / "run.json"
        call = read_jsonl(call_path)[0]
        call.update(
            {
                "reserved_cost_usd": 0.02,
                "observed_cost_usd": COST + 0.001,
                "input_tokens": call["input_tokens"] + 7,
                "latency_ms": call["latency_ms"] + 123.0,
                "response_sha256": "f" * 64,
                "error_type": "forged_completion",
            }
        )
        call_path.write_text(json.dumps(call) + "\n")
        trial = read_jsonl(trial_path)[0]
        trial.update(
            {
                "response_text": '{"value":"marked"}',
                "prediction": "marked",
                "correct": False,
                "prompt_hash": "e" * 64,
                "observed_cost_usd": call["observed_cost_usd"],
                "input_tokens": call["input_tokens"],
                "latency_ms": call["latency_ms"],
                "error_type": call["error_type"],
            }
        )
        trial_path.write_text(json.dumps(trial) + "\n")
        ledger = read_jsonl(ledger_path)
        ledger[0]["amount_usd"] = call["reserved_cost_usd"]
        ledger[1]["amount_usd"] = call["observed_cost_usd"]
        ledger_path.write_text("".join(json.dumps(row) + "\n" for row in ledger))
        run = json.loads(run_path.read_text())
        run["token_usage"]["input_tokens"] = call["input_tokens"]
        run["latency_ms"] = call["latency_ms"]
        run["cost_usd"] = call["observed_cost_usd"]
        write_json_atomic(run_path, run)
        active["new_call_record_sha256"] = canonical_sha256(call)
        active["observed_cost_usd"] = call["observed_cost_usd"]

    _republish_forged_committed_authority(
        release=release,
        state=state,
        journal_path=journal_path,
        journal=journal,
        audit_path=audit_path,
        audit=audit,
    )
    with pytest.raises(RuntimeError, match="committed migration journal is inconsistent"):
        _salvage(repository, predecessor, release, state)
    assert journal_path.is_file()
    assert audit_path.is_file()


def test_committed_recovery_rejects_registry_redefinition_before_deriving_maps(
    tmp_path,
    monkeypatch,
):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    journal_path, journal, audit_path, audit = _committed_recovery_state(
        repository,
        predecessor,
        release,
        state,
    )
    backup = Path(journal["backup"])
    predecessor_release_path = backup / "predecessor-release.json"
    predecessor_manifest = json.loads(predecessor_release_path.read_text())
    candidate_manifest = json.loads((release / "release.json").read_text())

    suite = json.loads((release / "suite.json").read_text())
    suite["form_sets"]["probe"][:2] = reversed(suite["form_sets"]["probe"][:2])
    suite_bytes = (json.dumps(suite, indent=2) + "\n").encode()
    (release / "suite.json").write_bytes(suite_bytes)
    protocols = json.loads((release / "protocols.json").read_text())
    protocols["protocols"][SAMPLE_PROTOCOLS[0]]["system_text"] += "\nforged authority"
    protocol_bytes = (json.dumps(protocols, indent=2) + "\n").encode()
    (release / "protocols.json").write_bytes(protocol_bytes)

    def forged_manifest_authority(manifest):
        authority = json.loads(json.dumps(manifest["authority"]))
        authority["suite_registry"].update(
            {"sha256": sha256(suite_bytes).hexdigest(), "bytes": len(suite_bytes)}
        )
        authority["protocol_registry"].update(
            {"sha256": sha256(protocol_bytes).hexdigest(), "bytes": len(protocol_bytes)}
        )
        return authority

    predecessor_manifest["authority"] = forged_manifest_authority(predecessor_manifest)
    candidate_manifest["authority"] = forged_manifest_authority(candidate_manifest)

    candidate_runs = {}
    candidate_id_map = {}
    for old_run_id in list(candidate_manifest["expected_run_ids"]):
        old_dir = state / old_run_id
        run = json.loads((old_dir / "run.json").read_text())
        form_ids = tuple(suite["form_sets"][run["form_set"]])
        run["authority"] = derive_run_authority(
            suite_bytes=suite_bytes,
            protocol_bytes=protocol_bytes,
            form_ids=form_ids,
            dialect_id=run["dialect_id"],
            protocol_id=run["protocol_id"],
            execution_spec=execution_spec_identity(run),
            catalog_retrieved_at=run["catalog_retrieved_at"],
            catalog_row=run["catalog_row"],
        ).to_dict()
        new_run_id = run_id_for(run)
        run["run_id"] = new_run_id
        run["expected_trial_ids"] = [
            trial_id_for(new_run_id, form_id, run["dialect_id"]) for form_id in form_ids
        ]
        new_dir = state / new_run_id
        old_dir.rename(new_dir)
        write_json_atomic(new_dir / "run.json", run)
        candidate_runs[run["protocol_id"]] = run
        candidate_id_map[old_run_id] = new_run_id

    old_runs_by_protocol = {
        json.loads(path.read_text())["protocol_id"]: json.loads(path.read_text())
        for path in (backup / "state").glob("run_*/run.json")
    }
    audit["run_id_map"] = {
        old_run["run_id"]: candidate_runs[protocol_id]["run_id"]
        for protocol_id, old_run in old_runs_by_protocol.items()
    }
    audit["trial_id_map"] = {
        old_trial_id: new_trial_id
        for protocol_id, old_run in old_runs_by_protocol.items()
        for old_trial_id, new_trial_id in zip(
            old_run["expected_trial_ids"],
            candidate_runs[protocol_id]["expected_trial_ids"],
            strict=True,
        )
    }
    active = audit["active_attempt"]
    active["new_run_id"] = candidate_id_map[active["new_run_id"]]
    active["new_trial_id"] = audit["trial_id_map"][active["old_trial_id"]]
    active["new_call_id"] = call_id_for(active["new_trial_id"], 1)
    candidate_manifest["expected_run_ids"] = [
        candidate_id_map[run_id] for run_id in candidate_manifest["expected_run_ids"]
    ]
    candidate_manifest["paid_run_approval"]["run_ids"] = [
        candidate_id_map[run_id] for run_id in candidate_manifest["paid_run_approval"]["run_ids"]
    ]
    audit["predecessor_authority"] = predecessor_manifest["authority"]
    audit["authority"] = candidate_manifest["authority"]
    audit["predecessor_authority_sha256"] = canonical_sha256(predecessor_manifest["authority"])
    audit["authority_sha256"] = canonical_sha256(candidate_manifest["authority"])
    journal["predecessor_authority_sha256"] = audit["predecessor_authority_sha256"]
    journal["candidate_authority_sha256"] = audit["authority_sha256"]
    write_json_atomic(predecessor_release_path, predecessor_manifest)
    journal["predecessor_release_sha256"] = sha256(
        predecessor_release_path.read_bytes()
    ).hexdigest()
    audit["predecessor_release_manifest_sha256"] = journal["predecessor_release_sha256"]
    write_json_atomic(release / "release.json", candidate_manifest)
    _republish_forged_committed_authority(
        release=release,
        state=state,
        journal_path=journal_path,
        journal=journal,
        audit_path=audit_path,
        audit=audit,
    )

    parsed_registry_or_run = False

    def reject_downstream_derivation(*_args, **_kwargs):
        nonlocal parsed_registry_or_run
        parsed_registry_or_run = True
        raise AssertionError("authority copies must fail before registry/run derivation")

    monkeypatch.setattr(migration, "load_suite", reject_downstream_derivation)
    monkeypatch.setattr(migration.RunManifest, "from_dict", reject_downstream_derivation)
    with pytest.raises(RuntimeError, match="committed migration journal is inconsistent") as exc:
        _salvage(repository, predecessor, release, state)
    assert "authority manifest does not match its recorded git tree" in str(exc.value.__cause__)
    assert parsed_registry_or_run is False
    assert journal_path.is_file()


def test_runtime_protocol_registry_is_irrelevant_to_verified_bundle_recovery(
    tmp_path,
    monkeypatch,
):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    divergent = {
        protocol_id: replace(
            protocol,
            system_text="forged runtime protocol outside release authority",
            answer_kind=(
                "structural_transcription"
                if protocol.answer_kind == "normal_value"
                else "normal_value"
            ),
        )
        for protocol_id, protocol in runtime_protocols.PROTOCOLS.items()
    }
    monkeypatch.setattr(runtime_protocols, "PROTOCOLS", divergent)

    def die(candidate):
        if candidate == "journal:committed":
            raise _ProcessDeath(candidate)

    with pytest.raises(_ProcessDeath, match="journal:committed"):
        _salvage(
            repository,
            predecessor,
            release,
            state,
            transition_observer=die,
        )
    with pytest.raises(RuntimeError, match="finalized interrupted migration"):
        _salvage(repository, predecessor, release, state)
    trials = [row for path in state.glob("run_*/trials.jsonl") for row in read_jsonl(path)]
    assert len(trials) == 1
    assert trials[0]["parse_status"] == "valid"


def test_cli_recovers_process_death_before_key_or_catalog_access(tmp_path, monkeypatch):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    before_release = (release / "release.json").read_bytes()
    before_state = _tree_snapshot(state)

    def die(candidate):
        if candidate == "rename:candidate":
            raise _ProcessDeath(candidate)

    with pytest.raises(_ProcessDeath):
        _salvage(
            repository,
            predecessor,
            release,
            state,
            transition_observer=die,
        )
    catalog_called = False

    def reject_catalog(*_args, **_kwargs):
        nonlocal catalog_called
        catalog_called = True
        raise AssertionError("catalog must not be called during recovery")

    monkeypatch.chdir(repository)
    monkeypatch.setattr("dbench.cli.fetch_openrouter_endpoint", reject_catalog)
    with pytest.raises(RuntimeError, match="rolled back interrupted migration"):
        cli_main(
            [
                "salvage-working-model-identity",
                "--release",
                str(release),
                "--state-root",
                str(state),
                "--env-file",
                str(tmp_path / "does-not-exist.env"),
                "--expected-source-commit",
                predecessor,
                "--requested-model",
                REQUESTED,
                "--resolved-model",
                RESOLVED,
                "--endpoint",
                ENDPOINT,
                "--provider",
                PROVIDER,
                "--expected-observed-cost-usd",
                str(COST),
                "--identity-event",
                EVENT,
            ]
        )
    assert catalog_called is False
    assert (release / "release.json").read_bytes() == before_release
    assert _tree_snapshot(state) == before_state


def test_transaction_renames_sync_exact_parents_and_crash_recovery(tmp_path, monkeypatch):
    repository, predecessor, release, state, _runs, _evidence = _fixture(tmp_path)
    renames = []
    durable_replace = migration.replace_path_durable

    def observe_replace(source, destination):
        durable_replace(source, destination)
        renames.append((Path(source).parent, Path(destination).parent))

    def die(candidate):
        if candidate == "rename:candidate":
            raise _ProcessDeath(candidate)

    monkeypatch.setattr(migration, "replace_path_durable", observe_replace)
    with pytest.raises(_ProcessDeath):
        _salvage(
            repository,
            predecessor,
            release,
            state,
            transition_observer=die,
        )
    journal = json.loads((state.parent / ".state.canonical-model-migration.json").read_text())
    backup = Path(journal["backup"])
    assert (state.parent, backup) in renames
    assert (state.parent, state.parent) in renames

    with pytest.raises(RuntimeError, match="rolled back interrupted migration"):
        _salvage(repository, predecessor, release, state)
    assert (backup, state.parent) in renames


def test_lifecycle_lock_blocks_runner_until_migration_publishes_new_ids(
    tmp_path, monkeypatch, capsys
):
    repository, predecessor, release, state, old_runs, _evidence = _fixture(tmp_path)
    monkeypatch.chdir(repository)
    prepared = threading.Event()
    proceed = threading.Event()
    outcome = {}

    def pause(candidate):
        if candidate == "journal:prepared":
            prepared.set()
            assert proceed.wait(timeout=10)

    def migrate():
        try:
            outcome["result"] = _salvage(
                repository,
                predecessor,
                release,
                state,
                transition_observer=pause,
            )
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=migrate)
    worker.start()
    assert prepared.wait(timeout=10)
    old_run_id = old_runs[SAMPLE_PROTOCOLS[0]].run_id
    with pytest.raises(RunAlreadyRunningError, match="lifecycle operation"):
        cli_main(
            [
                "resume",
                "--release",
                str(release),
                "--state-root",
                str(state),
                "--run-id",
                old_run_id,
            ]
        )
    with pytest.raises(RunAlreadyRunningError, match="lifecycle operation"):
        cli_main(
            [
                "plan",
                "--release",
                str(release),
                "--state-root",
                str(state),
                "--release-id",
                "concurrent-plan",
                "--repository-url",
                "https://example.invalid/repository",
                "--model",
                REQUESTED,
                "--dialect",
                "enclosure.plain-v1",
                "--protocol",
                SAMPLE_PROTOCOLS[0],
            ]
        )
    proceed.set()
    worker.join(timeout=30)
    assert not worker.is_alive()
    assert "error" not in outcome
    result = outcome["result"]
    assert not (state / old_run_id).exists()
    assert set(path.name for path in state.glob("run_*")) == set(result["run_ids"])
    assert (Path(result["predecessor_backup"]) / "state" / old_run_id).is_dir()

    monkeypatch.setattr(
        "dbench.cli.single_lof_task",
        lambda *, suite, form_set, dialect, protocol: _frozen_identity_task(
            suite_path=Path(suite),
            form_set=form_set,
            dialect_id=dialect,
            protocol=protocol_registry_from_bytes(
                (repository / PROTOCOL_REGISTRY_GIT_PATH).read_bytes()
            )[protocol],
        ),
    )
    env_file = tmp_path / "test.env"
    env_file.write_text("OPENROUTER_API_KEY=not-used-by-dry-run\n")
    env_file.chmod(0o600)
    assert (
        cli_main(
            [
                "resume",
                "--release",
                str(release),
                "--state-root",
                str(state),
                "--run-id",
                result["run_ids"][0],
                "--env-file",
                str(env_file),
            ]
        )
        == 0
    )
    assert result["run_ids"][0] in capsys.readouterr().out
