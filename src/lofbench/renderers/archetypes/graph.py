"""``graph@1``: nodes on a ring, family ``graph``.

Layout: one node per ``FormNode``, placed at N equally spaced angles
around a ring. Angles are exact ``Fraction``s of a full turn (``k/N``);
converted to Cartesian only through :func:`_geometry.polar_to_cartesian`,
the same stable-trig helper the enclosure and map-centred families reuse
-- called out explicitly here per the plan. Edges are drawn as chords
between parent and child ring positions (a circular dendrogram look).

Containment predicate: like trees, this is not spatial nesting, so the
containment signal is structural adjacency in the primitive tree
(:func:`_structural.structural_contains`). Ring position is not itself the
containment signal -- the drawn edge is -- so this module also ships
:func:`geometric_sanity`: every drawn edge's two endpoints must coincide,
within a small float epsilon (post stable-trig conversion), with the
parent's and child's actual ring positions. This is on top of, not instead
of, the structural check.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "4. Graph".

Injectors noted, not implemented (out of this task's scope per the plan):
ring-position jitter (bounded ``Fraction`` angle delta -- safe because the
predicate is edge-based, not position-based), arc-curvature style
variation, distractor isolated nodes (``node_id=None``, no edges).
"""

from __future__ import annotations

import random
from decimal import Decimal
from fractions import Fraction

from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode

from . import _svg
from ._geometry import polar_to_cartesian
from ._structural import flatten_tree, structural_contains

NAME = "graph"
VERSION = "1"
FAMILY = "graph"

RADIUS = 200.0  # ring radius, screen-scale units (post stable-trig, cosmetic)
NODE_R = 10.0
ENDPOINT_EPS = 1e-9


def _count_nodes(node: FormNode) -> int:
    return 1 + sum(_count_nodes(child) for child in node.children)


def _place(node: FormNode, index: list[int], total: int) -> Primitive:
    turn = Fraction(index[0], total)
    index[0] += 1
    x, y = polar_to_cartesian(Decimal(str(RADIUS)), turn)
    child_prims = tuple(_place(child, index, total) for child in node.children)
    return Primitive(
        node_id=node.id,
        kind="node",
        geom={"turn": turn, "x": float(x), "y": float(y), "r": NODE_R},
        children=child_prims,
    )


def _edges(prim: Primitive) -> list[Primitive]:
    edges = []
    for child in prim.children:
        edges.append(
            Primitive(
                node_id=None,
                kind="edge",
                geom={
                    "parent_id": prim.node_id,
                    "child_id": child.node_id,
                    "x1": prim.geom["x"],
                    "y1": prim.geom["y"],
                    "x2": child.geom["x"],
                    "y2": child.geom["y"],
                },
            )
        )
        edges.extend(_edges(child))
    return edges


class GraphArchetype:
    name = NAME
    version = VERSION
    family = FAMILY
    modality = "spatial"

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        del rng
        if not root.children:
            return BaseRender(modality="spatial", payload=_svg.Scene(primitives=()), node_map={})

        total = sum(_count_nodes(child) for child in root.children)
        index = [0]
        node_roots = [_place(child, index, total) for child in root.children]

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


ARCHETYPE = GraphArchetype()


def geometric_sanity(base: BaseRender) -> tuple[bool, str]:
    """Every drawn edge's endpoints coincide, within a small float epsilon,
    with the parent's and child's actual ring positions as recorded in
    ``node_map`` -- looked up independently of the edge's own copied
    coordinates, so a bug that wired an edge to the wrong position is
    actually catchable, not tautological."""
    node_map = base.node_map
    for edge in _find_edges(base):
        parent_id, child_id = edge.geom["parent_id"], edge.geom["child_id"]
        px, py = node_map[parent_id].geom["x"], node_map[parent_id].geom["y"]
        cx, cy = node_map[child_id].geom["x"], node_map[child_id].geom["y"]
        if abs(px - edge.geom["x1"]) > ENDPOINT_EPS or abs(py - edge.geom["y1"]) > ENDPOINT_EPS:
            return False, f"edge does not start at parent {parent_id!r}'s ring position"
        if abs(cx - edge.geom["x2"]) > ENDPOINT_EPS or abs(cy - edge.geom["y2"]) > ENDPOINT_EPS:
            return False, f"edge does not end at child {child_id!r}'s ring position"

    return True, "ok"


def _find_edges(base: BaseRender) -> list[Primitive]:
    scene: _svg.Scene = base.payload
    return [p for p in scene.primitives if p.kind == "edge"]


def to_svg(base: BaseRender, *, width: int = 640, height: int = 640) -> str:
    scene: _svg.Scene = base.payload
    cx, cy = width / 2, height / 2
    body: list[str] = []
    for prim in scene.primitives:
        if prim.kind == "edge":
            g = prim.geom
            body.append(
                _svg.svg_line(
                    cx + g["x1"],
                    cy + g["y1"],
                    cx + g["x2"],
                    cy + g["y2"],
                    stroke="black",
                    stroke_width=1,
                )
            )
    for prim in scene.primitives:
        if prim.kind == "node":
            g = prim.geom
            body.append(
                _svg.svg_circle(
                    cx + g["x"], cy + g["y"], g["r"], fill="white", stroke="black", stroke_width=1.5
                )
            )
    return _svg.svg_document(width, height, body)
