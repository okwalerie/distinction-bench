"""``parens@1``: the baseline text family, parenthesis-nested marks.

M5 item 1 (canonical) and item 3 (noisy_parens -> ``bracket_swap``) of
``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md``. See the architecture
doc's worked examples 1 and 2.
"""

from __future__ import annotations

import random

from ..noisy_parens import BRACKET_PAIRS
from ..pipeline.archetype import BaseRender, Primitive
from ..pipeline.nodes import FormNode, NodeId, iter_node_ids, nodes_from_parsed, nodes_to_form
from ..pipeline.registry import ARCHETYPE_REGISTRY
from ..pipeline.spec import DialectSpec, register_named_dialect

_ALL_OPEN_CHARS = frozenset(open_ for open_, _ in BRACKET_PAIRS)
_ALL_CLOSE_CHARS = frozenset(close for _, close in BRACKET_PAIRS)


def bracket_agnostic_string_to_form(s: str) -> list:
    """Parse-back that recognises ANY ``BRACKET_PAIRS`` glyph as an opener or
    closer, not just literal ``"()"`` -- the "bracket-agnostic reader" the
    architecture doc's worked example 2 calls for, so ``bracket_swap``'s
    swapped glyphs (possibly mismatched) still parse back to the true
    nesting. Everything else (a ``whitespace_jitter`` space, an injector's
    symbol text) is ignored, exactly like ``core.string_to_form`` already
    ignores non-paren characters.
    """
    stack: list[list] = [[]]
    for char in s:
        if char in _ALL_OPEN_CHARS:
            new_level: list = []
            stack[-1].append(new_level)
            stack.append(new_level)
        elif char in _ALL_CLOSE_CHARS:
            if len(stack) > 1:
                stack.pop()
    return stack[0]


def bracket_agnostic_reader(s: str) -> FormNode:
    """Adapts ``bracket_agnostic_string_to_form`` to the ``text_reader``
    shape ``induced_relation``/``verify`` expect: ``str -> FormNode``.
    """
    return nodes_from_parsed(bracket_agnostic_string_to_form(s))


def _node_map_from_root(root: FormNode) -> dict[NodeId, Primitive]:
    return {nid: Primitive(node_id=nid, kind="bracket", geom={}) for nid in iter_node_ids(root)}


class ParensArchetype:
    """The baseline text family: parenthesis-nested marks, serialised via the
    canonical (unspaced) grammar. ``whitespace_jitter`` and ``bracket_swap``
    compose on top without ever touching character positions.
    """

    name = "parens"
    version = "1"
    family = "parens"
    modality = "text"

    # M1/M2 review handoff F1: the parens family's own reader recognises any
    # BRACKET_PAIRS glyph as an opener/closer, not just literal "()", so a
    # render with bracket_swap applied still verifies. The canonical
    # "()"-only reader (the default when an archetype omits this attribute)
    # would otherwise see zero marks in a bracket-swapped payload.
    text_reader = staticmethod(bracket_agnostic_reader)

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        payload = nodes_to_form(root)
        return BaseRender(modality="text", payload=payload, node_map=_node_map_from_root(root))

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        return False  # text modality: induced_relation ignores pred entirely


ARCHETYPE_REGISTRY["parens@1"] = ParensArchetype()

# Worked example 1: canonical text, identity.
register_named_dialect(
    DialectSpec(dialect_id="parens.canonical", family="parens", archetype="parens@1")
)
# Whitespace-jittered canonical -- structure-preserving benign spacing only.
register_named_dialect(
    DialectSpec(
        dialect_id="parens.jitter-v1",
        family="parens",
        archetype="parens@1",
        injectors=[("whitespace_jitter", {"amp": 1})],
    )
)
# noisy_parens migration (M5 item 3): matched mode (one bracket pair per
# depth) and mismatched mode (independent open/close per mark).
register_named_dialect(
    DialectSpec(
        dialect_id="parens.noisy-v1",
        family="parens",
        archetype="parens@1",
        injectors=[("bracket_swap", {"mismatched": False})],
    )
)
register_named_dialect(
    DialectSpec(
        dialect_id="parens.noisy-mismatched-v1",
        family="parens",
        archetype="parens@1",
        injectors=[("bracket_swap", {"mismatched": True})],
    )
)
