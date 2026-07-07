"""``rooms@1``: floor plan with door gaps, family ``rooms``.

Layout: identical slice-and-dice subdivision to ``map``
(:func:`_layout.subdivide_rect`, :func:`_layout.shrink_rect`) -- reused,
not reimplemented, per the plan's explicit instruction. The door gap is
purely cosmetic: it does not change the rectangle geometry or the
containment predicate, only the drawn stroke. Each non-root rect gets a
literal break in its outline at the midpoint of one wall, chosen
deterministically by the axis it was split along (a child split along
``x`` gets its door on the left wall; split along ``y``, the top wall) --
any deterministic rule is fine here since the door has no structural
meaning, only a readable one.

Containment predicate: ``rect_subset``, shared with ``map`` -- the same
helper, not a reimplementation.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "7. Rooms".

Injectors noted, not implemented (out of this task's scope per the plan):
door-gap position jitter, door count/width variance, corridor/furniture
distractor rectangles (``node_id=None``).
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

NAME = "rooms"
VERSION = "1"
FAMILY = "rooms"

GAP = Fraction(1, 20)
DOOR_FRACTION = Fraction(1, 4)  # door width as a fraction of the wall it sits on


def _build(node: FormNode, rect: Rect, depth: int, door_wall: str | None) -> Primitive:
    shrunk = shrink_rect(rect, GAP)

    if not node.children:
        children_prims: tuple[Primitive, ...] = ()
    else:
        weights = [leaf_weight(c) for c in node.children]
        axis = "x" if depth % 2 == 0 else "y"
        sub_rects = subdivide_rect(shrunk, weights, split_axis=axis)
        child_door_wall = "left" if axis == "x" else "top"
        children_prims = tuple(
            _build(child, r, depth + 1, child_door_wall)
            for child, r in zip(node.children, sub_rects)
        )

    return Primitive(
        node_id=node.id,
        kind="room",
        geom={"rect": shrunk, "depth": depth, "door_wall": door_wall},
        children=children_prims,
    )


class RoomsArchetype:
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
        node_roots = [_build(child, r, 0, "left") for child, r in zip(root.children, sub_rects)]

        primitives: list[Primitive] = []
        for node_root in node_roots:
            primitives.extend(flatten_tree(node_root))

        scene = _svg.Scene(primitives=tuple(primitives))
        return BaseRender(modality="spatial", payload=scene, node_map=scene.node_map)

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        return rect_subset(child.geom["rect"], parent.geom["rect"])


ARCHETYPE = RoomsArchetype()


def _wall_segments_with_door(
    x0: float, y0: float, x1: float, y1: float, door_wall: str | None
) -> list[tuple[float, float, float, float]]:
    """Four wall segments; the ``door_wall`` one is split around its
    midpoint, leaving a literal gap of width ``DOOR_FRACTION`` of that
    wall's length."""
    walls = {
        "top": (x0, y0, x1, y0),
        "bottom": (x0, y1, x1, y1),
        "left": (x0, y0, x0, y1),
        "right": (x1, y0, x1, y1),
    }
    segments: list[tuple[float, float, float, float]] = []
    for name, (wx0, wy0, wx1, wy1) in walls.items():
        if name != door_wall:
            segments.append((wx0, wy0, wx1, wy1))
            continue
        gap = float(DOOR_FRACTION) / 2
        mx, my = (wx0 + wx1) / 2, (wy0 + wy1) / 2
        dx, dy = (wx1 - wx0), (wy1 - wy0)
        segments.append((wx0, wy0, mx - dx * gap, my - dy * gap))
        segments.append((mx + dx * gap, my + dy * gap, wx1, wy1))
    return segments


def to_svg(base: BaseRender, *, width: int = 640, height: int = 640) -> str:
    scene: _svg.Scene = base.payload
    body: list[str] = []
    for prim in sorted(scene.primitives, key=lambda p: p.geom["depth"]):
        x0, y0, x1, y1 = prim.geom["rect"]
        px0, py0, px1, py1 = (
            float(x0) * width,
            float(y0) * height,
            float(x1) * width,
            float(y1) * height,
        )
        for sx0, sy0, sx1, sy1 in _wall_segments_with_door(
            px0, py0, px1, py1, prim.geom["door_wall"]
        ):
            body.append(_svg.svg_line(sx0, sy0, sx1, sy1, stroke="black", stroke_width=1.5))
    return _svg.svg_document(width, height, body)
