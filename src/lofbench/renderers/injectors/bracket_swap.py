"""``bracket_swap``: substitute parenthesis glyphs for containment-preserving
bracket variants.

Ports ``NoisyParensRenderer`` (M5 item 3) onto the composed pipeline as a
structural injector: it walks the parsed tree (``root``), not a
character-scan depth counter, so ``mismatched`` is an ordinary parameter,
not a special case. Matched mode assigns one bracket pair per structural
depth, lazily, in pre-order -- exactly ``NoisyParensRenderer``'s
``depth_to_brackets`` assignment, keyed on node-id depth
(``node_id.count(".")``) instead of a running character-scan counter.
Mismatched mode draws an independent open and close glyph per mark, in the
same pre-order draw sequence as the character scan (open at entry, close at
exit), so given the same rng state this produces byte-identical output to
the legacy renderer.

Known composability gap: this injector rebuilds ``payload`` fresh from
``root``, so a ``whitespace_jitter`` staged before it in one spec's injector
list has no visible effect on the final string (worked example 2 composes
the two; closing this gap needs a payload-preserving substitution that walks
``base.payload`` by primitive position rather than re-deriving it from
``root``, which is out of scope for this pass -- see the DB-4 M3-M5 lattice
comment). Register ``whitespace_jitter`` and ``bracket_swap`` in separate
dialects until that lands.
"""

from __future__ import annotations

import random

from ..noisy_parens import BRACKET_PAIRS
from ..pipeline.archetype import BaseRender
from ..pipeline.nodes import FormNode
from ..pipeline.registry import INJECTOR_REGISTRY


def _render(
    node: FormNode,
    mismatched: bool,
    rng: random.Random,
    depth_pairs: dict[int, tuple[str, str]],
) -> str:
    parts: list[str] = []
    for child in node.children:
        child_depth = child.id.count(".")
        if mismatched:
            open_ch = rng.choice(BRACKET_PAIRS)[0]
        else:
            if child_depth not in depth_pairs:
                depth_pairs[child_depth] = rng.choice(BRACKET_PAIRS)
            open_ch = depth_pairs[child_depth][0]

        inner = _render(child, mismatched, rng, depth_pairs)

        if mismatched:
            close_ch = rng.choice(BRACKET_PAIRS)[1]
        else:
            close_ch = depth_pairs[child_depth][1]

        parts.append(f"{open_ch}{inner}{close_ch}")
    return "".join(parts)


class BracketSwapInjector:
    name = "bracket_swap"
    version = "1"
    modalities = frozenset({"text"})
    applicability = frozenset({"parens"})  # archetype-specific per the ECS amendment

    def apply(
        self, base: BaseRender, root: FormNode, rng: random.Random, **params: object
    ) -> BaseRender:
        mismatched = bool(params.get("mismatched", False))
        payload = _render(root, mismatched, rng, depth_pairs={})
        return BaseRender(modality="text", payload=payload, node_map=base.node_map)


INJECTOR_REGISTRY["bracket_swap"] = BracketSwapInjector()
