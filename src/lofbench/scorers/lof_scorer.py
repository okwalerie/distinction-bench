"""Custom scorers for Laws of Form benchmark using inspect_ai."""

from __future__ import annotations

import json
import re
from typing import Literal

from inspect_ai.scorer import Score, Scorer, Target, accuracy, mean, scorer, stderr
from inspect_ai.solver import TaskState

from lofbench.protocols import get_protocol

# Opening and closing bracket characters for normalization
OPEN_BRACKETS = set("([{<⟨〈〈《「『")
CLOSE_BRACKETS = set(")]}>⟩〉〉》」』")


def parse_protocol_answer(
    response: str,
    *,
    protocol_id: str,
    expected_normal_value: str,
    expected_tree: list,
) -> tuple[str, str, bool]:
    """Strictly parse one public-protocol response."""
    return get_protocol(protocol_id).parse_answer(
        response,
        expected_normal_value=expected_normal_value,
        expected_tree=expected_tree,
    )


@scorer(metrics=[accuracy(), stderr()])
def public_protocol_scorer() -> Scorer:
    """Score strict JSON for every v1 protocol through one implementation."""

    async def score(state: TaskState, target: Target) -> Score:
        parse_status, prediction, correct = parse_protocol_answer(
            state.output.completion,
            protocol_id=state.metadata["protocol_id"],
            expected_normal_value=state.metadata["normal_value"],
            expected_tree=state.metadata["abstract_form"],
        )
        return Score(
            value="C" if correct else "I",
            answer=prediction,
            explanation=f"parse_status={parse_status}",
            metadata={"parse_status": parse_status},
        )

    return score


def normalize_to_parens(expr: str) -> str:
    """
    Normalize any bracket notation to standard parentheses.

    Converts all opening brackets to '(' and closing brackets to ')'.
    This allows structural comparison regardless of bracket style.

    Args:
        expr: Expression string with any bracket notation

    Returns:
        Expression with only '(' and ')' brackets
    """
    result = []
    for char in expr:
        if char in OPEN_BRACKETS:
            result.append("(")
        elif char in CLOSE_BRACKETS:
            result.append(")")
        # Skip other characters (whitespace, etc.)
    return "".join(result)


def extract_single_answer(response: str) -> Literal["marked", "unmarked", "unknown"]:
    """
    Extract the final answer from LLM response for single form evaluation.

    Args:
        response: The model's response text

    Returns:
        "marked", "unmarked", or "unknown"
    """
    if not response:
        return "unknown"

    # Try XML tag pattern first
    pattern = r"<answer>\s*(marked|unmarked|\(\)|void|nothing)\s*</answer>"
    match = re.search(pattern, response, re.IGNORECASE)
    if match:
        ans = match.group(1).lower()
        return "marked" if ans in ("marked", "()") else "unmarked"

    # Fallback: find last occurrence of "marked" vs "unmarked"
    # Use regex to find whole words only (avoid "marked" matching inside "unmarked")
    response_lower = response.lower()

    mark_matches = list(re.finditer(r"\bmarked\b", response_lower))
    unmark_matches = list(re.finditer(r"\bunmarked\b", response_lower))

    mark_pos = mark_matches[-1].start() if mark_matches else -1
    unmark_pos = unmark_matches[-1].start() if unmark_matches else -1

    if mark_pos > unmark_pos:
        return "marked"
    elif unmark_pos > mark_pos:
        return "unmarked"
    return "unknown"


def extract_composite_answer(response: str, n: int) -> dict[str, list[str]]:
    """
    Extract composite answer from LLM response.

    Args:
        response: The model's response text
        n: Number of items expected

    Returns:
        Dictionary with:
          - "results": list of "marked"/"unmarked"/"unknown" for each item
          - "canonicals": list of canonical form strings (or "" if not provided)
    """
    empty: dict[str, list[str]] = {
        "results": ["unknown"] * n,
        "canonicals": [""] * n,
    }
    if not response:
        return empty

    # Try to find JSON in the response
    # Look for ```json ... ``` first (greedy match for nested objects)
    json_match = re.search(r"```json\s*(\{.*\})\s*```", response, re.DOTALL)
    if not json_match:
        # Try to find a JSON object containing "E1" (greedy to handle nested objects)
        json_match = re.search(r'(\{"E1".*\})', response, re.DOTALL)

    if json_match:
        try:
            data = json.loads(json_match.group(1))
            results: list[str] = []
            canonicals: list[str] = []

            for i in range(1, n + 1):
                key = f"E{i}"
                val = data.get(key, "unknown")

                # Handle new format: {"canonical": "...", "result": "..."}
                if isinstance(val, dict):
                    # Extract result
                    result = val.get("result", "unknown")
                    if isinstance(result, str):
                        result = result.lower()
                        if result in ("marked", "()"):
                            results.append("marked")
                        elif result in ("unmarked", "void", "nothing"):
                            results.append("unmarked")
                        else:
                            results.append("unknown")
                    else:
                        results.append("unknown")

                    # Extract canonical
                    canonical = val.get("canonical", "")
                    canonicals.append(str(canonical) if canonical else "")

                # Handle old format: "marked" or "unmarked" directly
                elif isinstance(val, str):
                    val = val.lower()
                    if val in ("marked", "()"):
                        results.append("marked")
                    elif val in ("unmarked", "void", "nothing"):
                        results.append("unmarked")
                    else:
                        results.append("unknown")
                    canonicals.append("")
                else:
                    results.append("unknown")
                    canonicals.append("")

            return {"results": results, "canonicals": canonicals}
        except (json.JSONDecodeError, TypeError):
            pass

    return empty


@scorer(metrics=[accuracy(), stderr()])
def lof_single_scorer() -> Scorer:
    """
    Scorer for single form evaluation tasks.

    Extracts the model's answer and compares it to the target.
    Returns "C" for correct, "I" for incorrect.
    """

    async def score(state: TaskState, target: Target) -> Score:
        # Extract the model's answer
        response = state.output.completion
        answer = extract_single_answer(response)

        # Get expected answer from target
        expected = target.text.lower()

        # Determine correctness
        is_correct = answer == expected
        value = "C" if is_correct else "I"

        # Create explanation
        explanation = f"Expected: {expected}, Got: {answer}"

        return Score(value=value, answer=answer, explanation=explanation)

    return score


@scorer(
    metrics={
        "all_correct": [mean()],
        "per_item_accuracy": [mean()],
        "structure_accuracy": [mean()],
    }
)
def lof_composite_scorer() -> Scorer:
    """
    Scorer for composite form evaluation tasks.

    Computes three metrics:
    - all_correct: 1.0 if all results correct, 0.0 otherwise (primary)
    - per_item_accuracy: fraction of results correctly evaluated (diagnostic)
    - structure_accuracy: fraction of canonical forms correct (diagnostic)

    Designed for use with epochs=Epochs(3, "at_least_2") at task level.
    Human baseline expectation: 98-100% all_correct.
    """

    async def score(state: TaskState, target: Target) -> Score:
        # Extract the model's answer
        response = state.output.completion

        # Get targets from metadata
        targets = state.metadata.get("targets", [])
        original_expressions = state.metadata.get("original_expressions", [])
        n = len(targets)

        # Extract composite answer
        extracted = extract_composite_answer(response, n)
        results = extracted["results"]
        canonicals = extracted["canonicals"]

        # Compute per-item result accuracy
        correct_results = sum(1 for i, result in enumerate(results) if result == targets[i])
        per_item_accuracy = correct_results / n if n > 0 else 0.0

        # Compute all_correct (results)
        all_correct = 1.0 if correct_results == n else 0.0

        # Compute structure_accuracy (canonical forms) using structural comparison
        correct_canonicals = sum(
            1
            for i, canonical in enumerate(canonicals)
            if canonical
            and i < len(original_expressions)
            and normalize_to_parens(canonical) == normalize_to_parens(original_expressions[i])
        )
        structure_accuracy = correct_canonicals / n if n > 0 else 0.0

        # Create explanation
        explanation = (
            f"Results correct: {correct_results}/{n}, "
            f"Canonicals correct: {correct_canonicals}/{n}, "
            f"All correct: {all_correct == 1.0}"
        )

        return Score(
            value={
                "all_correct": all_correct,
                "per_item_accuracy": per_item_accuracy,
                "structure_accuracy": structure_accuracy,
            },
            answer=str(extracted),
            explanation=explanation,
        )

    return score
