"""RNA dot-bracket notation dialect (`rna_dotbracket@1`, family `biopolymer`).

Phase A only: plain `render`/`parse` functions, not yet an `Archetype`. See
`lofbench.dialects_text` package docstring for the phasing rationale.

Grammar pin (binding; this is the formal spec required before Phase A code
was written)
------------------------------------------------------------------------
RNA secondary structure is conventionally written as two aligned lines: a
nucleotide sequence line and a dot-bracket structure line, one character
per position. The structure line's bracket skeleton is exactly the
canonical Laws of Form parenthesis string for the source form -- a mark's
open paren and close paren are the structure line's two paired positions
for that mark -- so containment is encoded identically to `parens.
canonical`, just relabelled onto a second line plus a decorative sequence
line.

1. The structure line is `form_string` itself, character for character.
   A void form renders an empty structure line.
2. The sequence line has exactly the same length as the structure line.
   Each position independently draws one nucleotide letter from the fixed
   alphabet `A`, `C`, `G`, `U` via `rng.choice`, in left-to-right order.
   This is the archetype's own draw (not an injector's); it runs even
   with no injector applied, which is why the two lines are always
   emitted together -- see the plan's "not just relabelled parens"
   argument.
3. Rendered form: exactly two lines joined by `\n`, in this fixed order:
   `f"sequence: {seq}"` then `f"structure: {struct}"`, where `seq` and
   `struct` are each their line's characters joined by a single ASCII
   space. There is no trailing newline.
4. `.` (dot) characters are reserved as decorative filler for the
   `unpaired_filler` injector (Phase B, not implemented here). Phase A's
   base render never emits them, but `parse` strips them unconditionally
   before bracket-matching, so filler is transparent to parsing by
   construction whenever Phase B adds it -- matching the plan's stated
   round-trip recipe ("strip the sequence line, strip `.` characters from
   the structure line, run the same bracket-matching reader").
5. Determinism: given the same `rng` state, `render` is a pure function
   of `form_string` and that state. No other randomness is used.

Round-trip: read the `structure:` line, discard the `sequence:` line
entirely (it carries no structural information, only decoration), strip
whitespace and `.` characters from the remainder, then hand the resulting
bracket string to `lofbench.core.string_to_form`.
"""

from __future__ import annotations

import random

from lofbench.core import string_to_form

NUCLEOTIDES = ("A", "C", "G", "U")


def render_rna_dotbracket(form_string: str, rng: random.Random | None = None) -> str:
    """Render a form string as a two-line RNA dot-bracket stimulus.

    Args:
        form_string: canonical parenthesis form, e.g. "(()())" ("" for void).
        rng: source of randomness for the decorative sequence line. A fresh
            `random.Random()` is used if not given (non-deterministic).

    Returns:
        Two lines joined by "\\n": "sequence: ..." then "structure: ...".
    """
    if rng is None:
        rng = random.Random()

    structure_chars = list(form_string)
    sequence_chars = [rng.choice(NUCLEOTIDES) for _ in structure_chars]

    seq_line = "sequence: " + " ".join(sequence_chars)
    struct_line = "structure: " + " ".join(structure_chars)
    return f"{seq_line}\n{struct_line}"


def parse_rna_dotbracket(rendered: str) -> list:
    """Invert `render_rna_dotbracket` back to the nested-list form.

    Ignores the sequence line entirely. Strips whitespace and any `.`
    filler characters from the structure line before bracket-matching.
    """
    lines = rendered.split("\n")
    struct_line = next(line for line in lines if line.startswith("structure:"))
    remainder = struct_line[len("structure:") :]
    cleaned = remainder.replace(" ", "").replace(".", "")
    return string_to_form(cleaned)
