"""Tests for lofbench.pricing."""

from lofbench.pricing import PRICING_TABLE, compute_cost_usd


def test_known_model_prices_nonzero_usage():
    cost = compute_cost_usd(
        "anthropic/claude-sonnet-4-20250514",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    rates = PRICING_TABLE["anthropic/claude-sonnet-4-20250514"]
    assert cost == rates.input + rates.output


def test_zero_usage_is_zero_cost_for_known_model():
    """An errored call that never reached the model (all-zero usage) prices to
    an explicit 0.0, distinct from an unknown model's None."""
    cost = compute_cost_usd(
        "anthropic/claude-sonnet-4-20250514",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == 0.0


def test_unknown_model_is_none_never_silent_zero():
    cost = compute_cost_usd("some/unreleased-model", input_tokens=100, output_tokens=100)
    assert cost is None


def test_cache_tokens_priced_separately_from_input_output():
    cost = compute_cost_usd(
        "anthropic/claude-opus-4-5-20251101",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=1_000_000,
        cache_write_tokens=0,
    )
    rates = PRICING_TABLE["anthropic/claude-opus-4-5-20251101"]
    assert cost == rates.cache_read


def test_every_model_seen_in_real_logs_has_pricing():
    """Plan finding: models seen in the 48 real logs must all have entries."""
    expected_models = {
        "anthropic/claude-sonnet-4-20250514",
        "anthropic/claude-sonnet-4-5-20250929",
        "anthropic/claude-opus-4-5-20251101",
        "openai/gpt-5.2",
        "google/gemini-2.5-flash",
        "google/gemini-3-flash-preview",
        "google/gemini-3-pro-preview",
        "google/gemini-3.0-pro",
    }
    assert expected_models <= set(PRICING_TABLE.keys())
