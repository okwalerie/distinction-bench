"""Core Laws of Form representation, simplification, and generation."""

from __future__ import annotations

import random

# =============================================================================
# Form Representation
# =============================================================================


def form_to_string(form: list) -> str:
    """Convert internal form representation (nested lists) to string.

    Examples:
        >>> form_to_string([[]])
        '()'
        >>> form_to_string([[], []])
        '()()'
        >>> form_to_string([[[]]])
        '(())'
    """
    if not form:
        return ""
    return "".join(f"({form_to_string(child)})" for child in form)


def string_to_form(s: str) -> list:
    """Parse string notation to internal form (nested lists).

    Examples:
        >>> string_to_form('()')
        [[]]
        >>> string_to_form('()()')
        [[], []]
        >>> string_to_form('(())')
        [[[]]]
    """
    result = []
    i = 0
    while i < len(s):
        if s[i] == "(":
            # Find matching close paren
            depth = 1
            j = i + 1
            while j < len(s) and depth > 0:
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                j += 1
            # Recursively parse contents
            result.append(string_to_form(s[i + 1 : j - 1]))
            i = j
        else:
            i += 1
    return result


def form_depth(form: list) -> int:
    """Calculate the nesting depth of a form (list representation)."""
    if not form:
        return 0
    return 1 + max(form_depth(child) for child in form)


def string_depth(s: str) -> int:
    """Calculate the nesting depth of a form string. O(n) and no allocation."""
    max_depth = 0
    current = 0
    for c in s:
        if c == "(":
            current += 1
            max_depth = max(max_depth, current)
        elif c == ")":
            current -= 1
    return max_depth


# =============================================================================
# Simplifier (Ground-Truth Oracle)
# =============================================================================


def simplify_string(s: str) -> tuple[str, list[tuple[str, str, str]]]:
    """
    O(n) simplification with step tracking.

    Applies Laws of Form axioms:
    - I1 (Calling): ()() -> () (adjacent identical marks condense)
    - I2 (Crossing): (()) -> void (nested mark cancels)

    Returns:
        (canonical_form, steps) where steps is [(before, after, axiom), ...]
        canonical_form is either "()" or "void"

    Examples:
        >>> simplify_string('()()')
        ('()', [('()()', '()', 'I1')])
        >>> simplify_string('(())')
        ('void', [('(())', '', 'I2')])
    """
    stack: list[list[str]] = [[]]  # Stack of frames, each frame is list of child forms
    steps = []

    for c in s:
        if c == "(":
            stack.append([])
        elif c == ")":
            children = stack.pop()

            # Apply I1: all identical → condense
            if len(children) > 1 and all(ch == children[0] for ch in children):
                before = "(" + "".join(children) + ")"
                children = [children[0]]
                after = "(" + "".join(children) + ")"
                steps.append((before, after, "I1"))

            # Apply I2: single mark inside → cancel to void
            content = "".join(children)
            if content == "()":
                before = "(())"
                steps.append((before, "", "I2"))
                # Don't append anything to parent (void)
            else:
                # Append this mark to parent
                stack[-1].append("(" + content + ")")

    # Root level
    result = stack[0]

    # Apply I1 at root if needed
    if len(result) > 1 and all(ch == result[0] for ch in result):
        before = "".join(result)
        result = [result[0]]
        after = "".join(result)
        steps.append((before, after, "I1"))

    final = "".join(result)
    return final if final else "void", steps


def canonical_string(s: str) -> str:
    """Get the canonical (simplified) form of a string.

    Returns either "()" or "void".
    """
    result, _ = simplify_string(s)
    return result


def evaluate(s: str) -> str:
    """Evaluate a form to 'marked' or 'unmarked'.

    Returns:
        'marked' if the form simplifies to ()
        'unmarked' if the form simplifies to void
    """
    result = canonical_string(s)
    return "marked" if result == "()" else "unmarked"


# =============================================================================
# Generator
# =============================================================================


def generate_form_string(
    min_depth: int = 1,
    max_depth: int = 3,
    max_width: int = 3,
    max_marks: int = 200,
    rng: random.Random | None = None,
) -> str:
    """
    Generate a random LoF form as a string directly (memory efficient).

    Args:
        min_depth: Minimum nesting depth (guaranteed)
        max_depth: Maximum nesting depth
        max_width: Maximum number of adjacent forms at any level
        max_marks: Maximum total number of marks (parenthesis pairs)
        rng: Random instance for reproducibility (uses global if None)

    Returns:
        A form as a string, e.g. "(()())" or ""
    """
    if rng is None:
        rng = random.Random()

    marks_used = [0]  # Mutable counter

    def build(remaining_min: int, remaining_max: int) -> str:
        if remaining_max <= 0 or marks_used[0] >= max_marks:
            return ""

        if remaining_min > 0:
            width = rng.randint(1, max_width)
        else:
            width = rng.randint(0, max_width)
            if width == 0:
                return ""

        # One child guaranteed deep, others can be shallow
        parts = []
        deep_idx = rng.randint(0, width - 1) if remaining_min > 0 else -1
        for i in range(width):
            if marks_used[0] >= max_marks:
                break
            marks_used[0] += 1
            if i == deep_idx:
                inner = build(remaining_min - 1, remaining_max - 1)
            else:
                inner = build(0, remaining_max - 1)
            parts.append(f"({inner})")
        return "".join(parts)

    return build(min_depth, max_depth)


# =============================================================================
# Test Case Generation
# =============================================================================

# Canonical difficulty configurations: (name, min_depth, max_depth, max_width, max_marks)
# These are invariant across the entire system
DIFFICULTY_CONFIGS = (
    ("1. easy", 2, 3, 2, 15),
    ("2. medium", 3, 4, 3, 20),
    ("3. hard", 4, 5, 6, 25),
    ("4. lunatic", 3, 8, 9, 30),
    ("5. extra", 4, 9, 3, 35),
)


def generate_test_cases(n: int = 4000, seed: int = 20251211) -> list[dict]:
    """Generate n test cases with varying difficulty.

    Uses DIFFICULTY_CONFIGS for consistent difficulty definitions.
    Distribution is uniform across all 5 difficulty levels. When n
    is not divisible by 5, extra cases go to earlier difficulties.

    Returns:
        List of dicts with keys: id, input, target, difficulty, depth, steps
    """
    rng = random.Random(seed)
    cases = []

    # Distribute n uniformly across difficulties
    base_per_diff = n // len(DIFFICULTY_CONFIGS)
    remainder = n % len(DIFFICULTY_CONFIGS)

    # Build list of difficulty assignments (uniform distribution)
    difficulties = []
    for i, config in enumerate(DIFFICULTY_CONFIGS):
        count = base_per_diff + (1 if i < remainder else 0)
        difficulties.extend([config] * count)

    rng.shuffle(difficulties)

    for i, (diff, min_d, max_d, max_w, max_m) in enumerate(difficulties):
        input_str = generate_form_string(
            min_depth=min_d, max_depth=max_d, max_width=max_w, max_marks=max_m, rng=rng
        )
        if not input_str:
            input_str = "()"  # Ensure non-empty
        depth = string_depth(input_str)

        # O(n) simplification directly on string
        canonical, steps = simplify_string(input_str)
        target = "marked" if canonical == "()" else "unmarked"

        cases.append(
            {
                "id": f"lof_{i + 1:03d}",
                "input": input_str,
                "target": target,
                "difficulty": diff,
                "depth": depth,
                "steps": len(steps),
            }
        )

    return cases


def generate_composite_test_cases(
    n_groups: int = 500, group_size: int = 8, seed: int = 2024
) -> list[dict]:
    """
    Generate composite test cases (groups of problems).

    Composite tests present N problems and ask for the count of () results.
    Random guessing drops from 50% to 1/(N+1).
    For N=8: random guess accuracy = 1/9 ≈ 11%

    Distribution is uniform across all 5 difficulty levels. When n_groups
    is not divisible by 5, extra cases go to earlier difficulties.

    Args:
        n_groups: Number of composite test cases
        group_size: Problems per group
        seed: Random seed for reproducibility

    Returns:
        List of dicts with keys: id, expressions, targets, count, difficulty, group_size
    """
    rng = random.Random(seed)
    cases = []

    # Distribute n_groups uniformly across difficulties
    base_per_diff = n_groups // len(DIFFICULTY_CONFIGS)
    remainder = n_groups % len(DIFFICULTY_CONFIGS)

    # Build list of difficulty assignments (uniform distribution)
    difficulty_assignments = []
    for i, config in enumerate(DIFFICULTY_CONFIGS):
        count = base_per_diff + (1 if i < remainder else 0)
        difficulty_assignments.extend([config] * count)

    # Shuffle to mix difficulties
    rng.shuffle(difficulty_assignments)

    for i, (diff_name, min_d, max_d, max_w, max_m) in enumerate(difficulty_assignments):
        expressions = []
        targets = []

        for _ in range(group_size):
            expr = generate_form_string(
                min_depth=min_d, max_depth=max_d, max_width=max_w, max_marks=max_m, rng=rng
            )
            if not expr:
                expr = "()"  # Ensure non-empty
            target = evaluate(expr)
            expressions.append(expr)
            targets.append(target)

        # Count how many simplify to marked
        mark_count = sum(1 for t in targets if t == "marked")

        cases.append(
            {
                "id": f"comp_{i + 1:03d}",
                "expressions": expressions,
                "targets": targets,
                "count": mark_count,
                "difficulty": diff_name,
                "group_size": group_size,
            }
        )

    return cases
