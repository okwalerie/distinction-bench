"""Plain-English containment prose dialect (`prose@1`, family `prose`).

Phase A only: plain `render`/`parse` functions, not yet an `Archetype`. See
`lofbench.dialects_text` package docstring for the phasing rationale.

Grammar pin (binding; this is the formal spec required before Phase A code
was written, resolving the sibling-joining rule, the collapse boundary,
and numeral handling the plan left implicit)
------------------------------------------------------------------------
Vocabulary is fixed for Phase A (`box`, `holding`, `empty`, `thing`/
`things`, `namely`, `and`, `nothing`); only the `synonym_jitter` injector
(Phase B, not implemented here) varies wording, per "no unseeded stylistic
branching inside the archetype itself".

Numerals: `NUMERAL_WORDS = ("zero", "one", ..., "nine")`, covering 0
through 9 (`DIFFICULTY_CONFIGS`'s `max_width` never exceeds 9).

1. A leaf mark (no children) is a *singular description*:
   `"an empty box"`.
2. A non-leaf mark is a singular description: `"a box holding " +
   GROUP(children)`, where `GROUP` is defined next.
3. `GROUP(marks)` describes a list of one or more sibling marks (used both
   for a mark's children and for the top-level list of forms):
   - exactly one mark: its own singular description (points 1-2), with no
     numeral -- this is the two-child worked example's base case, e.g.
     `(()())` is one top-level mark holding two leaves.
   - two or more marks, **and every one of them is a bare leaf**: collapse
     to a counted plural noun phrase, `f"{NUMERAL_WORDS[n]} empty boxes"`
     (e.g. `"two empty boxes"`). This is the *only* condition that
     collapses -- the plan leaves "which sibling patterns collapse"
     unpinned; this plan pins it to "count is not part of the singular
     description grammar, so only a group whose members are structurally
     identical *and leaves* can be safely summarised by a bare count
     without losing information; anything else must render each member
     explicitly.
   - two or more marks, otherwise (at least one has children, or they
     differ in shape): an explicit, **count-prefixed** list: `f"{NUMERAL_
     WORDS[n]} things: " + oxford_join(singular description of each
     member)`, where `oxford_join` is `"A and B"` for two members, or
     `"A, B, ..., and Z"` (Oxford comma) for three or more.

     The count prefix is not cosmetic: it is what keeps the grammar
     unambiguously parseable at arbitrary nesting depth. Plain
     `"A and B and C"` joining (no prefix) is ambiguous to parse back
     once a member of the list is itself a multi-child mark holding its
     own "and"-joined sub-list, because a bare recursive-descent reader
     cannot tell, from token content alone, whether a subsequent "and"
     continues the inner mark's own list or the outer list. Reading the
     count first turns "keep consuming while I see a separator" (ambiguous)
     into "consume exactly N descriptions" (unambiguous), because each
     description is self-terminating by the same rule at every depth.
     This is a deliberate deviation from the plan's flat worked-example
     prose ("two empty boxes" is unaffected and still matches the plan's
     example verbatim); it is required to make the "explicit list"
     branch the plan calls for actually invertible.
4. Top level: `render(form_string)` is `"nothing."` for a void form
   (`string_to_form(form_string) == []`), else `GROUP(top_level_marks) +
   "."`. Exactly one trailing period, at the very end only -- inner
   clauses never get their own period.
5. Determinism: no randomness. `rng` is accepted for interface parity with
   the other Phase A dialects; `synonym_jitter` (Phase B) is what would
   consume it.

Round-trip: a small recursive-descent parser keyed on the closed
vocabulary above. `parse_group` first checks for the two bounded-length
patterns (`NUMERAL empty box(es)` and `NUMERAL things: ...`) before
falling back to a single singular description; `parse_singular` matches
`"an empty box"` or `"a box holding" + GROUP`. Because every branch is
self-terminating (either a fixed three-token pattern, or a counted loop
of exactly N recursive calls), nesting to any depth is unambiguous.
"""

from __future__ import annotations

import random

from lofbench.core import string_to_form
from lofbench.dialects_text._common import TextArchetypeMixin, oxford_join_spanned
from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode, nodes_from_parsed

# `synonym_jitter` (Phase B) vocabulary. `parse_prose`'s reader below is
# widened to accept this whole closed set (not just the base "box"/
# "holding") so it round-trips jittered output too; the base render
# (`render_prose`/`_render_singular`/`_render_group`, unchanged from Phase
# A) still only ever emits "box"/"holding"/"boxes", so this widening is
# additive and does not change Phase A's own test outcomes.
NOUN_SYNONYMS = ("box", "container", "vessel", "crate")
NOUN_PLURALS = {
    "box": "boxes",
    "container": "containers",
    "vessel": "vessels",
    "crate": "crates",
}
VERB_SYNONYMS = ("holding", "containing", "enclosing")
_ALL_PLURALS = frozenset(NOUN_PLURALS.values())

NUMERAL_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
)


def _oxford_join(parts: list[str]) -> str:
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _render_singular(mark: list) -> str:
    if not mark:
        return "an empty box"
    return "a box holding " + _render_group(mark)


def _render_group(marks: list) -> str:
    n = len(marks)
    if n == 1:
        return _render_singular(marks[0])
    if all(not m for m in marks):
        return f"{NUMERAL_WORDS[n]} empty boxes"
    joined = _oxford_join([_render_singular(m) for m in marks])
    return f"{NUMERAL_WORDS[n]} things: {joined}"


def render_prose(form_string: str, rng: random.Random | None = None) -> str:
    """Render a form string as plain-English containment prose.

    Args:
        form_string: canonical parenthesis form, e.g. "(()())" ("" for void).
        rng: unused; accepted for interface parity (see grammar pin, point 5).

    Returns:
        A single English sentence describing the containment structure.
    """
    marks = string_to_form(form_string)
    if not marks:
        return "nothing."
    return _render_group(marks) + "."


def _tokenize(text: str) -> list[str]:
    text = text.replace(":", " : ").replace(",", " , ").replace(".", " . ")
    return text.split()


def _parse_singular(tokens: list[str], pos: int) -> tuple[list, int]:
    if (
        pos + 2 < len(tokens)
        and tokens[pos] == "an"
        and tokens[pos + 1] == "empty"
        and tokens[pos + 2] in NOUN_SYNONYMS
    ):
        return [], pos + 3
    if (
        pos + 2 < len(tokens)
        and tokens[pos] == "a"
        and tokens[pos + 1] in NOUN_SYNONYMS
        and tokens[pos + 2] in VERB_SYNONYMS
    ):
        return _parse_group(tokens, pos + 3)
    snippet = tokens[pos : pos + 5]
    raise ValueError(f"cannot parse prose singular description at token {pos}: {snippet!r}")


def _parse_group(tokens: list[str], pos: int) -> tuple[list, int]:
    if (
        pos + 2 < len(tokens)
        and tokens[pos] in NUMERAL_WORDS
        and tokens[pos + 1] == "empty"
        and tokens[pos + 2] in _ALL_PLURALS
    ):
        n = NUMERAL_WORDS.index(tokens[pos])
        return [[] for _ in range(n)], pos + 3

    if (
        pos + 2 < len(tokens)
        and tokens[pos] in NUMERAL_WORDS
        and tokens[pos + 1] == "things"
        and tokens[pos + 2] == ":"
    ):
        n = NUMERAL_WORDS.index(tokens[pos])
        pos += 3
        marks: list = []
        for i in range(n):
            mark, pos = _parse_singular(tokens, pos)
            marks.append(mark)
            if i < n - 1:
                if tokens[pos] == ",":
                    pos += 1
                if tokens[pos] == "and":
                    pos += 1
        return marks, pos

    mark, pos = _parse_singular(tokens, pos)
    return [mark], pos


def parse_prose(rendered: str) -> list:
    """Invert `render_prose` back to the nested-list form."""
    if rendered == "nothing.":
        return []
    tokens = _tokenize(rendered)
    marks, pos = _parse_group(tokens, 0)
    return marks


# =============================================================================
# Phase B: Archetype + Injector wrapping (task_01KWQKYTN19RZN2BFKAAGQCFKA, Phase B)
# =============================================================================
#
# `_render_singular_spanned`/`_render_group_spanned` mirror
# `_render_singular`/`_render_group` above exactly (same grammar, same
# string output with `jitter=False`), but thread a `FormNode` (for its
# `id`) through the recursion and return a `{node_id: (start, end)}` span
# dict alongside the text, computed incrementally as strings are
# concatenated. When two or more leaf siblings collapse to one shared
# phrase ("two empty boxes"), every one of those sibling ids maps to the
# *same* span -- the text genuinely does not distinguish them further, so
# this is not a loss of information, just an honest reflection of what the
# rendered string can support.


def _render_singular_spanned(
    node: FormNode, rng: random.Random, jitter: bool
) -> tuple[str, dict[str, tuple[int, int]]]:
    noun = rng.choice(NOUN_SYNONYMS) if jitter else "box"
    if not node.children:
        text = f"an empty {noun}"
        return text, {node.id: (0, len(text))}
    verb = rng.choice(VERB_SYNONYMS) if jitter else "holding"
    prefix = f"a {noun} {verb} "
    group_text, group_spans = _render_group_spanned(node.children, rng, jitter)
    text = prefix + group_text
    offset = len(prefix)
    spans = {nid: (s + offset, e + offset) for nid, (s, e) in group_spans.items()}
    spans[node.id] = (0, len(text))
    return text, spans


def _render_group_spanned(
    nodes: tuple[FormNode, ...], rng: random.Random, jitter: bool
) -> tuple[str, dict[str, tuple[int, int]]]:
    n = len(nodes)
    if n == 1:
        return _render_singular_spanned(nodes[0], rng, jitter)
    if all(not nd.children for nd in nodes):
        noun_plural = NOUN_PLURALS[rng.choice(NOUN_SYNONYMS)] if jitter else "boxes"
        text = f"{NUMERAL_WORDS[n]} empty {noun_plural}"
        spans = {nd.id: (0, len(text)) for nd in nodes}
        return text, spans
    items = [_render_singular_spanned(nd, rng, jitter) for nd in nodes]
    joined, joined_spans = oxford_join_spanned(items)
    prefix = f"{NUMERAL_WORDS[n]} things: "
    offset = len(prefix)
    spans = {nid: (s + offset, e + offset) for nid, (s, e) in joined_spans.items()}
    text = prefix + joined
    return text, spans


def _build_spanned(root: FormNode, rng: random.Random, *, jitter: bool = False) -> BaseRender:
    if not root.children:
        return BaseRender(modality="text", payload="nothing.", node_map={})
    text, spans = _render_group_spanned(root.children, rng, jitter)
    payload = text + "."
    node_map = {
        nid: Primitive(node_id=nid, kind="span", geom={"start": s, "end": e})
        for nid, (s, e) in spans.items()
    }
    return BaseRender(modality="text", payload=payload, node_map=node_map)


class ProseArchetype(TextArchetypeMixin):
    """``prose@1``, family ``prose``. Wraps this module's render/parse pair;
    parse-back (``parse_prose``) is the structure verification.
    """

    name = "prose@1"
    version = "1"
    family = "prose"

    # M4: `parse_prose`'s closed-vocabulary reader recognises this
    # dialect's own English grammar, not literal parens -- without this,
    # the default canonical-parens reader sees zero marks in a prose
    # payload and `structure_verified` is always False.
    text_reader = staticmethod(lambda s: nodes_from_parsed(parse_prose(s)))

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        return _build_spanned(root, rng, jitter=False)


class SynonymJitterInjector:
    """``synonym_jitter``: swap "box"/"holding" for a closed-vocabulary
    synonym at every occurrence. Rebuilds fresh from ``root`` via
    ``_build_spanned(jitter=True)``; ``parse_prose``'s widened vocabulary
    (see module docstring note above `NOUN_SYNONYMS`) still round-trips it.
    """

    name = "synonym_jitter"
    version = "1"
    modalities = frozenset({"text"})
    applicability = frozenset({"prose@1"})

    def apply(
        self, base: BaseRender, root: FormNode, rng: random.Random, **params: object
    ) -> BaseRender:
        return _build_spanned(root, rng, jitter=True)
