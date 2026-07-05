"""``map-centred@1``: annular treemap (sunburst), family ``map_centred``.

Layout: a decorative centre disk (cosmetic, ``node_id=None``) sits at the
middle. Depth ``d`` (``d=0`` for the top-level marks) occupies a fixed
annulus band ``[R0 + d*BAND, R0 + (d+1)*BAND]``. Within a band, a node's
angular span is an exact ``Fraction`` of a full turn, proportional to
leaf-count weight (:func:`_layout.partition_interval`, the same helper
``map`` uses for its rectangle footprint, applied here to angle instead of
``x``). Convert ``(radius, turn)`` to Cartesian only through
:func:`_geometry.polar_to_cartesian`, reusing the graph family's stable-trig
helper -- called out explicitly here, per the plan.

Wraparound safety, pinned as a plain representation choice rather than a
runtime check: every angular span is stored as an ordinary
``[lo, hi)`` ``Fraction`` sub-interval of ``[0, 1)``, built by recursively
partitioning the *parent's own* sub-interval (never re-based at zero, never
represented as a range that wraps past 1 back to 0). Because
:func:`_layout.partition_interval` only ever slices the interval it is
given, and the top-level call starts from the full ``[0, 1)`` circle, no
wedge can ever straddle the zero seam -- there is no modular arithmetic
anywhere in this module for that reason.

Containment predicate: an interval-subset test in polar coordinates,
combining two independent 1-D interval-subset checks -- the same "extends
to the rim" idea ``blocks`` uses for its ``z`` axis, applied to the radial
axis here: a node's *verification* radial interval is
``(R0 + depth*BAND, R_MAX)``, not the single visual band it is drawn in,
so a deeper descendant's radial interval starts further out but shares the
same outer bound and nests exactly. The angular interval is the
genuinely discriminating axis, laminar by construction for the same reason
``map``'s rectangles are. Do the interval comparisons as exact
``Fraction``s; only the final draw step (:func:`to_svg`) converts to
``Decimal``/``float`` through ``stable_trig``.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "6. Map-centred".

Injectors noted, not implemented (out of this task's scope per the plan):
wedge-angle jitter, annulus-width jitter, distractor wedge
(``node_id=None``).
"""

from __future__ import annotations

import random
from decimal import Decimal
from fractions import Fraction

from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode

from . import _svg
from ._geometry import Interval, interval_subset, polar_to_cartesian
from ._layout import partition_interval, shrink_interval
from ._structural import flatten_tree, leaf_weight

NAME = "map_centred"
VERSION = "1"
FAMILY = "map_centred"

GAP = Fraction(1, 20)
R0 = 40.0  # centre disk radius, screen units
BAND = 50.0  # annulus band width per depth level
R_MAX = Fraction(10_000)  # comfortably beyond any generated form's deepest band


def _radial_verify(depth: int) -> Interval:
    return Fraction(depth), R_MAX


def _build(node: FormNode, angle: Interval, depth: int) -> Primitive:
    shrunk_angle = shrink_interval(angle, GAP)
    box = (shrunk_angle, _radial_verify(depth))

    if not node.children:
        children_prims: tuple[Primitive, ...] = ()
    else:
        weights = [leaf_weight(c) for c in node.children]
        sub_angles = partition_interval(shrunk_angle[0], shrunk_angle[1], weights)
        children_prims = tuple(
            _build(child, a, depth + 1) for child, a in zip(node.children, sub_angles)
        )

    return Primitive(
        node_id=node.id,
        kind="wedge",
        geom={"angle": shrunk_angle, "depth": depth, "box": box},
        children=children_prims,
    )


class MapCentredArchetype:
    name = NAME
    version = VERSION
    family = FAMILY
    modality = "spatial"

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        del rng
        if not root.children:
            return BaseRender(modality="spatial", payload=_svg.Scene(primitives=()), node_map={})

        weights = [leaf_weight(c) for c in root.children]
        sub_angles = partition_interval(Fraction(0), Fraction(1), weights)
        node_roots = [_build(child, a, 0) for child, a in zip(root.children, sub_angles)]

        centre = Primitive(node_id=None, kind="centre", geom={})
        primitives: list[Primitive] = [centre]
        for node_root in node_roots:
            primitives.extend(flatten_tree(node_root))

        scene = _svg.Scene(primitives=tuple(primitives))
        return BaseRender(modality="spatial", payload=scene, node_map=scene.node_map)

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        p_angle, p_radial = parent.geom["box"]
        c_angle, c_radial = child.geom["box"]
        return interval_subset(c_angle, p_angle) and interval_subset(c_radial, p_radial)


ARCHETYPE = MapCentredArchetype()


def _arc_points(radius: float, angle: Interval, samples: int = 6) -> list[tuple[float, float]]:
    lo, hi = angle
    points = []
    for i in range(samples + 1):
        turn = lo + (hi - lo) * Fraction(i, samples)
        x, y = polar_to_cartesian(Decimal(radius), turn)
        points.append((float(x), float(y)))
    return points


def to_svg(base: BaseRender, *, width: int = 640, height: int = 640) -> str:
    scene: _svg.Scene = base.payload
    cx, cy = width / 2, height / 2
    body: list[str] = []

    for prim in sorted(scene.primitives, key=lambda p: p.geom.get("depth", -1)):
        if prim.kind == "centre":
            body.append(_svg.svg_circle(cx, cy, R0, fill="white", stroke="black", stroke_width=1.2))
            continue
        depth = prim.geom["depth"]
        r_in, r_out = R0 + depth * BAND, R0 + (depth + 1) * BAND
        angle = prim.geom["angle"]
        outer = _arc_points(r_out, angle)
        inner = list(reversed(_arc_points(r_in, angle)))
        ring = outer + inner
        d = "M " + " L ".join(f"{cx + x},{cy + y}" for x, y in ring) + " Z"
        body.append(_svg.svg_path(d, fill="white", stroke="black", stroke_width=1.2))

    return _svg.svg_document(width, height, body)
