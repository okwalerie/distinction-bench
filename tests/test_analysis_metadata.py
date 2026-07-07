"""Tests for the DB-4 M8 ``get_log_metadata`` rewrite.

``get_log_metadata`` now reads dialect/family/injector params from
sample-level provenance (``sample.metadata.render_metadata``) when a log
went through the composed pipeline, and falls back to the old
``task_args``-only derivation only for legacy (pre-DB-4) logs -- see
``.lattice/notes/rendering-architecture-2026-07-04.md``'s "Provenance
metadata schema" and the M8 section of
``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from inspect_ai.log import read_eval_log

from lofbench.analysis import get_log_metadata
from lofbench.renderers import get_renderer

FIXTURES = Path(__file__).parent / "fixtures" / "logs"


def _fake_log(*, task_args, model, samples):
    eval_ns = SimpleNamespace(
        task_args=task_args,
        model=model,
        model_generate_config=None,
        dataset=None,
        config=None,
    )
    return SimpleNamespace(eval=eval_ns, samples=samples)


def _fake_sample(*, sample_id, metadata, usage=None):
    output = SimpleNamespace(usage=usage) if usage is not None else None
    return SimpleNamespace(id=sample_id, metadata=metadata, output=output)


class TestComposedPathReadsSampleProvenance:
    """A composed-pipeline log carries the full render_provenance schema on
    each sample; get_log_metadata must read dialect/family/mismatched from
    there, never from task_args (which a composed run never populates with
    a matching renderer_config anyway)."""

    def _render_metadata_for(self, dialect_id: str, form_string: str = "(()())") -> dict:
        renderer = get_renderer(dialect_id)
        return renderer.render(form_string).metadata

    def test_dialect_and_family_from_sample_provenance(self):
        rm = self._render_metadata_for("parens.noisy-mismatched-v1")
        log = _fake_log(
            task_args={"renderer": "composed"},  # composed logs never set renderer_config
            model="anthropic/claude-sonnet-4-20250514",
            samples=[_fake_sample(sample_id="lof_001", metadata={"render_metadata": rm})],
        )
        meta = get_log_metadata(log)
        assert meta["dialect"] == "parens.noisy-mismatched-v1"
        assert meta["family"] == "parens"
        assert meta["provenance_source"] == "sample_provenance"

    def test_mismatched_read_from_bracket_swap_injector_not_top_level_config(self):
        """The bug the rewrite fixes: mismatched lives inside a bracket_swap
        injector entry under the composed scheme, not a top-level
        renderer_config key. The old `config.get('mismatched')` lookup
        always read False here."""
        rm_mismatched = self._render_metadata_for("parens.noisy-mismatched-v1")
        rm_balanced = self._render_metadata_for("parens.noisy-v1")

        meta_mismatched = {"render_metadata": rm_mismatched}
        meta_balanced = {"render_metadata": rm_balanced}
        log_mismatched = _fake_log(
            task_args={"renderer": "composed"},
            model="anthropic/claude-sonnet-4-20250514",
            samples=[_fake_sample(sample_id="lof_001", metadata=meta_mismatched)],
        )
        log_balanced = _fake_log(
            task_args={"renderer": "composed"},
            model="anthropic/claude-sonnet-4-20250514",
            samples=[_fake_sample(sample_id="lof_001", metadata=meta_balanced)],
        )

        assert get_log_metadata(log_mismatched)["mismatched"] is True
        assert get_log_metadata(log_balanced)["mismatched"] is False

    def test_composite_style_render_metadata_list_uses_first_entry(self):
        """Composite samples carry render_metadata as a list (one dict per
        expression); the first entry is representative since one renderer
        instance renders the whole group."""
        rm = self._render_metadata_for("parens.noisy-mismatched-v1")
        log = _fake_log(
            task_args={"renderer": "composed"},
            model="openai/gpt-5.2",
            samples=[_fake_sample(sample_id="comp_001", metadata={"render_metadata": [rm, rm]})],
        )
        meta = get_log_metadata(log)
        assert meta["dialect"] == "parens.noisy-mismatched-v1"
        assert meta["provenance_source"] == "sample_provenance"


class TestLegacyFallbackPath:
    """Pre-DB-4 logs carry no dialect_id in render_metadata at all (only a
    bare renderer-specific dict like {"spacing": False}); the 48 real pilot
    logs in logs/ must still resolve via task_args."""

    def test_real_single_fixture_resolves_canonical(self):
        log = read_eval_log(str(FIXTURES / "single_small.eval"))
        meta = get_log_metadata(log)
        assert meta["dialect"] == "canonical"
        assert meta["provenance_source"] == "legacy_task_args"

    def test_real_composite_fixture_resolves_canonical(self):
        log = read_eval_log(str(FIXTURES / "composite_small_items_schema.eval"))
        meta = get_log_metadata(log)
        assert meta["dialect"] == "canonical"
        assert meta["provenance_source"] == "legacy_task_args"

    def test_noisy_parens_balanced_vs_mismatched_labels_survive(self):
        balanced = _fake_log(
            task_args={"renderer": "noisy_parens", "renderer_config": {"mismatched": False}},
            model="anthropic/claude-sonnet-4-20250514",
            samples=[_fake_sample(sample_id="lof_001", metadata={"render_metadata": {}})],
        )
        mismatched = _fake_log(
            task_args={"renderer": "noisy_parens", "renderer_config": {"mismatched": True}},
            model="anthropic/claude-sonnet-4-20250514",
            samples=[_fake_sample(sample_id="lof_001", metadata={"render_metadata": {}})],
        )
        assert get_log_metadata(balanced)["dialect"] == "noisy-balanced"
        assert get_log_metadata(mismatched)["dialect"] == "noisy-mismatch"
        assert get_log_metadata(balanced)["mismatched"] is False
        assert get_log_metadata(mismatched)["mismatched"] is True

    def test_legacy_renderer_kwargs_key_also_checked(self):
        """Some real pre-DB-4 logs use `renderer_kwargs` instead of
        `renderer_config` for the same payload (confirmed against
        tests/fixtures/logs/*.eval task_args)."""
        log = _fake_log(
            task_args={"renderer": "noisy_parens", "renderer_kwargs": {"mismatched": True}},
            model="anthropic/claude-sonnet-4-20250514",
            samples=[_fake_sample(sample_id="lof_001", metadata={"render_metadata": {}})],
        )
        assert get_log_metadata(log)["dialect"] == "noisy-mismatch"
        assert get_log_metadata(log)["mismatched"] is True

    def test_dead_parens_branch_is_gone(self):
        """Old code special-cased renderer == 'parens' -> dialect ==
        'canonical'. No renderer is ever registered under the literal name
        "parens" in this repo (the identity renderer is "canonical"), so
        that branch matched nothing real. The rewrite must NOT reproduce
        it: a (never-real) renderer literally named "parens" now falls
        through to its own name, proving the breaking rename is actually
        caught rather than silently kept alive."""
        log = _fake_log(
            task_args={"renderer": "parens", "renderer_config": {}},
            model="anthropic/claude-sonnet-4-20250514",
            samples=[_fake_sample(sample_id="lof_001", metadata={"render_metadata": {}})],
        )
        meta = get_log_metadata(log)
        assert meta["dialect"] == "parens"
        assert meta["dialect"] != "canonical"


class TestCostExtractionHook:
    """M8 also adds a cost-extraction hook so analysis.py doesn't drop the
    cost fields the DB-5 pipeline (lofbench.pricing) already computes."""

    def test_total_cost_usd_sums_priced_samples(self):
        usage_a = SimpleNamespace(input_tokens=1000, output_tokens=500)
        usage_b = SimpleNamespace(input_tokens=2000, output_tokens=1000)
        log = _fake_log(
            task_args={"renderer": "canonical"},
            model="anthropic/claude-sonnet-4-20250514",
            samples=[
                _fake_sample(sample_id="lof_001", metadata={}, usage=usage_a),
                _fake_sample(sample_id="lof_002", metadata={}, usage=usage_b),
            ],
        )
        meta = get_log_metadata(log)
        # rates: input=3.00, output=15.00 per 1M tokens
        expected = (1000 * 3.00 + 500 * 15.00 + 2000 * 3.00 + 1000 * 15.00) / 1_000_000
        assert meta["total_cost_usd"] is not None
        assert abs(meta["total_cost_usd"] - expected) < 1e-9

    def test_total_cost_usd_none_for_unpriced_model(self):
        usage = SimpleNamespace(input_tokens=100, output_tokens=50)
        log = _fake_log(
            task_args={"renderer": "canonical"},
            model="some-provider/unlisted-model",
            samples=[_fake_sample(sample_id="lof_001", metadata={}, usage=usage)],
        )
        assert get_log_metadata(log)["total_cost_usd"] is None

    def test_real_fixture_has_priced_cost(self):
        log = read_eval_log(str(FIXTURES / "single_small.eval"))
        meta = get_log_metadata(log)
        assert meta["total_cost_usd"] is not None
        assert meta["total_cost_usd"] >= 0
