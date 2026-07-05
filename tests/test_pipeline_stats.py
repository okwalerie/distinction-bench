"""Tests for the hand-rolled McNemar/bootstrap/sensitivity statistics.

No scipy/statsmodels: exact binomial McNemar via math.comb, and a seeded
stdlib random.Random bootstrap, per the plan's "prefer stdlib" constraint.
"""

from __future__ import annotations

import pandas as pd

from lofbench.pipeline import bootstrap_ci, compute_sensitivity, mcnemar_exact_p


def _row(
    model,
    reasoning_setting,
    dialect_id,
    form_id,
    correct,
    suite_version="pilot-v0",
    applied_injectors=None,
):
    return {
        "suite_version": suite_version,
        "model": model,
        "reasoning_setting": reasoning_setting,
        "dialect_id": dialect_id,
        "form_id": form_id,
        "form_string": form_id,  # 1:1 with form_id for these synthetic rows
        "correct": correct,
        "applied_injectors": applied_injectors,
    }


class TestMcNemarExact:
    def test_no_discordant_pairs_is_p_one(self):
        assert mcnemar_exact_p(0, 0) == 1.0

    def test_symmetric_discordance_is_p_one(self):
        # b == c: no evidence of directional difference. n=2 gives a raw
        # value > 1 before capping (2 * 0.75 = 1.5) -- the cap matters.
        assert mcnemar_exact_p(1, 1) == 1.0

    def test_hand_computed_asymmetric_case(self):
        # b=0, c=5: X ~ Binomial(5, 0.5). P(X<=0) = P(X>=5) = 1/32.
        # p = 2 * min(1/32, 1/32) = 1/16 = 0.0625.
        p = mcnemar_exact_p(0, 5)
        assert abs(p - 0.0625) < 1e-9

    def test_more_discordance_gives_smaller_p(self):
        assert mcnemar_exact_p(0, 10) < mcnemar_exact_p(3, 7) < mcnemar_exact_p(4, 6)


class TestBootstrapCi:
    def test_deterministic_given_seed(self):
        canon = [True, True, False, True, False]
        treat = [False, False, False, True, False]
        a = bootstrap_ci(canon, treat, seed=42)
        b = bootstrap_ci(canon, treat, seed=42)
        assert a == b

    def test_empty_input_is_nan(self):
        lo, hi = bootstrap_ci([], [], seed=1)
        assert lo != lo  # nan != nan
        assert hi != hi

    def test_constant_drop_has_zero_width_ci(self):
        # Every distinct form: canonical always correct, treatment always wrong.
        # Every bootstrap resample has drop == 1.0 regardless of which indices
        # are drawn, so the CI must collapse to a point.
        canon = [True] * 10
        treat = [False] * 10
        lo, hi = bootstrap_ci(canon, treat, seed=7)
        assert lo == hi == 1.0

    def test_ci_low_le_high(self):
        canon = [True, False, True, True, False, True, False]
        treat = [False, False, True, False, False, True, True]
        lo, hi = bootstrap_ci(canon, treat, seed=3)
        assert lo <= hi


class TestComputeSensitivity:
    def test_basic_pairing_and_accuracy(self):
        rows = []
        # 4 forms, canonical all correct, treatment 1 wrong out of 4.
        for i, treat_correct in enumerate([True, True, True, False]):
            rows.append(_row("modelA", "high", "canonical", f"f{i}", True))
            rows.append(_row("modelA", "high", "noisy-mismatch", f"f{i}", treat_correct))
        df = pd.DataFrame(rows)
        sens = compute_sensitivity(df)
        assert len(sens) == 1
        row = sens.iloc[0]
        assert row["model"] == "modelA"
        assert row["reasoning_setting"] == "high"
        assert row["dialect_id"] == "noisy-mismatch"
        assert row["n_paired"] == 4
        assert row["n_distinct_forms"] == 4
        assert row["accuracy_canonical"] == 1.0
        assert row["accuracy_treatment"] == 0.75
        assert abs(row["paired_drop"] - 0.25) < 1e-9
        assert row["mcnemar_b"] == 1  # canonical-correct, treatment-wrong
        assert row["mcnemar_c"] == 0
        assert row["coverage"] == 1.0  # no injector provenance -> always 1.0 pre-DB-4

    def test_duplicate_trivial_forms_reduce_n_distinct_but_not_n_paired(self):
        rows = [
            _row("modelA", "high", "canonical", "()", True),
            _row("modelA", "high", "canonical", "()", True),  # duplicate trivial form
            _row("modelA", "high", "canonical", "(())", False),
            _row("modelA", "high", "noisy-mismatch", "()", False),
            _row("modelA", "high", "noisy-mismatch", "()", False),  # duplicate trivial form
            _row("modelA", "high", "noisy-mismatch", "(())", True),
        ]
        df = pd.DataFrame(rows)
        sens = compute_sensitivity(df)
        row = sens.iloc[0]
        # raw join: 2 canonical "()" x 2 treatment "()" = 4, plus 1x1 for "(())" = 5
        assert row["n_paired"] == 5
        # after de-dup by form_string, only 2 distinct forms remain per arm
        assert row["n_distinct_forms"] == 2
        assert row["n_distinct_forms"] <= row["n_paired"]

    def test_reasoning_settings_are_not_mixed(self):
        """A treatment-only reasoning_setting with no matching canonical arm
        at that setting must not silently borrow another setting's canonical
        baseline (the corruption the plan review explicitly flagged)."""
        rows = [
            _row("modelA", "high", "canonical", "f1", True),
            _row("modelA", "high", "noisy-mismatch", "f1", False),
            # "low" has a treatment arm but no canonical arm at "low":
            _row("modelA", "low", "noisy-mismatch", "f1", False),
        ]
        df = pd.DataFrame(rows)
        sens = compute_sensitivity(df)
        assert len(sens) == 1
        assert sens.iloc[0]["reasoning_setting"] == "high"

    def test_no_canonical_arm_yields_no_row(self):
        rows = [_row("modelA", "high", "noisy-mismatch", "f1", False)]
        df = pd.DataFrame(rows)
        sens = compute_sensitivity(df)
        assert sens.empty

    def test_empty_items_returns_empty_frame(self):
        assert compute_sensitivity(pd.DataFrame()).empty
