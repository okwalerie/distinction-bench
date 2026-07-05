"""Charts, matrix, and walkthrough pages baked from DB-5's parquet/jsonl artifacts.

Phase 1 shipped these as "awaiting suite v1 data" placeholders (see
``lofsite.data``'s module docstring for the artifact-name fix that unblocked
this). Phase 2 bakes real content, fresh per request, from whatever
``lofbench.pipeline`` last wrote -- when the expected artifact is absent or
fails a schema_version check, the page still degrades to a placeholder
rather than crashing, preserving the phase-1 behaviour exactly.

Every page here shows ``layout.pilot_banner``: today's only real data is
pre-DB-4 pilot-v0 logs, never suite v1, and the page must say so plainly
rather than let a reader mistake a pilot number for a frozen-suite score.
"""

from __future__ import annotations

from fasthtml.common import Details, Div, NotStr, P, Small, Strong, Summary

from lofsite import layout
from lofsite.charts_data import accuracy_matrix, pick_examples, sensitivity_rows
from lofsite.data import (
    items_status,
    load_items,
    load_sensitivity,
    load_transcripts,
    sensitivity_status,
    transcripts_status,
)
from lofsite.svg_charts import accuracy_matrix_svg, sensitivity_svg


def _awaiting_data_message(status_path) -> str:
    return (
        "Awaiting suite v1 data. This page bakes from a DB-5 parquet artifact "
        f"expected at {status_path} -- not yet present. It will render "
        "automatically once DB-5 lands and the site's deploy step re-bakes."
    )


def _suite_version_label(df, fallback: str) -> str:
    if df is None or "suite_version" not in df or df.empty:
        return fallback
    versions = sorted(v for v in df["suite_version"].unique() if v is not None)
    if not versions:
        return fallback
    return "/".join(versions)


def _error_placeholder(error: str):
    return Div(f"Cannot render this page from the current artifact: {error}", cls="placeholder")


# ---------------------------------------------------------------------------
# Headline sensitivity chart
# ---------------------------------------------------------------------------


def charts_page():
    result = load_sensitivity()
    if result.frame is None:
        status = sensitivity_status()
        body = (
            [P(_awaiting_data_message(status.path), cls="placeholder")]
            if not status.available
            else [_error_placeholder(result.error)]
        )
        return layout.page(
            "Headline chart",
            "/charts",
            P("Per-model sensitivity score: paired accuracy drop vs. the canonical dialect."),
            *body,
        )

    rows = sensitivity_rows(result.frame)
    suite_version = _suite_version_label(result.frame, "unknown")
    svg = sensitivity_svg(rows)
    return layout.page(
        "Headline chart",
        "/charts",
        layout.pilot_banner(suite_version),
        P(
            "Per-model, per-dialect paired accuracy drop against the canonical "
            "dialect. Positive = canonical scored higher (the dialect hurt "
            "accuracy); negative = the dialect scored higher than canonical. "
            "Whiskers are a 95% bootstrap CI over distinct forms; the p-value "
            "is the exact binomial McNemar test on discordant pairs."
        ),
        Div(NotStr(svg), cls="viz-root"),
        Small(
            f"{len(rows)} (model, reasoning_setting, dialect) contrasts, "
            f"suite_version={suite_version}.",
            cls="caption",
        ),
    )


# ---------------------------------------------------------------------------
# Model-by-dialect accuracy matrix
# ---------------------------------------------------------------------------


def matrix_page():
    result = load_items()
    if result.frame is None:
        status = items_status()
        body = (
            [P(_awaiting_data_message(status.path), cls="placeholder")]
            if not status.available
            else [_error_placeholder(result.error)]
        )
        return layout.page(
            "Model-by-dialect matrix",
            "/matrix",
            P("Accuracy broken down by model and dialect."),
            *body,
        )

    matrix = accuracy_matrix(result.frame)
    suite_version = _suite_version_label(result.frame, "unknown")
    svg = accuracy_matrix_svg(matrix)
    return layout.page(
        "Model-by-dialect matrix",
        "/matrix",
        layout.pilot_banner(suite_version),
        P(
            "Raw accuracy per model and dialect, aggregated across reasoning "
            "settings and epochs (see the headline chart for the "
            "reasoning-setting-resolved paired comparison). A dashed "
            "“not run” cell means that model/dialect combination has "
            "no rows in this artifact -- never fabricated as 0%."
        ),
        Div(NotStr(svg), cls="viz-root"),
        Small(f"suite_version={suite_version}.", cls="caption"),
    )


# ---------------------------------------------------------------------------
# Transcript walkthroughs
# ---------------------------------------------------------------------------


def _depth_note(example) -> str:
    if example.depth is not None:
        return f"depth {example.depth}"
    return "depth: not available (composite pilot-v0 rows carry no per-item depth)"


def _difficulty_note(example) -> str:
    return example.difficulty if example.difficulty else "unknown"


def _rendered_input_block(example):
    if example.is_image:
        return P(
            "This example's dialect is an image modality. DB-5's transcript "
            "extract does not currently store image bytes, so the rendered "
            "form itself cannot be shown here -- only the outcome below.",
            cls="caption",
        )
    if example.rendered_input is None:
        return P(
            "Rendered prompt text was not captured for this row (a known gap "
            "for some composite pilot-v0 samples).",
            cls="caption",
        )
    return Details(
        Summary("Rendered prompt (as shown to the model)"),
        Div(example.rendered_input, cls="transcript-block"),
    )


def _walkthrough_card(label: str, example):
    if example is None:
        return Div(P(f"No {label.lower()} example available in this artifact."), cls="panel")
    status_cls = "correct" if example.correct else "incorrect"
    status_text = "CORRECT" if example.correct else "INCORRECT"
    return Div(
        Div(
            Strong(label),
            " ",
            Div(status_text, cls=f"status-badge {status_cls}", style="display:inline-block;"),
        ),
        P(
            f"model={example.model}  dialect={example.dialect_id}  "
            f"target={example.target}  predicted={example.predicted}  "
            f"difficulty={_difficulty_note(example)}  {_depth_note(example)}"
        ),
        Details(
            Summary("Canonical form string"),
            Div(example.form_string, cls="transcript-block"),
        ),
        _rendered_input_block(example),
        Details(
            Summary("Full model completion"),
            Div(example.full_completion_text, cls="transcript-block"),
        ),
        cls="panel",
    )


def walkthroughs_page():
    transcripts_result = load_transcripts()
    if transcripts_result.frame is None:
        status = transcripts_status()
        body = (
            [P(_awaiting_data_message(status.path), cls="placeholder")]
            if not status.available
            else [_error_placeholder(transcripts_result.error)]
        )
        return layout.page(
            "Transcript walkthroughs",
            "/walkthroughs",
            P("Correct and failed real transcripts, per form and dialect."),
            *body,
        )

    # Items is a separate artifact: if it's absent or schema-mismatched, the
    # walkthrough still renders from transcripts alone -- depth/difficulty
    # just degrade to "not available" rather than blocking the whole page.
    items_result = load_items()
    examples = pick_examples(transcripts_result.frame, items_result.frame)
    suite_version = _suite_version_label(transcripts_result.frame, "unknown")

    return layout.page(
        "Transcript walkthroughs",
        "/walkthroughs",
        layout.pilot_banner(suite_version),
        P("One correct and one incorrect real transcript, picked from the current artifact."),
        _walkthrough_card("Correct example", examples["correct"]),
        _walkthrough_card("Failed example", examples["incorrect"]),
    )
