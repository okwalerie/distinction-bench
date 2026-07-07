"""``enclosure@1``: nested-circle packing, family ``enclosure``.

DB-4's own M5 migration of ``lofbench.renderers.svg_circle_renderer`` (the
plan's "only substantial code write"). Keeps the legacy renderer's radial
packer *shape* (adaptive per-branching-factor scale, children distributed
radially around their parent's centre) but recomputes every coordinate in
platform-stable arithmetic -- :mod:`decimal.Decimal` throughout, via
``_geometry.stable_sin``/``stable_cos``/``stable_sqrt``, never bare
``math.sin``/``math.cos``/``math.sqrt`` -- so the containment predicate
cannot flip across machines at the tight grandparent-to-grandchild margins
radial packing produces. See ``.lattice/notes/rendering-architecture-
2026-07-04.md``, worked example 3, and
``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md`` (DB-4, M5 item 5).

Per that acceptance criterion, this is a *structural*, not pixel, migration:
the doc explicitly allows the new packer's drawn output to differ from the
legacy ``SVGCircleRenderer``'s, as long as containment verifies, seeding is
deterministic, and the divergence is documented (it is, here and in the
M5-rest landing comment). No minimum-radius rescale pass is ported from the
legacy renderer -- that was a legibility nicety, not a correctness
requirement, and dropping it keeps this module's arithmetic uniformly
"one pass, no post-hoc rewrite" rather than needing a second walk that
rebuilds every ``Primitive`` in the tree at a new scale.

Containment predicate: circle-in-circle, ``dist(parent, child) + r_child <=
r_parent - EPS``. ``EPS`` is far larger than any drift Decimal arithmetic
could plausibly introduce (the default context carries 28 significant
digits; ``EPS`` is ``1e-4``) -- see :data:`EPS` and
``tests/archetypes/test_enclosure.py``'s epsilon unit tests, including
tangent and touching circles that must read as NOT contained.
"""

from __future__ import annotations

import random
from decimal import Decimal
from fractions import Fraction

from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode
from lofbench.renderers.pipeline.registry import ARCHETYPE_REGISTRY

from . import _svg
from ._geometry import PI, polar_to_cartesian, stable_sin, stable_sqrt
from ._structural import flatten_tree

NAME = "enclosure"
VERSION = "1"
FAMILY = "enclosure"

# Circle-in-circle admission margin. Far larger than Decimal-context drift
# (the default context carries 28 significant digits, so drift is on the
# order of 1e-28) -- this is a real geometric margin, not a rounding fudge.
EPS = Decimal("0.0001")

ROOT_RADIUS = Decimal("0.9")  # matches the legacy renderer's implicit canvas radius
MIN_SCALE = Decimal("0.70")
RING_MARGIN = Decimal("0.85")


def _scale_for(n_children: int, depth: int) -> Decimal:
    """Adaptive per-branching-factor scale, ported from the legacy
    renderer's ``_calculate_scale`` with ``math.sqrt`` replaced by
    :func:`stable_sqrt`."""
    if n_children == 0:
        return Decimal(0)
    if n_children == 1:
        return MIN_SCALE
    base_scale = Decimal("0.75") / (Decimal(1) + Decimal("0.4") * stable_sqrt(Decimal(n_children)))
    depth_boost = min(Decimal("0.15"), Decimal(depth) * Decimal("0.03"))
    return min(MIN_SCALE, base_scale + depth_boost)


def _max_child_radius(parent_r: Decimal, n_children: int) -> Decimal:
    """Radial-overlap ceiling: the largest ``child_r`` for which no two
    adjacent siblings on the ring can overlap.

    The legacy renderer's ``max_child_r_radial`` bounded this using *arc
    length* between adjacent centres (``ring_radius * 2*pi/n >= 2*child_r``)
    as a stand-in for the true straight-line (chord) distance between them.
    Arc length is always >= chord length, so that bound is too loose --
    for ``n_children == 2`` in particular (adjacent siblings placed
    diametrically opposite, angle pi apart) it permits genuine overlap:
    verifying this migration against the whole generation set (M5's own
    acceptance criterion) caught exactly that as a cousin-circle geometric
    containment violation the legacy renderer's untested code never
    surfaced. Fixed here by using the true chord distance,
    ``2 * ring_radius * sin(pi / n_children)``, via :func:`stable_sin`
    rather than a bare ``math.sin`` -- solving
    ``ring_radius >= child_r / sin(pi / n_children)`` for ``child_r`` gives
    the bound below. This is a correctness fix over the legacy formula, not
    merely a platform-stability port, and is exactly the kind of divergence
    the doc's M5 acceptance criterion anticipates and allows.
    """
    half_angle = PI / Decimal(n_children)
    sin_half = stable_sin(half_angle)
    return parent_r * RING_MARGIN / (RING_MARGIN + Decimal(1) / sin_half)


def _child_position(
    i: int,
    n_children: int,
    parent_cx: Decimal,
    parent_cy: Decimal,
    parent_r: Decimal,
    child_r: Decimal,
) -> tuple[Decimal, Decimal]:
    if n_children == 1:
        return parent_cx, parent_cy
    ring_radius = (parent_r - child_r) * RING_MARGIN
    turn = Fraction(i, n_children)
    dx, dy = polar_to_cartesian(ring_radius, turn)
    return parent_cx + dx, parent_cy + dy


def _layout(
    nodes: tuple[FormNode, ...],
    parent_cx: Decimal,
    parent_cy: Decimal,
    parent_r: Decimal,
    depth: int,
) -> list[Primitive]:
    n = len(nodes)
    if n == 0:
        return []

    scale = _scale_for(n, depth)
    child_r = parent_r * scale
    if n > 1:
        child_r = min(child_r, _max_child_radius(parent_r, n))

    primitives: list[Primitive] = []
    for i, node in enumerate(nodes):
        cx, cy = _child_position(i, n, parent_cx, parent_cy, parent_r, child_r)
        children = tuple(_layout(node.children, cx, cy, child_r, depth + 1))
        primitives.append(
            Primitive(
                node_id=node.id,
                kind="circle",
                geom={"cx": cx, "cy": cy, "r": child_r, "depth": depth + 1},
                children=children,
            )
        )
    return primitives


def _distance(ax: Decimal, ay: Decimal, bx: Decimal, by: Decimal) -> Decimal:
    dx, dy = ax - bx, ay - by
    return stable_sqrt(dx * dx + dy * dy)


class EnclosureArchetype:
    name = NAME
    version = VERSION
    family = FAMILY
    modality = "spatial"

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        del rng  # no randomness in the base archetype; an injector would consume it
        primitives: list[Primitive] = []
        for prim in _layout(root.children, Decimal(0), Decimal(0), ROOT_RADIUS, 0):
            primitives.extend(flatten_tree(prim))
        scene = _svg.Scene(primitives=tuple(primitives))
        return BaseRender(modality="spatial", payload=scene, node_map=scene.node_map)

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        if parent.node_id is None or child.node_id is None:
            return False
        pg, cg = parent.geom, child.geom
        dist = _distance(pg["cx"], pg["cy"], cg["cx"], cg["cy"])
        return dist + cg["r"] <= pg["r"] - EPS


ARCHETYPE = EnclosureArchetype()
ARCHETYPE_REGISTRY["enclosure@1"] = ARCHETYPE


def to_svg(base: BaseRender, *, width: int = 640, height: int = 640) -> str:
    """Symbolic SVG emission, matching DB-3's archetype convention
    (``to_svg(base) -> str``). Wired into ``pipeline.emit``'s spatial
    branch via ``archetypes/__init__.py``'s ``to_svg`` lookup, same as
    every DB-3 spatial family. Fill is forced to ``none`` for model-facing
    stimuli, per the architecture doc's "colour cues are site pedagogy
    only" decision.
    """
    scene: _svg.Scene = base.payload
    cx0, cy0 = Decimal(width) / 2, Decimal(height) / 2
    canvas_scale = Decimal(min(width, height)) / 2
    body: list[str] = []
    for prim in sorted(scene.primitives, key=lambda p: p.geom.get("depth", 0)):
        g = prim.geom
        body.append(
            _svg.svg_circle(
                float(cx0 + g["cx"] * canvas_scale),
                float(cy0 + g["cy"] * canvas_scale),
                float(g["r"] * canvas_scale),
                fill="none",
                stroke="black",
                stroke_width=1.5,
            )
        )
    return _svg.svg_document(width, height, body)


ARCHETYPE.to_svg = to_svg  # type: ignore[attr-defined]
