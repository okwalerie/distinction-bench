"""``paths_lite@1``: interval-nesting stand-in for the literal re-entrant
wiring family, dialect ``paths.arc-nest-v1``, family ``paths``.

The plan's feasibility call (section 8) defers the literal re-entrant
crossing-wire panel: there is no closed-form layout for wire routing with
over/under nesting analogous to a treemap or circle-packer, no existing
routing algorithm in this codebase to lean on, and a containment predicate
for "this wire loop encloses that pin" presupposes the routing already
exists. This module ships the recommended cheap stand-in instead: the same
interval-nesting idea as ``rna_arc`` (:mod:`nesting`), drawn as a "staple"
shape -- two verticals plus a horizontal, fully rational coordinates, no
circular SVG arcs -- rather than RNA sequence letters and round arcs.

This is a simplified interval-nesting stand-in for the taxonomy's literal
wiring diagram, not a reproduction of it. Say so wherever this dialect is
described to a reader, per the plan's explicit instruction, so a future
chart legend does not conflate the two.
"""

from __future__ import annotations

import random

from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode

from . import _svg
from .nesting import build_nesting_base, nesting_predicate, unused_rng

NAME = "paths_lite"
VERSION = "1"
FAMILY = "paths"

X_SCALE = 60.0
MARGIN = 20.0
BASELINE_Y = 250.0
STEP_HEIGHT = 14.0  # per-depth staple rise


class PathsLiteArchetype:
    name = NAME
    version = VERSION
    family = FAMILY
    modality = "spatial"

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        unused_rng(rng)
        return build_nesting_base(root, kind="staple")

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        return nesting_predicate(parent, child)


ARCHETYPE = PathsLiteArchetype()


def to_svg(base: BaseRender, *, width: int = 800, height: int = 300) -> str:
    """Draws each node's span as a "staple": two verticals plus a
    horizontal, rising higher on screen the deeper the node's own nesting
    goes within its subtree -- a flat, all-rational stand-in for a literal
    wire loop."""
    scene: _svg.Scene = base.payload
    body: list[str] = [
        _svg.svg_line(
            MARGIN, BASELINE_Y, width - MARGIN, BASELINE_Y, stroke="black", stroke_width=1
        )
    ]

    max_depth = max((p.geom["depth"] for p in scene.primitives), default=0)
    for prim in sorted(scene.primitives, key=lambda p: -p.geom["depth"]):
        lo, hi = prim.geom["span"]
        x0 = MARGIN + float(lo) * X_SCALE
        x1 = MARGIN + float(hi) * X_SCALE
        # Deeper subtree nesting draws shorter (closer to baseline); a node
        # near the root of its own local nesting draws taller, so a chain
        # of staples reads as visibly stacked, not flat.
        rise = (max_depth - prim.geom["depth"] + 1) * STEP_HEIGHT
        top = BASELINE_Y - rise
        d = f"M {x0},{BASELINE_Y} L {x0},{top} L {x1},{top} L {x1},{BASELINE_Y}"
        body.append(_svg.svg_path(d, fill="none", stroke="black", stroke_width=1.2))

    return _svg.svg_document(width, height, body)
