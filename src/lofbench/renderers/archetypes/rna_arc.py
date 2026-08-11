"""``rna_arc@1``: RNA arc diagram, family ``biopolymer``.

The architecture doc's worked example 4 -- a novel self-similar spatial
dialect outside the nine visual families, disposed to DB-3 per the
orchestrator. One non-crossing arc per node over a baseline sequence; a
parent's arc spans its children's arcs by construction. Layout and
predicate are shared with ``paths_lite`` via :mod:`nesting` -- see that
module's docstring for the full derivation.

DB-2's text sibling ``rna_dotbracket@1`` shares the ``biopolymer`` family
name but a different modality; a one-line vocabulary sync is worth doing
but is not a blocking dependency here.
"""

from __future__ import annotations

import random

from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode

from . import _svg
from .nesting import build_nesting_base, nesting_predicate, unused_rng

NAME = "rna_arc"
VERSION = "1"
FAMILY = "biopolymer"

X_SCALE = 60.0
Y_SCALE = 40.0
MARGIN = 20.0
BASELINE_Y = 200.0


class RnaArcArchetype:
    name = NAME
    version = VERSION
    family = FAMILY
    modality = "spatial"

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        unused_rng(rng)
        return build_nesting_base(root, kind="rna_arc")

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        return nesting_predicate(parent, child)


ARCHETYPE = RnaArcArchetype()


def to_svg(base: BaseRender, *, width: int = 800, height: int = 300) -> str:
    """Draws each node's span as an arc over the baseline, with a letter
    glyph at each leaf position -- the RNA-sequence-and-arcs reading."""
    scene: _svg.Scene = base.payload
    body: list[str] = [
        _svg.svg_line(
            MARGIN, BASELINE_Y, width - MARGIN, BASELINE_Y, stroke="black", stroke_width=1
        )
    ]
    leaves = sorted(
        (p for p in scene.primitives if p.geom["raw"][0] == p.geom["raw"][1]),
        key=lambda p: p.geom["raw"][0],
    )
    for leaf in leaves:
        x = MARGIN + float(leaf.geom["raw"][0]) * X_SCALE
        body.append(_svg.svg_text(x, BASELINE_Y + 16, "n", text_anchor="middle"))

    for prim in sorted(scene.primitives, key=lambda p: -p.geom["depth"]):
        lo, hi = prim.geom["span"]
        x0 = MARGIN + float(lo) * X_SCALE
        x1 = MARGIN + float(hi) * X_SCALE
        cx = (x0 + x1) / 2
        arc_height = (x1 - x0) / 2 * Y_SCALE / X_SCALE + 10
        d = f"M {x0},{BASELINE_Y} Q {cx},{BASELINE_Y - arc_height} {x1},{BASELINE_Y}"
        body.append(_svg.svg_path(d, fill="none", stroke="black", stroke_width=1.2))

    return _svg.svg_document(width, height, body)
