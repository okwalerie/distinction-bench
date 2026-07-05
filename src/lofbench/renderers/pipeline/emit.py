"""Terminal step: turn a base render into the payload the task layer understands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .archetype import BaseRender


@dataclass
class Emission:
    payload: str
    is_image: bool


def emit(base: BaseRender, style: dict[str, Any]) -> Emission:
    """Text: serialise the payload. Spatial: build symbolic SVG, then
    rasterise to a PNG data URI.

    The spatial path is M5 scope -- it needs the pinned rasteriser
    (cairosvg or resvg) and the enclosure archetype's platform-stable
    packer. M1/M2 only exercise the text path end to end; a spatial
    ``BaseRender`` reaching ``emit`` before M5 lands is a programming error,
    not a silent no-op, so this raises rather than emitting nothing.
    """
    if base.modality == "text":
        return Emission(payload=str(base.payload), is_image=False)

    raise NotImplementedError(
        "Spatial emission (symbolic SVG plus pinned-rasteriser PNG) lands in M5."
    )
