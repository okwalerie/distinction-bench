"""Application-owned validation for the frozen public sample release."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow.parquet as pq

from dbench.model_identity_audit import MODEL_IDENTITY_AUDIT_FIELDS
from dbench.provider_evidence import validate_openrouter_run_policy
from lofbench.authority import canonical_sha256
from lofbench.protocols import load_protocol_registry
from lofbench.release_bundle import (
    PublicationView,
    ReleasePolicyContext,
    ReleaseReissueProvenance,
)

_LEGACY_SITE_FILES = {
    "index.html",
    "forms.html",
    "atlas.html",
    "runs.html",
    "human.html",
    "downloads.html",
    "CNAME",
    "downloads/human-trial.schema.json",
    "downloads/request-started.jsonl",
}
_LEGACY_SITE_MARKERS = {
    "runs.html": (
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
        "input/output/reasoning tokens",
    ),
    "human.html": (
        "HumanTrialRecord",
        "familiarity_band",
        "elapsed_ms",
        "human-trial.schema.json",
    ),
    "forms.html": ("system prompt", "possible confounds", "frozen form sets"),
    "downloads.html": (
        "sha256",
        "caveats",
        "human-trial.schema.json",
        "request-started.jsonl",
        "full sealed distribution",
        "release.json",
        ".tar.gz",
    ),
}
_LEGACY_PUBLICATION_GAPS = {
    ("file", "downloads/request-started.jsonl"),
    ("downloads.html", "request-started.jsonl"),
    ("downloads.html", "full sealed distribution"),
    ("downloads.html", "release.json"),
    ("downloads.html", ".tar.gz"),
}
_SAMPLE_REISSUE_SOURCE_CONTRACT = {
    "source_release_id": "v1.0.0-sample.1",
    "source_repository_commit": "aa542e748000c05ab5dcf4f2cc48e02e3d976433",
    "source_release_manifest_sha256": (
        "26db305cc0df104ddd12bd9118c426263b062478bac8a33f054605a44e8b5333"
    ),
    "source_bundle_sha256": "a4b2fe1c97d6ff208006ba73f4a69ef929976a33b35984435187adf898473258",
    "source_archive_sha256": "2b44f288eb73c9e88220f912b320243fbb5bfeac574ac7dbee45ba27ef6b4cb4",
    "source_archive_bytes": 128_604_522,
    "source_file_count": 7_298,
    "source_tree_entries": 7_327,
    "migration_audit_sha256": "aefb04b663fc93fa8d55a6db0f99af377e8b80288d532a284a810e12ba844974",
}
_SAMPLE_REISSUE_RUN_IDS = frozenset(
    {
        "run_b774a85c7ef012f6764e24ae",
        "run_e1d20bb0df788eb70f646cab",
        "run_f6dcb089075447af8c9b5fed",
        "run_541eca2746213cfa3e84e85e",
    }
)


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(child) for child in value]
    return value


def _known_reissue_identity(context: ReleasePolicyContext) -> bool:
    run_ids = {run.run_id for run in context.runs}
    expected = context.manifest.get("expected_run_ids")
    expected_ids = set(expected) if isinstance(expected, tuple) else set()
    return context.manifest.get("release_id") == _SAMPLE_REISSUE_SOURCE_CONTRACT[
        "source_release_id"
    ] and (run_ids == _SAMPLE_REISSUE_RUN_IDS or expected_ids == _SAMPLE_REISSUE_RUN_IDS)


def _validate_reissued_sample(context: ReleasePolicyContext, *, required: bool) -> None:
    origin = context.manifest.get("origin")
    if not required and (not isinstance(origin, Mapping) or origin.get("kind") != "reissue"):
        return
    if not isinstance(origin, Mapping) or origin.get("kind") != "reissue":
        raise RuntimeError("known sample lineage must preserve its reissue origin")
    provenance_value = context.manifest.get("reissued_from")
    migrations = context.manifest.get("working_state_migrations")
    if not isinstance(provenance_value, Mapping) or not isinstance(migrations, Sequence):
        raise RuntimeError("reissued sample lacks typed provenance or migration audit")
    provenance = ReleaseReissueProvenance.from_dict(provenance_value)
    if len(migrations) != 1 or not isinstance(migrations[0], Mapping):
        raise RuntimeError("reissued sample must preserve one exact migration audit")
    audit = migrations[0]
    runs = list(context.runs)
    trials = list(context.trials)
    calls = list(context.calls)
    run_ids = {run.run_id for run in runs} or set(context.manifest["expected_run_ids"])
    trial_ids = {row["trial_id"] for row in trials}
    calls_by_id = {row["call_id"]: row for row in calls}
    active = audit.get("active_attempt")
    provenance_contract = {
        field: getattr(provenance, field)
        for field in _SAMPLE_REISSUE_SOURCE_CONTRACT
        if field != "migration_audit_sha256"
    }
    if (
        provenance_contract
        != {
            field: value
            for field, value in _SAMPLE_REISSUE_SOURCE_CONTRACT.items()
            if field != "migration_audit_sha256"
        }
        or canonical_sha256(_plain_json(audit))
        != _SAMPLE_REISSUE_SOURCE_CONTRACT["migration_audit_sha256"]
        or set(audit) != MODEL_IDENTITY_AUDIT_FIELDS
        or audit.get("schema_version") != 1
        or audit.get("kind") != "openrouter-canonical-model-identity-v1"
        or audit.get("attempts_retained") != 1
        or audit.get("remaining_attempts") != 19
        or not isinstance(audit.get("run_id_map"), Mapping)
        or set(audit["run_id_map"].values()) != run_ids
        or not isinstance(audit.get("trial_id_map"), Mapping)
        or (trial_ids and set(audit["trial_id_map"].values()) != trial_ids)
        or provenance.source_release_id != context.manifest["release_id"]
        or provenance.source_repository_commit != audit.get("repository_commit")
        or not isinstance(active, Mapping)
        or active.get("new_run_id") not in run_ids
        or (trial_ids and active.get("new_trial_id") not in trial_ids)
    ):
        raise RuntimeError("reissued sample migration provenance is inconsistent")
    if runs and any(
        run.requested_model_id != audit.get("requested_model_id")
        or run.resolved_model_id != audit.get("resolved_model_id")
        or run.endpoint != audit.get("endpoint")
        or run.catalog_row["selected_endpoint"]["provider_name"] != audit.get("provider")
        or canonical_sha256(run.catalog_row) != audit.get("authenticated_catalog_sha256")
        for run in runs
    ):
        raise RuntimeError("reissued sample identity differs from its migration audit")
    if calls:
        call = calls_by_id.get(active.get("new_call_id"))
        if (
            call is None
            or call["run_id"] != active.get("new_run_id")
            or call["trial_id"] != active.get("new_trial_id")
            or canonical_sha256(dict(call)) != active.get("new_call_record_sha256")
            or call["observed_cost_usd"] != active.get("observed_cost_usd")
        ):
            raise RuntimeError("reissued sample active attempt differs from its migration audit")


def _validate_sample_core(context: ReleasePolicyContext) -> None:
    contract = context.manifest.get("sample_contract")
    if not contract:
        return
    if context.manifest["release_id"] != "v1.0.0-sample.1":
        raise RuntimeError("sample contract is attached to the wrong release id")
    expected_protocols = set(contract["protocol_ids"])
    if (
        expected_protocols != set(context.manifest["protocol_ids"])
        or contract["form_set"] != "probe"
        or contract["dialect_id"] != "enclosure.plain-v1"
        or contract["execution_surface"] != "direct_api"
        or contract["max_transport_attempts"] != 1
        or contract["trials_per_run"] != 5
        or contract["total_attempts"] != 20
    ):
        raise RuntimeError("sample contract does not declare the frozen 4 x 5 invariant")
    runs = context.runs
    if len(runs) > len(expected_protocols):
        raise RuntimeError("sample bundle contains too many protocol runs")
    if runs:
        if not {run.protocol_id for run in runs} <= expected_protocols or len(
            {run.protocol_id for run in runs}
        ) != len(runs):
            raise RuntimeError("sample protocols do not match the contract")
        if any(
            run.form_set != contract["form_set"]
            or len(run.expected_trial_ids) != contract["trials_per_run"]
            or run.max_transport_attempts != contract["max_transport_attempts"]
            or run.execution_surface != contract["execution_surface"]
            or run.dialect_id != contract["dialect_id"]
            for run in runs
        ):
            raise RuntimeError("sample run shape does not match the contract")
        for field in (
            "dialect_id",
            "requested_model_id",
            "resolved_model_id",
            "endpoint",
            "provider",
        ):
            if len({getattr(run, field) for run in runs}) != 1:
                raise RuntimeError(f"sample runs do not share one {field}")
        for run in runs:
            validate_openrouter_run_policy(run.to_dict())
    admitted = [run for run in runs if run.status == "admitted"]
    if admitted:
        expected_forms = set(context.suite.form_sets["probe"])
        forms_by_run = {
            run.run_id: {
                row["abstract_form_id"] for row in context.trials if row["run_id"] == run.run_id
            }
            for run in admitted
        }
        if len({tuple(sorted(ids)) for ids in forms_by_run.values()}) != 1:
            raise RuntimeError("sample runs do not use the same frozen probe forms")
        if any(ids != expected_forms for ids in forms_by_run.values()):
            raise RuntimeError("sample runs do not use the exact frozen probe form set")
    if context.manifest["status"] != "sealed" and not context.sealing:
        return
    if len(admitted) != len(expected_protocols):
        raise RuntimeError("sealed sample requires four admitted runs")
    if len(context.trials) != contract["total_attempts"]:
        raise RuntimeError("sealed sample does not contain exactly twenty trials")
    if len(context.calls) != contract["total_attempts"]:
        raise RuntimeError("sealed sample does not contain exactly twenty attempts")
    approval = context.manifest.get("paid_run_approval") or {}
    spend_caps = context.manifest.get("spend_caps_usd") or {}
    if (
        approval.get("release_id") != context.manifest["release_id"]
        or approval.get("max_spend_usd") != 30.0
        or approval.get("paid_calls") != contract["total_attempts"]
        or set(approval.get("run_ids", ())) != {run.run_id for run in admitted}
        or approval.get("model_id") != admitted[0].requested_model_id
        or approval.get("endpoint") != admitted[0].endpoint
        or approval.get("provider") != admitted[0].catalog_row["selected_endpoint"]["provider_name"]
        or not approval.get("approved_by")
        or not approval.get("scope")
        or spend_caps.get("global") != 30.0
        or spend_caps.get("cohorts", {}).get("sample") != 30.0
    ):
        raise RuntimeError("sealed sample lacks exact paid approval or spend caps")
    profiles = pq.read_table(context.root / "profiles.parquet").to_pylist()
    if (
        len(profiles) != len(expected_protocols)
        or {row["protocol_id"] for row in profiles} != expected_protocols
        or any(row["coverage"] != 1.0 for row in profiles)
    ):
        raise RuntimeError("sealed sample requires complete derived profiles")


def _legacy_site_gaps(context: ReleasePolicyContext) -> set[tuple[str, str]]:
    site = context.root / "site"
    gaps = {
        ("file", relative) for relative in _LEGACY_SITE_FILES if not (site / relative).is_file()
    }
    for relative, markers in _LEGACY_SITE_MARKERS.items():
        path = site / relative
        if not path.is_file():
            continue
        page = path.read_text()
        gaps.update((relative, marker) for marker in markers if marker not in page)
    return gaps


def _validate_public_site(context: ReleasePolicyContext) -> None:
    from lofsite.build import verify_public_site

    verify_public_site(
        PublicationView(
            root=context.root,
            release_id=str(context.manifest["release_id"]),
            repository_url=str(context.manifest["repository_url"]),
            status=str(context.manifest["status"]),
            suite_version=str(context.manifest["suite_version"]),
            stimuli_materialized=bool(context.manifest.get("stimuli_materialized")),
            expected_run_ids=tuple(context.manifest["expected_run_ids"]),
            sample_contract=context.manifest.get("sample_contract"),
            paid_run_approval=context.manifest.get("paid_run_approval"),
            suite=context.suite,
            protocols=load_protocol_registry(context.root / "protocols.json"),
            runs=context.runs,
            trials=context.trials,
            profiles=tuple(pq.read_table(context.root / "profiles.parquet").to_pylist()),
            effects=tuple(pq.read_table(context.root / "effects.parquet").to_pylist()),
        ),
        context.root / "site",
    )


def validate_sample_release(context: ReleasePolicyContext) -> None:
    """Validate the current sample evidence and publication contract."""
    known_reissue = _known_reissue_identity(context)
    if not context.manifest.get("sample_contract"):
        if known_reissue:
            raise RuntimeError("known sample lineage cannot remove its sample contract")
        return
    _validate_sample_core(context)
    _validate_reissued_sample(context, required=known_reissue)
    if context.manifest["status"] != "sealed" and not context.sealing:
        return
    _validate_public_site(context)


def validate_legacy_sample_reissue_source(context: ReleasePolicyContext) -> None:
    """Accept only the one sealed sample predating the portable-download policy.

    This is deliberately a source-authentication policy for reissue, not a switch
    on ordinary validation. Every evidence, accounting, approval, metrics, and
    sealed-checksum rule remains current; only the enumerated publication gaps
    are admitted.
    """
    if not context.manifest.get("sample_contract"):
        raise RuntimeError("legacy reissue source lacks the frozen sample contract")
    _validate_sample_core(context)
    if context.manifest["status"] != "sealed" or context.sealing:
        raise RuntimeError("legacy reissue source must be an already sealed sample")
    gaps = _legacy_site_gaps(context)
    if gaps != _LEGACY_PUBLICATION_GAPS:
        raise RuntimeError(f"legacy reissue source has the wrong stale shape: {sorted(gaps)}")
