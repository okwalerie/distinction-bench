"""Shared 1-D interval-nesting layout for ``rna_arc`` and ``paths_lite``.

Per the plan's "9. RNA arc" section: both families are, geometrically,
the same idea -- one arc (or "staple" shape) per node over a shared
baseline, a parent's span strictly containing its children's spans -- so
the nesting layout and containment predicate are written once here and
specialised only in drawing style by :mod:`rna_arc` and
:mod:`paths_lite`.

Layout: a post-order walk assigns each *leaf* a sequential integer
baseline position (the same idea as :mod:`trees`'s x-slot assignment).
Each node's raw span is the ``(min, max)`` leaf position across its
subtree. A literal ``(raw_lo, raw_hi)`` span would not be *strictly*
narrower than its parent's in a single-child chain (a very common LoF
shape, e.g. ``(())`` -- the inner mark has exactly the same one leaf as
the outer mark, so their raw spans would be identical). To guarantee
strict nesting even there, every node's span is padded by a fixed
``HALF``-width margin around its raw range and then shrunk inward by
``DEPTH_MARGIN * depth``: a strictly deeper node always subtracts a
strictly larger margin, so ``child.span`` nests inside ``parent.span``
with real slack even when their raw leaf ranges coincide exactly. Leaves
themselves get the same treatment starting from a raw range of
``(pos, pos)``, so every node -- leaf or internal -- has a genuine
nonzero-width span.

Containment predicate: ``interval_subset`` on the 1-D spans, exact
``Fraction`` comparison (:func:`_geometry.interval_subset`). Because
distinct nodes' raw ranges are disjoint contiguous integer partitions
(the same laminar argument as ``trees``' x-slots: adjacent leaves differ
by exactly 1, and ``2 * HALF < 1`` keeps padded-but-unrelated spans from
touching), this predicate is transitive-safe and matches
``containment_relation`` exactly.
"""

from __future__ import annotations

import random
from fractions import Fraction

from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode

from . import _svg
from ._geometry import Interval, interval_subset
from ._structural import flatten_tree

HALF = Fraction(2, 5)  # < 1/2: keeps padded spans of adjacent leaves from touching
DEPTH_MARGIN = Fraction(1, 50)  # per-depth strict-nesting margin


def _build(
    node: FormNode, depth: int, counter: list[int], kind: str
) -> tuple[Primitive, tuple[int, int]]:
    if not node.children:
        pos = counter[0]
        counter[0] += 1
        raw = (pos, pos)
        children_prims: tuple[Primitive, ...] = ()
    else:
        results = [_build(child, depth + 1, counter, kind) for child in node.children]
        children_prims = tuple(r[0] for r in results)
        child_raws = [r[1] for r in results]
        raw = (min(r[0] for r in child_raws), max(r[1] for r in child_raws))

    base_lo = Fraction(raw[0]) - HALF
    base_hi = Fraction(raw[1]) + HALF
    span: Interval = (base_lo + DEPTH_MARGIN * depth, base_hi - DEPTH_MARGIN * depth)

    prim = Primitive(
        node_id=node.id,
        kind=kind,
        geom={"span": span, "depth": depth, "raw": raw},
        children=children_prims,
    )
    return prim, raw


def build_nesting_base(root: FormNode, *, kind: str) -> BaseRender:
    """Shared ``build()`` body for both nesting archetypes. ``kind`` names
    the ``Primitive.kind`` so each module's ``to_svg`` can draw its own
    style (RNA arc vs. paths-lite staple) from otherwise-identical geom."""
    if not root.children:
        return BaseRender(modality="spatial", payload=_svg.Scene(primitives=()), node_map={})

    counter = [0]
    node_roots = [_build(child, 0, counter, kind)[0] for child in root.children]

    primitives: list[Primitive] = []
    for node_root in node_roots:
        primitives.extend(flatten_tree(node_root))

    scene = _svg.Scene(primitives=tuple(primitives))
    return BaseRender(modality="spatial", payload=scene, node_map=scene.node_map)


def nesting_predicate(parent: Primitive, child: Primitive) -> bool:
    """Shared ``predicate()`` body: exact 1-D interval subset on ``span``."""
    return interval_subset(child.geom["span"], parent.geom["span"])


def unused_rng(rng: random.Random) -> None:
    """No randomness in the base archetype; injectors would consume it.
    Named rather than a bare ``del`` so both wrapping modules read the same
    intent from one place."""
    del rng
