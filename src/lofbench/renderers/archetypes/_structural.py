"""Structural helpers shared across archetypes: subtree weighting for
proportional layout, and the descendant-set predicate for the node-link
families (trees, graph) where geometric nesting is not the containment
signal.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "Node ids and
the containment predicate for trees and graph".
"""

from __future__ import annotations

from lofbench.renderers.pipeline.archetype import Primitive
from lofbench.renderers.pipeline.nodes import FormNode


def leaf_weight(node: FormNode) -> int:
    """Number of leaves in ``node``'s subtree, minimum 1. Used by the
    weighted-subdivision families (map, map-centred, rooms) so a
    heavier-branching child gets proportionally more space."""
    if not node.children:
        return 1
    return sum(leaf_weight(child) for child in node.children)


def descendant_ids(prim: Primitive) -> frozenset[str]:
    """Every real node id in ``prim``'s subtree, found by walking
    ``prim.children`` recursively (the primitive tree mirrors ``FormNode``
    by construction for trees and graph). Decorative primitives
    (``node_id is None``) are skipped but still walked through."""
    ids: set[str] = set()
    for child in prim.children:
        if child.node_id is not None:
            ids.add(child.node_id)
        ids |= descendant_ids(child)
    return frozenset(ids)


def structural_contains(parent: Primitive, child: Primitive) -> bool:
    """The "structural adjacency" predicate the plan names explicitly as
    passing by construction and verifying nothing geometric on its own:
    ``child`` is a descendant of ``parent`` in the primitive tree, which
    mirrors ``FormNode`` exactly. Trees and graph pair this with a
    geometric sanity check (see each module's ``geometric_sanity``) so a
    passing verification is evidence about the drawn picture, not just
    about which Python object references which.
    """
    if parent.node_id is None or child.node_id is None:
        return False
    return child.node_id in descendant_ids(parent)


def flatten_tree(root: Primitive) -> list[Primitive]:
    """Pre-order flatten of a node-primitive tree (root first, then each
    child's subtree), for building a ``Scene``'s draw list."""
    out = [root]
    for child in root.children:
        out.extend(flatten_tree(child))
    return out
