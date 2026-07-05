"""``trees@1``: node-link tree diagram, family ``trees``.

Layout: standard recursive tree layout, entirely integer/``Fraction``
arithmetic, zero transcendentals. A post-order walk assigns each leaf a
sequential integer x-slot; each internal node's x is the exact ``Fraction``
mean of its children's x. Depth gives y directly (``y = depth``, root at
the top).

Containment predicate: this is a node-link diagram, not spatial nesting --
a child's circle is not drawn inside its parent's circle the way enclosure
nests ovals. So the containment signal is structural adjacency in the
primitive tree (:func:`_structural.structural_contains`), which the plan
names explicitly as passing "by construction" and proving nothing
geometric on its own -- a layout bug that overlapped two node discs, drew
a child above its parent, or drew an edge not actually touching its two
node positions would still pass a purely structural check. So this module
also ships :func:`geometric_sanity`, checked in tests alongside
``verify()``: no two node discs overlap, and every child's y strictly
exceeds its parent's y. Both checks together are the containment evidence
for this family; structural adjacency alone is not.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "2. Trees" and
"Node ids and the containment predicate for trees and graph".

Injectors noted, not implemented (out of this task's scope per the plan):
layout jitter (rational x/y perturbation), distractor leaves
(``node_id=None``, dashed stroke, never reparented), node-glyph
substitution (style only).
"""

from __future__ import annotations

import random
from fractions import Fraction
from itertools import combinations

from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode

from . import _svg
from ._structural import flatten_tree, structural_contains

NAME = "trees"
VERSION = "1"
FAMILY = "trees"

# Fixed radius in slot units. < 0.5 guarantees no two discs overlap: any two
# distinct nodes differ by >= 1 in x (same depth) or >= 1 in y (different
# depth) -- see the module docstring's derivation -- so an all-pairs
# Euclidean distance is always >= 1 > 2 * RADIUS.
RADIUS = Fraction(2, 5)
X_SCALE = 60
Y_SCALE = 60
MARGIN = 20


def _assign_leaf_slots(node: FormNode, depth: int, counter: list[int]) -> Primitive:
    if not node.children:
        x = Fraction(counter[0])
        counter[0] += 1
        return Primitive(
            node_id=node.id, kind="circle", geom={"x": x, "y": Fraction(depth), "r": RADIUS}
        )

    child_prims = [_assign_leaf_slots(child, depth + 1, counter) for child in node.children]
    x = sum((p.geom["x"] for p in child_prims), Fraction(0)) / len(child_prims)
    return Primitive(
        node_id=node.id,
        kind="circle",
        geom={"x": x, "y": Fraction(depth), "r": RADIUS},
        children=tuple(child_prims),
    )


def _edges(root: Primitive) -> list[Primitive]:
    edges = []
    for child in root.children:
        edges.append(
            Primitive(
                node_id=None,
                kind="edge",
                geom={
                    "x1": root.geom["x"],
                    "y1": root.geom["y"],
                    "x2": child.geom["x"],
                    "y2": child.geom["y"],
                    "parent_id": root.node_id,
                    "child_id": child.node_id,
                },
            )
        )
        edges.extend(_edges(child))
    return edges


class TreesArchetype:
    name = NAME
    version = VERSION
    family = FAMILY
    modality = "spatial"

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        del rng  # no randomness in the base archetype; injectors would consume it
        if not root.children:
            return BaseRender(modality="spatial", payload=_svg.Scene(primitives=()), node_map={})

        counter = [0]
        node_roots = [_assign_leaf_slots(child, 0, counter) for child in root.children]
        edges: list[Primitive] = []
        for node_root in node_roots:
            edges.extend(_edges(node_root))

        primitives: list[Primitive] = []
        for node_root in node_roots:
            primitives.extend(flatten_tree(node_root))
        primitives.extend(edges)

        scene = _svg.Scene(primitives=tuple(primitives))
        return BaseRender(modality="spatial", payload=scene, node_map=scene.node_map)

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        return structural_contains(parent, child)


ARCHETYPE = TreesArchetype()


def geometric_sanity(base: BaseRender) -> tuple[bool, str]:
    """Disc non-overlap plus below-parent y-ordering, checked directly off
    the laid-out coordinates -- on top of, not instead of, the structural
    predicate above."""
    nodes = [p for p in base.node_map.values()]
    for a, b in combinations(nodes, 2):
        dx = a.geom["x"] - b.geom["x"]
        dy = a.geom["y"] - b.geom["y"]
        dist_sq = dx * dx + dy * dy
        min_dist = a.geom["r"] + b.geom["r"]
        if dist_sq < min_dist * min_dist:
            return False, f"node discs {a.node_id!r} and {b.node_id!r} overlap"

    for prim in base.node_map.values():
        for child in prim.children:
            if child.node_id is None:
                continue
            if child.geom["y"] <= prim.geom["y"]:
                return (
                    False,
                    f"child {child.node_id!r} is not laid out below parent {prim.node_id!r}",
                )

    return True, "ok"


def to_svg(base: BaseRender, *, width: int = 640, height: int = 480) -> str:
    """DB-3's own symbolic SVG emission -- viewing and sanity-checking now,
    PNG rasterisation through the pinned rasteriser is DB-4 M5 scope."""
    scene: _svg.Scene = base.payload
    body: list[str] = []
    for prim in scene.primitives:
        if prim.kind == "edge":
            g = prim.geom
            body.append(
                _svg.svg_line(
                    float(g["x1"]) * X_SCALE + MARGIN,
                    float(g["y1"]) * Y_SCALE + MARGIN,
                    float(g["x2"]) * X_SCALE + MARGIN,
                    float(g["y2"]) * Y_SCALE + MARGIN,
                    stroke="black",
                    stroke_width=1,
                )
            )
    for prim in scene.primitives:
        if prim.kind == "circle":
            g = prim.geom
            body.append(
                _svg.svg_circle(
                    float(g["x"]) * X_SCALE + MARGIN,
                    float(g["y"]) * Y_SCALE + MARGIN,
                    float(g["r"]) * X_SCALE,
                    fill="white",
                    stroke="black",
                    stroke_width=1.5,
                    stroke_dasharray="4,2" if prim.node_id is None else None,
                )
            )
    return _svg.svg_document(width, height, body)
