"""Input validation for the sandbox form field.

`lofbench.core.string_to_form` is not a safe validator: it silently skips any
non-paren character and never raises on unbalanced input (it just stops
parsing early). It cannot be trusted with public form-field input. This
module is the guard that sits in front of it, per the DB-6 plan's 5-step
contract:

1. Character whitelist: only ``(``, ``)``, and whitespace.
2. Balance check: parens must nest to zero net depth and never go negative.
3. Size cap: reject input over ``MAX_CHARS`` characters.
4. Depth cap: reject nesting over ``MAX_DEPTH``.
5. On success, round-trip the input through ``string_to_form`` /
   ``form_to_string`` so every renderer receives a canonical,
   whitespace-free form string rather than raw user text.
"""

from __future__ import annotations

from dataclasses import dataclass

from lofbench.core import form_to_string, string_to_form

MAX_CHARS = 200
MAX_DEPTH = 20

_ALLOWED_NON_PAREN = set(" \t\n\r\f\v")


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating a raw sandbox submission."""

    ok: bool
    form_string: str | None = None
    error: str | None = None


def validate_form_input(raw: str) -> ValidationResult:
    """Validate and canonicalise a raw sandbox submission.

    Returns a `ValidationResult`. On success, `form_string` is a
    whitespace-free, round-tripped canonical form string safe to hand to any
    renderer. On failure, `error` is a plain-English message safe to show a
    user; no renderer has been called.
    """
    if raw is None:
        return ValidationResult(ok=False, error="Enter a form using ( and ) characters.")

    stripped = raw.strip()
    if not stripped:
        return ValidationResult(ok=False, error="Enter a form using ( and ) characters.")

    # 3. Size cap (checked against the raw submission, before any stripping).
    if len(raw) > MAX_CHARS:
        return ValidationResult(
            ok=False,
            error=f"Input is too long ({len(raw)} characters). Max is {MAX_CHARS}.",
        )

    # 1. Character whitelist.
    for ch in raw:
        if ch not in "()" and ch not in _ALLOWED_NON_PAREN:
            return ValidationResult(
                ok=False,
                error=f"Unexpected character {ch!r}. Only '(', ')', and whitespace are allowed.",
            )

    # 2. Balance check + 4. depth cap, in a single pass.
    depth = 0
    max_depth = 0
    for ch in raw:
        if ch == "(":
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return ValidationResult(
                    ok=False,
                    error="Unbalanced parentheses: a ')' appears with no matching '('.",
                )

    if depth != 0:
        return ValidationResult(
            ok=False,
            error=f"Unbalanced parentheses: {depth} unclosed '(' remain.",
        )

    if max_depth > MAX_DEPTH:
        return ValidationResult(
            ok=False,
            error=f"Form nests too deep (depth {max_depth}, max is {MAX_DEPTH}).",
        )

    # 5. Round-trip through the canonical parser/serialiser.
    canonical = form_to_string(string_to_form(raw))
    return ValidationResult(ok=True, form_string=canonical)
