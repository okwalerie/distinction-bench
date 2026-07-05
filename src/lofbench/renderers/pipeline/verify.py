"""The one predicate used between every stage: does the render still encode
the same containment as the parsed form?

See ``.lattice/notes/rendering-architecture-2026-07-04.md``,
"Structure-preservation verification".
"""

from __future__ import annotations

from typing import Any

from .archetype import BaseRender, induced_relation
from .nodes import FormNode, containment_relation, relation_hash


def verify(
    base: BaseRender, root: FormNode, pred: Any, *, text_reader: Any = None
) -> tuple[bool, str]:
    """Equality of input and render relation hashes is the isomorphism proof.

    Called after the archetype builds and after every injector (M4): the
    caller re-verifies at each stage and resamples or drops an injector that
    breaks it, per ``ComposedRenderer.render``.

    ``text_reader`` is forwarded to ``induced_relation`` for text-modality
    archetypes whose emitted grammar the canonical parens reader cannot
    parse back -- for example the parens family once ``bracket_swap`` has
    substituted glyphs (M1/M2 review handoff F1). Spatial verification
    ignores it.
    """
    observed = induced_relation(base, pred, text_reader=text_reader)
    observed_hash = relation_hash(observed)
    expected_hash = relation_hash(containment_relation(root))
    return observed_hash == expected_hash, observed_hash
