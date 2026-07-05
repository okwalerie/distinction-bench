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
from lofbench.dialects_text._common import TextArchetypeMixin
from lofbench.renderers.pipeline.archetype import BaseRender, Primitive
from lofbench.renderers.pipeline.nodes import FormNode, nodes_from_parsed

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


# =============================================================================
# Phase B: Archetype + Injector wrapping (task_01KWQKYTN19RZN2BFKAAGQCFKA, Phase B)
# =============================================================================
#
# ``_build_spanned`` tracks absolute indent *columns* rather than
# `depth * INDENT_WIDTH`, so the per-level increment can vary under jitter
# (a different width at every level, even every line) while every child's
# column still stays strictly deeper than its parent's -- safe by
# construction under the off-side-rule reader, exactly per the grammar
# pin's injector-safety argument. With ``jitter=False`` the increment is
# always ``INDENT_WIDTH`` and the label is always ``LABEL``, reproducing
# ``render_tree_indent``'s output byte-for-byte (no randomness drawn).

LABEL_CHOICES = (LABEL, "node", "item")
COMMENT_WORDS = ("note", "todo", "fixme", "context")


def _emit(
    node: FormNode,
    col: int,
    lines: list[str],
    spans: dict[str, tuple[int, int]],
    *,
    jitter: bool,
    rng: random.Random,
) -> None:
    start = len(lines)
    label = rng.choice(LABEL_CHOICES) if jitter else LABEL
    lines.append(" " * col + label)
    # Siblings must share one indent column (the off-side rule), so the
    # increment is drawn once per *parent*, not once per child -- drawing
    # it inside the loop would let two siblings land at different columns,
    # which the reader would then (correctly, per the off-side rule) read
    # as parent/child instead of siblings.
    child_col = col + (rng.randint(1, 6) if jitter else INDENT_WIDTH)
    for i, child in enumerate(node.children):
        if jitter and i > 0 and rng.random() < 0.4:
            if rng.random() < 0.5:
                lines.append("")
            else:
                lines.append(" " * child_col + "# " + rng.choice(COMMENT_WORDS))
        _emit(child, child_col, lines, spans, jitter=jitter, rng=rng)
    spans[node.id] = (start, len(lines))


def _build_spanned(root: FormNode, rng: random.Random, *, jitter: bool = False) -> BaseRender:
    lines: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    for i, child in enumerate(root.children):
        if jitter and i > 0 and rng.random() < 0.4:
            if rng.random() < 0.5:
                lines.append("")
            else:
                lines.append("# " + rng.choice(COMMENT_WORDS))
        _emit(child, 0, lines, spans, jitter=jitter, rng=rng)

    payload = "\n".join(lines)

    line_starts: list[int] = []
    pos = 0
    for line in lines:
        line_starts.append(pos)
        pos += len(line) + 1

    node_map: dict[str, Primitive] = {}
    for node_id, (start_line, end_line) in spans.items():
        char_start = line_starts[start_line]
        char_end = line_starts[end_line - 1] + len(lines[end_line - 1])
        node_map[node_id] = Primitive(
            node_id=node_id, kind="span", geom={"start": char_start, "end": char_end}
        )
    return BaseRender(modality="text", payload=payload, node_map=node_map)


class TreeIndentArchetype(TextArchetypeMixin):
    """``tree_indent@1``, family ``trees``. Wraps this module's render/parse
    pair; parse-back (``parse_tree_indent``) is the structure verification.
    """

    name = "tree_indent@1"
    version = "1"
    family = "trees"

    # M4: the off-side-rule reader (`parse_tree_indent`) recognises this
    # dialect's own indentation grammar, not literal parens -- without this,
    # `induced_relation`'s default canonical-parens reader sees zero marks
    # in an indent-only payload and `structure_verified` is always False.
    text_reader = staticmethod(lambda s: nodes_from_parsed(parse_tree_indent(s)))

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        return _build_spanned(root, rng, jitter=False)


class IndentStyleJitterInjector:
    """``indent_style_jitter``: vary the label word, spaces-per-level, and
    insert blank/comment lines between siblings. Rebuilds fresh from
    ``root`` via ``_build_spanned(jitter=True)``, which only ever deepens
    a child's column relative to its parent's, never violates the
    off-side rule, and is therefore safe by construction.
    """

    name = "indent_style_jitter"
    version = "1"
    modalities = frozenset({"text"})
    applicability = frozenset({"tree_indent@1"})

    def apply(
        self, base: BaseRender, root: FormNode, rng: random.Random, **params: object
    ) -> BaseRender:
        return _build_spanned(root, rng, jitter=True)
