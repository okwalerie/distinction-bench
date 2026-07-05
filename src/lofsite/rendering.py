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


def render_all_dialects(form_string: str) -> list[DialectPanel]:
    """Render `form_string` through every registered dialect.

    Args:
        form_string: A canonical, already-validated form string (see
            `lofsite.validation.validate_form_input`).

    Returns:
        One `DialectPanel` per entry in `list_renderers()`, in sorted order.
    """
    panels = []
    for key in list_renderers():
        renderer = get_renderer(key)
        result = renderer.render(form_string)
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
    return panels
