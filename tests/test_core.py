"""Tests for lofbench core functionality."""

import random

from lofbench import (
    canonical_string,
    evaluate,
    form_depth,
    form_to_string,
    generate_form_string,
    generate_test_cases,
    simplify_string,
    string_depth,
    string_to_form,
)


class TestParsing:
    """Test parsing roundtrips."""

    def test_roundtrip_simple(self):
        cases = ["()", "(())", "()()", "((()))", "(()())"]
        for s in cases:
            form = string_to_form(s)
            back = form_to_string(form)
            assert back == s, f"{s} -> {form} -> {back}"

    def test_empty(self):
        assert string_to_form("") == []
        assert form_to_string([]) == ""

    def test_nested_structure(self):
        # ()
        assert string_to_form("()") == [[]]
        # (())
        assert string_to_form("(())") == [[[]]]
        # ()()
        assert string_to_form("()()") == [[], []]
        # (()())
        assert string_to_form("(()())") == [[[], []]]


class TestDepth:
    """Test depth calculations."""

    def test_form_depth(self):
        assert form_depth([]) == 0
        assert form_depth([[]]) == 1
        assert form_depth([[[]]]) == 2
        assert form_depth([[], []]) == 1
        assert form_depth([[[], []]]) == 2

    def test_string_depth(self):
        assert string_depth("") == 0
        assert string_depth("()") == 1
        assert string_depth("(())") == 2
        assert string_depth("()()") == 1
        assert string_depth("((()))") == 3
        assert string_depth("(()())") == 2

    def test_depth_consistency(self):
        """form_depth and string_depth should match."""
        cases = ["()", "(())", "()()", "((()))", "(()())", "(()(()))"]
        for s in cases:
            form = string_to_form(s)
            assert form_depth(form) == string_depth(s), s


class TestSimplifier:
    """Test the ground-truth simplifier."""

    def test_i1_condense(self):
        # ()() -> ()
        result, steps = simplify_string("()()")
        assert result == "()"
        assert any(axiom == "I1" for _, _, axiom in steps)

    def test_i2_cancel(self):
        # (()) -> void
        result, steps = simplify_string("(())")
        assert result == "void"
        assert any(axiom == "I2" for _, _, axiom in steps)

    def test_combined(self):
        # (()()) -> (()) -> void (I1 then I2)
        result, _ = simplify_string("(()())")
        assert result == "void"

    def test_known_cases(self):
        cases = [
            ("()()", "()"),
            ("(())", "void"),
            ("((()))", "()"),
            ("(()())", "void"),
            ("()(())", "()"),
            ("(()(()))", "void"),
            ("(())()", "()"),
            ("(())(())", "void"),
        ]
        for input_s, expected in cases:
            result = canonical_string(input_s)
            assert result == expected, f"{input_s} -> {result}, expected {expected}"

    def test_evaluate(self):
        assert evaluate("()") == "marked"
        assert evaluate("(())") == "unmarked"
        assert evaluate("()()") == "marked"
        assert evaluate("(()())") == "unmarked"


class TestGenerator:
    """Test form generation."""

    def test_produces_valid_forms(self):
        """Generated forms should be well-formed (balanced parens)."""
        rng = random.Random(42)
        for _ in range(100):
            s = generate_form_string(min_depth=1, max_depth=4, rng=rng)
            # Check balanced parens
            depth = 0
            for c in s:
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                assert depth >= 0, f"Unbalanced: {s}"
            assert depth == 0, f"Unbalanced: {s}"

    def test_respects_depth(self):
        """Generated forms should respect depth constraints."""
        rng = random.Random(42)
        for _ in range(50):
            s = generate_form_string(min_depth=2, max_depth=4, rng=rng)
            if s:  # non-empty
                d = string_depth(s)
                assert d >= 2, f"Depth {d} < 2: {s}"
                assert d <= 4, f"Depth {d} > 4: {s}"

    def test_deterministic(self):
        """Same seed should produce same output."""
        s1 = generate_form_string(min_depth=2, max_depth=4, rng=random.Random(123))
        s2 = generate_form_string(min_depth=2, max_depth=4, rng=random.Random(123))
        assert s1 == s2


class TestDataset:
    """Test dataset generation."""

    def test_generate_cases(self):
        cases = generate_test_cases(n=20, seed=42)
        assert len(cases) == 20
        for c in cases:
            assert "id" in c
            assert "input" in c
            assert "target" in c
            assert c["target"] in ("marked", "unmarked")
            # Verify target is correct
            assert evaluate(c["input"]) == c["target"]

    def test_deterministic(self):
        c1 = generate_test_cases(n=10, seed=42)
        c2 = generate_test_cases(n=10, seed=42)
        assert c1 == c2
