"""``whitespace_jitter``: insert benign whitespace around parens tokens.

Ports ``CanonicalRenderer``'s optional ``spacing=True`` behaviour onto the
composed pipeline as a generic text injector, applicable to any text
archetype whose payload uses literal ``"("``/``")"`` for nesting (worked
example 2). It never touches a node id or the containment relation --
inserted whitespace is discarded by every parens-family reader, which
already ignores non-bracket characters.

Ordering note: this injector matches on literal ``"("``/``")"``. In a spec
that also runs ``bracket_swap``, ``whitespace_jitter`` must come first (as
in the architecture doc's worked example 2) -- once brackets are swapped to
non-parens glyphs, this injector's own char match no longer applies.
"""

from __future__ import annotations

import random

from ..pipeline.archetype import BaseRender
from ..pipeline.nodes import FormNode
from ..pipeline.registry import INJECTOR_REGISTRY


class WhitespaceJitterInjector:
    name = "whitespace_jitter"
    version = "1"
    modalities = frozenset({"text"})
    applicability = frozenset({"text"})  # generic text injector, per the 4 July amendment

    def apply(
        self, base: BaseRender, root: FormNode, rng: random.Random, **params: object
    ) -> BaseRender:
        amp = int(params.get("amp", 1))  # type: ignore[arg-type]
        prob = float(params.get("prob", 0.5))  # type: ignore[arg-type]
        s = str(base.payload)
        result: list[str] = []
        for i, char in enumerate(s):
            result.append(char)
            if i < len(s) - 1:
                nxt = s[i + 1]
                if (char == ")" and nxt in "()") or (char == "(" and nxt == "("):
                    if rng.random() < prob:
                        result.append(" " * amp)
        return BaseRender(modality="text", payload="".join(result), node_map=base.node_map)


INJECTOR_REGISTRY["whitespace_jitter"] = WhitespaceJitterInjector()
