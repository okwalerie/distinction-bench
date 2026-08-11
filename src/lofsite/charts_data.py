"""Pandas aggregation over DB-5's tidy artifacts, baked fresh per request.

Kept separate from ``lofsite.data`` (location/presence/schema) and
``lofsite.svg_charts`` (pure SVG rendering) so each layer is independently
testable: feed a synthetic DataFrame here and assert on the aggregated rows;
feed synthetic rows to ``svg_charts`` and assert on the markup.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SensitivityRow:
    """One row ready for the headline sensitivity chart."""

    model: str
    reasoning_setting: str | None
    dialect_id: str
    paired_drop: float
    ci_low: float
    ci_high: float
    mcnemar_p: float
    n_distinct_forms: int
    coverage: float

    @property
    def label(self) -> str:
        setting = self.reasoning_setting if self.reasoning_setting is not None else "default"
        return f"{self.model} · {setting} · {self.dialect_id}"


def sensitivity_rows(df: pd.DataFrame) -> list[SensitivityRow]:
    """Sort deterministically (model, reasoning_setting, dialect_id) so the
    chart is stable across rebakes of the same artifact."""
    if df.empty:
        return []
    sorted_df = df.sort_values(["model", "reasoning_setting", "dialect_id"], na_position="first")
    rows = []
    for _, r in sorted_df.iterrows():
        rows.append(
            SensitivityRow(
                model=r["model"],
                reasoning_setting=r["reasoning_setting"],
                dialect_id=r["dialect_id"],
                paired_drop=float(r["paired_drop"]),
                ci_low=float(r["bootstrap_ci_low"]),
                ci_high=float(r["bootstrap_ci_high"]),
                mcnemar_p=float(r["mcnemar_p"]),
                n_distinct_forms=int(r["n_distinct_forms"]),
                coverage=float(r["coverage"]),
            )
        )
    return rows


@dataclass(frozen=True)
class MatrixCell:
    accuracy: float
    n: int


@dataclass(frozen=True)
class MatrixData:
    row_labels: list[str]
    col_labels: list[str]
    cells: dict[tuple[str, str], MatrixCell]


def accuracy_matrix(items_df: pd.DataFrame) -> MatrixData:
    """Model x dialect_id accuracy, aggregated across reasoning settings and
    epochs (the page captions this). A (model, dialect_id) combo the pilot
    data never ran (not every model saw every dialect) is simply absent
    from ``cells`` -- the page renders an honest empty cell rather than a
    fabricated 0%.
    """
    if items_df.empty:
        return MatrixData(row_labels=[], col_labels=[], cells={})
    grouped = items_df.groupby(["model", "dialect_id"])["correct"].agg(["mean", "count"])
    row_labels = sorted(items_df["model"].unique())
    col_labels = sorted(items_df["dialect_id"].unique())
    cells = {
        (model, dialect_id): MatrixCell(accuracy=float(row["mean"]), n=int(row["count"]))
        for (model, dialect_id), row in grouped.iterrows()
    }
    return MatrixData(row_labels=row_labels, col_labels=col_labels, cells=cells)


@dataclass(frozen=True)
class WalkthroughExample:
    """One transcript, joined with its item context where available."""

    form_id: str
    form_string: str
    dialect_id: str
    model: str
    correct: bool
    target: str
    predicted: str
    full_completion_text: str
    rendered_input: str | None
    is_image: bool
    depth: int | None
    difficulty: str | None


def _join_item_context(transcripts_df: pd.DataFrame, items_df: pd.DataFrame | None) -> pd.DataFrame:
    """Left-join transcript rows onto items for depth/difficulty display,
    keyed on (call_id, item_index) -- unique in both tables per DB-5's
    schema. Composite pilot-v0 rows carry null depth/steps by design
    (single-task samples only) -- the walkthrough page must show that
    plainly (see ``pick_examples``) rather than treat null as zero depth,
    per the settled DB-5/DB-6 contract noted in the DB-6 plan.
    """
    if items_df is None or items_df.empty:
        out = transcripts_df.copy()
        out["depth"] = None
        out["difficulty"] = None
        return out
    key_cols = ["call_id", "item_index"]
    slim_items = items_df[[*key_cols, "depth", "difficulty"]].drop_duplicates(key_cols)
    return transcripts_df.merge(slim_items, on=key_cols, how="left")


def pick_examples(
    transcripts_df: pd.DataFrame, items_df: pd.DataFrame | None
) -> dict[str, WalkthroughExample | None]:
    """Pick one correct and one incorrect transcript, deterministically.

    Prefers a row with a captured ``rendered_input`` (the prompt text/block
    actually shown to the model) over one where it's null, for a more
    legible walkthrough -- but falls back to any row of the right
    correctness if none has it, rather than showing nothing.
    """
    if transcripts_df.empty:
        return {"correct": None, "incorrect": None}
    joined = _join_item_context(transcripts_df, items_df)
    joined = joined.sort_values(["call_id", "item_index"])

    def _pick(want_correct: bool) -> WalkthroughExample | None:
        subset = joined[joined["correct"] == want_correct]
        if subset.empty:
            return None
        with_prompt = subset[subset["rendered_input"].notna()]
        row = with_prompt.iloc[0] if not with_prompt.empty else subset.iloc[0]
        depth = row.get("depth")
        difficulty = row.get("difficulty")
        return WalkthroughExample(
            form_id=row["form_id"],
            form_string=row["form_string"],
            dialect_id=row["dialect_id"],
            model=row["model"],
            correct=bool(row["correct"]),
            target=row["target"],
            predicted=row["predicted"],
            full_completion_text=row["full_completion_text"],
            rendered_input=(None if pd.isna(row["rendered_input"]) else row["rendered_input"]),
            is_image=bool(row["is_image"]),
            depth=(None if pd.isna(depth) else int(depth)),
            difficulty=(None if pd.isna(difficulty) else str(difficulty)),
        )

    return {"correct": _pick(True), "incorrect": _pick(False)}
