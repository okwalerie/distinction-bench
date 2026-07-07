"""Tests for the shared interval-nesting layout (:mod:`nesting`) and both
archetypes built on it: ``rna_arc@1`` and ``paths_lite@1``.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "8. Paths" and
"9. RNA arc".
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest
from _helpers import all_test_forms, assert_verifies

from lofbench.renderers.archetypes import nesting, paths_lite, rna_arc
from lofbench.renderers.pipeline.archetype import Primitive
from lofbench.renderers.pipeline.nodes import form_to_nodes

ARCHETYPES = {"rna_arc": rna_arc.ARCHETYPE, "paths_lite": paths_lite.ARCHETYPE}


class TestDeterminism:
    @pytest.mark.parametrize("name", ARCHETYPES)
    def test_two_fresh_renders_are_identical(self, name):
        archetype = ARCHETYPES[name]
        root = form_to_nodes("((())(()()))")
        a = archetype.build(root, random.Random(1))
        b = archetype.build(root, random.Random(2))
        assert a.node_map == b.node_map

    @pytest.mark.parametrize("name", ARCHETYPES)
    def test_void_form_renders_empty_scene(self, name):
        archetype = ARCHETYPES[name]
        root = form_to_nodes("")
        base = archetype.build(root, random.Random(1))
        assert base.node_map == {}


class TestVerification:
    @pytest.mark.parametrize("name", ARCHETYPES)
    @pytest.mark.parametrize("form_string", all_test_forms())
    def test_containment_relation_matches(self, name, form_string):
        assert_verifies(ARCHETYPES[name], form_string)


class TestSingleChainStrictNesting:
    """The case the module docstring calls out by name: a single-child
    chain gives parent and child the *same* raw leaf range, so strictness
    depends entirely on the per-depth margin, not on the raw range differing."""

    def test_deeply_chained_form_nests_strictly_at_every_level(self):
        root = form_to_nodes("(((((())))))")
        base = nesting.build_nesting_base(root, kind="arc")
        chain = sorted(base.node_map.values(), key=lambda p: p.geom["depth"])
        for shallower, deeper in zip(chain, chain[1:]):
            assert nesting.nesting_predicate(shallower, deeper) is True
            lo_s, hi_s = shallower.geom["span"]
            lo_d, hi_d = deeper.geom["span"]
            assert lo_d > lo_s and hi_d < hi_s, "nesting must be strict, not merely equal"


class TestPredicateUnits:
    def _prim(self, node_id, span, depth):
        return Primitive(node_id=node_id, kind="arc", geom={"span": span, "depth": depth})

    def test_nested_span_passes(self):
        parent = self._prim("0", (Fraction(-1, 2), Fraction(5, 2)), 0)
        child = self._prim("0.0", (Fraction(0), Fraction(2)), 1)
        assert nesting.nesting_predicate(parent, child) is True

    def test_tight_case_does_not_silently_pass_a_near_miss(self):
        parent = self._prim("0", (Fraction(0), Fraction(1)), 0)
        just_outside = self._prim("0.0", (Fraction(0), Fraction(1) + Fraction(1, 10**6)), 1)
        assert nesting.nesting_predicate(parent, just_outside) is False

    def test_disjoint_spans_do_not_nest(self):
        left = self._prim("0", (Fraction(0), Fraction(1)), 1)
        right = self._prim("1", (Fraction(2), Fraction(3)), 1)
        assert nesting.nesting_predicate(left, right) is False


class TestSvg:
    @pytest.mark.parametrize("name,module", [("rna_arc", rna_arc), ("paths_lite", paths_lite)])
    def test_to_svg_produces_well_formed_svg_document(self, name, module):
        import xml.dom.minidom as minidom

        root = form_to_nodes("(()(()()))")
        base = module.ARCHETYPE.build(root, random.Random(1))
        svg = module.to_svg(base)
        assert svg.startswith("<svg")
        minidom.parseString(svg)
