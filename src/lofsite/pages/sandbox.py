"""Sandbox: type a form, see it rendered in every registered dialect at once.

Server-rendered, one HTTP round trip per submission -- no client-side
rendering, no pre-baked form set. Per the DB-6 plan: "every request is
server-rendered from live input."
"""

from __future__ import annotations

from fasthtml.common import H3, Button, Div, Form, Img, P, Pre, Small, Textarea

from lofsite import layout
from lofsite.rendering import DialectPanel, render_all_dialects
from lofsite.validation import validate_form_input

EXAMPLE_FORM = "(()())"


def _dialect_panel(panel: DialectPanel):
    caption = None
    if panel.renderer_name != panel.registry_key:
        caption = Small(
            f"registry key: {panel.registry_key!r} (renderer name: {panel.renderer_name!r})",
            cls="caption",
        )
    if panel.kind == "image":
        body = Img(src=panel.content, alt=f"{panel.registry_key} rendering")
    else:
        body = Pre(panel.content)
    return Div(H3(panel.registry_key), body, caption, cls="panel")


def _sandbox_form():
    return Form(
        Textarea(
            EXAMPLE_FORM,
            name="form_input",
            rows="3",
            placeholder="Type a form using ( and ) -- e.g. (()())",
            maxlength="200",
        ),
        Div(
            Small("Only '(' ')' and whitespace; max 200 characters, max nesting depth 20."),
        ),
        Button("Render", type="submit"),
        cls="sandbox-form",
        hx_post="/render",
        hx_target="#sandbox-results",
        hx_swap="outerHTML",
    )


def sandbox_results(raw_text: str | None):
    """Build the results fragment for a submission (or the example on first load)."""
    text = raw_text if raw_text is not None else EXAMPLE_FORM
    result = validate_form_input(text)
    if not result.ok:
        return Div(result.error, cls="error", id="sandbox-results")

    panels = render_all_dialects(result.form_string)
    grid = Div(*[_dialect_panel(p) for p in panels], cls="panel-grid")
    return Div(
        P(f"Canonical form: {result.form_string!r}"),
        grid,
        id="sandbox-results",
    )


def sandbox_page():
    return layout.page(
        "Sandbox",
        "/",
        P(
            "Type a form (nested parentheses). It is rendered, server-side, "
            "through every registered dialect below -- one source of truth, "
            "many surfaces."
        ),
        _sandbox_form(),
        sandbox_results(None),
    )
