"""Indentation / tree-outline notation dialect (`tree_indent@1`, family `trees`).

Phase A only: plain `render`/`parse` functions, not yet an `Archetype`. See
`lofbench.dialects_text` package docstring for the phasing rationale.

Grammar pin (binding; this is the formal spec required before Phase A code
was written)
------------------------------------------------------------------------
One line per mark. A child's indent is strictly deeper than its parent's;
siblings share the same indent; a shallower indent closes frames until the
column matches again -- the off-side rule, as in Python's own indentation
sensitive blocks.

1. A void form renders as the empty string (no lines at all).
2. Each mark renders as one line: `" " * (depth * INDENT_WIDTH) + "mark"`,
   where `depth` is 0 for a top-level mark, `INDENT_WIDTH = 4`. The label
   token `"mark"` is decorative only -- the reader never inspects it, only
   the leading indent column (this is what makes the indent-jitter
   injector safe by construction: it may change the label word, the
   number of spaces per level, or insert blank/comment lines, and the
   off-side-rule reader still recovers the exact tree).
3. Traversal order is pre-order: a mark's own line, then its children's
   lines (each one level deeper), left to right.
4. Comment-line syntax (pinned now for the `indent_style_jitter` injector,
   Phase B, not implemented here): a line is a comment if its content
   after stripping leading whitespace begins with `#`. Comment lines are
   skipped unconditionally by the reader, at any indent -- their indent
   column is never compared against the stack, so a jittered comment can
   never be mistaken for a sibling or child frame. Blank (whitespace-only)
   lines are skipped the same way.
5. Determinism: the skeleton is a pure function of tree shape and needs no
   randomness (like the existing `sexpr` and `nested_list` renderers).
   `rng` is accepted for interface parity with the other Phase A dialects
   but is unused.

Round-trip: an indentation-stack reader. Drop blank and comment lines.
For each remaining line, count leading spaces as its indent. Push a new
frame when the indent is strictly deeper than the current top of stack;
pop frames while the current top's indent is greater than or equal to the
new line's indent. This recovers the exact tree shape independent of the
actual whitespace width used, per point 2 above.
"""

from __future__ import annotations

import random

from lofbench.core import string_to_form

INDENT_WIDTH = 4
LABEL = "mark"


def render_tree_indent(form_string: str, rng: random.Random | None = None) -> str:
    """Render a form string as an indentation-based tree outline.

    Args:
        form_string: canonical parenthesis form, e.g. "(()())" ("" for void).
        rng: unused; accepted for interface parity (see grammar pin, point 5).

    Returns:
        One "mark" line per node, newline-joined; "" for a void form.
    """
    form = string_to_form(form_string)
    lines: list[str] = []

    def emit(marks: list, depth: int) -> None:
        for mark in marks:
            lines.append(" " * (depth * INDENT_WIDTH) + LABEL)
            emit(mark, depth + 1)

    emit(form, 0)
    return "\n".join(lines)


def parse_tree_indent(rendered: str) -> list:
    """Invert `render_tree_indent` back to the nested-list form.

    Skips blank lines and comment lines (stripped content starting with
    `#`). Recovers structure purely from relative indent depth via an
    indentation stack, per the grammar pin above.
    """
    root: list = []
    # Stack of (indent_column, list_to_append_children_into).
    stack: list[tuple[int, list]] = [(-1, root)]

    for raw_line in rendered.split("\n"):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))

        while stack[-1][0] >= indent:
            stack.pop()

        new_mark: list = []
        stack[-1][1].append(new_mark)
        stack.append((indent, new_mark))

    return root
