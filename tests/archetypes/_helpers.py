"""Shared helpers for the DB-3 archetype test suite. Not itself a test
module (no ``test_`` prefix, so pytest does not collect it)."""

from __future__ import annotations

import random
from collections.abc import Iterator

from lofbench.core import generate_form_string
from lofbench.renderers.pipeline.nodes import form_to_nodes
from lofbench.renderers.pipeline.verify import verify

# Hand-picked forms covering void, single mark, siblings, chains and mixed
# branching -- cheap, deterministic edge cases alongside the generated sweep.
HAND_PICKED_FORMS = [
    "()",
    "(())",
    "()()",
    "(()())",
    "((()))",
    "(()()())",
    "((())(()))",
    "(((()))((())))",
    "()()()()",
    "((((()))))",
]


def generated_forms(seed: int = 2026, count: int = 60) -> Iterator[str]:
    """Forms spanning depth 1-5, branching 1-4 -- the plan's acceptance
    criterion coverage for containment-predicate verification."""
    rng = random.Random(seed)
    for _ in range(count):
        yield generate_form_string(min_depth=1, max_depth=5, max_width=4, rng=rng) or "()"


def all_test_forms() -> list[str]:
    return HAND_PICKED_FORMS + list(generated_forms())


def assert_verifies(archetype, form_string: str) -> None:
    """The one machine-verified check every archetype must pass: the
    induced relation from the archetype's own predicate equals the form's
    real containment relation."""
    root = form_to_nodes(form_string)
    base = archetype.build(root, random.Random(1))
    ok, _ = verify(base, root, archetype.predicate)
    assert ok, f"{archetype.name}@{archetype.version} failed to verify on {form_string!r}"
