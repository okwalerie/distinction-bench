"""Tests for the DB-4 M8 task-layer provenance stamp.

``single_lof_task``/``composite_lof_task`` stamp ``suite_version``,
``dialect_id``, ``family``, and ``form_id`` into ``Task.metadata`` and every
sample's metadata, alongside the existing ``renderer``/``renderer_config``/
``render_seed``/``difficulty`` fields -- see the M8 section of
``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md``. Composed dialects
(DB-2/DB-3 archetypes via ``ComposedRenderer``) already stamp
suite_version/dialect_id/family via ``renderers/pipeline/provenance.py``;
this task-layer stamp's real job is ``form_id`` (which a ``ComposedRenderer``
can never supply on its own) plus fallbacks for legacy (pre-M5) renderers.
"""

from __future__ import annotations

from lofbench.tasks.composite import composite_lof_task
from lofbench.tasks.single import single_lof_task


class TestSingleTaskLegacyRenderer:
    def test_default_canonical_renderer_stamps_fallbacks(self):
        t = single_lof_task(n=3, seed=1)
        assert t.metadata["suite_version"] == "adhoc"
        assert t.metadata["dialect_id"] == "canonical"
        assert t.metadata["family"] == "canonical"

        for sample in t.dataset.samples:
            assert sample.metadata["suite_version"] == "adhoc"
            assert sample.metadata["dialect_id"] == "canonical"
            assert sample.metadata["family"] == "canonical"
            # form_id is always the real suite-table id (the case id),
            # never absent, and matches sample.id.
            assert sample.metadata["form_id"] == sample.id
            assert sample.metadata["render_metadata"]["form_id"] == sample.id

    def test_noisy_parens_mismatched_still_works_and_stamps(self):
        """-T renderer=noisy_parens -T renderer_config='{"mismatched": true}'
        must keep working (no forced migration) and still gets a fallback
        stamp."""
        t = single_lof_task(
            n=2, seed=1, renderer="noisy_parens", renderer_config={"mismatched": True}
        )
        assert t.metadata["dialect_id"] == "noisy_parens"
        for sample in t.dataset.samples:
            assert sample.metadata["dialect_id"] == "noisy_parens"
            assert sample.metadata["form_id"] == sample.id


class TestSingleTaskComposedRenderer:
    def test_composed_named_dialect_uses_real_provenance_not_fallback(self):
        t = single_lof_task(n=2, seed=1, renderer="parens.noisy-mismatched-v1")
        assert t.metadata["suite_version"] == "adhoc"  # DialectSpec default
        assert t.metadata["dialect_id"] == "parens.noisy-mismatched-v1"
        assert t.metadata["family"] == "parens"

        for sample in t.dataset.samples:
            assert sample.metadata["dialect_id"] == "parens.noisy-mismatched-v1"
            assert sample.metadata["family"] == "parens"
            rm = sample.metadata["render_metadata"]
            # Real ComposedRenderer provenance survives untouched.
            assert rm["structure_verified"] is True
            assert rm["injectors"][0]["params"]["mismatched"] is True
            # form_id is the field ComposedRenderer can never supply on its
            # own; the task layer must inject it.
            assert rm["form_id"] == sample.id


class TestCompositeTaskStamp:
    def test_default_canonical_renderer_stamps_fallbacks(self):
        t = composite_lof_task(n_groups=2, group_size=3, seed=1)
        assert t.metadata["dialect_id"] == "canonical"

        for sample in t.dataset.samples:
            assert sample.metadata["dialect_id"] == "canonical"
            # Group-level form_id is the group's own sample id (no
            # per-expression id exists at the generator level).
            assert sample.metadata["form_id"] == sample.id
            rm_list = sample.metadata["render_metadata"]
            assert len(rm_list) == 3
            for j, rm in enumerate(rm_list):
                assert rm["form_id"] == f"{sample.id}#{j}"

    def test_composed_named_dialect(self):
        t = composite_lof_task(n_groups=1, group_size=2, seed=1, renderer="parens.noisy-v1")
        assert t.metadata["dialect_id"] == "parens.noisy-v1"
        assert t.metadata["family"] == "parens"
        sample = t.dataset.samples[0]
        assert sample.metadata["dialect_id"] == "parens.noisy-v1"
        for j, rm in enumerate(sample.metadata["render_metadata"]):
            assert rm["dialect_id"] == "parens.noisy-v1"
            assert rm["form_id"] == f"{sample.id}#{j}"
