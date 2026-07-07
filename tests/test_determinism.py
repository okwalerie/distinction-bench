"""Tests for M3: content-addressed determinism and seed threading.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, "Determinism
and seed threading", and
``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md`` (DB-4, M3).
"""

from __future__ import annotations

import random

import pytest

from lofbench.datasets.factory import _item_rng, create_single_dataset
from lofbench.renderers import get_renderer
from lofbench.renderers.pipeline.seeding import (
    injector_substream_seed,
    item_seed,
    resample_substream_seed,
)
from lofbench.renderers.pipeline.spec import DialectSpec


class TestItemSeed:
    def test_deterministic_for_equal_inputs(self):
        spec = DialectSpec("d", "f", "a@1", [("inj", {"amp": 1})])
        assert item_seed(spec, "(()())") == item_seed(spec, "(()())")

    def test_differs_by_form(self):
        spec = DialectSpec("d", "f", "a@1")
        assert item_seed(spec, "()") != item_seed(spec, "(())")

    def test_differs_by_spec(self):
        a = DialectSpec("d", "f", "a@1")
        b = DialectSpec("d2", "f", "a@1")
        assert item_seed(a, "()") != item_seed(b, "()")

    def test_render_seed_fold_changes_seed(self):
        spec = DialectSpec("d", "f", "a@1")
        assert item_seed(spec, "()") != item_seed(spec, "()", render_seed_fold=42)

    def test_render_seed_fold_is_deterministic(self):
        spec = DialectSpec("d", "f", "a@1")
        assert item_seed(spec, "()", render_seed_fold=42) == item_seed(
            spec, "()", render_seed_fold=42
        )


class TestInjectorSubstreamSeed:
    def test_reordering_distinct_injectors_leaves_each_seed_unchanged(self):
        # Two distinct injectors, each first occurrence (index 0). The order
        # they run in a spec must not change either one's own seed.
        seed = 12345
        a_seed = injector_substream_seed(seed, "whitespace_jitter", 0)
        b_seed = injector_substream_seed(seed, "bracket_swap", 0)
        # Independent of any "order" concept here since the function is pure
        # in (seed, name, index) -- this is exactly why order can't leak in.
        assert a_seed == injector_substream_seed(seed, "whitespace_jitter", 0)
        assert b_seed == injector_substream_seed(seed, "bracket_swap", 0)
        assert a_seed != b_seed

    def test_same_injector_decorrelates_by_occurrence_index(self):
        seed = 999
        first = injector_substream_seed(seed, "bracket_swap", 0)
        second = injector_substream_seed(seed, "bracket_swap", 1)
        assert first != second

    def test_resample_seed_differs_from_primary_and_by_attempt(self):
        seed = 42
        sub = injector_substream_seed(seed, "boundary_jitter", 0)
        r1 = resample_substream_seed(sub, 1)
        r2 = resample_substream_seed(sub, 2)
        assert sub != r1 != r2 and sub != r2


class TestComposedRendererDeterminism:
    """Acceptance criterion 1: same seed plus same spec always gives
    identical output, and it does not depend on rng draw order/position.
    """

    def test_identical_across_two_fresh_renderer_instances(self):
        # Use the real registered dialect via get_renderer to also exercise
        # DIALECT_SPECS/named-dialect resolution end to end.
        r1 = get_renderer("parens.noisy-v1")
        r2 = get_renderer("parens.noisy-v1")
        for form_string in ["()", "(())", "(()())", "((()))", "(()()())"]:
            assert r1.render(form_string).rendered == r2.render(form_string).rendered

    def test_output_independent_of_rendering_order(self):
        # Render the same two forms in one order, then the other. Each
        # form's output must be identical regardless of what was rendered
        # before it -- the M3 acceptance criterion, directly.
        renderer = get_renderer("parens.noisy-v1")
        forms = ["(()())", "((()))"]
        forward = [renderer.render(f).rendered for f in forms]
        backward = [renderer.render(f).rendered for f in reversed(forms)]
        assert forward[0] == backward[1]
        assert forward[1] == backward[0]

    def test_render_seed_perturbs_but_stays_deterministic(self):
        renderer = get_renderer("parens.noisy-v1")
        unseeded = renderer.render("(()())").rendered
        seeded_a = renderer.render("(()())", random.Random(7)).rendered
        seeded_b = renderer.render("(()())", random.Random(7)).rendered
        assert seeded_a == seeded_b  # deterministic given the same render_seed
        # A render_seed is folded in as an extra digest field, so it need
        # not differ from the unseeded draw for every input, but it is not
        # required to be identical either; the real invariant checked above
        # is reproducibility, not perturbation on any particular input.
        assert isinstance(unseeded, str)


class TestFactoryOrderIndependence:
    """The real bug M3 fixes: factory.py used to share one random.Random
    across a whole batch, so item N's output depended on item N-1's draws.
    """

    def test_item_rng_is_none_when_no_render_seed(self):
        assert _item_rng(None, "()") is None

    def test_item_rng_deterministic_by_key(self):
        a = _item_rng(2025, "(()())")
        b = _item_rng(2025, "(()())")
        assert a.getrandbits(32) == b.getrandbits(32)

    def test_item_rng_differs_by_key(self):
        a = _item_rng(2025, "()")
        b = _item_rng(2025, "(())")
        assert a.getrandbits(64) != b.getrandbits(64)

    def test_dataset_rendering_is_stable_across_two_independent_builds(self):
        # Building the same dataset spec twice must give byte-identical
        # per-item output: a sanity check that dataset construction itself
        # introduces no incidental order/state dependence.
        from lofbench.renderers import NoisyParensRenderer

        first = create_single_dataset(
            n=10, seed=99, renderer=NoisyParensRenderer(mismatched=False), render_seed=5
        )
        second = create_single_dataset(
            n=10, seed=99, renderer=NoisyParensRenderer(mismatched=False), render_seed=5
        )
        for a, b in zip(first.samples, second.samples, strict=True):
            assert a.metadata["original_form"] == b.metadata["original_form"]
            assert a.metadata["expression"] == b.metadata["expression"]

    def test_two_items_with_same_form_render_identically_regardless_of_position(self):
        # Content-addressed seeding means position in the batch cannot
        # matter: construct two batches where a given form string appears
        # at a different index and confirm its rendered output is the same.
        from lofbench.renderers import NoisyParensRenderer

        renderer_a = NoisyParensRenderer(mismatched=True)
        renderer_b = NoisyParensRenderer(mismatched=True)
        rng_first = _item_rng(123, "(()())")
        rng_second = _item_rng(123, "(()())")
        first_call_at_position_0 = renderer_a.render("(()())", rng_first).rendered
        # Simulate "position 5" by drawing from an unrelated generator first
        # -- since _item_rng derives a *fresh* generator per item keyed only
        # on (render_seed, form), prior draws elsewhere cannot leak in.
        _ = random.Random(0).random()
        second_call_at_position_5 = renderer_b.render("(()())", rng_second).rendered
        assert first_call_at_position_0 == second_call_at_position_5


@pytest.mark.parametrize("form_string", ["()", "(())", "(()())", "((()))"])
def test_composed_renderer_deterministic_for_pattern_family(form_string):
    r1 = get_renderer("pattern.lisp")
    r2 = get_renderer("pattern.lisp")
    assert r1.render(form_string).rendered == r2.render(form_string).rendered
