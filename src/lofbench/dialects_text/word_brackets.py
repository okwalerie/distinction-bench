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
from lofbench.dialects_text._common import TextArchetypeMixin
from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode, nodes_from_parsed

OPEN_TOKEN = "BEGIN"
CLOSE_TOKEN = "END"

_TOKEN_TO_BRACKET = {OPEN_TOKEN: "(", CLOSE_TOKEN: ")"}

# Word-token vocabulary pool for the mismatched mode below -- open/close
# words are chosen independently per bracket position, mirroring
# `noisy_parens.NoisyParensRenderer`'s existing mismatched-mode precedent
# (any opening-class token opens, any closing-class token closes; the two
# need not come from the same pair).
WORD_PAIRS: tuple[tuple[str, str], ...] = (
    ("BEGIN", "END"),
    ("OPEN", "CLOSE"),
    ("START", "STOP"),
)
_OPEN_WORDS = frozenset(pair[0] for pair in WORD_PAIRS)
_CLOSE_WORDS = frozenset(pair[1] for pair in WORD_PAIRS)


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


def parse_word_brackets_mismatched(rendered: str) -> list:
    """Invert a mismatched-mode render (see `WordDelimiterSwapInjector`).

    Any token in `_OPEN_WORDS` opens, any token in `_CLOSE_WORDS` closes --
    open/close need not come from the same `WORD_PAIRS` entry, exactly
    `noisy_parens.NoisyParensRenderer`'s existing mismatched-mode contract.
    """
    brackets = []
    for token in rendered.split():
        if token in _OPEN_WORDS:
            brackets.append("(")
        elif token in _CLOSE_WORDS:
            brackets.append(")")
        else:
            raise ValueError(f"unknown word-bracket token: {token!r}")
    return string_to_form("".join(brackets))


# =============================================================================
# Phase B: Archetype + Injector wrapping (task_01KWQKYTN19RZN2BFKAAGQCFKA, Phase B)
# =============================================================================
#
# Deviation from the plan: the plan's stated preference is to reuse the
# existing `parens@1` archetype for this dialect ("a family is a config,
# not a class") rather than a new archetype file. `parens@1` does not exist
# yet -- migrating the legacy `canonical`/`CanonicalRenderer` into a
# pipeline `Archetype` is DB-4's M3-M5 scope, explicitly out of this task's
# boundary (no edits to existing legacy renderers). Registering a
# `DialectSpec` against an archetype key that is not yet in
# `ARCHETYPE_REGISTRY` would leave this dialect permanently broken
# (`KeyError` at render time) until M3-M5 lands, which fails this task's
# own "reachable via get_renderer and renders correctly" acceptance
# criterion right now. So this module defines its own small `word_brackets@1`
# archetype (family stays `parens`, matching the plan) instead. `parens@1`
# has since landed (DB-4 M5), but it is not a drop-in replacement:
# `parens@1` emits single-glyph brackets (`(`/`)`, possibly bracket-swapped
# glyphs), never the multi-character `BEGIN`/`END`-style word tokens this
# dialect's grammar pin requires. `word_brackets@1` is therefore permanent,
# not a provisional stand-in awaiting retirement.


def _emit(
    node: FormNode,
    tokens: list[str],
    rng: random.Random,
    *,
    fixed_pair: tuple[str, str] | None,
    mismatched: bool,
    spans: dict[str, tuple[int, int]],
) -> None:
    start = len(tokens)
    if mismatched:
        open_tok, close_tok = rng.choice(WORD_PAIRS)
    else:
        open_tok, close_tok = fixed_pair
    tokens.append(open_tok)
    for child in node.children:
        _emit(child, tokens, rng, fixed_pair=fixed_pair, mismatched=mismatched, spans=spans)
    tokens.append(close_tok)
    spans[node.id] = (start, len(tokens))


def _build_spanned(
    root: FormNode,
    rng: random.Random,
    *,
    word_pairs: tuple[str, str] | None = None,
    mismatched: bool = False,
) -> BaseRender:
    """Build the space-joined token payload plus a node_map of character
    spans. A node's span covers its own opening-through-closing token pair
    (its whole subtree's token range) mapped into the joined string's
    character offsets.

    ``word_pairs`` fixes one pair for every mark with no `rng` draw (used
    by the archetype's no-jitter ``build``, so it stays a pure function of
    tree shape, byte-identical to `render_word_brackets`). Leaving it
    unset draws one pair once via `rng` and reuses it for the whole render
    (the injector's matched-jitter mode -- a real vocabulary swap, not the
    archetype's fixed BEGIN/END). ``mismatched=True`` draws an independent
    pair per mark instead, per `noisy_parens`'s existing mismatched-mode
    contract.
    """
    if not mismatched and word_pairs is None:
        word_pairs = rng.choice(WORD_PAIRS)

    tokens: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    for child in root.children:
        _emit(child, tokens, rng, fixed_pair=word_pairs, mismatched=mismatched, spans=spans)

    payload = " ".join(tokens)

    token_starts: list[int] = []
    pos = 0
    for token in tokens:
        token_starts.append(pos)
        pos += len(token) + 1  # +1 for the separating space

    node_map: dict[str, Primitive] = {}
    for node_id, (start_idx, end_idx) in spans.items():
        start_char = token_starts[start_idx]
        end_char = token_starts[end_idx - 1] + len(tokens[end_idx - 1])
        node_map[node_id] = Primitive(
            node_id=node_id, kind="span", geom={"start": start_char, "end": end_char}
        )
    return BaseRender(modality="text", payload=payload, node_map=node_map)


class WordBracketsArchetype(TextArchetypeMixin):
    """``word_brackets@1``, family ``parens``. Provisional stand-in for
    reusing ``parens@1`` (see module-level deviation note above). Wraps
    this module's render/parse pair; parse-back (``parse_word_brackets``)
    is the structure verification.
    """

    name = "word_brackets@1"
    version = "1"
    family = "parens"

    # M4: reads via the mismatched-capable parser (a superset of the
    # canonical-mode vocabulary), so both `parens.word-brackets-v1`
    # (matched BEGIN/END) and `parens.word-brackets-mismatched-v1`
    # (independent open/close word choice per mark) verify through the
    # same reader.
    text_reader = staticmethod(lambda s: nodes_from_parsed(parse_word_brackets_mismatched(s)))

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        return _build_spanned(root, rng, word_pairs=(OPEN_TOKEN, CLOSE_TOKEN))


class WordDelimiterSwapInjector:
    """``word_delimiter_swap``: the injector the plan's open question asks
    about. The M1/M2 ``Injector`` protocol (``apply(base, root, rng,
    **params) -> BaseRender``) suffices -- no M4 per-stage-verify or
    resample machinery is needed, since the swap is expressed as a fresh,
    structurally-safe-by-construction rebuild from ``root`` (mirroring the
    other four injectors in this task), not post-hoc string surgery.

    ``params["mismatched"]`` (default ``False``) selects matched-per-depth
    (reusing the same word pair for every mark, a fixed choice per call) vs
    mismatched (an independent word pair drawn per mark).
    """

    name = "word_delimiter_swap"
    version = "1"
    modalities = frozenset({"text"})
    applicability = frozenset({"word_brackets@1"})

    def apply(
        self, base: BaseRender, root: FormNode, rng: random.Random, **params: object
    ) -> BaseRender:
        mismatched = bool(params.get("mismatched", False))
        return _build_spanned(root, rng, mismatched=mismatched)
