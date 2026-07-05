"""Placeholder pages for the DB-5-dependent charts and walkthroughs.

Per the DB-6 plan's "what works before DB-4/DB-5 land" section: the headline
chart, model-by-dialect matrix, paired-delta charts, and transcript
walkthroughs all need DB-5's parquet artifact. Until that artifact exists,
these pages degrade to a clear "awaiting suite v1 data" placeholder rather
than failing the app boot. Baking real charts from the artifact is out of
phase-1 scope; this module only wires the route and the degrade-gracefully
behaviour so the site can ship before DB-5 lands.
"""

from __future__ import annotations

from fasthtml.common import P

from lofsite import layout
from lofsite.data import headline_status, items_status


def _awaiting_data_message(status_path) -> str:
    return (
        "Awaiting suite v1 data. This page bakes from a DB-5 parquet artifact "
        f"expected at {status_path} -- not yet present. It will render "
        "automatically once DB-5 lands and the site's deploy step re-bakes."
    )


def charts_page():
    status = headline_status()
    body = (
        [P(_awaiting_data_message(status.path), cls="placeholder")]
        if not status.available
        else [P("Headline chart data found, but phase-1 baking is not yet implemented.")]
    )
    return layout.page(
        "Headline chart",
        "/charts",
        P("Per-model sensitivity score: paired accuracy drop vs. the canonical dialect."),
        *body,
    )


def matrix_page():
    status = items_status()
    body = (
        [P(_awaiting_data_message(status.path), cls="placeholder")]
        if not status.available
        else [P("Matrix data found, but phase-1 baking is not yet implemented.")]
    )
    return layout.page(
        "Model-by-dialect matrix",
        "/matrix",
        P("Accuracy broken down by model and dialect, with paired-delta detail."),
        *body,
    )


def walkthroughs_page():
    status = items_status()
    body = (
        [P(_awaiting_data_message(status.path), cls="placeholder")]
        if not status.available
        else [
            P(
                "Transcript data found, but phase-1 walkthrough rendering is not "
                "yet implemented. Note: composite pilot-v0 logs carry null "
                "depth/steps, so a deep-nesting filter must skip or clearly "
                "label those rows rather than treating null as zero depth."
            )
        ]
    )
    return layout.page(
        "Transcript walkthroughs",
        "/walkthroughs",
        P("Correct and failed real transcripts, per form and dialect."),
        *body,
    )
