"""Pricing table for eval-log token cost derivation.

No log emitted by inspect_ai carries a cost field (only raw token counts in
``sample.output.usage``). This module owns a small, versioned $/1M-token table
so the data pipeline (and DB-7's budget sheet) can derive ``cost_usd`` per
call without re-deriving rates ad hoc.

Rates are the provider's published per-million-token list price at time of
writing, best-effort and approximate -- they are not fetched live and will
drift. ``PRICING_TABLE_VERSION`` is stamped into every ``calls`` row so a
rerun with updated rates is diffable against an older one.

Reasoning/thinking tokens are billed as output tokens by every provider seen
in these logs (``sample.output.usage.reasoning_tokens`` is already a subset
of ``output_tokens``, confirmed against real logs where
``input_tokens + output_tokens == total_tokens``). They are therefore never
priced separately here -- doing so would double count.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICING_TABLE_VERSION = "pricing-v1-2026-07-04"


@dataclass(frozen=True)
class ModelRates:
    """$ per 1,000,000 tokens, by usage category."""

    input: float
    output: float
    cache_read: float
    cache_write: float


# Best-effort published list prices at time of writing. Approximate; not
# fetched live. Update PRICING_TABLE_VERSION whenever this table changes so
# historical cost rollups stay diffable.
PRICING_TABLE: dict[str, ModelRates] = {
    "anthropic/claude-sonnet-4-20250514": ModelRates(
        input=3.00, output=15.00, cache_read=0.30, cache_write=3.75
    ),
    "anthropic/claude-sonnet-4-5-20250929": ModelRates(
        input=3.00, output=15.00, cache_read=0.30, cache_write=3.75
    ),
    "anthropic/claude-opus-4-5-20251101": ModelRates(
        input=5.00, output=25.00, cache_read=0.50, cache_write=6.25
    ),
    "openai/gpt-5.2": ModelRates(input=1.25, output=10.00, cache_read=0.125, cache_write=1.25),
    "google/gemini-2.5-flash": ModelRates(
        input=0.30, output=2.50, cache_read=0.075, cache_write=0.30
    ),
    "google/gemini-3-flash-preview": ModelRates(
        input=0.30, output=2.50, cache_read=0.075, cache_write=0.30
    ),
    "google/gemini-3-pro-preview": ModelRates(
        input=1.25, output=10.00, cache_read=0.125, cache_write=1.25
    ),
    "google/gemini-3.0-pro": ModelRates(
        input=1.25, output=10.00, cache_read=0.125, cache_write=1.25
    ),
}


def compute_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Derive $ cost for one call from raw token counts.

    Returns ``None`` (never a silent 0.0) when the model has no entry in
    ``PRICING_TABLE``, so an unpriced model can never look like a free run
    in a budget rollup. A call that made no API request at all (e.g. an
    errored sample with no usage) should pass all-zero token counts, which
    correctly prices to 0.0 for a known model.

    Reasoning tokens are not a parameter here on purpose: they are already
    included in ``output_tokens`` by every provider seen in these logs, so
    passing them again would double count. See module docstring.
    """
    rates = PRICING_TABLE.get(model)
    if rates is None:
        return None
    return (
        input_tokens * rates.input
        + output_tokens * rates.output
        + cache_read_tokens * rates.cache_read
        + cache_write_tokens * rates.cache_write
    ) / 1_000_000
