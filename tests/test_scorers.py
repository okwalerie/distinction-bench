"""Tests for lofbench.scorers module."""

from lofbench.scorers import (
    extract_composite_answer,
    extract_single_answer,
    normalize_to_parens,
)


class TestExtractSingleAnswer:
    """Test answer extraction from LLM responses."""

    def test_extract_tagged_marked(self):
        assert extract_single_answer("<answer>marked</answer>") == "marked"
        assert extract_single_answer("<answer>MARKED</answer>") == "marked"
        assert extract_single_answer("<answer>()</answer>") == "marked"

    def test_extract_tagged_unmarked(self):
        assert extract_single_answer("<answer>unmarked</answer>") == "unmarked"
        assert extract_single_answer("<answer>void</answer>") == "unmarked"
        assert extract_single_answer("<answer>VOID</answer>") == "unmarked"

    def test_extract_fallback_marked(self):
        assert extract_single_answer("The final answer is marked.") == "marked"

    def test_extract_fallback_unmarked(self):
        assert extract_single_answer("The expression is unmarked.") == "unmarked"
        assert extract_single_answer("The result is unmarked") == "unmarked"

    def test_extract_empty(self):
        assert extract_single_answer("") == "unknown"

    def test_extract_ambiguous(self):
        assert extract_single_answer("no clear answer here") == "unknown"


class TestExtractCompositeAnswer:
    """Test composite answer extraction (JSON format)."""

    def test_extract_json_fenced_old_format(self):
        """Test backward compatibility with old flat format."""
        response = """Here are my answers:
```json
{"E1": "marked", "E2": "unmarked", "E3": "marked"}
```
"""
        result = extract_composite_answer(response, 3)
        assert result["results"] == ["marked", "unmarked", "marked"]
        assert result["canonicals"] == ["", "", ""]

    def test_extract_json_new_format(self):
        """Test new format with canonical and result."""
        response = """
```json
{"E1": {"canonical": "(())", "result": "unmarked"}, "E2": {"canonical": "()()", "result": "marked"}}
```
"""
        result = extract_composite_answer(response, 2)
        assert result["results"] == ["unmarked", "marked"]
        assert result["canonicals"] == ["(())", "()()"]

    def test_extract_json_bare(self):
        response = '{"E1": "marked", "E2": "unmarked"}'
        result = extract_composite_answer(response, 2)
        assert result["results"] == ["marked", "unmarked"]

    def test_extract_empty(self):
        result = extract_composite_answer("", 4)
        assert result["results"] == ["unknown"] * 4
        assert result["canonicals"] == [""] * 4

    def test_extract_unparseable(self):
        result = extract_composite_answer("no json here", 4)
        assert result["results"] == ["unknown"] * 4
        assert result["canonicals"] == [""] * 4

    def test_extract_mixed_formats(self):
        """Test handling of result values like void, nothing, ()."""
        response = (
            '{"E1": {"canonical": "()", "result": "void"}, '
            '"E2": {"canonical": "", "result": "()"}}'
        )
        result = extract_composite_answer(response, 2)
        assert result["results"] == ["unmarked", "marked"]


class TestNormalizeToParens:
    """Test bracket normalization for structural comparison."""

    def test_normalize_parens(self):
        """Standard parens unchanged."""
        assert normalize_to_parens("(())") == "(())"
        assert normalize_to_parens("()()") == "()()"

    def test_normalize_brackets(self):
        """Square brackets converted."""
        assert normalize_to_parens("[[]]") == "(())"
        assert normalize_to_parens("[][]") == "()()"

    def test_normalize_braces(self):
        """Curly braces converted."""
        assert normalize_to_parens("{{}}") == "(())"

    def test_normalize_mixed(self):
        """Mixed brackets normalized."""
        assert normalize_to_parens("[()]") == "(())"
        assert normalize_to_parens("{[()]}") == "((()))"
        assert normalize_to_parens("()[]{}") == "()()()"

    def test_normalize_unicode(self):
        """Unicode brackets normalized."""
        assert normalize_to_parens("⟨⟩") == "()"
        assert normalize_to_parens("⟨⟨⟩⟩") == "(())"

    def test_normalize_mismatched(self):
        """Mismatched brackets still normalize by position."""
        assert normalize_to_parens("[)") == "()"
        assert normalize_to_parens("[⟩") == "()"
        assert normalize_to_parens("({⟩]") == "(())"

    def test_normalize_empty(self):
        """Empty string returns empty."""
        assert normalize_to_parens("") == ""

    def test_normalize_strips_whitespace(self):
        """Whitespace is stripped."""
        assert normalize_to_parens("( ( ) )") == "(())"
        assert normalize_to_parens("[ ] [ ]") == "()()"
