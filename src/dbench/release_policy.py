"""Application-owned validation for the frozen public sample release."""

from __future__ import annotations

import pyarrow.parquet as pq

from dbench.provider_evidence import validate_openrouter_run_policy
from lofbench.release_bundle import ReleasePolicyContext


def validate_sample_release(context: ReleasePolicyContext) -> None:
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
    site = context.root / "site"
    required_files = {
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
    missing = [relative for relative in required_files if not (site / relative).is_file()]
    if missing:
        raise RuntimeError(f"sealed sample site is missing required artifacts: {missing}")
    required_markers = {
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
    for relative, markers in required_markers.items():
        page = (site / relative).read_text()
        absent = [marker for marker in markers if marker not in page]
        if absent:
            raise RuntimeError(f"sealed sample site {relative} lacks required evidence: {absent}")
