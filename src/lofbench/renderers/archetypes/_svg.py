"""Tiny SVG string builders and the flat ``Scene`` container the spatial
archetypes in this package share.

This is DB-3's own scratch emission path, not the pipeline's ``emit()``:
symbolic SVG is built here so every archetype's scene can be viewed and
sanity-checked now; PNG rasterisation through a pinned rasteriser is DB-4
M5 scope, per this task's boundary. ``Scene.to_svg`` is a self-contained
renderer an archetype module owns; DB-4 may later fold the same primitives
into ``pipeline.emit``'s spatial path.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

from lofbench.renderers.pipeline.archetype import Primitive


def _attrs(attrs: dict[str, object]) -> str:
    return "".join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items() if v is not None)


def svg_circle(cx: float, cy: float, r: float, **attrs: object) -> str:
    return f'<circle cx="{cx}" cy="{cy}" r="{r}"{_attrs(attrs)}/>'


def svg_ellipse(cx: float, cy: float, rx: float, ry: float, **attrs: object) -> str:
    return f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}"{_attrs(attrs)}/>'


def svg_rect(x: float, y: float, w: float, h: float, **attrs: object) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}"{_attrs(attrs)}/>'


def svg_line(x1: float, y1: float, x2: float, y2: float, **attrs: object) -> str:
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"{_attrs(attrs)}/>'


def svg_path(d: str, **attrs: object) -> str:
    return f'<path d="{d}"{_attrs(attrs)}/>'


def svg_text(x: float, y: float, text: str, **attrs: object) -> str:
    return f'<text x="{x}" y="{y}"{_attrs(attrs)}>{escape(str(text))}</text>'


def svg_document(width: float, height: float, body: list[str]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">' + "".join(body) + "</svg>"
    )


@dataclass(frozen=True)
class Scene:
    """A flat, ordered list of primitives ready to draw, plus the labelled
    subset that becomes ``BaseRender.node_map``.

    ``primitives`` is everything: real nodes, connectors/edges
    (``kind="edge"``, ``node_id=None``), and distractors
    (``node_id=None``). Draw order is list order.
    """

    primitives: tuple[Primitive, ...]

    @property
    def node_map(self) -> dict[str, Primitive]:
        return {p.node_id: p for p in self.primitives if p.node_id is not None}
