"""Tests for the M5 injector migrations: whitespace_jitter, bracket_swap,
preset. See ``.lattice/notes/rendering-architecture-2026-07-04.md`` and
``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md`` (DB-4, M5).
"""

from __future__ import annotations

import random

import pytest

from lofbench.core import generate_form_string
from lofbench.renderers import get_renderer
from lofbench.renderers.archetypes.parens import bracket_agnostic_reader
from lofbench.renderers.injectors.bracket_swap import BracketSwapInjector
from lofbench.renderers.injectors.preset import PresetInjector
from lofbench.renderers.injectors.whitespace_jitter import WhitespaceJitterInjector
from lofbench.renderers.pipeline.archetype import BaseRender
from lofbench.renderers.pipeline.nodes import containment_relation, form_to_nodes, nodes_to_form
from lofbench.renderers.pipeline.registry import INJECTOR_REGISTRY
from lofbench.renderers.sexpr import PRESETS

SEEDED_FORMS = [generate_form_string(rng=random.Random(i)) or "()" for i in range(50)]


class TestInjectorRegistration:
    @pytest.mark.parametrize("name", ["whitespace_jitter", "bracket_swap", "preset"])
    def test_registered(self, name):
        assert name in INJECTOR_REGISTRY


class TestWhitespaceJitter:
    def test_preserves_containment(self):
        injector = WhitespaceJitterInjector()
        for form_string in SEEDED_FORMS:
            root = form_to_nodes(form_string)
            base = BaseRender(modality="text", payload=nodes_to_form(root), node_map={})
            jittered = injector.apply(base, root, random.Random(3), amp=2, prob=1.0)
            assert containment_relation(form_to_nodes(jittered.payload)) == containment_relation(
                root
            )

    def test_deterministic_given_same_rng_state(self):
        injector = WhitespaceJitterInjector()
        root = form_to_nodes("(()()(()))")
        base = BaseRender(modality="text", payload=nodes_to_form(root), node_map={})
        a = injector.apply(base, root, random.Random(11), amp=1).payload
        b = injector.apply(base, root, random.Random(11), amp=1).payload
        assert a == b

    def test_no_jitter_is_identity(self):
        injector = WhitespaceJitterInjector()
        root = form_to_nodes("(()())")
        base = BaseRender(modality="text", payload=nodes_to_form(root), node_map={})
        result = injector.apply(base, root, random.Random(0), prob=0.0)
        assert result.payload == "(()())"


class TestBracketSwap:
    @pytest.mark.parametrize("mismatched", [False, True])
    def test_preserves_containment(self, mismatched):
        injector = BracketSwapInjector()
        for form_string in SEEDED_FORMS:
            root = form_to_nodes(form_string)
            base = BaseRender(modality="text", payload=nodes_to_form(root), node_map={})
            swapped = injector.apply(base, root, random.Random(5), mismatched=mismatched)
            recovered = containment_relation(bracket_agnostic_reader(swapped.payload))
            assert recovered == containment_relation(root)

    def test_matched_mode_uses_one_pair_per_depth(self):
        # Every mark at the same structural depth gets the same bracket
        # pair in matched mode -- the "walks the node map" replacement for
        # the deleted character-scan depth counter.
        injector = BracketSwapInjector()
        root = form_to_nodes("(()())")  # two depth-1 marks (siblings)
        base = BaseRender(modality="text", payload=nodes_to_form(root), node_map={})
        swapped = injector.apply(base, root, random.Random(1), mismatched=False).payload
        # Both inner marks share one bracket pair, so the string is exactly
        # one glyph pair repeated twice, wrapped in the outer pair.
        assert len(swapped) == 6  # outer open/close + 2x inner open/close

    def test_deterministic_given_same_rng_state(self):
        injector = BracketSwapInjector()
        root = form_to_nodes("(()(()))")
        base = BaseRender(modality="text", payload=nodes_to_form(root), node_map={})
        a = injector.apply(base, root, random.Random(9), mismatched=True).payload
        b = injector.apply(base, root, random.Random(9), mismatched=True).payload
        assert a == b


class TestPresetInjector:
    @pytest.mark.parametrize("preset", ["default", "lisp", "scheme", "python", "rust", "java"])
    def test_matches_legacy_sexpr_preset(self, preset):
        injector = PresetInjector()
        old = get_renderer("sexpr", preset=preset)
        for form_string in SEEDED_FORMS:
            root = form_to_nodes(form_string)
            base = BaseRender(modality="text", payload="unused", node_map={})
            result = injector.apply(base, root, random.Random(0), name=preset)
            assert result.payload == old.render(form_string).rendered

    def test_unknown_preset_falls_back_to_default(self):
        injector = PresetInjector()
        root = form_to_nodes("()")
        base = BaseRender(modality="text", payload="unused", node_map={})
        default_payload = injector.apply(base, root, random.Random(0), name="default").payload
        fallback_payload = injector.apply(
            base, root, random.Random(0), name="not-a-real-preset"
        ).payload
        assert fallback_payload == default_payload

    def test_explicit_overrides_beat_preset(self):
        injector = PresetInjector()
        root = form_to_nodes("()")
        base = BaseRender(modality="text", payload="unused", node_map={})
        result = injector.apply(base, root, random.Random(0), name="lisp", symbol="mark")
        assert "mark" in result.payload
        assert PRESETS["lisp"]["symbol"] not in result.payload
