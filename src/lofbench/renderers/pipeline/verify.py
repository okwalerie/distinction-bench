"""The one predicate used between every stage: does the render still encode
the same containment as the parsed form?

See ``.lattice/notes/rendering-architecture-2026-07-04.md``,
"Structure-preservation verification".
"""

from __future__ import annotations

from typing import Any

from .archetype import BaseRender, induced_relation
from .nodes import FormNode, containment_relation, relation_hash


def verify(base: BaseRender, root: FormNode, pred: Any) -> tuple[bool, str]:
    """Equality of input and render relation hashes is the isomorphism proof.

    M1 skeleton: called once, after the archetype builds. Per-injector
    re-verification with resample-or-drop on violation, and the full
    ``render_provenance`` schema, are M4 scope.
    """
    observed = induced_relation(base, pred)
    observed_hash = relation_hash(observed)
    expected_hash = relation_hash(containment_relation(root))
    return observed_hash == expected_hash, observed_hash
