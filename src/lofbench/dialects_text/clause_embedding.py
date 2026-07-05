"""Nested clause embedding dialect (`clause_embedding@1`, family `embedding`).

Phase A only: plain `render`/`parse` functions, not yet an `Archetype`. See
`lofbench.dialects_text` package docstring for the phasing rationale.

Review-fix context: the plan's original worked example ("the fox that
watches the rabbit that hides and the mouse that hides") right-branches --
each mark's children are appended after its own head clause -- which does
not reproduce the depth-compounding processing difficulty of genuine
centre-embedding (Chomsky and Miller's result nests a relative clause
inside the *subject* position, before the main verb, as in "the rat the
cat the dog chased chased squeaked"). This module implements genuine
centre-embedding instead, and pins the one thing the plan explicitly left
to the implementer: how width (a mark with more than one child) maps onto
a structure whose citation is about a single linear embedding chain.

Grammar pin (binding; this is the formal spec required before Phase A code
was written)
------------------------------------------------------------------------
Vocabulary is fixed for Phase A: noun `"creature"`, transitive verb
`"watches"` (signals "an embedded clause follows"), intransitive verb
`"sleeps"` (signals "no further embedding" -- a leaf). Verb identity
within a transitivity class is cosmetic and is exactly what
`vocabulary_jitter` (Phase B, not implemented here) would vary; verb
*class* is the structural signal and is fixed by the grammar itself, not
left to chance.

`NUMERAL_WORDS = ("zero", "one", ..., "nine")`, covering the count 2
through 9 that width can take (`max_width` never exceeds 9; 0 and 1 are
never reached by the numeral branch -- see point 2).

A single mark renders as a *clause*, `f"{np} {verb}"`, built recursively:

1. A leaf mark (no children): `np = "the creature"`, `verb = "sleeps"`.
2. A mark with exactly one child (the pure centre-embedding case, the one
   the psycholinguistic citation is actually about): recursively render
   the child as its own full clause `f"{child_np} {child_verb}"`, then
   `np = f"the creature that {child_np} {child_verb}"`, `verb =
   "watches"`. This nests the child's entire clause -- including its own
   trailing verb -- inside the subject position, *before* this level's
   own verb, which is what makes verbs stack at the end in reverse
   embedding order (innermost first): e.g. depth 3 renders "the creature
   that the creature that the creature sleeps watches watches.", where
   the three verbs read leaf-to-root, exactly mirroring the cited
   example's "chased chased squeaked" verb stack.
3. A mark with two or more children (width; the pinned generalisation):
   render each child as its own full clause, then join them with a
   **count-prefixed** conjoined subject: `np = f"the creature that
   {NUMERAL_WORDS[n]} creatures namely {oxford_join(child_clauses)}"`,
   `verb = "watches"` (a single verb for this level, appended once after
   the whole conjoined list, not once per child). The count prefix is
   required for the same reason as in the `prose` dialect: a plain
   "and"-joined list of child clauses, each of which may itself contain
   "and", is not parseable back unambiguously by a bare recursive
   descent reader once nesting is more than one level deep, because nothing
   marks where an inner list's separators end and an outer list's begin.
   Reading the count up front makes list length a known quantity instead
   of something inferred from lookahead, at every depth.
4. Top level: `form_string` may hold zero, one, or more *top-level* marks
   with no shared parent. A void form renders as `"nothing."`. Otherwise
   each top-level mark renders as its own full clause plus a period, and
   sentences are joined with a single space: `f"{clause}." for clause in
   clauses` joined by `" "`.
5. Determinism: no randomness in Phase A -- the skeleton, like
   `tree_indent`, is a pure function of tree shape. `rng` is accepted for
   interface parity; `vocabulary_jitter` (Phase B) is what would consume
   it.

Round-trip: a recursive-descent parser keyed on `"that"` (marks an
embedded clause follows, either the single-child form or the counted
conjoined-subject form) and a `NUMERAL creatures namely` lookahead
(disambiguates width from the single-child case). Because every branch is
either a fixed leaf pattern or a counted loop of exactly N recursive
calls, nesting to any depth and width up to 9 is unambiguous. The verb
word itself is never inspected for identity, only consumed as a single
token -- this is what keeps parsing robust to `vocabulary_jitter` varying
verb choice within a class, even though that injector is not built here.
"""

from __future__ import annotations

import random

from lofbench.core import string_to_form
from lofbench.dialects_text._common import TextArchetypeMixin, oxford_join_spanned
from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode, nodes_from_parsed

NOUN = "creature"
TRANS_VERB = "watches"
INTRANS_VERB = "sleeps"

# `vocabulary_jitter` (Phase B) vocabulary. `parse_clause_embedding`'s
# reader below is widened to accept this whole closed set (not just the
# base "creature") so it round-trips jittered output too; the base render
# (`render_clause_embedding`/`_render_clause_parts`, unchanged from Phase
# A) still only ever emits "creature", so this widening is additive and
# does not change Phase A's own test outcomes. Verb identity was already
# never checked by the parser (only consumed positionally), so no verb
# vocabulary needs threading through here.
NOUN_SYNONYMS = ("creature", "animal", "being")
NOUN_PLURALS = {"creature": "creatures", "animal": "animals", "being": "beings"}
TRANS_VERB_SYNONYMS = ("watches", "observes", "tracks")
INTRANS_VERB_SYNONYMS = ("sleeps", "rests", "waits")
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


def _render_clause_parts(mark: list) -> tuple[str, str]:
    """Return (subject_np, trailing_verb) for one mark, per the grammar pin."""
    if not mark:
        return f"the {NOUN}", INTRANS_VERB

    if len(mark) == 1:
        child_np, child_verb = _render_clause_parts(mark[0])
        np = f"the {NOUN} that {child_np} {child_verb}"
        return np, TRANS_VERB

    child_clauses = [f"{np} {verb}" for np, verb in (_render_clause_parts(c) for c in mark)]
    n = len(mark)
    np = f"the {NOUN} that {NUMERAL_WORDS[n]} creatures namely {_oxford_join(child_clauses)}"
    return np, TRANS_VERB


def _render_full_clause(mark: list) -> str:
    np, verb = _render_clause_parts(mark)
    return f"{np} {verb}"


def render_clause_embedding(form_string: str, rng: random.Random | None = None) -> str:
    """Render a form string as genuine centre-embedded relative clauses.

    Args:
        form_string: canonical parenthesis form, e.g. "(()())" ("" for void).
        rng: unused; accepted for interface parity (see grammar pin, point 5).

    Returns:
        One period-terminated sentence per top-level mark, space-joined;
        "nothing." for a void form.
    """
    marks = string_to_form(form_string)
    if not marks:
        return "nothing."
    return " ".join(f"{_render_full_clause(m)}." for m in marks)


def _tokenize(text: str) -> list[str]:
    text = text.replace(",", " , ").replace(".", " . ")
    return text.split()


def _parse_full_clause(tokens: list[str], pos: int) -> tuple[list, int]:
    """Parse one full clause ("the creature [that ...] VERB") from `pos`.

    Returns the mark it represents (a nested list) and the position right
    after the trailing verb token.
    """
    if not (pos + 1 < len(tokens) and tokens[pos] == "the" and tokens[pos + 1] in NOUN_SYNONYMS):
        raise ValueError(f"expected 'the {NOUN}' at token {pos}: {tokens[pos : pos + 5]!r}")
    pos += 2

    if pos < len(tokens) and tokens[pos] == "that":
        pos += 1
        if (
            pos + 2 < len(tokens)
            and tokens[pos] in NUMERAL_WORDS
            and tokens[pos + 1] in _ALL_PLURALS
            and tokens[pos + 2] == "namely"
        ):
            n = NUMERAL_WORDS.index(tokens[pos])
            pos += 3
            children: list = []
            for i in range(n):
                child_mark, pos = _parse_full_clause(tokens, pos)
                children.append(child_mark)
                if i < n - 1:
                    if tokens[pos] == ",":
                        pos += 1
                    if tokens[pos] == "and":
                        pos += 1
            pos += 1  # consume this level's trailing verb
            return children, pos

        child_mark, pos = _parse_full_clause(tokens, pos)
        pos += 1  # consume this level's trailing verb
        return [child_mark], pos

    # Leaf: no embedding, just this mark's own verb.
    pos += 1  # consume the (intransitive) verb
    return [], pos


def parse_clause_embedding(rendered: str) -> list:
    """Invert `render_clause_embedding` back to the nested-list form."""
    if rendered == "nothing.":
        return []
    tokens = _tokenize(rendered)
    marks: list = []
    pos = 0
    while pos < len(tokens):
        mark, pos = _parse_full_clause(tokens, pos)
        if tokens[pos] != ".":
            raise ValueError(f"expected '.' at token {pos}: {tokens[pos : pos + 5]!r}")
        pos += 1
        marks.append(mark)
    return marks


# =============================================================================
# Phase B: Archetype + Injector wrapping (task_01KWQKYTN19RZN2BFKAAGQCFKA, Phase B)
# =============================================================================
#
# `_render_clause_parts_spanned` mirrors `_render_clause_parts` above
# exactly (same grammar, same string output with `jitter=False`), but
# threads a `FormNode` (for its `id`) through the recursion and returns a
# `{node_id: (start, end)}` span dict alongside `(np, verb)`, computed
# incrementally. Each node's own span covers its whole clause -- `np`
# through the trailing verb -- 0-relative to that clause's own start; a
# parent shifts a child's spans by the child's position within the
# parent's own np string.


def _render_clause_parts_spanned(
    node: FormNode, rng: random.Random, jitter: bool
) -> tuple[str, str, dict[str, tuple[int, int]]]:
    noun = rng.choice(NOUN_SYNONYMS) if jitter else NOUN

    if not node.children:
        verb = rng.choice(INTRANS_VERB_SYNONYMS) if jitter else INTRANS_VERB
        np = f"the {noun}"
        full = f"{np} {verb}"
        return np, verb, {node.id: (0, len(full))}

    trans_verb = rng.choice(TRANS_VERB_SYNONYMS) if jitter else TRANS_VERB

    if len(node.children) == 1:
        child_np, child_verb, child_spans = _render_clause_parts_spanned(
            node.children[0], rng, jitter
        )
        prefix = f"the {noun} that "
        np = f"{prefix}{child_np} {child_verb}"
        offset = len(prefix)
        spans = {nid: (s + offset, e + offset) for nid, (s, e) in child_spans.items()}
        full = f"{np} {trans_verb}"
        spans[node.id] = (0, len(full))
        return np, trans_verb, spans

    child_items = []
    for child in node.children:
        c_np, c_verb, c_spans = _render_clause_parts_spanned(child, rng, jitter)
        child_items.append((f"{c_np} {c_verb}", c_spans))
    n = len(node.children)
    plural = NOUN_PLURALS[noun] if jitter else "creatures"
    prefix = f"the {noun} that {NUMERAL_WORDS[n]} {plural} namely "
    joined, joined_spans = oxford_join_spanned(child_items)
    offset = len(prefix)
    spans = {nid: (s + offset, e + offset) for nid, (s, e) in joined_spans.items()}
    np = f"{prefix}{joined}"
    full = f"{np} {trans_verb}"
    spans[node.id] = (0, len(full))
    return np, trans_verb, spans


def _build_spanned(root: FormNode, rng: random.Random, *, jitter: bool = False) -> BaseRender:
    if not root.children:
        return BaseRender(modality="text", payload="nothing.", node_map={})

    node_map: dict[str, Primitive] = {}
    sentences: list[str] = []
    pos = 0
    for i, mark in enumerate(root.children):
        np, verb, spans = _render_clause_parts_spanned(mark, rng, jitter)
        full = f"{np} {verb}"
        sentence = full + "."
        for nid, (s, e) in spans.items():
            node_map[nid] = Primitive(
                node_id=nid, kind="span", geom={"start": pos + s, "end": pos + e}
            )
        sentences.append(sentence)
        pos += len(sentence)
        if i < len(root.children) - 1:
            pos += 1  # separating space

    payload = " ".join(sentences)
    return BaseRender(modality="text", payload=payload, node_map=node_map)


class ClauseEmbeddingArchetype(TextArchetypeMixin):
    """``clause_embedding@1``, family ``embedding``. Wraps this module's
    render/parse pair; parse-back (``parse_clause_embedding``) is the
    structure verification.
    """

    name = "clause_embedding@1"
    version = "1"
    family = "embedding"

    # M4: `parse_clause_embedding`'s recursive-descent reader recognises
    # this dialect's own centre-embedded clause grammar, not literal
    # parens -- without this, the default canonical-parens reader sees
    # zero marks in a clause-embedding payload and `structure_verified`
    # is always False.
    text_reader = staticmethod(lambda s: nodes_from_parsed(parse_clause_embedding(s)))

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        return _build_spanned(root, rng, jitter=False)


class VocabularyJitterInjector:
    """``vocabulary_jitter``: swap nouns and verbs within their transitivity
    class. Rebuilds fresh from ``root`` via ``_build_spanned(jitter=True)``;
    ``parse_clause_embedding``'s widened noun vocabulary (see module
    docstring note above `NOUN_SYNONYMS`) still round-trips it, and verb
    identity was never checked by the parser to begin with.
    """

    name = "vocabulary_jitter"
    version = "1"
    modalities = frozenset({"text"})
    applicability = frozenset({"clause_embedding@1"})

    def apply(
        self, base: BaseRender, root: FormNode, rng: random.Random, **params: object
    ) -> BaseRender:
        return _build_spanned(root, rng, jitter=True)
