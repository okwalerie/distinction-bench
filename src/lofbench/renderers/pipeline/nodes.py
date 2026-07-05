"""Canonical structural tree over parsed Laws of Form strings.

Wraps ``lofbench.core.string_to_form``/``form_to_string`` into a stable,
addressable node tree so every dialect can be checked against the one
relation that matters: containment. See
``.lattice/notes/rendering-architecture-2026-07-04.md``, "Core abstractions".
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import blake2b

from lofbench.core import form_to_string, string_to_form

NodeId = str


@dataclass(frozen=True)
class FormNode:
    """One mark in the parsed tree, or the synthetic root wrapper.

    ``id`` is a stable, dialect-invariant structural path such as
    ``"0.1.0"``: top-level mark 0, its child 1, that child's child 0.

    ``form_to_nodes`` always returns a wrapper root carrying the sentinel id
    ``""``. The wrapper is not itself a mark -- it exists only so a form
    with zero, one, or many top-level marks has a single return value. It
    never appears in ``containment_relation`` or a ``BaseRender.node_map``.
    """

    id: NodeId
    children: tuple[FormNode, ...] = ()


def _build_children(marks: list, prefix: str) -> tuple[FormNode, ...]:
    nodes = []
    for i, mark_children in enumerate(marks):
        node_id = f"{prefix}.{i}" if prefix else str(i)
        nodes.append(FormNode(id=node_id, children=_build_children(mark_children, node_id)))
    return tuple(nodes)


def nodes_from_parsed(parsed: list) -> FormNode:
    """Build a node tree directly from an already-parsed nested-list structure
    (the shape ``string_to_form`` returns), skipping the string-parsing step.

    Used by parse-back readers that recognise a different bracket grammar
    than the canonical parens grammar -- for example the parens family's
    bracket-agnostic reader (see ``lofbench.renderers.archetypes.parens``),
    which parses with its own bracket-glyph set and then hands the resulting
    nested list here to build a real ``FormNode`` tree (M4/M5, handoff F1).
    """
    return FormNode(id="", children=_build_children(parsed, ""))


def form_to_nodes(form_string: str) -> FormNode:
    """Parse ``form_string`` into a stable node tree. Wraps ``string_to_form``."""
    parsed = string_to_form(form_string)
    return nodes_from_parsed(parsed)


def _node_to_list(node: FormNode) -> list:
    return [_node_to_list(child) for child in node.children]


def nodes_to_form(root: FormNode) -> str:
    """Serialise a node tree back to canonical form syntax.

    Left inverse of ``form_to_nodes``: ``nodes_to_form(form_to_nodes(s)) ==
    s`` for every canonical ``s`` -- ``string_to_form`` already discards
    incidental whitespace, so the round trip is exact against
    ``form_to_string``'s own canonical (unspaced) serialisation.
    """
    return form_to_string(_node_to_list(root))


def iter_node_ids(node: FormNode) -> Iterator[NodeId]:
    """Yield every real mark id in ``node``'s subtree, root wrapper excluded.

    Used to assert a ``BaseRender.node_map`` covers every structural node
    exactly once -- the admission rule the architecture doc calls out.
    """
    for child in node.children:
        yield child.id
        yield from iter_node_ids(child)


def containment_relation(root: FormNode) -> frozenset[tuple[NodeId, NodeId]]:
    """Transitive ancestor closure over real marks.

    The one relation every dialect must preserve. The synthetic root
    wrapper (id ``""``) never appears as an ancestor or descendant:
    top-level marks are siblings, not contained in one another, and the
    wrapper carries no structure of its own.
    """
    pairs: set[tuple[NodeId, NodeId]] = set()

    def walk(node: FormNode) -> None:
        if node.id:
            for descendant_id in iter_node_ids(node):
                pairs.add((node.id, descendant_id))
        for child in node.children:
            walk(child)

    walk(root)
    return frozenset(pairs)


def relation_hash(rel: frozenset[tuple[NodeId, NodeId]]) -> str:
    """blake2b over the sorted relation. Stable and diffable across suite versions."""
    ordered = sorted(rel)
    payload = "\n".join(f"{ancestor}\x00{descendant}" for ancestor, descendant in ordered)
    return blake2b(payload.encode(), digest_size=16).hexdigest()
