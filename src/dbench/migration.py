"""Explicit one-time repair of pre-canonical OpenRouter working state."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample

from dbench.openrouter import EndpointSelection
from dbench.provider_evidence import project_openrouter_evidence, project_provider_evidence
from dbench.release_policy import validate_sample_release
from lofbench.accounting import DEFAULT_GLOBAL_CAP_USD, SpendLedger
from lofbench.authority import authority_from_git, canonical_sha256
from lofbench.orchestration import (
    RunOrchestrator,
    call_record_from_projection,
    project_attempt,
    trial_record_from_projection,
)
from lofbench.protocols import get_protocol
from lofbench.records import (
    AttemptEvidence,
    LedgerEvent,
    RequestStartedRecord,
    RunManifest,
    execution_spec_identity,
)
from lofbench.release_bundle import ReleaseBundle
from lofbench.run_models import (
    InMemoryExecutor,
    call_id_for,
    plan_run,
    request_sha256_for,
    trial_id_for,
)
from lofbench.state_io import (
    append_jsonl_fsynced,
    read_jsonl,
    replace_path_durable,
    state_lifecycle_lock,
    write_json_atomic,
)
from lofbench.suites import load_suite

SAMPLE_RELEASE_ID = "v1.0.0-sample.1"
SAMPLE_PROTOCOLS = (
    "reduce-infer-v1",
    "reduce-taught-v1",
    "transcribe-infer-v1",
    "transcribe-taught-v1",
)
_SOURCE_PATHS = ("src/dbench", "src/lofbench")
_REPAIR_SOURCE_PATHS = frozenset(
    {
        "src/dbench/cli.py",
        "src/dbench/migration.py",
        "src/dbench/openrouter.py",
        "src/dbench/provider_evidence.py",
        "src/lofbench/orchestration.py",
        "src/lofbench/state_io.py",
    }
)
IDENTITY_EVENT_ID = "ev_01KZHVJWTQAXJEACS9BTVN34Z9"
IDENTITY_EVENT_ACTOR = "agent:codex-root"
IDENTITY_EVENT_TYPE = "comment_added"
IDENTITY_EVENT_BODY = (
    "live sample attempt 1/20 stopped fail-closed after generation accounting recovery: "
    "chat reports requested google/gemini-3.1-flash-lite, while generation reports canonical "
    "provider model google/gemini-3.1-flash-lite-20260507. key observed cost $0.002320875; "
    "reservation remains. authenticated /models/user exposes canonical_slug at plan time, but "
    "endpoint selector ignored it. no retry/new inference until plan identity is corrected and "
    "the retained first attempt is safely re-keyed into the canonical run; total sample attempt "
    "budget remains 20."
)
IDENTITY_EVENT_BODY_SHA256 = "908c7d1a5e0b42765fc807402051ab43900c03f8985ec6e86d7ee4aff3cc8103"
_IDENTITY_EVENT_PATH = ".lattice/events/task_01KZGZ7YTE0GCWF9E29XZVJP17.jsonl"
MigrationObserver = Callable[[str], None]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git(repository_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _require_predecessor_commit(
    repository_root: Path,
    *,
    predecessor: str,
) -> str:
    current = _git(repository_root, "rev-parse", "HEAD")
    if _git(repository_root, "status", "--porcelain"):
        raise RuntimeError("working-state migration requires a clean repository")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", predecessor, current],
        cwd=repository_root,
        check=False,
    )
    if ancestor.returncode != 0:
        raise RuntimeError("release source commit is not an ancestor of the migration commit")
    source_commits = _git(
        repository_root,
        "rev-list",
        f"{predecessor}..{current}",
        "--",
        *_SOURCE_PATHS,
    ).splitlines()
    changed_source_paths = set(
        _git(
            repository_root,
            "diff",
            "--name-only",
            f"{predecessor}..{current}",
            "--",
            *_SOURCE_PATHS,
        ).splitlines()
    )
    source_merges = _git(
        repository_root,
        "rev-list",
        "--merges",
        f"{predecessor}..{current}",
        "--",
        *_SOURCE_PATHS,
    )
    if (
        not source_commits
        or source_merges
        or not changed_source_paths
        or not changed_source_paths <= _REPAIR_SOURCE_PATHS
    ):
        raise RuntimeError("migration accepts only the bounded canonical-identity repair lineage")
    changed_registries = _git(
        repository_root,
        "diff",
        "--name-only",
        f"{predecessor}..{current}",
        "--",
        "src/lofbench/registries/suites-v1.json",
        "src/lofbench/registries/protocols-v1.json",
    )
    if changed_registries:
        raise RuntimeError("migration cannot change the frozen suite or protocol authority")
    return current


def _require_identity_event(
    repository_root: Path,
    *,
    commit: str,
    identity_event_id: str,
) -> None:
    if identity_event_id != IDENTITY_EVENT_ID:
        raise RuntimeError("migration identity event is not the exact live mismatch event")
    if sha256(IDENTITY_EVENT_BODY.encode()).hexdigest() != IDENTITY_EVENT_BODY_SHA256:
        raise RuntimeError("compiled migration identity-event authority is inconsistent")
    try:
        payload = _git(repository_root, "show", f"{commit}:{_IDENTITY_EVENT_PATH}")
        rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
    except (json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("source commit lacks readable tracked identity-event authority") from exc
    matches = [row for row in rows if row.get("id") == IDENTITY_EVENT_ID]
    if len(matches) != 1:
        raise RuntimeError("source commit lacks one exact tracked identity event")
    event = matches[0]
    body = (event.get("data") or {}).get("body")
    if (
        event.get("schema_version") != 1
        or event.get("task_id") != "task_01KZGZ7YTE0GCWF9E29XZVJP17"
        or event.get("actor") != IDENTITY_EVENT_ACTOR
        or event.get("type") != IDENTITY_EVENT_TYPE
        or body != IDENTITY_EVENT_BODY
        or sha256(body.encode()).hexdigest() != IDENTITY_EVENT_BODY_SHA256
    ):
        raise RuntimeError("tracked identity event contradicts the live mismatch authority")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("durable write made no progress")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        replace_path_durable(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _require_regular_tree(root: Path, *, label: str) -> None:
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError(f"{label} is not a regular directory")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise RuntimeError(f"{label} contains a symbolic link")


def _state_digest(state_root: Path) -> str:
    return canonical_sha256(
        {
            path.relative_to(state_root).as_posix(): _file_sha256(path)
            for path in sorted(state_root.rglob("*"))
            if path.is_file() and not path.name.endswith(".lock")
        }
    )


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def _observe(observer: MigrationObserver | None, transition: str) -> None:
    if observer is not None:
        observer(transition)


def _journal_path(state_root: Path) -> Path:
    return state_root.parent / f".{state_root.name}.canonical-model-migration.json"


def _write_journal(
    path: Path,
    journal: dict[str, Any],
    phase: str,
    observer: MigrationObserver | None,
) -> None:
    journal["phase"] = phase
    write_json_atomic(path, journal)
    _observe(observer, f"journal:{phase}")


def _existing_state_digest(path: Path) -> str | None:
    return _state_digest(path) if path.is_dir() else None


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} is not a json object")
    return value


def _validate_committed_migration_audit(
    *,
    journal: dict[str, Any],
    release_root: Path,
    state_root: Path,
    backup: Path,
) -> None:
    audit_path = backup / "migration-audit.json"
    predecessor_release_path = backup / "predecessor-release.json"
    audit = _read_json_object(audit_path, label="persisted migration audit")
    state_audit = _read_json_object(
        state_root / "model-identity-migration.json",
        label="candidate state migration audit",
    )
    predecessor_release = _read_json_object(
        predecessor_release_path,
        label="predecessor release",
    )
    candidate_release = _read_json_object(
        release_root / "release.json",
        label="candidate release",
    )
    expected_event_authority = {
        "actor": IDENTITY_EVENT_ACTOR,
        "body_sha256": IDENTITY_EVENT_BODY_SHA256,
        "type": IDENTITY_EVENT_TYPE,
    }
    if (
        _file_sha256(audit_path) != journal.get("migration_audit_sha256")
        or audit != state_audit
        or audit.get("schema_version") != 1
        or audit.get("kind") != "openrouter-canonical-model-identity-v1"
        or audit.get("identity_event_id") != IDENTITY_EVENT_ID
        or audit.get("identity_event_authority") != expected_event_authority
        or audit.get("predecessor_release_manifest_sha256")
        != journal.get("predecessor_release_sha256")
        or audit.get("predecessor_state_sha256") != journal.get("predecessor_state_sha256")
        or audit.get("predecessor_repository_commit")
        != predecessor_release.get("repository_commit")
        or audit.get("repository_commit") != candidate_release.get("repository_commit")
        or audit.get("predecessor_authority") != predecessor_release.get("authority")
        or audit.get("authority") != candidate_release.get("authority")
        or audit.get("attempts_retained") != 1
        or audit.get("remaining_attempts") != 19
    ):
        raise RuntimeError("persisted migration audit authority is inconsistent")

    old_ids = predecessor_release.get("expected_run_ids")
    new_ids = candidate_release.get("expected_run_ids")
    run_id_map = audit.get("run_id_map")
    trial_id_map = audit.get("trial_id_map")
    migrations = candidate_release.get("working_state_migrations")
    if (
        not isinstance(old_ids, list)
        or len(old_ids) != 4
        or not isinstance(new_ids, list)
        or len(new_ids) != 4
        or not isinstance(run_id_map, dict)
        or set(run_id_map) != set(old_ids)
        or set(run_id_map.values()) != set(new_ids)
        or not isinstance(trial_id_map, dict)
        or not isinstance(migrations, list)
        or not migrations
        or migrations[-1] != audit
    ):
        raise RuntimeError("persisted migration audit run mapping is inconsistent")

    predecessor_state = backup / "state"
    old_runs = {
        run_id: RunManifest.from_dict(
            _read_json_object(
                predecessor_state / run_id / "run.json",
                label="predecessor run",
            )
        )
        for run_id in old_ids
    }
    new_runs = {
        run_id: RunManifest.from_dict(
            _read_json_object(state_root / run_id / "run.json", label="candidate run")
        )
        for run_id in new_ids
    }
    predecessor_run_sha256 = audit.get("predecessor_run_manifest_sha256")
    if (
        {path.name for path in state_root.glob("run_*")} != set(new_ids)
        or not isinstance(predecessor_run_sha256, dict)
        or predecessor_run_sha256
        != {run_id: canonical_sha256(run.to_dict()) for run_id, run in old_runs.items()}
        or set(trial_id_map)
        != {trial_id for run in old_runs.values() for trial_id in run.expected_trial_ids}
        or set(trial_id_map.values())
        != {trial_id for run in new_runs.values() for trial_id in run.expected_trial_ids}
        or any(
            old_runs[old_id].protocol_id != new_runs[run_id_map[old_id]].protocol_id
            for old_id in old_ids
        )
        or any(
            canonical_sha256(run.catalog_row) != audit.get("authenticated_catalog_sha256")
            for run in new_runs.values()
        )
    ):
        raise RuntimeError("persisted migration audit identity hashes are inconsistent")

    active_runs = [run for run in new_runs.values() if run.attempts]
    if (
        len(active_runs) != 1
        or active_runs[0].attempts != 1
        or active_runs[0].status != "probed"
        or sum(run.attempts for run in new_runs.values()) != 1
        or sum(len(run.expected_trial_ids) - run.attempts for run in new_runs.values()) != 19
        or any(
            run.status != "planned" or run.attempts != 0
            for run in new_runs.values()
            if run.run_id != active_runs[0].run_id
        )
    ):
        raise RuntimeError("committed migration state does not retain exactly one attempt")
    active = active_runs[0]
    active_dir = state_root / active.run_id
    old_active_id = next(
        (old_id for old_id, new_id in run_id_map.items() if new_id == active.run_id),
        None,
    )
    if old_active_id is None:
        raise RuntimeError("persisted migration audit lacks the active run mapping")
    old_active_dir = predecessor_state / old_active_id
    requests = read_jsonl(active_dir / "request-started.jsonl")
    evidence = read_jsonl(active_dir / "transcripts.jsonl")
    calls = read_jsonl(active_dir / "calls.jsonl")
    trials = read_jsonl(active_dir / "trials.jsonl")
    old_requests = read_jsonl(old_active_dir / "request-started.jsonl")
    old_evidence = read_jsonl(old_active_dir / "transcripts.jsonl")
    old_calls = read_jsonl(old_active_dir / "calls.jsonl")
    active_audit = audit.get("active_attempt")
    ledger = read_jsonl(state_root / "spend-ledger.jsonl")
    if (
        not isinstance(active_audit, dict)
        or old_runs[old_active_id].attempts != 1
        or sum(run.attempts for run in old_runs.values()) != 1
        or len(requests) != 1
        or len(evidence) != 2
        or len(calls) != 1
        or len(trials) != 1
        or len(old_requests) != 1
        or len(old_evidence) != 2
        or len(old_calls) != 2
        or active_audit.get("old_run_id") != old_active_id
        or active_audit.get("new_run_id") != active.run_id
        or trial_id_map.get(active_audit.get("old_trial_id")) != active_audit.get("new_trial_id")
        or active_audit.get("old_call_id") != old_calls[-1].get("call_id")
        or active_audit.get("new_call_id") != calls[0].get("call_id")
        or active_audit.get("old_request_sha256") != old_requests[0].get("request_sha256")
        or active_audit.get("new_request_sha256") != requests[0].get("request_sha256")
        or active_audit.get("old_evidence_sha256")
        != [row.get("evidence_sha256") for row in old_evidence]
        or active_audit.get("new_evidence_sha256")
        != [row.get("evidence_sha256") for row in evidence]
        or active_audit.get("provider_evidence_sha256")
        != [canonical_sha256(row["provider_evidence"]) for row in old_evidence]
        or active_audit.get("new_call_record_sha256") != canonical_sha256(calls[0])
        or audit.get("predecessor_call_record_sha256")
        != [canonical_sha256(row) for row in old_calls]
        or active.cost_usd != active_audit.get("observed_cost_usd")
        or calls[0].get("status") != "complete"
        or calls[0].get("observed_cost_usd") != active.cost_usd
        or trials[0].get("parse_status") != "valid"
        or trials[0].get("trial_id") != active_audit.get("new_trial_id")
        or any(
            row.get("run_id") != active.run_id for row in [*requests, *evidence, *calls, *trials]
        )
        or any(row.get("call_id") != active_audit.get("new_call_id") for row in requests + evidence)
        or [row.get("revision") for row in evidence] != [0, 1]
        or evidence[1].get("predecessor_evidence_sha256") != evidence[0].get("evidence_sha256")
        or [row.get("event_type") for row in ledger] != ["reserved", "settled"]
        or ledger[-1].get("amount_usd") != active.cost_usd
        or any(row.get("run_id") != active.run_id for row in ledger)
        or any(row.get("call_id") != active_audit.get("new_call_id") for row in ledger)
    ):
        raise RuntimeError("persisted migration audit attempt closure is inconsistent")


def _recover_interrupted_migration(
    *,
    journal_path: Path,
    release_root: Path,
    state_root: Path,
) -> str | None:
    """Recover one journaled process death while the lifecycle lock is held."""
    if not journal_path.exists():
        return None
    try:
        journal = json.loads(journal_path.read_text())
        phase = journal["phase"]
        stage = Path(journal["stage"])
        backup = Path(journal["backup"])
        predecessor_state = backup / "state"
        predecessor_release = backup / "predecessor-release.json"
        predecessor_state_sha256 = journal["predecessor_state_sha256"]
        predecessor_release_sha256 = journal["predecessor_release_sha256"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"migration recovery journal is unreadable and retained at {journal_path}"
        ) from exc
    if (
        journal.get("schema_version") != 1
        or journal.get("kind") != "openrouter-canonical-model-identity-transaction-v1"
        or Path(journal.get("state_root", "")) != state_root
        or Path(journal.get("release_root", "")) != release_root
        or stage
        != state_root.parent
        / f".{state_root.name}.canonical-model-stage-{predecessor_state_sha256[:12]}"
        or backup
        != state_root.parent
        / f"{state_root.name}.pre-canonical-model-{predecessor_state_sha256[:12]}"
    ):
        raise RuntimeError(f"migration recovery journal has foreign authority: {journal_path}")

    if phase == "committed":
        try:
            if (
                _existing_state_digest(state_root) != journal.get("candidate_state_sha256")
                or _file_sha256(release_root / "release.json")
                != journal.get("candidate_release_sha256")
                or _existing_state_digest(predecessor_state) != predecessor_state_sha256
                or not predecessor_release.is_file()
                or _file_sha256(predecessor_release) != predecessor_release_sha256
            ):
                raise RuntimeError("committed migration content digests are inconsistent")
            _validate_committed_migration_audit(
                journal=journal,
                release_root=release_root,
                state_root=state_root,
                backup=backup,
            )
        except Exception as exc:
            raise RuntimeError(
                f"committed migration journal is inconsistent and retained at {journal_path}"
            ) from exc
        journal_path.unlink()
        _fsync_directory(journal_path.parent)
        return phase

    current_state_sha256 = _existing_state_digest(state_root)
    backup_state_sha256 = _existing_state_digest(predecessor_state)
    if current_state_sha256 != predecessor_state_sha256:
        if backup_state_sha256 != predecessor_state_sha256:
            raise RuntimeError(
                f"interrupted migration phase {phase!r} lacks a recoverable predecessor state; "
                f"journal retained at {journal_path}"
            )
        if state_root.exists():
            if current_state_sha256 != journal.get("candidate_state_sha256"):
                raise RuntimeError(
                    f"interrupted migration phase {phase!r} contains an unknown current state; "
                    f"journal retained at {journal_path}"
                )
            shutil.rmtree(state_root)
        replace_path_durable(predecessor_state, state_root)

    release_path = release_root / "release.json"
    if _file_sha256(release_path) != predecessor_release_sha256:
        if (
            not predecessor_release.is_file()
            or _file_sha256(predecessor_release) != predecessor_release_sha256
        ):
            raise RuntimeError(
                f"interrupted migration phase {phase!r} lacks recoverable release bytes; "
                f"journal retained at {journal_path}"
            )
        _write_bytes_atomic(release_path, predecessor_release.read_bytes())
    if (
        _existing_state_digest(state_root) != predecessor_state_sha256
        or _file_sha256(release_path) != predecessor_release_sha256
    ):
        raise RuntimeError(
            f"interrupted migration phase {phase!r} could not restore the predecessor; "
            f"journal retained at {journal_path}"
        )
    if stage.exists():
        shutil.rmtree(stage)
    if backup.exists():
        shutil.rmtree(backup)
    journal_path.unlink()
    _fsync_directory(journal_path.parent)
    return phase


def recover_working_model_identity_transaction(
    *,
    release_root: Path,
    state_root: Path,
) -> str | None:
    """Recover a journaled migration before credentials or catalog access."""
    with state_lifecycle_lock(state_root, blocking=False):
        return _recover_interrupted_migration(
            journal_path=_journal_path(state_root),
            release_root=release_root,
            state_root=state_root,
        )


def _effective_calls(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        result[row["call_id"]] = row
    return result


def _frozen_identity_task(
    *,
    suite_path: Path,
    form_set: str,
    dialect_id: str,
    protocol_id: str,
) -> Task:
    """Build non-executable task identity from frozen rows, without rasterizing.

    The migration never crosses the executor seam. It needs only the same frozen
    ids and hashes that define run/request/trial identity and scoring.
    """
    suite = load_suite(path=suite_path)
    protocol = get_protocol(protocol_id)
    spec = suite.specs[dialect_id]
    forms = {row["abstract_form_id"]: row for row in suite.forms}
    cells = {(row["abstract_form_id"], row["dialect_id"]): row for row in suite.cells}
    samples = []
    for form_id in suite.form_sets[form_set]:
        form = forms[form_id]
        cell = cells[(form_id, dialect_id)]
        samples.append(
            Sample(
                id=f"{form_id}:{dialect_id}:{protocol_id}",
                input="migration-never-executes",
                target=protocol.target_for(form),
                metadata={
                    "suite_version": suite.suite_version,
                    "abstract_form_id": form_id,
                    "abstract_form": form["abstract_form"],
                    "reference_transcription": form["reference_transcription"],
                    "normal_value": form["normal_value"],
                    "difficulty": form["difficulty"],
                    "dialect_id": dialect_id,
                    "family": spec.family,
                    "archetype": spec.archetype,
                    "modality": spec.modality,
                    "model_format": spec.model_format,
                    "protocol_id": protocol_id,
                    "prompt_hash": protocol.prompt_hash(
                        reading_rule=spec.reading_rule,
                        model_payload_sha256=cell["model_payload_sha256"],
                    ),
                    "symbolic_payload_hash": cell["symbolic_payload_hash"],
                    "model_payload_sha256": cell["model_payload_sha256"],
                },
            )
        )
    return Task(
        dataset=MemoryDataset(samples=samples),
        metadata={
            "suite_version": suite.suite_version,
            "form_set": form_set,
            "form_ids": list(suite.form_sets[form_set]),
            "dialect_id": dialect_id,
            "family": spec.family,
            "protocol_id": protocol_id,
            "release_eligible": True,
        },
    )


def _validate_old_catalog(run: RunManifest, *, expected_provider: str) -> None:
    catalog = run.catalog_row
    selected = catalog.get("selected_endpoint")
    zdr = catalog.get("zdr_selected_endpoint")
    if (
        catalog.get("authenticated") is not True
        or not isinstance(selected, dict)
        or not isinstance(zdr, dict)
        or selected.get("model_id") != run.requested_model_id
        or zdr.get("model_id") != run.requested_model_id
        or selected.get("tag") != run.endpoint
        or zdr.get("tag") != run.endpoint
        or selected.get("provider_name") != zdr.get("provider_name")
        or selected.get("provider_name") != expected_provider
        or selected.get("pricing") != zdr.get("pricing")
        or "authenticated_user_model" in catalog
        or "authenticated_user_models_retrieval" in catalog
    ):
        raise RuntimeError("predecessor run lacks its exact legacy endpoint authority")


def _load_predecessor(
    release_root: Path,
    state_root: Path,
    repository_root: Path,
    *,
    expected_source_commit: str,
    expected_requested_model_id: str,
    expected_endpoint: str,
    expected_provider: str,
) -> tuple[ReleaseBundle, dict[str, RunManifest], RunManifest, list[AttemptEvidence]]:
    _require_regular_tree(release_root, label="predecessor release")
    _require_regular_tree(state_root, label="predecessor state")
    bundle = ReleaseBundle.open(
        release_root,
        repository_root=repository_root,
        evidence_projector=project_provider_evidence,
        release_policy_validator=validate_sample_release,
    )
    bundle.validate()
    manifest = bundle.manifest
    contract = manifest.get("sample_contract") or {}
    if (
        manifest.get("release_id") != SAMPLE_RELEASE_ID
        or manifest.get("status") != "working"
        or manifest.get("repository_commit") != expected_source_commit
        or manifest.get("admitted_run_ids") != []
        or bundle.runs()
        or contract.get("protocol_ids") != list(SAMPLE_PROTOCOLS)
        or contract.get("total_attempts") != 20
        or contract.get("trials_per_run") != 5
        or contract.get("max_transport_attempts") != 1
        or contract.get("form_set") != "probe"
        or contract.get("dialect_id") != "enclosure.plain-v1"
        or contract.get("execution_surface") != "direct_api"
    ):
        raise RuntimeError("release is not the exact unsealed predecessor sample")
    expected_run_ids = manifest.get("expected_run_ids")
    if not isinstance(expected_run_ids, list) or len(expected_run_ids) != 4:
        raise RuntimeError("predecessor release does not declare exactly four runs")
    approval = manifest.get("paid_run_approval") or {}
    if (
        set(approval.get("run_ids", ())) != set(expected_run_ids)
        or approval.get("paid_calls") != 20
        or approval.get("model_id") != expected_requested_model_id
        or approval.get("release_id") != SAMPLE_RELEASE_ID
        or approval.get("endpoint") != expected_endpoint
        or approval.get("provider") != expected_provider
    ):
        raise RuntimeError("predecessor paid approval does not close over the sample")
    allowed_root = {
        "cost-sheet.json",
        "spend-ledger.jsonl",
        "spend-ledger.jsonl.lock",
        *expected_run_ids,
    }
    if {path.name for path in state_root.iterdir()} != allowed_root:
        raise RuntimeError("predecessor state root contains unexpected files")

    runs: dict[str, RunManifest] = {}
    active: list[RunManifest] = []
    for run_id in expected_run_ids:
        run_dir = state_root / run_id
        run = RunManifest.from_dict(json.loads((run_dir / "run.json").read_text()))
        bundle.validate_planned_run(run)
        _validate_old_catalog(run, expected_provider=expected_provider)
        if (
            run.run_id != run_id
            or run.authoritative_run_id() != run_id
            or run.requested_model_id != expected_requested_model_id
            or run.resolved_model_id != expected_requested_model_id
            or run.endpoint != expected_endpoint
            or run.form_set != "probe"
            or run.dialect_id != "enclosure.plain-v1"
            or run.max_transport_attempts != 1
            or len(run.expected_trial_ids) != 5
            or run.protocol_id in runs
        ):
            raise RuntimeError("predecessor run identity is not the exact legacy shape")
        runs[run.protocol_id] = run
        names = {path.name for path in run_dir.iterdir()}
        allowed_names = {
            "run.json",
            ".run.lock",
            "request-started.jsonl",
            "transcripts.jsonl",
            "calls.jsonl",
        }
        if not names <= allowed_names:
            raise RuntimeError("predecessor run directory contains unexpected files")
        records = {
            name: read_jsonl(run_dir / name)
            for name in (
                "request-started.jsonl",
                "transcripts.jsonl",
                "calls.jsonl",
                "trials.jsonl",
            )
        }
        if any(records.values()):
            if names != allowed_names:
                raise RuntimeError("active predecessor run files are incomplete")
            active.append(run)
        elif (
            names != {"run.json"}
            or run.status != "planned"
            or run.attempts != 0
            or run.cost_usd != 0
        ):
            raise RuntimeError("inactive predecessor runs must remain untouched plans")
    if set(runs) != set(SAMPLE_PROTOCOLS) or len(active) != 1:
        raise RuntimeError("predecessor state must contain one active run and three plans")

    active_run = active[0]
    active_dir = state_root / active_run.run_id
    requests = read_jsonl(active_dir / "request-started.jsonl")
    evidence = [
        AttemptEvidence.from_dict(row) for row in read_jsonl(active_dir / "transcripts.jsonl")
    ]
    calls = read_jsonl(active_dir / "calls.jsonl")
    trials = read_jsonl(active_dir / "trials.jsonl")
    ledger_rows = [
        LedgerEvent.from_dict(row) for row in read_jsonl(state_root / "spend-ledger.jsonl")
    ]
    if (
        active_run.status != "probed"
        or active_run.attempts != 1
        or active_run.cost_usd != 0
        or len(requests) != 1
        or len(evidence) != 2
        or len(calls) != 2
        or trials
        or len(ledger_rows) != 1
        or ledger_rows[0].event_type != "reserved"
    ):
        raise RuntimeError("active predecessor run is not the retained first-attempt state")
    if (
        [row.revision for row in evidence] != [0, 1]
        or evidence[0].predecessor_evidence_sha256
        or evidence[1].predecessor_evidence_sha256 != evidence[0].evidence_sha256
        or any(row.authoritative_digest() != row.evidence_sha256 for row in evidence)
    ):
        raise RuntimeError("predecessor evidence revision chain is invalid")
    old_trial_id = active_run.expected_trial_ids[0]
    old_call_id = call_id_for(old_trial_id, 1)
    request = RequestStartedRecord.from_dict(requests[0])
    reservation = ledger_rows[0]
    active_task = _frozen_identity_task(
        suite_path=release_root / "suite.json",
        form_set=active_run.form_set,
        dialect_id=active_run.dialect_id,
        protocol_id=active_run.protocol_id,
    )
    active_sample = list(active_task.dataset.samples)[0]
    expected_request_sha256 = request_sha256_for(
        call_id=old_call_id,
        trial_id=old_trial_id,
        run_id=active_run.run_id,
        attempt=1,
        prompt_hash=active_sample.metadata["prompt_hash"],
        model_payload_sha256=active_sample.metadata["model_payload_sha256"],
    )
    if any(
        (row.call_id, row.trial_id, row.run_id, row.attempt)
        != (old_call_id, old_trial_id, active_run.run_id, 1)
        for row in evidence
    ) or (
        request.call_id,
        request.trial_id,
        request.run_id,
        request.attempt,
    ) != (old_call_id, old_trial_id, active_run.run_id, 1):
        raise RuntimeError("predecessor request/evidence ids are not deterministic")
    if request.request_sha256 != expected_request_sha256:
        raise RuntimeError("predecessor request hash is not authoritative")
    if (
        reservation.call_id,
        reservation.trial_id,
        reservation.run_id,
        reservation.cohort,
    ) != (old_call_id, old_trial_id, active_run.run_id, active_run.cohort):
        raise RuntimeError("predecessor reservation identity is invalid")
    cost_sheet = json.loads((state_root / "cost-sheet.json").read_text())
    cost_rows = cost_sheet.get("runs")
    if (
        cost_sheet.get("model_id") != expected_requested_model_id
        or cost_sheet.get("endpoint") != expected_endpoint
        or cost_sheet.get("provider") != expected_provider
        or not isinstance(cost_rows, list)
        or len(cost_rows) != 4
    ):
        raise RuntimeError("predecessor cost sheet does not match the exact sample")
    cost_by_protocol = {row.get("protocol_id"): row for row in cost_rows if isinstance(row, dict)}
    if set(cost_by_protocol) != set(SAMPLE_PROTOCOLS) or any(
        row.get("run_id") != runs[protocol_id].run_id or row.get("trials") != 5
        for protocol_id, row in cost_by_protocol.items()
    ):
        raise RuntimeError("predecessor cost-sheet runs are not authoritative")
    try:
        expected_reservation = Decimal(
            str(cost_by_protocol[active_run.protocol_id]["conservative_reservation_usd"])
        ) / Decimal(5)
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("predecessor cost-sheet reservation is invalid") from exc
    if Decimal(str(reservation.amount_usd)) != expected_reservation:
        raise RuntimeError("predecessor reservation does not match its cost sheet")
    expected_calls = []
    for index, record in enumerate(evidence):
        projection = project_openrouter_evidence(
            record.provider_evidence,
            active_run.to_dict(),
        )
        expected_error = (
            "missing_generation_accounting" if index == 0 else "contradictory_planned_model"
        )
        if projection.status != "accounting_unknown" or projection.error_type != expected_error:
            raise RuntimeError("predecessor evidence is not the exact legacy identity failure")
        if index == 1:
            projection = replace(projection, error_type="contradictory_model")
        expected_calls.append(
            call_record_from_projection(
                call_id=old_call_id,
                trial_id=old_trial_id,
                run=active_run,
                attempt=1,
                reservation=reservation.amount_usd,
                projection=projection,
                evidence=record,
            ).to_dict()
        )
    effective = _effective_calls(calls)
    if calls != expected_calls or len(effective) != 1:
        raise RuntimeError("predecessor call is not the exact legacy identity failure")
    final_call = expected_calls[-1]
    if (
        active_run.token_usage
        != {
            "input_tokens": final_call["input_tokens"],
            "output_tokens": final_call["output_tokens"],
            "reasoning_tokens": final_call["reasoning_tokens"],
        }
        or active_run.latency_ms != final_call["latency_ms"]
    ):
        raise RuntimeError("predecessor run aggregates do not match its effective call")
    return bundle, runs, active_run, evidence


def _write_staged_state(
    stage: Path,
    *,
    runs: dict[str, RunManifest],
    tasks: dict[str, Any],
    active_protocol: str,
    old_run: RunManifest,
    old_evidence: list[AttemptEvidence],
    old_request: RequestStartedRecord,
    old_reservation: LedgerEvent,
    expected_cost_usd: float,
    audit: dict[str, Any],
    cost_sheet: dict[str, Any],
) -> dict[str, RunManifest]:
    active_run = runs[active_protocol]
    sample = list(tasks[active_protocol].dataset.samples)[0]
    new_trial_id = trial_id_for(
        active_run.run_id,
        sample.metadata["abstract_form_id"],
        sample.metadata["dialect_id"],
    )
    new_call_id = call_id_for(new_trial_id, 1)
    new_request = RequestStartedRecord(
        call_id=new_call_id,
        trial_id=new_trial_id,
        run_id=active_run.run_id,
        attempt=1,
        request_sha256=request_sha256_for(
            call_id=new_call_id,
            trial_id=new_trial_id,
            run_id=active_run.run_id,
            attempt=1,
            prompt_hash=sample.metadata["prompt_hash"],
            model_payload_sha256=sample.metadata["model_payload_sha256"],
        ),
        started_at=old_request.started_at,
    )
    new_evidence = AttemptEvidence.capture(
        call_id=new_call_id,
        trial_id=new_trial_id,
        run_id=active_run.run_id,
        attempt=1,
        provider_evidence=old_evidence[0].provider_evidence,
    )
    revised_evidence = new_evidence.revise(old_evidence[1].provider_evidence)
    first_projection = project_attempt(
        new_evidence.provider_evidence,
        active_run,
        project_openrouter_evidence,
    )
    projection = project_attempt(
        revised_evidence.provider_evidence,
        active_run,
        project_openrouter_evidence,
    )
    if (
        first_projection.status != "accounting_unknown"
        or first_projection.error_type != "missing_generation_accounting"
        or projection.status != "complete"
        or projection.error_type
        or projection.observed_cost_usd != expected_cost_usd
    ):
        raise RuntimeError("retained provider evidence does not reproject to the exact completion")
    if any(
        before.provider_evidence != after.provider_evidence
        for before, after in zip(old_evidence, (new_evidence, revised_evidence), strict=True)
    ):
        raise RuntimeError("migration changed retained raw provider evidence")
    call = call_record_from_projection(
        call_id=new_call_id,
        trial_id=new_trial_id,
        run=active_run,
        attempt=1,
        reservation=old_reservation.amount_usd,
        projection=projection,
        evidence=revised_evidence,
    )
    trial = trial_record_from_projection(
        run=active_run,
        sample=sample,
        protocol=get_protocol(active_protocol),
        projection=projection,
        evidence=revised_evidence,
        attempt=1,
    )
    if trial.parse_status != "valid":
        raise RuntimeError("retained first response is not a valid scored trial")
    migrated_active = replace(
        active_run,
        status="probed",
        attempts=1,
        token_usage={
            "input_tokens": projection.input_tokens,
            "output_tokens": projection.output_tokens,
            "reasoning_tokens": projection.reasoning_tokens,
        },
        latency_ms=projection.latency_ms,
        cost_usd=projection.observed_cost_usd,
    )
    runs = {**runs, active_protocol: migrated_active}
    for protocol_id, run in runs.items():
        write_json_atomic(stage / run.run_id / "run.json", run.to_dict())
        if protocol_id != active_protocol:
            continue
        append_jsonl_fsynced(stage / run.run_id / "request-started.jsonl", new_request.to_dict())
        append_jsonl_fsynced(stage / run.run_id / "transcripts.jsonl", new_evidence.to_dict())
        append_jsonl_fsynced(stage / run.run_id / "transcripts.jsonl", revised_evidence.to_dict())
        append_jsonl_fsynced(stage / run.run_id / "calls.jsonl", call.to_dict())
        append_jsonl_fsynced(stage / run.run_id / "trials.jsonl", trial.to_dict())
    migrated_at = audit["migrated_at"]
    append_jsonl_fsynced(
        stage / "spend-ledger.jsonl",
        LedgerEvent(
            event_type="reserved",
            call_id=new_call_id,
            trial_id=new_trial_id,
            run_id=migrated_active.run_id,
            cohort=migrated_active.cohort,
            amount_usd=old_reservation.amount_usd,
            at=old_reservation.at,
        ).to_dict(),
    )
    append_jsonl_fsynced(
        stage / "spend-ledger.jsonl",
        LedgerEvent(
            event_type="settled",
            call_id=new_call_id,
            trial_id=new_trial_id,
            run_id=migrated_active.run_id,
            cohort=migrated_active.cohort,
            amount_usd=projection.observed_cost_usd,
            at=migrated_at,
        ).to_dict(),
    )
    audit["active_attempt"] = {
        "old_run_id": old_run.run_id,
        "new_run_id": migrated_active.run_id,
        "old_trial_id": old_evidence[0].trial_id,
        "new_trial_id": new_trial_id,
        "old_call_id": old_evidence[0].call_id,
        "new_call_id": new_call_id,
        "old_request_sha256": old_request.request_sha256,
        "new_request_sha256": new_request.request_sha256,
        "old_evidence_sha256": [row.evidence_sha256 for row in old_evidence],
        "new_evidence_sha256": [new_evidence.evidence_sha256, revised_evidence.evidence_sha256],
        "new_call_record_sha256": canonical_sha256(call.to_dict()),
        "provider_evidence_sha256": [
            canonical_sha256(row.provider_evidence.to_dict()) for row in old_evidence
        ],
        "observed_cost_usd": projection.observed_cost_usd,
    }
    write_json_atomic(stage / "model-identity-migration.json", audit)
    write_json_atomic(stage / "cost-sheet.json", cost_sheet)
    return runs


def salvage_working_model_identity(
    *,
    release_root: Path,
    state_root: Path,
    repository_root: Path,
    selection: EndpointSelection,
    expected_source_commit: str,
    expected_requested_model_id: str,
    expected_resolved_model_id: str,
    expected_endpoint: str,
    expected_provider: str,
    expected_observed_cost_usd: float,
    identity_event_id: str,
    transition_observer: MigrationObserver | None = None,
) -> dict[str, Any]:
    """Re-key one exact unsealed predecessor release without provider inference."""
    journal_path = _journal_path(state_root)
    with state_lifecycle_lock(state_root, blocking=False):
        recovered_phase = _recover_interrupted_migration(
            journal_path=journal_path,
            release_root=release_root,
            state_root=state_root,
        )
        if recovered_phase is not None:
            action = "finalized" if recovered_phase == "committed" else "rolled back"
            raise RuntimeError(
                f"{action} interrupted migration phase {recovered_phase!r}; rerun explicitly"
            )
        if (
            selection.model_id != expected_requested_model_id
            or selection.resolved_model_id != expected_resolved_model_id
            or selection.endpoint_tag != expected_endpoint
            or selection.provider_name != expected_provider
            or identity_event_id != IDENTITY_EVENT_ID
            or not math.isfinite(expected_observed_cost_usd)
            or expected_observed_cost_usd < 0
        ):
            raise RuntimeError(
                "authenticated selection does not match the explicit migration scope"
            )
        current_commit = _require_predecessor_commit(
            repository_root,
            predecessor=expected_source_commit,
        )
        _require_identity_event(
            repository_root,
            commit=current_commit,
            identity_event_id=identity_event_id,
        )
        bundle, old_runs, old_active, old_evidence = _load_predecessor(
            release_root,
            state_root,
            repository_root,
            expected_source_commit=expected_source_commit,
            expected_requested_model_id=expected_requested_model_id,
            expected_endpoint=expected_endpoint,
            expected_provider=expected_provider,
        )
        old_release_bytes = (release_root / "release.json").read_bytes()
        old_manifest = json.loads(old_release_bytes)
        old_state_sha256 = _state_digest(state_root)
        old_request = RequestStartedRecord.from_dict(
            read_jsonl(state_root / old_active.run_id / "request-started.jsonl")[0]
        )
        old_call_rows = read_jsonl(state_root / old_active.run_id / "calls.jsonl")
        old_reservation = LedgerEvent.from_dict(read_jsonl(state_root / "spend-ledger.jsonl")[0])
        old_execution = execution_spec_identity(old_active.to_dict())
        execution = selection.execution_spec(
            cohort=old_active.cohort,
            max_transport_attempts=old_active.max_transport_attempts,
        )
        new_execution = execution_spec_identity(asdict(execution))
        for field in old_execution:
            if field == "resolved_model_id":
                continue
            if old_execution[field] != new_execution[field]:
                raise RuntimeError(
                    f"authenticated selection changed predecessor execution field {field!r}"
                )

        suite = load_suite(path=release_root / "suite.json")
        tasks: dict[str, Any] = {}
        runs: dict[str, RunManifest] = {}
        for protocol_id in SAMPLE_PROTOCOLS:
            task = _frozen_identity_task(
                suite_path=release_root / "suite.json",
                form_set="probe",
                dialect_id="enclosure.plain-v1",
                protocol_id=protocol_id,
            )
            run = plan_run(
                task,
                suite_version=suite.suite_version,
                form_set="probe",
                dialect_id="enclosure.plain-v1",
                protocol_id=protocol_id,
                execution=execution,
                suite_registry_path=release_root / "suite.json",
                protocol_registry_path=release_root / "protocols.json",
            )
            tasks[protocol_id] = task
            runs[protocol_id] = run
        if len({run.run_id for run in runs.values()}) != 4:
            raise RuntimeError("canonical migration did not produce four distinct runs")

        source_authority, source_suite, source_protocols = authority_from_git(
            repository_root, commit=current_commit
        )
        if (
            source_suite != (release_root / "suite.json").read_bytes()
            or source_protocols != (release_root / "protocols.json").read_bytes()
        ):
            raise RuntimeError("current repository authority differs from the predecessor release")
        migrated_at = _now()
        run_id_map = {
            old_runs[protocol_id].run_id: runs[protocol_id].run_id
            for protocol_id in SAMPLE_PROTOCOLS
        }
        trial_id_map = {
            old_trial: new_trial
            for protocol_id in SAMPLE_PROTOCOLS
            for old_trial, new_trial in zip(
                old_runs[protocol_id].expected_trial_ids,
                runs[protocol_id].expected_trial_ids,
                strict=True,
            )
        }
        audit: dict[str, Any] = {
            "schema_version": 1,
            "kind": "openrouter-canonical-model-identity-v1",
            "identity_event_id": identity_event_id,
            "identity_event_authority": {
                "actor": IDENTITY_EVENT_ACTOR,
                "body_sha256": IDENTITY_EVENT_BODY_SHA256,
                "type": IDENTITY_EVENT_TYPE,
            },
            "migrated_at": migrated_at,
            "predecessor_repository_commit": expected_source_commit,
            "repository_commit": current_commit,
            "predecessor_authority": old_manifest["authority"],
            "authority": source_authority.to_dict(),
            "predecessor_release_manifest_sha256": _file_sha256(release_root / "release.json"),
            "predecessor_state_sha256": old_state_sha256,
            "predecessor_run_manifest_sha256": {
                run.run_id: canonical_sha256(run.to_dict()) for run in old_runs.values()
            },
            "authenticated_catalog_sha256": canonical_sha256(selection.catalog_row),
            "predecessor_call_record_sha256": [canonical_sha256(row) for row in old_call_rows],
            "requested_model_id": expected_requested_model_id,
            "resolved_model_id": expected_resolved_model_id,
            "endpoint": expected_endpoint,
            "provider": expected_provider,
            "run_id_map": run_id_map,
            "trial_id_map": trial_id_map,
            "attempts_retained": 1,
            "remaining_attempts": 19,
        }
        old_cost_sheet = json.loads((state_root / "cost-sheet.json").read_text())
        old_cost_sheet.update(
            {
                "model_id": expected_requested_model_id,
                "resolved_model_id": expected_resolved_model_id,
                "catalog_retrieved_at": selection.retrieved_at,
                "authenticated_user_model": selection.catalog_row["authenticated_user_model"],
                "authenticated_user_models_retrieval": selection.catalog_row[
                    "authenticated_user_models_retrieval"
                ],
            }
        )
        old_costs = {row["protocol_id"]: row for row in old_cost_sheet["runs"]}
        old_cost_sheet["runs"] = [
            {**old_costs[protocol_id], "run_id": runs[protocol_id].run_id}
            for protocol_id in SAMPLE_PROTOCOLS
        ]

        stage = state_root.parent / (
            f".{state_root.name}.canonical-model-stage-{old_state_sha256[:12]}"
        )
        backup = state_root.parent / (
            f"{state_root.name}.pre-canonical-model-{old_state_sha256[:12]}"
        )
        if backup.exists() or stage.exists():
            raise RuntimeError("predecessor backup already exists; migration is one-time only")
        journal: dict[str, Any] = {
            "schema_version": 1,
            "kind": "openrouter-canonical-model-identity-transaction-v1",
            "release_root": str(release_root),
            "state_root": str(state_root),
            "stage": str(stage),
            "backup": str(backup),
            "predecessor_release_sha256": sha256(old_release_bytes).hexdigest(),
            "predecessor_state_sha256": old_state_sha256,
            "candidate_release_sha256": "",
            "candidate_state_sha256": "",
            "migration_audit_sha256": "",
        }
        _write_journal(journal_path, journal, "initializing", transition_observer)
        try:
            stage.mkdir()
            backup.mkdir()
            _fsync_directory(state_root.parent)
            _observe(transition_observer, "workspace:created")
            runs = _write_staged_state(
                stage,
                runs=runs,
                tasks=tasks,
                active_protocol=old_active.protocol_id,
                old_run=old_active,
                old_evidence=old_evidence,
                old_request=old_request,
                old_reservation=old_reservation,
                expected_cost_usd=expected_observed_cost_usd,
                audit=audit,
                cost_sheet=old_cost_sheet,
            )
            ledger = SpendLedger(
                stage / "spend-ledger.jsonl",
                global_cap=DEFAULT_GLOBAL_CAP_USD,
                cohort_caps={"sample": DEFAULT_GLOBAL_CAP_USD},
            )
            for protocol_id in SAMPLE_PROTOCOLS:
                reconciled = RunOrchestrator(
                    stage / runs[protocol_id].run_id,
                    InMemoryExecutor([]),
                    ledger=ledger,
                    evidence_projector=project_openrouter_evidence,
                ).reconcile(runs[protocol_id], tasks[protocol_id])
                runs[protocol_id] = reconciled
            if ledger.totals("sample") != (expected_observed_cost_usd, 0.0):
                raise RuntimeError("migrated ledger did not settle the exact retained cost")

            new_manifest = dict(old_manifest)
            new_manifest["repository_commit"] = current_commit
            new_manifest["authority"] = source_authority.to_dict()
            new_manifest["expected_run_ids"] = sorted(run.run_id for run in runs.values())
            approval = dict(new_manifest["paid_run_approval"])
            approval["run_ids"] = [run_id_map[run_id] for run_id in approval["run_ids"]]
            new_manifest["paid_run_approval"] = approval
            new_manifest["working_state_migrations"] = [
                *(old_manifest.get("working_state_migrations") or []),
                audit,
            ]
            candidate = ReleaseBundle(
                release_root,
                new_manifest,
                repository_root=repository_root,
                evidence_projector=project_provider_evidence,
                release_policy_validator=validate_sample_release,
            )
            candidate.validate()
            for run in runs.values():
                candidate.validate_planned_run(run)

            journal["candidate_state_sha256"] = _state_digest(stage)
            journal["candidate_release_sha256"] = sha256(_json_bytes(new_manifest)).hexdigest()
            _write_bytes_atomic(backup / "predecessor-release.json", old_release_bytes)
            _observe(transition_observer, "backup:release")
            write_json_atomic(backup / "migration-audit.json", audit)
            journal["migration_audit_sha256"] = _file_sha256(backup / "migration-audit.json")
            _observe(transition_observer, "backup:audit")
            _write_journal(journal_path, journal, "prepared", transition_observer)

            replace_path_durable(state_root, backup / "state")
            _observe(transition_observer, "rename:predecessor")
            _write_journal(journal_path, journal, "predecessor_moved", transition_observer)
            replace_path_durable(stage, state_root)
            _observe(transition_observer, "rename:candidate")
            _write_journal(journal_path, journal, "candidate_installed", transition_observer)
            write_json_atomic(release_root / "release.json", new_manifest)
            _observe(transition_observer, "release:installed")
            _write_journal(journal_path, journal, "release_installed", transition_observer)
            actual = ReleaseBundle.open(
                release_root,
                repository_root=repository_root,
                evidence_projector=project_provider_evidence,
                release_policy_validator=validate_sample_release,
            )
            actual.validate()
            for run in runs.values():
                actual.validate_planned_run(run)
            _write_journal(journal_path, journal, "committed", transition_observer)
            _validate_committed_migration_audit(
                journal=journal,
                release_root=release_root,
                state_root=state_root,
                backup=backup,
            )
            journal_path.unlink()
            _fsync_directory(journal_path.parent)
        except Exception as migration_error:
            try:
                recovered_phase = _recover_interrupted_migration(
                    journal_path=journal_path,
                    release_root=release_root,
                    state_root=state_root,
                )
            except Exception as rollback_error:
                raise ExceptionGroup(
                    "working-state migration and rollback both failed",
                    [migration_error, rollback_error],
                ) from migration_error
            if recovered_phase == "committed":
                raise RuntimeError(
                    "migration committed but final journal cleanup failed; committed state retained"
                ) from migration_error
            raise
        return {
            "release_id": SAMPLE_RELEASE_ID,
            "requested_model_id": expected_requested_model_id,
            "resolved_model_id": expected_resolved_model_id,
            "run_ids": [runs[protocol_id].run_id for protocol_id in SAMPLE_PROTOCOLS],
            "retained_attempts": 1,
            "remaining_attempts": 19,
            "observed_cost_usd": expected_observed_cost_usd,
            "predecessor_backup": str(backup),
        }
