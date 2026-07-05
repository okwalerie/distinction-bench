"""Tests for M7: the frozen suite and payload hashing.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, "Frozen forms
and payload hashing", and ``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md``
(DB-4, M7). ``suites/v1.json`` is the checked-in frozen artifact; some
tests here rerun it in full, which renders every (form, dialect) cell
again (including the nine spatial families through the pinned rasteriser)
and is therefore the slowest module in the suite -- accepted deliberately,
since the rerun gate's correctness on the real committed artifact is
exactly what M7 promises.
"""

from __future__ import annotations

import json

import pytest

from lofbench.core import DIFFICULTY_CONFIGS
from lofbench.renderers import get_renderer
from lofbench.suites import (
    DEFAULT_SUITE_VERSION,
    EXCLUDED_DIALECT_IDS,
    compute_cells,
    freeze_suite,
    frozen_dialect_specs,
    generate_form_table,
    load_suite,
    verify_suite,
)


class TestGenerateFormTable:
    def test_per_tier_count_and_total(self):
        forms = generate_form_table(seed=1, per_tier=5)
        assert len(forms) == 5 * len(DIFFICULTY_CONFIGS)
        by_tier: dict[str, int] = {}
        for form in forms:
            by_tier[form["difficulty"]] = by_tier.get(form["difficulty"], 0) + 1
        assert all(count == 5 for count in by_tier.values())

    def test_deterministic_for_same_seed(self):
        a = generate_form_table(seed=42, per_tier=6)
        b = generate_form_table(seed=42, per_tier=6)
        assert a == b

    def test_differs_for_different_seed(self):
        a = generate_form_table(seed=1, per_tier=6)
        b = generate_form_table(seed=2, per_tier=6)
        assert [f["form_string"] for f in a] != [f["form_string"] for f in b]

    def test_deduplicated_by_form_string(self):
        forms = generate_form_table(seed=1, per_tier=8)
        strings = [f["form_string"] for f in forms]
        assert len(strings) == len(set(strings))

    def test_form_ids_are_sequential_not_positional_lof_scheme_reused(self):
        # Ids are assigned once over the deduplicated table in tier order --
        # not lof_{i:03d} recomputed from a shuffled generate_test_cases
        # list, which the doc explicitly warns invalidates form_id meaning
        # across differently-sized runs.
        forms = generate_form_table(seed=1, per_tier=4)
        ids = [f["form_id"] for f in forms]
        assert ids == [f"lof_{i + 1:03d}" for i in range(len(forms))]

    def test_target_matches_simplification(self):
        from lofbench.core import evaluate

        forms = generate_form_table(seed=1, per_tier=4)
        for form in forms:
            assert form["target"] == evaluate(form["form_string"])


class TestFrozenDialectSpecs:
    def test_excludes_legacy_alias(self):
        specs = frozen_dialect_specs()
        assert "circle" not in specs
        assert EXCLUDED_DIALECT_IDS == frozenset({"circle"})

    def test_threads_suite_version(self):
        specs = frozen_dialect_specs(suite_version="test-v99")
        assert specs
        assert all(spec.suite_version == "test-v99" for spec in specs.values())

    def test_includes_parens_and_pattern_and_spatial_families(self):
        specs = frozen_dialect_specs()
        assert "parens.canonical" in specs
        assert "pattern.default" in specs
        assert "trees.canonical-v1" in specs
        assert "enclosure.canonical-v1" in specs
        assert "biopolymer.rna-dotbracket-v1" in specs  # DB-2 text dialect


class TestComputeCellsCollisionDetection:
    def test_identical_specs_under_different_ids_collide(self):
        # Two dialect_ids pointing at the literally same archetype and
        # empty injector list must be flagged as a collision on every
        # form -- exactly the "circle" vs "enclosure.canonical-v1"
        # situation the module docstring names, reproduced here in
        # miniature so the detector's own logic is tested in isolation
        # from the real 120-form/29-dialect grid.
        from dataclasses import replace as dc_replace

        base_specs = frozen_dialect_specs()
        parens_spec = base_specs["parens.canonical"]
        specs = {
            "parens.canonical": parens_spec,
            "parens.canonical-duplicate": dc_replace(
                parens_spec, dialect_id="parens.canonical-duplicate"
            ),
        }
        forms = generate_form_table(seed=1, per_tier=2)
        cells, collisions = compute_cells(forms, specs)
        assert len(cells) == 2 * len(forms)
        assert len(collisions) == len(forms)  # one collision per form
        for collision in collisions:
            assert collision["dialect_ids"] == ["parens.canonical", "parens.canonical-duplicate"]

    def test_distinct_dialects_do_not_spuriously_collide_on_a_nontrivial_form(self):
        specs = frozen_dialect_specs()
        subset = {
            k: specs[k]
            for k in ("parens.canonical", "parens.jitter-v1", "parens.noisy-v1", "pattern.default")
        }
        forms = [
            {
                "form_id": "lof_test_001",
                "form_string": "(()(()())((())))",
                "difficulty": "3. hard",
                "target": "unmarked",
            }
        ]
        _cells, collisions = compute_cells(forms, subset)
        assert collisions == []


class TestFreezeAndLoadRoundTrip:
    def test_freeze_then_load_round_trips(self, tmp_path):
        out_path = tmp_path / "test-suite.json"
        freeze_suite(version="test-suite", seed=7, per_tier=2, out_path=out_path)
        loaded = load_suite(version="test-suite", path=out_path)
        assert loaded.suite_version == "test-suite"
        assert len(loaded.forms) == 2 * len(DIFFICULTY_CONFIGS)
        assert all(spec.suite_version == "test-suite" for spec in loaded.specs.values())
        assert len(loaded.cells) == len(loaded.forms) * len(loaded.specs)

    def test_known_trivial_form_collision_is_flagged_not_hidden(self, tmp_path):
        # The doc's own worked scenario: on a trivial single-mark form,
        # whitespace_jitter has no adjacent-token pair to insert whitespace
        # around at all (its trigger conditions need "))", "((", or ")("
        # sequences a lone "()" never contains), so parens.canonical and
        # parens.jitter-v1 emit a byte-identical payload regardless of rng
        # -- deterministically, not by chance, unlike bracket_swap's
        # per-mark random draw on a deeper form.
        specs = {
            k: v
            for k, v in frozen_dialect_specs(suite_version="test-collision").items()
            if k in ("parens.canonical", "parens.jitter-v1")
        }
        forms = [
            {"form_id": "lof_001", "form_string": "()", "difficulty": "1. easy", "target": "marked"}
        ]
        cells, collisions = compute_cells(forms, specs)
        assert len(cells) == 2
        assert len(collisions) == 1
        assert collisions[0]["form_id"] == "lof_001"
        assert collisions[0]["dialect_ids"] == ["parens.canonical", "parens.jitter-v1"]


class TestVerifySuiteRerunGate:
    def test_mismatch_fails_loudly(self, tmp_path):
        out_path = tmp_path / "tamper-suite.json"
        freeze_suite(version="tamper-suite", seed=3, per_tier=2, out_path=out_path)
        data = json.loads(out_path.read_text())
        data["cells"][0]["payload_hash"] = "0" * 32  # deliberately wrong
        out_path.write_text(json.dumps(data))

        with pytest.raises(RuntimeError, match="rerun gate failed"):
            verify_suite(version="tamper-suite", path=out_path)

    def test_clean_suite_passes(self, tmp_path):
        out_path = tmp_path / "clean-suite.json"
        freeze_suite(version="clean-suite", seed=11, per_tier=2, out_path=out_path)
        verify_suite(version="clean-suite", path=out_path)  # must not raise

    def test_committed_v1_suite_reruns_clean(self):
        # The real, checked-in artifact. Renders all ~3,480 cells again
        # (including every spatial family through cairosvg) -- slow but
        # this is exactly what M7 promises: a rerun of the frozen suite
        # reproduces every payload hash.
        verify_suite(version=DEFAULT_SUITE_VERSION)


class TestUnversionedEditFailsTheGate:
    def test_a_renderer_edit_that_changes_the_payload_is_caught(self, tmp_path, monkeypatch):
        # Simulate an "un-versioned renderer edit" by monkeypatching the
        # registered parens archetype's build() to emit a different string
        # for the same form, then confirm the rerun gate catches it --
        # acceptance criterion 6's second half, without needing to
        # actually edit source and revert it.
        out_path = tmp_path / "edit-suite.json"
        freeze_suite(version="edit-suite", seed=5, per_tier=1, out_path=out_path)

        from lofbench.renderers.pipeline.registry import ARCHETYPE_REGISTRY

        parens = ARCHETYPE_REGISTRY["parens@1"]
        original_build = parens.build

        def _mutated_build(root, rng):
            base = original_build(root, rng)
            # Prepend a structurally-inert marker the canonical reader
            # ignores at the containment level but that changes the exact
            # emitted string (and hence the payload hash) -- whitespace is
            # not part of the canonical grammar's node identity.
            from lofbench.renderers.pipeline.archetype import BaseRender

            return BaseRender(
                modality="text", payload=" " + str(base.payload), node_map=base.node_map
            )

        monkeypatch.setattr(parens, "build", _mutated_build)

        with pytest.raises(RuntimeError, match="rerun gate failed"):
            verify_suite(version="edit-suite", path=out_path)


class TestGetRendererResolvesEveryFrozenDialect:
    """Sanity: every dialect_id the suite freezes must actually resolve
    through the normal get_renderer/-T path -- the suite is worthless if
    it names a dialect the registry cannot construct."""

    def test_all_frozen_dialects_resolve(self):
        specs = frozen_dialect_specs()
        for dialect_id in specs:
            get_renderer(dialect_id)  # must not raise
