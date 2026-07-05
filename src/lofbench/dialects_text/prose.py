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
    if tokens[pos : pos + 3] == ["an", "empty", "box"]:
        return [], pos + 3
    if tokens[pos : pos + 3] == ["a", "box", "holding"]:
        return _parse_group(tokens, pos + 3)
    snippet = tokens[pos : pos + 5]
    raise ValueError(f"cannot parse prose singular description at token {pos}: {snippet!r}")


def _parse_group(tokens: list[str], pos: int) -> tuple[list, int]:
    if (
        pos + 2 < len(tokens)
        and tokens[pos] in NUMERAL_WORDS
        and tokens[pos + 1] == "empty"
        and tokens[pos + 2] in ("box", "boxes")
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
