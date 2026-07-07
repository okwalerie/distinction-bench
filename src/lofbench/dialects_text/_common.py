"""Shared Phase B plumbing for DB-2's text archetypes.

Not itself a dialect. Every archetype module in this package imports from
here to avoid five copies of the same boilerplate:

- ``TextArchetypeMixin``: the ``modality = "text"`` field and the
  ``predicate`` stub every text ``Archetype`` needs (unused for modality
  text -- ``induced_relation`` folds the parse-back tree instead; see
  ``lofbench.renderers.pipeline.archetype``). Mirrors the
  ``_IdentityTextArchetype`` fixture convention already established in
  ``tests/test_spec.py``.
- ``oxford_join_spanned``: the character-offset-tracking twin of the
  ``_oxford_join`` helper duplicated in ``prose.py`` and
  ``clause_embedding.py`` -- both dialects join a list of already-rendered
  (and already-spanned) child descriptions the same way, so the offset
  bookkeeping lives in one place.
"""

from __future__ import annotations

from lofbench.renderers.pipeline.archetype import Primitive


class TextArchetypeMixin:
    """Common fields/methods for a text-modality ``Archetype`` implementation."""

    modality = "text"

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        """Unused for modality "text" -- ``induced_relation`` folds the
        parse-back tree instead of calling this. Stubbed per the plan's open
        question on whether text archetypes need a real predicate; kept as
        an explicit ``False`` (not a raise) to match the ``test_spec.py``
        fixture convention DB-4's own M2 tests already established.
        """
        return False


def oxford_join_spanned(items: list[tuple[str, dict]]) -> tuple[str, dict]:
    """Join already-rendered ``(text, spans)`` items the way ``_oxford_join``
    joins plain strings, but also shift every child span by its item's
    position in the joined string.

    ``spans`` in each item and in the return value map node id -> (start,
    end) character offsets *relative to the start of that item's own text*.
    Mirrors the two dialects' identical string-joining rule: `"A and B"` for
    two items, Oxford-comma `"A, B, ..., and Z"` for three or more.
    """
    texts = [t for t, _ in items]
    offsets: list[int] = []
    if len(texts) == 2:
        offsets = [0, len(texts[0]) + len(" and ")]
        joined = f"{texts[0]} and {texts[1]}"
    else:
        pos = 0
        for i, t in enumerate(texts):
            offsets.append(pos)
            pos += len(t)
            if i < len(texts) - 2:
                pos += len(", ")
            elif i == len(texts) - 2:
                pos += len(", and ")
        joined = ", ".join(texts[:-1]) + f", and {texts[-1]}"

    combined_spans: dict = {}
    for (_, spans), offset in zip(items, offsets, strict=True):
        for node_id, (start, end) in spans.items():
            combined_spans[node_id] = (start + offset, end + offset)
    return joined, combined_spans
