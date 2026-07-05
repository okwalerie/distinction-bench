"""``blocks@1``: rational dimetric stacking, family ``blocks``.

Layout: a 1-D interval subdivision problem, not a true isometric
projection problem, per the plan's explicit framing. Each node gets a 3-D
axis-aligned box ``(x_interval, y_interval, z_interval)``:

- ``x``: at each depth, siblings divide their parent's ``x`` footprint by
  leaf-count weight (:func:`_layout.partition_interval`), then shrink
  inward by a fixed gap so siblings read as separated blocks. This is the
  genuinely discriminating axis -- a laminar partition of ``[0, 1)``, so
  any two nodes not in an ancestor-descendant relation get disjoint
  ``x``-intervals (see the trees module for the same disjointness
  argument, applied here to widths instead of leaf slots).
- ``y``: a fixed footprint depth, ``(0, 1)``, identical for every node.
  The plan only calls out dividing "along x"; this keeps the family cheap
  (no squarified footprint) while still giving the dimetric projection's
  ``screen_x = (x - y) * X_SCALE`` formula a real second plan axis to
  skew against.
- ``z``: ``(depth, Z_TOP)`` -- not the single-layer band a viewer would
  visually read off the stack, but the column from this node's own layer
  out to a fixed point past the deepest possible layer. A descendant's
  ``z``-interval starts strictly later (greater depth) than its
  ancestor's and shares the same upper bound, so it nests exactly the way
  the drawn stack visually reads bottom (root, wide) to top (leaves,
  narrow) even though the verification box is not the drawn slab.

Containment predicate: ``interval_subset_3d`` over the three axes --
the architecture doc's explicit call-out for this family. Because every
bound is a ``Fraction``, the test is exact; no epsilon is needed at all,
pre- or post-emission.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "3. Blocks".

Injectors noted, not implemented (out of this task's scope per the plan):
isometric jitter (rational footprint/height perturbation), distractor
floating blocks (``node_id=None``), top-face label/glyph swap.
"""

from __future__ import annotations

import random
from fractions import Fraction

from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode

from . import _svg
from ._geometry import interval_subset_3d
from ._layout import partition_interval, shrink_interval
from ._structural import flatten_tree, leaf_weight

NAME = "blocks"
VERSION = "1"
FAMILY = "blocks"

GAP = Fraction(1, 20)
Y_LO, Y_HI = Fraction(0), Fraction(1)
Z_TOP = Fraction(64)  # comfortably beyond any generated form's depth

X_SCALE = 220.0
Y_SCALE = 60.0
Z_SCALE = 50.0


def _build(node: FormNode, x_range: tuple[Fraction, Fraction], depth: int) -> Primitive:
    x_interval = shrink_interval(x_range, GAP)
    box = (x_interval, (Y_LO, Y_HI), (Fraction(depth), Z_TOP))

    if not node.children:
        children_prims: tuple[Primitive, ...] = ()
    else:
        weights = [leaf_weight(c) for c in node.children]
        sub_intervals = partition_interval(x_interval[0], x_interval[1], weights)
        children_prims = tuple(
            _build(child, span, depth + 1) for child, span in zip(node.children, sub_intervals)
        )

    return Primitive(
        node_id=node.id,
        kind="block",
        geom={"x": x_interval, "depth": depth, "box": box},
        children=children_prims,
    )


class BlocksArchetype:
    name = NAME
    version = VERSION
    family = FAMILY
    modality = "spatial"

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        del rng  # no randomness in the base archetype; injectors would consume it
        if not root.children:
            return BaseRender(modality="spatial", payload=_svg.Scene(primitives=()), node_map={})

        weights = [leaf_weight(c) for c in root.children]
        sub_intervals = partition_interval(Fraction(0), Fraction(1), weights)
        node_roots = [_build(child, span, 0) for child, span in zip(root.children, sub_intervals)]

        primitives: list[Primitive] = []
        for node_root in node_roots:
            primitives.extend(flatten_tree(node_root))

        scene = _svg.Scene(primitives=tuple(primitives))
        return BaseRender(modality="spatial", payload=scene, node_map=scene.node_map)

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        return interval_subset_3d(child.geom["box"], parent.geom["box"])


ARCHETYPE = BlocksArchetype()


def to_svg(base: BaseRender, *, width: int = 640, height: int = 480) -> str:
    """DB-3's own symbolic SVG emission -- viewing and sanity-checking now,
    PNG rasterisation through the pinned rasteriser is DB-4 M5 scope. Draws
    the top face of each node's own single-layer slab (``[depth, depth+1]``
    visually), not the verification box, which extends conceptually to
    ``Z_TOP`` and is never itself drawn."""
    scene: _svg.Scene = base.payload
    origin_x, origin_y = width / 2, height - 40
    body: list[str] = []

    def project(px: float, py: float, depth: int) -> tuple[float, float]:
        sx = (px - py) * X_SCALE
        sy = (px + py) * Y_SCALE / 2 - depth * Z_SCALE
        return origin_x + sx, origin_y + sy

    for prim in sorted(scene.primitives, key=lambda p: p.geom["depth"]):
        x0, x1 = prim.geom["x"]
        depth = prim.geom["depth"]
        y0f, y1f = float(Y_LO), float(Y_HI)
        corners = [
            project(float(x0), y0f, depth),
            project(float(x1), y0f, depth),
            project(float(x1), y1f, depth),
            project(float(x0), y1f, depth),
        ]
        d = "M " + " L ".join(f"{x},{y}" for x, y in corners) + " Z"
        body.append(_svg.svg_path(d, fill="white", stroke="black", stroke_width=1.2))

    return _svg.svg_document(width, height, body)
