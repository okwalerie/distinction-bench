"""Sandbox rendering: fan a canonical form string out across every registered dialect.

Iterates `lofbench.renderers.list_renderers()` / `get_renderer()` rather than
hard-coding a dialect list, so new archetypes registered by other DB tasks
appear in the sandbox with no site code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lofbench.renderers import get_renderer, list_renderers


@dataclass(frozen=True)
class DialectPanel:
    """One dialect's rendering of a form, ready for display."""

    registry_key: str
    renderer_name: str
    kind: str  # "text" or "image"
    content: str
    metadata: dict[str, Any]


class DialectFanOut(list[DialectPanel]):
    """`list[DialectPanel]` (so existing iteration-based call sites keep
    working unchanged) plus a `skipped` attribute naming registry entries
    that could not be constructed with no arguments.

    Parameterised mechanisms like ``composed`` (which needs a caller-supplied
    `DialectSpec`) are not standalone dialects, so the sandbox honestly
    reports that it does not show them rather than crashing or silently
    omitting them without a trace.
    """

    def __init__(self, panels: list[DialectPanel], skipped: list[str]) -> None:
        super().__init__(panels)
        self.skipped = skipped


def render_all_dialects(form_string: str) -> DialectFanOut:
    """Render `form_string` through every zero-arg-constructible dialect.

    Args:
        form_string: A canonical, already-validated form string (see
            `lofsite.validation.validate_form_input`).

    Returns:
        A `DialectFanOut` (list of `DialectPanel`, one per zero-arg-
        constructible entry in `list_renderers()`, sorted order) whose
        `.skipped` attribute lists registry keys that raised on
        construction (e.g. `"composed"`, which requires a `DialectSpec` or
        spec kwargs) *or* on render (a spatial dialect whose rasterisation
        path isn't landed yet).
    """
    panels = []
    skipped = []
    for key in list_renderers():
        try:
            renderer = get_renderer(key)
        except (ValueError, TypeError):
            # Registry entries that require constructor arguments (e.g.
            # ComposedRenderer without a spec) are parameterised mechanisms,
            # not standalone dialects -- exclude them from the sandbox but
            # keep them visible in `skipped` so the fan-out stays honest.
            skipped.append(key)
            continue
        try:
            result = renderer.render(form_string)
        except NotImplementedError:
            # A registered dialect whose spatial rasterisation path isn't
            # landed yet (DB-4 M5 / DB-9's spatial-archetype activation).
            # lofbench.renderers.pipeline.emit's own docstring calls
            # reaching a spatial BaseRender before the rasteriser lands "a
            # programming error, not a silent no-op" for the pipeline's
            # internal callers -- but the sandbox fans out over every
            # zero-arg-constructible dialect on ordinary user input, so a
            # not-yet-ready spatial dialect appearing in list_renderers()
            # must degrade the same way a construction-time skip does:
            # honest and visible in `skipped`, never a 500.
            skipped.append(key)
            continue
        kind = "image" if result.metadata.get("format") == "image" else "text"
        panels.append(
            DialectPanel(
                registry_key=key,
                renderer_name=result.renderer_name,
                kind=kind,
                content=result.rendered,
                metadata=result.metadata,
            )
        )
    return DialectFanOut(panels, skipped)
