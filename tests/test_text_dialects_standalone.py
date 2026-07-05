"""Phase A round-trip and determinism tests for DB-2's text dialects.

Standalone: depends only on `lofbench.core` and the five
`lofbench.dialects_text` modules, nothing from the (not yet existing)
DB-4 archetype/registry machinery. See
`.lattice/plans/task_01KWQKYTN19RZN2BFKAAGQCFKA.md` for the phased plan.
"""

from __future__ import annotations

import random

import pytest

from lofbench.core import DIFFICULTY_CONFIGS, generate_form_string, string_to_form
from lofbench.dialects_text.clause_embedding import parse_clause_embedding, render_clause_embedding
from lofbench.dialects_text.prose import parse_prose, render_prose
from lofbench.dialects_text.rna_dotbracket import parse_rna_dotbracket, render_rna_dotbracket
from lofbench.dialects_text.tree_indent import parse_tree_indent, render_tree_indent
from lofbench.dialects_text.word_brackets import parse_word_brackets, render_word_brackets

DIALECTS = {
    "rna_dotbracket": (render_rna_dotbracket, parse_rna_dotbracket),
    "tree_indent": (render_tree_indent, parse_tree_indent),
    "word_brackets": (render_word_brackets, parse_word_brackets),
    "prose": (render_prose, parse_prose),
    "clause_embedding": (render_clause_embedding, parse_clause_embedding),
}

EDGE_CASE_FORMS = [
    "",  # void
    "()",  # single mark
    "()()",  # wide, two identical leaves at root
    "(())",  # deep nesting, depth 2
    "(()())",  # the plan's worked example
    "((()))",  # deep nesting, depth 3
    "()()()()",  # wide fan-out, four identical leaves
    "(()(())((())))",  # mixed shapes at one level (breaks any leaf-only collapse)
    "()" * 9,  # wide fan-out at the max_width=9 boundary
    "(" * 9 + ")" * 9,  # deep nesting at the max_depth=9 boundary
]

N_GENERATED_PER_TIER = 200
GENERATE_SEED = 20260704


def _generated_forms() -> list[str]:
    forms = []
    for _, min_d, max_d, max_w, max_m in DIFFICULTY_CONFIGS:
        rng = random.Random(GENERATE_SEED)
        for _ in range(N_GENERATED_PER_TIER):
            forms.append(
                generate_form_string(
                    min_depth=min_d, max_depth=max_d, max_width=max_w, max_marks=max_m, rng=rng
                )
            )
    return forms


GENERATED_FORMS = _generated_forms()


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("form_string", EDGE_CASE_FORMS)
def test_round_trip_edge_cases(dialect, form_string):
    render, parse = DIALECTS[dialect]
    rng = random.Random(42)
    rendered = render(form_string, rng)
    assert parse(rendered) == string_to_form(form_string)


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("form_string", GENERATED_FORMS)
def test_round_trip_generated(dialect, form_string):
    render, parse = DIALECTS[dialect]
    rng = random.Random(GENERATE_SEED)
    rendered = render(form_string, rng)
    assert parse(rendered) == string_to_form(form_string)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_deterministic_under_fixed_seed(dialect):
    render, _ = DIALECTS[dialect]
    for form_string in EDGE_CASE_FORMS:
        first = render(form_string, random.Random(1234))
        second = render(form_string, random.Random(1234))
        assert first == second


@pytest.mark.parametrize("dialect", DIALECTS)
def test_deterministic_across_generated_forms(dialect):
    render, _ = DIALECTS[dialect]
    for form_string in GENERATED_FORMS[:50]:
        first = render(form_string, random.Random(99))
        second = render(form_string, random.Random(99))
        assert first == second


class TestWorkedExample:
    """Pin the plan's `(()())` worked example for each dialect (one example
    rendering, spot-checked, in addition to the generic round-trip tests)."""

    FORM = "(()())"

    def test_rna_dotbracket(self):
        rendered = render_rna_dotbracket(self.FORM, random.Random(0))
        lines = rendered.split("\n")
        assert lines[0].startswith("sequence: ")
        assert lines[1] == "structure: ( ( ) ( ) )"
        assert parse_rna_dotbracket(rendered) == string_to_form(self.FORM)

    def test_tree_indent(self):
        rendered = render_tree_indent(self.FORM)
        assert rendered == "mark\n    mark\n    mark"
        assert parse_tree_indent(rendered) == string_to_form(self.FORM)

    def test_word_brackets(self):
        rendered = render_word_brackets(self.FORM)
        assert rendered == "BEGIN BEGIN END BEGIN END END"
        assert parse_word_brackets(rendered) == string_to_form(self.FORM)

    def test_prose(self):
        rendered = render_prose(self.FORM)
        assert rendered == "a box holding two empty boxes."
        assert parse_prose(rendered) == string_to_form(self.FORM)

    def test_clause_embedding(self):
        rendered = render_clause_embedding(self.FORM)
        assert rendered == (
            "the creature that two creatures namely the creature sleeps "
            "and the creature sleeps watches."
        )
        assert parse_clause_embedding(rendered) == string_to_form(self.FORM)


class TestVoidForm:
    """Void form is a first-class edge case every dialect must name explicitly."""

    def test_all_dialects_round_trip_void(self):
        for render, parse in DIALECTS.values():
            rendered = render("", random.Random(7))
            assert parse(rendered) == []


class TestProseDisambiguation:
    """Regression: a mixed-shape sibling group must not collapse, and must
    still round-trip even when a member has its own nested explicit list."""

    def test_mixed_shapes_do_not_collapse(self):
        form = "()(())"  # one leaf, one depth-2 mark: not all-leaf, no collapse
        rendered = render_prose(form)
        assert "things:" in rendered
        assert parse_prose(rendered) == string_to_form(form)

    def test_nested_explicit_lists_are_unambiguous(self):
        # Root has 2 children; the second child itself has 2 mixed-shape
        # children. If list-joining were not count-bounded, the "and"
        # from the inner list could be mistaken for the outer list's join.
        form = "()" + "(" + "()" + "(())" + ")"
        rendered = render_prose(form)
        assert parse_prose(rendered) == string_to_form(form)


class TestClauseEmbeddingDisambiguation:
    """Same disambiguation concern as prose, for the centre-embedding dialect."""

    def test_nested_width_is_unambiguous(self):
        form = "()" + "(" + "()" + "(())" + ")"
        rendered = render_clause_embedding(form)
        assert parse_clause_embedding(rendered) == string_to_form(form)

    def test_pure_chain_is_genuine_centre_embedding(self):
        # A depth-3 linear chain: verbs must stack at the end, deepest first,
        # per the Chomsky/Miller-style centre-embedding shape.
        form = "((()))"
        rendered = render_clause_embedding(form)
        expected = "the creature that the creature that the creature sleeps watches watches."
        assert rendered == expected
        assert parse_clause_embedding(rendered) == string_to_form(form)
