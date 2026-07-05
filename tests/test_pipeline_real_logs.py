"""Integration test against the full real logs/ directory.

``logs/`` is local working data -- it is excluded from git via
``.git/info/exclude`` (confirmed: ``git ls-files logs`` is empty), so it is
not present in a fresh clone or CI. This test is a local-only sanity check,
skipped gracefully rather than failing, whenever ``logs/`` is absent. The
fast, always-on fixture-based reconciliation lives in
``test_pipeline_extract.py``.

The pipeline runs ONCE per test module (module-scoped fixture) because a
full pass over the 47 processable logs takes minutes, not seconds.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from lofbench.pipeline import run_pipeline

LOG_DIR = Path(__file__).parent.parent / "logs"

pytestmark = pytest.mark.skipif(
    not LOG_DIR.exists(), reason="logs/ is local working data, not checked into git"
)


@pytest.fixture(scope="module")
def pipeline_run(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("real-log-artifacts")
    summary = run_pipeline(str(LOG_DIR), str(out_dir), suite_version="pilot-v0", max_log_mb=400.0)
    return summary, out_dir


def test_full_real_log_run_reconciles(pipeline_run):
    summary, out_dir = pipeline_run

    assert summary.logs_seen == 48
    # One log (~614MB cancelled Opus run) exceeds the default 400MB safety
    # valve and is skipped -- reported, not silently absent.
    assert len(summary.logs_skipped) == 1
    assert summary.logs_processed == 47

    items_df = pd.read_parquet(out_dir / "pilot-v0" / "items.parquet")
    calls_df = pd.read_parquet(out_dir / "pilot-v0" / "calls.parquet")
    assert len(items_df) == summary.items_written
    assert len(calls_df) == summary.calls_written
    assert summary.items_written > 0
    assert summary.calls_written > 0

    # Items reconcile with calls: every composite call explodes to exactly
    # the rows its metadata['targets'] defines (== group_size), every single
    # call to one row. Cross-check per call_id against the calls table.
    grouped = items_df.groupby("call_id").size()
    call_sizes = calls_df.set_index("call_id")["group_size"]
    common = grouped.index.intersection(call_sizes.index)
    assert (grouped.loc[common] == call_sizes.loc[common]).all()

    # No unrecognised (third) composite answer schema in real data, per the
    # plan review's empirical scan (28+ results/canonicals, 11+
    # items/total_marked, 0 unknown).
    assert summary.items_parse_failures == []


def test_gemini_2_5_flash_canonical_vs_noisy_join_is_nonempty(pipeline_run):
    """AC3: the canonical-vs-treatment pairing join must be non-empty for at
    least one real (model, dialect) pair. gemini-2.5-flash has both dialects
    at matching reasoning-token budgets in the real log set."""
    _summary, out_dir = pipeline_run
    sensitivity_df = pd.read_parquet(out_dir / "pilot-v0" / "sensitivity.parquet")

    gemini_rows = sensitivity_df[sensitivity_df["model"] == "google/gemini-2.5-flash"]
    assert not gemini_rows.empty
    assert (gemini_rows["n_paired"] > 0).any()
    # AC4: n_distinct_forms <= n_paired always.
    assert (sensitivity_df["n_distinct_forms"] <= sensitivity_df["n_paired"]).all()


def test_cancelled_and_errored_logs_do_not_crash(pipeline_run):
    """Finding 6: one cancelled, one errored log must not crash the run --
    already covered by the fixture not raising, but assert explicitly that
    we saw calls with no scored items (the errored composite samples)."""
    summary, _out_dir = pipeline_run
    assert summary.calls_with_no_scored_items > 0
