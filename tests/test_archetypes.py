"""Tests for the M5 archetype migrations: parens@1 and pattern@1.

See ``.lattice/notes/rendering-architecture-2026-07-04.md`` and
``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md`` (DB-4, M5).

Structural equivalence (acceptance criterion 3) is checked, per family, by
the property the migration must actually preserve:

- ``pattern`` (sexpr): every preset is deterministic with no randomness on
  either the legacy or the new path, so the two are byte-identical.
- ``parens``/``bracket_swap`` (noisy_parens): both draw glyphs randomly, so
  byte-identical output across the two independent seeding schemes (the
  legacy renderer's shared-``rng`` draws vs. the composed pipeline's
  content-addressed substreams) is not a meaningful bar. Both are checked
  against the one thing that must actually match: parsed back through the
  bracket-agnostic reader, each recovers exactly the source form's
  containment relation -- which is what "structurally equivalent" means for
  this family.
"""

from __future__ import annotations

import random

import pytest

from lofbench.core import generate_form_string
from lofbench.renderers import get_renderer
from lofbench.renderers.archetypes.parens import (
    ParensArchetype,
    bracket_agnostic_reader,
    bracket_agnostic_string_to_form,
)
from lofbench.renderers.archetypes.pattern import PatternArchetype, render_pattern
from lofbench.renderers.pipeline.nodes import containment_relation, form_to_nodes, nodes_to_form

SEEDED_FORMS = [generate_form_string(rng=random.Random(i)) or "()" for i in range(50)]


class TestParensArchetype:
    def test_build_matches_canonical_serialisation(self):
        archetype = ParensArchetype()
        for form_string in SEEDED_FORMS:
            root = form_to_nodes(form_string)
            base = archetype.build(root, random.Random(0))
            assert base.payload == nodes_to_form(root) == form_string

    def test_node_map_covers_every_node_once(self):
        archetype = ParensArchetype()
        root = form_to_nodes("(()())")
        base = archetype.build(root, random.Random(0))
        from lofbench.renderers.pipeline.archetype import assert_node_map_complete

        assert_node_map_complete(root, base.node_map)  # F2 checker, no raise

    def test_bracket_agnostic_reader_recovers_canonical_forms(self):
        for form_string in SEEDED_FORMS:
            expected = containment_relation(form_to_nodes(form_string))
            recovered = containment_relation(bracket_agnostic_reader(form_string))
            assert recovered == expected


class TestParensCanonicalDialect:
    """Acceptance criterion 3: 'canonical' resolves to structurally
    equivalent output -- here, identical output, since neither path is
    randomised.
    """

    def test_matches_legacy_canonical_no_spacing(self):
        old = get_renderer("canonical")  # spacing=False default
        new = get_renderer("parens.reference-v1")
        for form_string in SEEDED_FORMS:
            assert old.render(form_string).rendered == new.render(form_string).rendered


class TestBracketSwapStructuralEquivalence:
    """Acceptance criterion 3 for 'noisy_parens': both the legacy renderer
    and the new bracket_swap-based dialect preserve containment -- the
    property "structurally equivalent" actually asserts for a family whose
    glyphs are randomised.
    """

    @pytest.mark.parametrize("mismatched", [False, True])
    def test_legacy_renderer_preserves_containment(self, mismatched):
        old = get_renderer("noisy_parens", mismatched=mismatched)
        for form_string in SEEDED_FORMS:
            expected = containment_relation(form_to_nodes(form_string))
            rendered = old.render(form_string, random.Random(7)).rendered
            recovered = containment_relation(bracket_agnostic_reader(rendered))
            assert recovered == expected

    def test_new_dialect_preserves_containment_matched(self):
        new = get_renderer("parens.noisy-v1")
        for form_string in SEEDED_FORMS:
            expected = containment_relation(form_to_nodes(form_string))
            result = new.render(form_string)
            assert result.metadata["structure_verified"] is True
            recovered = containment_relation(bracket_agnostic_reader(result.rendered))
            assert recovered == expected

    def test_new_dialect_preserves_containment_mismatched(self):
        new = get_renderer("parens.noisy-mismatched-v1")
        for form_string in SEEDED_FORMS:
            expected = containment_relation(form_to_nodes(form_string))
            result = new.render(form_string)
            assert result.metadata["structure_verified"] is True
            recovered = containment_relation(bracket_agnostic_reader(result.rendered))
            assert recovered == expected


class TestBracketAgnosticStringToForm:
    def test_ignores_non_bracket_characters(self):
        # whitespace_jitter-style noise between tokens must not confuse the
        # reader.
        assert bracket_agnostic_string_to_form("( x ( y ) z )") == [[[]]]

    def test_recognises_every_bracket_pair(self):
        assert bracket_agnostic_string_to_form("⟨⟩") == [[]]  # angle brackets
        assert bracket_agnostic_string_to_form("[{}]") == [[[]]]


class TestPatternArchetype:
    def test_default_build_matches_legacy_default_preset(self):
        archetype = PatternArchetype()
        old = get_renderer("sexpr")  # preset="default"
        for form_string in SEEDED_FORMS:
            root = form_to_nodes(form_string)
            base = archetype.build(root, random.Random(0))
            assert base.payload == old.render(form_string).rendered

    @pytest.mark.parametrize("preset", ["default", "lisp", "scheme", "python", "rust", "java"])
    def test_render_pattern_matches_legacy_for_every_migrated_preset(self, preset):
        from lofbench.renderers.sexpr import PRESETS

        cfg = PRESETS[preset]
        old = get_renderer("sexpr", preset=preset)
        for form_string in SEEDED_FORMS:
            root = form_to_nodes(form_string)
            rendered = render_pattern(
                root, cfg["symbol"], cfg["open"], cfg["close"], cfg["separator"]
            )
            assert rendered == old.render(form_string).rendered

    def test_haskell_preset_not_migrated(self):
        # Documented limitation: haskell's " "/"" delimiters have no bracket
        # a parse-back reader can key on, so it is intentionally left off
        # the named-dialect registry (see archetypes/pattern.py).
        with pytest.raises(ValueError):
            get_renderer("pattern.haskell")


class TestPatternNamedDialects:
    """Acceptance criterion 3 for 'sexpr': byte-identical output to the
    legacy renderer for every migrated preset, via the named dialect path.
    """

    @pytest.mark.parametrize("preset", ["default", "lisp", "scheme", "python", "rust", "java"])
    def test_named_dialect_matches_legacy_sexpr(self, preset):
        old = get_renderer("sexpr", preset=preset)
        dialect_id = "pattern.plain-v1" if preset == "default" else f"pattern.{preset}-v1"
        new = get_renderer(dialect_id)
        for form_string in SEEDED_FORMS:
            old_result = old.render(form_string)
            new_result = new.render(form_string)
            assert old_result.rendered == new_result.rendered
            assert new_result.metadata["structure_verified"] is True
            assert new_result.metadata["roundtrip_ok"] is True
