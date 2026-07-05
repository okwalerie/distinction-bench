"""``map@1``: rectangular treemap with gaps, family ``map``.

Layout: slice-and-dice, not squarified -- alternate horizontal/vertical
cuts by depth parity (:func:`_layout.subdivide_rect`), sized by leaf-count
weight. A fixed-width gap separates siblings and separates a child from
its parent's border, so the map reads as bordered regions rather than a
flush grid (:func:`_layout.shrink_rect`).

Containment predicate: ``rect_subset`` (this module's own thin wrapper is
unnecessary -- :func:`_geometry.rect_subset` is used directly), exact
``Fraction`` comparison, no epsilon needed pre-emission. Because every
child rect is built by subdividing, then shrinking, the parent's own
already-placed rect, the child-in-parent subset holds by construction at
every depth, not just for direct parent-child pairs (the same
laminar-partition argument as blocks' ``x`` axis, applied in 2-D).

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "5. Map".
Module is named ``map_rect`` (not ``map``) to avoid shadowing the
built-in.

Injectors noted, not implemented (out of this task's scope per the plan):
gap-width jitter, region distractor (``node_id=None``), region
style/colour variance (site-only per the decisions doc).
"""

from __future__ import annotations

import random
from fractions import Fraction

from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode

from . import _svg
from ._geometry import Rect, rect_subset
from ._layout import shrink_rect, subdivide_rect
from ._structural import flatten_tree, leaf_weight

NAME = "map"
VERSION = "1"
FAMILY = "map"

GAP = Fraction(1, 20)


def _build(node: FormNode, rect: Rect, depth: int) -> Primitive:
    shrunk = shrink_rect(rect, GAP)

    if not node.children:
        children_prims: tuple[Primitive, ...] = ()
    else:
        weights = [leaf_weight(c) for c in node.children]
        axis = "x" if depth % 2 == 0 else "y"
        sub_rects = subdivide_rect(shrunk, weights, split_axis=axis)
        children_prims = tuple(
            _build(child, r, depth + 1) for child, r in zip(node.children, sub_rects)
        )

    return Primitive(
        node_id=node.id, kind="rect", geom={"rect": shrunk, "depth": depth}, children=children_prims
    )


class MapArchetype:
    name = NAME
    version = VERSION
    family = FAMILY
    modality = "spatial"

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        del rng
        if not root.children:
            return BaseRender(modality="spatial", payload=_svg.Scene(primitives=()), node_map={})

        weights = [leaf_weight(c) for c in root.children]
        canvas: Rect = (Fraction(0), Fraction(0), Fraction(1), Fraction(1))
        sub_rects = subdivide_rect(canvas, weights, split_axis="x")
        node_roots = [_build(child, r, 0) for child, r in zip(root.children, sub_rects)]

        primitives: list[Primitive] = []
        for node_root in node_roots:
            primitives.extend(flatten_tree(node_root))

        scene = _svg.Scene(primitives=tuple(primitives))
        return BaseRender(modality="spatial", payload=scene, node_map=scene.node_map)

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        return rect_subset(child.geom["rect"], parent.geom["rect"])


ARCHETYPE = MapArchetype()


def to_svg(base: BaseRender, *, width: int = 640, height: int = 640) -> str:
    scene: _svg.Scene = base.payload
    body: list[str] = []
    for prim in sorted(scene.primitives, key=lambda p: p.geom["depth"]):
        x0, y0, x1, y1 = prim.geom["rect"]
        body.append(
            _svg.svg_rect(
                float(x0) * width,
                float(y0) * height,
                float(x1 - x0) * width,
                float(y1 - y0) * height,
                fill="white",
                stroke="black",
                stroke_width=1.2,
            )
        )
    return _svg.svg_document(width, height, body)
