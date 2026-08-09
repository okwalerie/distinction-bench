"""No-inference reissue of one authenticated sealed sample distribution."""

from __future__ import annotations

import json
import math
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow.parquet as pq

from dbench.migration import (
    IDENTITY_EVENT_ACTOR,
    IDENTITY_EVENT_BODY_SHA256,
    IDENTITY_EVENT_ID,
    IDENTITY_EVENT_TYPE,
    _require_identity_event,
)
from dbench.provider_evidence import project_provider_evidence
from dbench.release_policy import (
    validate_legacy_sample_reissue_source,
    validate_sample_release,
)
from lofbench.accounting import SpendLedger
from lofbench.authority import AuthorityManifest, canonical_sha256, verify_authority_copies
from lofbench.metrics import write_release_metrics
from lofbench.records import (
    AttemptEvidence,
    CallRecord,
    LedgerEvent,
    RequestStartedRecord,
    RunManifest,
    TrialRecord,
)
from lofbench.release_bundle import ReleaseBundle, ReleaseReissueProvenance
from lofbench.run_models import trial_id_for
from lofbench.state_io import read_jsonl, replace_path_durable, state_lifecycle_lock
from lofbench.suites import load_suite

_MIGRATION_KIND = "openrouter-canonical-model-identity-v1"
_REISSUE_KIND = "sealed-release-reissue-v1"
_EXPECTED_ATTEMPTS = 20
_EXPECTED_OBSERVED_USD = 0.069506875
_MIGRATION_FIELDS = {
    "schema_version",
    "kind",
    "identity_event_id",
    "identity_event_authority",
    "migrated_at",
    "predecessor_repository_commit",
    "repository_commit",
    "predecessor_authority",
    "authority",
    "predecessor_authority_sha256",
    "authority_sha256",
    "predecessor_release_manifest_sha256",
    "predecessor_state_sha256",
    "predecessor_run_manifest_sha256",
    "authenticated_catalog_sha256",
    "predecessor_call_record_sha256",
    "requested_model_id",
    "resolved_model_id",
    "endpoint",
    "provider",
    "run_id_map",
    "trial_id_map",
    "attempts_retained",
    "remaining_attempts",
}


@dataclass(frozen=True)
class ReissuedRelease:
    target_root: Path
    release_id: str
    run_ids: tuple[str, ...]
    attempts: int
    observed_cost_usd: float
    source_release_manifest_sha256: str
    source_archive_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_root": str(self.target_root),
            "release_id": self.release_id,
            "run_ids": list(self.run_ids),
            "attempts": self.attempts,
            "observed_cost_usd": self.observed_cost_usd,
            "source_release_manifest_sha256": self.source_release_manifest_sha256,
            "source_archive_sha256": self.source_archive_sha256,
        }


def _digest(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"sha256": sha256(payload).hexdigest(), "bytes": len(payload)}


def _require_digest(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RuntimeError(f"{label} is not a sha256 digest")
    return value


def _verified_archive(source_root: Path, archive_path: Path) -> tuple[str, int]:
    """Prove a normalized external archive is byte-for-byte the sealed directory."""
    if not archive_path.is_file() or archive_path.is_symlink():
        raise RuntimeError("source archive is not one regular file")
    try:
        if archive_path.resolve().is_relative_to(source_root.resolve()):
            raise RuntimeError("source archive must be external to the sealed bundle")
    except OSError as exc:
        raise RuntimeError("source archive path cannot be resolved") from exc
    expected = {path.relative_to(source_root).as_posix(): path for path in source_root.rglob("*")}
    seen: set[str] = set()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            name = member.name.removeprefix("./").rstrip("/")
            pure = PurePosixPath(name)
            if (
                not name
                or pure.is_absolute()
                or ".." in pure.parts
                or name in seen
                or name not in expected
            ):
                raise RuntimeError("source archive member set is unsafe or inconsistent")
            seen.add(name)
            source = expected[name]
            if (
                member.uid != 0
                or member.gid != 0
                or member.uname
                or member.gname
                or member.mtime != 0
            ):
                raise RuntimeError("source archive metadata is not normalized")
            if source.is_dir() and not source.is_symlink():
                if not member.isdir():
                    raise RuntimeError("source archive directory type is inconsistent")
            elif source.is_file() and not source.is_symlink():
                if not member.isfile() or member.size != source.stat().st_size:
                    raise RuntimeError("source archive file type or size is inconsistent")
                extracted = archive.extractfile(member)
                if (
                    extracted is None
                    or sha256(extracted.read()).hexdigest() != _digest(source)["sha256"]
                ):
                    raise RuntimeError("source archive payload differs from the sealed bundle")
            else:
                raise RuntimeError("sealed source contains an unsupported filesystem entry")
    if seen != set(expected):
        raise RuntimeError("source archive does not contain the exact sealed bundle tree")
    payload = archive_path.read_bytes()
    return sha256(payload).hexdigest(), len(payload)


def _source_bundle_digest(source_root: Path) -> tuple[str, int]:
    files = {
        path.relative_to(source_root).as_posix(): _digest(path)
        for path in sorted(source_root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    return canonical_sha256(files), len(files)


def _tree_digest(root: Path) -> str:
    return canonical_sha256(
        {
            path.relative_to(root).as_posix(): _digest(path)
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }
    )


def _verified_migration_audit(
    source: ReleaseBundle,
    *,
    repository_root: Path,
) -> dict[str, Any]:
    migrations = source.manifest.get("working_state_migrations")
    if (
        not isinstance(migrations, list)
        or len(migrations) != 1
        or not isinstance(migrations[0], dict)
    ):
        raise RuntimeError("sealed reissue source must preserve one complete migration audit")
    audit = migrations[0]
    if set(audit) != _MIGRATION_FIELDS:
        raise RuntimeError("sealed reissue migration audit schema is invalid")
    event_authority = {
        "actor": IDENTITY_EVENT_ACTOR,
        "body_sha256": IDENTITY_EVENT_BODY_SHA256,
        "type": IDENTITY_EVENT_TYPE,
    }
    if (
        audit["schema_version"] != 1
        or audit["kind"] != _MIGRATION_KIND
        or audit["identity_event_id"] != IDENTITY_EVENT_ID
        or audit["identity_event_authority"] != event_authority
        or audit["repository_commit"] != source.manifest["repository_commit"]
        or audit["authority"] != source.manifest["authority"]
        or audit["attempts_retained"] != 1
        or audit["remaining_attempts"] != 19
    ):
        raise RuntimeError("sealed reissue migration audit authority is inconsistent")
    try:
        timestamp = datetime.fromisoformat(audit["migrated_at"])
        predecessor = AuthorityManifest.from_dict(audit["predecessor_authority"])
        candidate = AuthorityManifest.from_dict(audit["authority"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("sealed reissue migration audit values are invalid") from exc
    if timestamp.tzinfo is None:
        raise RuntimeError("sealed reissue migration timestamp is invalid")
    suite_bytes = (source.root / "suite.json").read_bytes()
    protocol_bytes = (source.root / "protocols.json").read_bytes()
    verify_authority_copies(
        predecessor,
        suite_bytes=suite_bytes,
        protocol_bytes=protocol_bytes,
        repository_root=repository_root,
    )
    verify_authority_copies(
        candidate,
        suite_bytes=suite_bytes,
        protocol_bytes=protocol_bytes,
        repository_root=repository_root,
    )
    if (
        predecessor.source_commit != audit["predecessor_repository_commit"]
        or candidate.source_commit != audit["repository_commit"]
        or canonical_sha256(predecessor.to_dict()) != audit["predecessor_authority_sha256"]
        or canonical_sha256(candidate.to_dict()) != audit["authority_sha256"]
    ):
        raise RuntimeError("sealed reissue migration authority digests are inconsistent")
    _require_identity_event(
        repository_root,
        commit=candidate.source_commit,
        identity_event_id=audit["identity_event_id"],
    )
    for field in (
        "predecessor_release_manifest_sha256",
        "predecessor_state_sha256",
        "authenticated_catalog_sha256",
    ):
        _require_digest(audit[field], f"migration audit {field}")

    runs = source.runs()
    run_id_map = audit["run_id_map"]
    if (
        len(runs) != 4
        or not isinstance(run_id_map, dict)
        or len(run_id_map) != 4
        or set(run_id_map.values()) != {run.run_id for run in runs}
        or any(not isinstance(value, str) or not value.startswith("run_") for value in run_id_map)
    ):
        raise RuntimeError("sealed reissue migration run map is invalid")
    predecessor_hashes = audit["predecessor_run_manifest_sha256"]
    if not isinstance(predecessor_hashes, dict) or set(predecessor_hashes) != set(run_id_map):
        raise RuntimeError("sealed reissue predecessor-run hashes are incomplete")
    for digest in predecessor_hashes.values():
        _require_digest(digest, "migration predecessor run hash")
    call_hashes = audit["predecessor_call_record_sha256"]
    if not isinstance(call_hashes, list) or len(call_hashes) != 2:
        raise RuntimeError("sealed reissue predecessor-call hashes are incomplete")
    for digest in call_hashes:
        _require_digest(digest, "migration predecessor call hash")

    by_new_run = {new: old for old, new in run_id_map.items()}
    trials = pq.read_table(source.root / "trials.parquet").to_pylist()
    expected_trial_map = {
        trial_id_for(by_new_run[row["run_id"]], row["abstract_form_id"], row["dialect_id"]): row[
            "trial_id"
        ]
        for row in trials
    }
    if audit["trial_id_map"] != expected_trial_map:
        raise RuntimeError("sealed reissue migration trial map is not authoritative")
    identity = {
        "requested_model_id": audit["requested_model_id"],
        "resolved_model_id": audit["resolved_model_id"],
        "endpoint": audit["endpoint"],
        "provider": audit["provider"],
    }
    if any(
        any(getattr(run, field) != value for field, value in identity.items())
        or canonical_sha256(run.catalog_row) != audit["authenticated_catalog_sha256"]
        for run in runs
    ):
        raise RuntimeError("sealed reissue migration identity differs from admitted runs")
    return json.loads(json.dumps(audit))


def _source_records(source: ReleaseBundle) -> tuple[list[dict[str, Any]], ...]:
    return (
        pq.read_table(source.root / "trials.parquet").to_pylist(),
        pq.read_table(source.root / "calls.parquet").to_pylist(),
        read_jsonl(source.root / "request-started.jsonl"),
        read_jsonl(source.root / "ledger.jsonl"),
        read_jsonl(source.root / "transcripts.jsonl"),
    )


def _readmit_complete_state(
    target: ReleaseBundle,
    source: ReleaseBundle,
    *,
    state_root: Path,
) -> tuple[int, float]:
    source_trials, source_calls, source_requests, source_ledger, source_evidence = _source_records(
        source
    )
    source_runs = source.runs()
    expected_ids = {run.run_id for run in source_runs}
    state_run_dirs = {
        path.name for path in state_root.iterdir() if path.is_dir() and path.name.startswith("run_")
    }
    if state_run_dirs != expected_ids:
        raise RuntimeError("complete state directories do not equal the sealed run set")
    attempts = 0
    costs: list[float] = []
    for admitted in source_runs:
        state_dir = state_root / admitted.run_id
        state_run = RunManifest.from_dict(json.loads((state_dir / "run.json").read_text()))
        if state_run != replace(admitted, status="complete"):
            raise RuntimeError("complete state run differs from its sealed admitted manifest")
        trials = read_jsonl(state_dir / "trials.jsonl")
        calls_by_id = {row["call_id"]: row for row in read_jsonl(state_dir / "calls.jsonl")}
        calls = list(calls_by_id.values())
        requests = read_jsonl(state_dir / "request-started.jsonl")
        evidence = read_jsonl(state_dir / "transcripts.jsonl")
        ledger = [
            row
            for row in read_jsonl(state_root / "spend-ledger.jsonl")
            if row["run_id"] == admitted.run_id
        ]
        expected_trials = [row for row in source_trials if row["run_id"] == admitted.run_id]
        expected_calls = [row for row in source_calls if row["run_id"] == admitted.run_id]
        expected_requests = [row for row in source_requests if row["run_id"] == admitted.run_id]
        expected_evidence = [row for row in source_evidence if row["run_id"] == admitted.run_id]
        expected_ledger = [row for row in source_ledger if row["run_id"] == admitted.run_id]
        if (
            trials != expected_trials
            or calls != expected_calls
            or requests != expected_requests
            or evidence != expected_evidence
            or ledger != expected_ledger
        ):
            raise RuntimeError("complete state evidence differs from the sealed release core")
        typed_calls = [CallRecord(**row) for row in calls]
        target.admit_run(
            state_run,
            [TrialRecord(**row) for row in trials],
            calls=typed_calls,
            request_starts=[RequestStartedRecord.from_dict(row) for row in requests],
            ledger_events=[LedgerEvent.from_dict(row) for row in ledger],
            evidence=[AttemptEvidence.from_dict(row) for row in evidence],
        )
        attempts += len(typed_calls)
        costs.extend(call.observed_cost_usd for call in typed_calls)
    observed = math.fsum(costs)
    ledger_events = [
        LedgerEvent.from_dict(row) for row in read_jsonl(state_root / "spend-ledger.jsonl")
    ]
    ledger_observed, ledger_reserved = SpendLedger._totals(ledger_events, "sample")
    if (
        attempts != _EXPECTED_ATTEMPTS
        or not math.isclose(observed, _EXPECTED_OBSERVED_USD, rel_tol=0.0, abs_tol=1e-15)
        or not math.isclose(ledger_observed, observed, rel_tol=0.0, abs_tol=1e-15)
        or ledger_reserved != 0.0
    ):
        raise RuntimeError("reissued sample does not preserve exact attempt and spend closure")
    return attempts, observed


def reissue_sealed_release(
    *,
    source_root: Path,
    source_archive: Path,
    target_root: Path,
    state_root: Path,
    repository_root: Path,
) -> ReissuedRelease:
    """Authenticate and re-admit a stale sealed sample without any execution path."""
    if source_root.is_symlink() or source_archive.is_symlink():
        raise RuntimeError("reissue source directory and archive cannot be symbolic links")
    requested_target = target_root
    source_root = source_root.resolve()
    source_archive = source_archive.resolve()
    target_root = target_root.resolve(strict=False)
    state_root = state_root.resolve()
    repository_root = repository_root.resolve()
    if source_root == target_root or source_root.parent != target_root.parent:
        raise RuntimeError("reissue target must be a distinct sibling of the sealed source")
    if requested_target.exists() or requested_target.is_symlink() or target_root.exists():
        raise FileExistsError(f"reissue target must be absent: {target_root}")
    if not source_root.is_dir() or source_root.is_symlink():
        raise RuntimeError("reissue source must be one regular sealed directory")

    with state_lifecycle_lock(state_root, blocking=False):
        source = ReleaseBundle.open(
            source_root,
            repository_root=repository_root,
            evidence_projector=project_provider_evidence,
            release_policy_validator=validate_legacy_sample_reissue_source,
        )
        if source.manifest.get("reissued_from") is not None:
            raise RuntimeError("legacy sealed source has already been reissued")
        try:
            source.validate()
        except Exception as exc:
            raise RuntimeError("sealed reissue source core is invalid") from exc
        audit = _verified_migration_audit(source, repository_root=repository_root)
        archive_sha256, archive_bytes = _verified_archive(source_root, source_archive)
        bundle_sha256, file_count = _source_bundle_digest(source_root)
        release_sha256 = sha256((source_root / "release.json").read_bytes()).hexdigest()
        provenance = ReleaseReissueProvenance(
            schema_version=1,
            kind=_REISSUE_KIND,
            source_release_id=source.manifest["release_id"],
            source_repository_commit=source.manifest["repository_commit"],
            source_release_manifest_sha256=release_sha256,
            source_bundle_sha256=bundle_sha256,
            source_archive_sha256=archive_sha256,
            source_archive_bytes=archive_bytes,
            source_file_count=file_count,
            reissued_at=datetime.now(UTC).isoformat(),
        )
        stage = Path(
            tempfile.mkdtemp(prefix=f".{target_root.name}.reissue-", dir=target_root.parent)
        )
        try:
            target = ReleaseBundle.create_working(
                stage,
                release_id=source.manifest["release_id"],
                repository_url=source.manifest["repository_url"],
                repository_root=repository_root,
                suite_path=source_root / "suite.json",
                expected_run_ids=source.manifest["expected_run_ids"],
                paid_run_approval=source.manifest["paid_run_approval"],
                materialize_stimuli=bool(source.manifest["stimuli_materialized"]),
                sample_contract=source.manifest["sample_contract"],
                spend_caps_usd=source.manifest["spend_caps_usd"],
                evidence_projector=project_provider_evidence,
                release_policy_validator=validate_sample_release,
                reissued_from=provenance,
                working_state_migrations=[audit],
            )
            if source.manifest["stimuli_materialized"] and _tree_digest(
                target.root / "stimuli"
            ) != _tree_digest(source.root / "stimuli"):
                raise RuntimeError("reissued stimuli differ from the sealed source")
            attempts, observed = _readmit_complete_state(target, source, state_root=state_root)
            write_release_metrics(
                target.root,
                suite=load_suite(
                    version=target.manifest["suite_version"], path=target.root / "suite.json"
                ),
                runs=target.runs(),
            )
            target.validate()
            if (
                _source_bundle_digest(source_root) != (bundle_sha256, file_count)
                or sha256((source_root / "release.json").read_bytes()).hexdigest() != release_sha256
                or sha256(source_archive.read_bytes()).hexdigest() != archive_sha256
            ):
                raise RuntimeError("sealed source or archive changed during reissue")
            if target_root.exists():
                raise FileExistsError(f"reissue target appeared during validation: {target_root}")
            replace_path_durable(stage, target_root)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        return ReissuedRelease(
            target_root=target_root,
            release_id=source.manifest["release_id"],
            run_ids=tuple(source.manifest["expected_run_ids"]),
            attempts=attempts,
            observed_cost_usd=observed,
            source_release_manifest_sha256=release_sha256,
            source_archive_sha256=archive_sha256,
        )
