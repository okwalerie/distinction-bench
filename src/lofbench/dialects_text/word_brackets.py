"""Word brackets (BEGIN/END style) dialect (family `parens`, reuses `parens@1`).

Phase A only: plain `render`/`parse` functions, standing in for what Phase B
wires as a new `DialectSpec` on the existing `parens@1` archetype (no new
archetype file -- see the plan's "a family is a config, not a class"
rationale). Not yet wired to the real `parens@1` archetype or to
`lofbench.renderers`.

Grammar pin (binding)
---------------------
1. Token vocabulary is the fixed word pair `BEGIN` (open) / `END` (close).
   Multi-character tokens cannot be concatenated with no separator the way
   single-glyph brackets can (`BEGINBEGINEND...` is ambiguous), so a
   mandatory single ASCII space separates every token.
2. For each character in `form_string`, in order, emit one token: `BEGIN`
   for `(`, `END` for `)`. Join tokens with a single space. A void form
   renders as the empty string.
3. Determinism: the mapping is a pure function of `form_string`; no
   randomness is used. `rng` is accepted for interface parity with the
   other Phase A dialects (Phase B's `word_delimiter_swap` injector would
   vary the vocabulary and matched/mismatched pairing; not implemented
   here).

Round-trip: tokenise on whitespace (any run of whitespace is a separator,
so this stays robust to a Phase B jitter that varies spacing), map each
token back to `(` or `)`, concatenate, and parse with
`lofbench.core.string_to_form`.
"""

from __future__ import annotations

import random

from lofbench.core import string_to_form

OPEN_TOKEN = "BEGIN"
CLOSE_TOKEN = "END"

_TOKEN_TO_BRACKET = {OPEN_TOKEN: "(", CLOSE_TOKEN: ")"}


def render_word_brackets(form_string: str, rng: random.Random | None = None) -> str:
    """Render a form string as space-separated BEGIN/END tokens.

    Args:
        form_string: canonical parenthesis form, e.g. "(()())" ("" for void).
        rng: unused; accepted for interface parity (see grammar pin, point 3).

    Returns:
        Space-separated `BEGIN`/`END` tokens; "" for a void form.
    """
    tokens = [OPEN_TOKEN if ch == "(" else CLOSE_TOKEN for ch in form_string]
    return " ".join(tokens)


def parse_word_brackets(rendered: str) -> list:
    """Invert `render_word_brackets` back to the nested-list form."""
    brackets = "".join(_TOKEN_TO_BRACKET[token] for token in rendered.split())
    return string_to_form(brackets)
