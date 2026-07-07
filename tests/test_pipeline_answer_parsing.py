"""Tests for the composite dual-schema answer parser.

Grounded in the plan's finding 2: 28+ of the 48 real logs use the current
scorer's {"results": [...], "canonicals": [...]}; 11+ use an earlier
scorer's {"items": [...], "total_marked": ...} (removed from the source
tree). Both must parse; a third/unknown shape must be counted as a parse
failure, never silently dropped.
"""

from lofbench.pipeline import parse_composite_answer


def test_results_canonicals_schema():
    answer = str({"results": ["marked", "unmarked", "marked"], "canonicals": ["()", "", "()"]})
    predicted, ok = parse_composite_answer(answer, 3)
    assert ok is True
    assert predicted == ["marked", "unmarked", "marked"]


def test_items_total_marked_schema():
    answer = str({"items": ["unmarked", "marked"], "total_marked": 1})
    predicted, ok = parse_composite_answer(answer, 2)
    assert ok is True
    assert predicted == ["unmarked", "marked"]


def test_unrecognised_third_schema_counted_not_dropped():
    """An unknown shape must never crash and must never silently vanish:
    predicted defaults to 'unknown' for every item (which always scores
    incorrect against a real target), and ok=False so the caller can count it."""
    answer = str({"totally_new_field": [1, 2, 3]})
    predicted, ok = parse_composite_answer(answer, 3)
    assert ok is False
    assert predicted == ["unknown", "unknown", "unknown"]


def test_empty_answer_string():
    predicted, ok = parse_composite_answer("", 4)
    assert ok is False
    assert predicted == ["unknown"] * 4


def test_none_answer():
    predicted, ok = parse_composite_answer(None, 4)
    assert ok is False
    assert predicted == ["unknown"] * 4


def test_unparseable_string():
    predicted, ok = parse_composite_answer("not a python literal {{{", 2)
    assert ok is False
    assert predicted == ["unknown", "unknown"]


def test_length_mismatch_is_a_parse_failure():
    """A schema that matches by key but has the wrong item count is not
    trustworthy positional alignment -- treat as a parse failure."""
    answer = str({"results": ["marked"], "canonicals": [""]})
    predicted, ok = parse_composite_answer(answer, 3)
    assert ok is False
    assert predicted == ["unknown", "unknown", "unknown"]


def test_non_dict_literal():
    answer = "[1, 2, 3]"
    predicted, ok = parse_composite_answer(answer, 3)
    assert ok is False
    assert predicted == ["unknown", "unknown", "unknown"]
