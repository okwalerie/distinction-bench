from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from lofbench.metrics import compute_controlled_effects, compute_profiles
from lofbench.records import RunManifest
from lofbench.suites import load_suite


def _run(model: str, expected: tuple[str, ...]) -> RunManifest:
    return RunManifest.plan(
        suite_version="v1",
        form_set="probe",
        dialect_set="test",
        protocol_id="reduce-infer-v1",
        requested_model_id=model,
        resolved_model_id=model,
        execution_surface="direct_api",
        provider="test",
        endpoint="test",
        routing_policy={},
        privacy_policy={},
        sdk_version="test",
        reasoning={"effort": "default"},
        generation={"temperature": 0},
        billing_channel="test",
        cohort="test",
        max_transport_attempts=1,
        pricing={"prompt": 0.0, "completion": 0.0, "image": 0.0},
        authority={
            "suite_registry_sha256": "0" * 64,
            "protocol_registry_sha256": "0" * 64,
            "selected_form_set_sha256": "0" * 64,
            "selected_cells_sha256": "0" * 64,
            "selected_protocol_sha256": "0" * 64,
            "endpoint_catalog_sha256": "0" * 64,
            "execution_spec_sha256": "0" * 64,
        },
        expected_trial_ids=expected,
        status="admitted",
    )


def _rows(run: RunManifest, dialect_predictions: dict[str, list[tuple[str, str]]]):
    rows = []
    index = 0
    for dialect_id, predictions in dialect_predictions.items():
        for form_id, target_prediction in predictions:
            target, prediction = target_prediction.split("/")
            index += 1
            rows.append(
                {
                    "trial_id": f"trial_{index}",
                    "run_id": run.run_id,
                    "abstract_form_id": form_id,
                    "dialect_id": dialect_id,
                    "protocol_id": run.protocol_id,
                    "execution_surface": run.execution_surface,
                    "requested_model_id": run.requested_model_id,
                    "resolved_model_id": run.resolved_model_id,
                    "parse_status": "valid",
                    "prediction": prediction,
                    "correct": prediction == target,
                    "latency_ms": 1.0,
                    "input_tokens": 10,
                    "output_tokens": 1,
                    "reasoning_tokens": 0,
                    "observed_cost_usd": 0.001,
                }
            )
    return rows


def _with_expected(run: RunManifest, rows: list[dict]) -> RunManifest:
    return replace(run, expected_trial_ids=tuple(row["trial_id"] for row in rows))


def test_consistently_wrong_is_invariant_but_not_competent():
    suite = load_suite()
    run = _run("wrong", ())
    rows = _rows(
        run,
        {
            "parens.reference-v1": [("f1", "marked/unmarked"), ("f2", "unmarked/marked")],
            "parens.jitter-v1": [("f1", "marked/unmarked"), ("f2", "unmarked/marked")],
        },
    )
    run = _with_expected(run, rows)
    profile = compute_profiles(pd.DataFrame(rows), [run], suite).iloc[0]
    assert profile["competence"] == 0.0
    assert profile["within_family_invariance"] == 1.0


def test_perfect_model_has_perfect_competence_and_invariance():
    suite = load_suite()
    run = _run("perfect", ())
    rows = _rows(
        run,
        {
            "parens.reference-v1": [("f1", "marked/marked"), ("f2", "unmarked/unmarked")],
            "parens.jitter-v1": [("f1", "marked/marked"), ("f2", "unmarked/unmarked")],
        },
    )
    run = _with_expected(run, rows)
    profile = compute_profiles(pd.DataFrame(rows), [run], suite).iloc[0]
    assert profile["competence"] == 1.0
    assert profile["within_family_invariance"] == 1.0


def test_representation_sensitive_model_separates_competence_and_invariance():
    suite = load_suite()
    run = _run("sensitive", ())
    rows = _rows(
        run,
        {
            "parens.reference-v1": [("f1", "marked/marked"), ("f2", "unmarked/unmarked")],
            "parens.jitter-v1": [("f1", "marked/unmarked"), ("f2", "unmarked/marked")],
        },
    )
    run = _with_expected(run, rows)
    profile = compute_profiles(pd.DataFrame(rows), [run], suite).iloc[0]
    assert profile["competence"] == 0.5
    assert profile["within_family_invariance"] == 0.0


def test_family_macro_does_not_weight_families_by_dialect_count():
    suite = load_suite()
    run = _run("macro", ())
    rows = _rows(
        run,
        {
            "parens.reference-v1": [("f", "marked/marked")],
            "parens.jitter-v1": [("f", "marked/marked")],
            "parens.noisy-v1": [("f", "marked/marked")],
            "parens.noisy-mismatched-v1": [("f", "marked/marked")],
            "pattern.plain-v1": [("f", "marked/unmarked")],
        },
    )
    run = _with_expected(run, rows)
    profile = compute_profiles(pd.DataFrame(rows), [run], suite).iloc[0]
    assert profile["competence"] == 0.5


def test_invalid_is_incorrect_and_missing_is_not_zero_filled():
    suite = load_suite()
    run = _run("missing", ("trial_1", "trial_missing"))
    rows = _rows(run, {"parens.reference-v1": [("f", "marked/marked")]})
    rows[0]["parse_status"] = "invalid"
    rows[0]["prediction"] = ""
    rows[0]["correct"] = False
    profile = compute_profiles(pd.DataFrame(rows), [run], suite).iloc[0]
    assert profile["competence"] == 0.0
    assert profile["invalid_output_rate"] == 1.0
    assert profile["coverage"] == 0.5


def test_text_and_spatial_competence_remain_separate():
    suite = load_suite()
    run = _run("modal", ())
    rows = _rows(
        run,
        {
            "parens.reference-v1": [("f", "marked/marked")],
            "enclosure.plain-v1": [("f", "marked/unmarked")],
        },
    )
    run = _with_expected(run, rows)
    profile = compute_profiles(pd.DataFrame(rows), [run], suite).iloc[0]
    assert profile["text_competence"] == 1.0
    assert profile["spatial_competence"] == 0.0


def test_controlled_effect_can_help_and_reports_exact_pairing():
    suite = load_suite()
    run = _run("helped", ())
    rows = _rows(
        run,
        {
            "parens.reference-v1": [("f1", "marked/unmarked"), ("f2", "marked/marked")],
            "parens.jitter-v1": [("f1", "marked/marked"), ("f2", "marked/marked")],
        },
    )
    run = _with_expected(run, rows)
    effect = compute_controlled_effects(pd.DataFrame(rows), [run], suite).iloc[0]
    assert effect["signed_paired_effect"] == 0.5
    assert effect["mcnemar_b"] == 0
    assert effect["mcnemar_c"] == 1
    assert effect["coverage"] == 1.0


def test_non_admitted_run_is_rejected():
    suite = load_suite()
    run = replace(_run("pilot", ("trial_1",)), status="complete")
    rows = _rows(run, {"parens.reference-v1": [("f", "marked/marked")]})
    with pytest.raises(RuntimeError, match="non-admitted"):
        compute_profiles(pd.DataFrame(rows), [run], suite)
