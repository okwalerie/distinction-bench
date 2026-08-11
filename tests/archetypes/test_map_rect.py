"""Tests for ``map@1``: determinism and the exact ``rect_subset``
predicate across a representative generation set.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "5. Map".
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest
from _helpers import all_test_forms, assert_verifies

from lofbench.renderers.archetypes import map_rect
from lofbench.renderers.pipeline.archetype import Primitive
from lofbench.renderers.pipeline.nodes import form_to_nodes

ARCHETYPE = map_rect.ARCHETYPE


class TestDeterminism:
    def test_two_fresh_renders_are_identical(self):
        root = form_to_nodes("((())(()()))")
        a = ARCHETYPE.build(root, random.Random(1))
        b = ARCHETYPE.build(root, random.Random(2))
        assert a.node_map == b.node_map

    def test_void_form_renders_empty_scene(self):
        root = form_to_nodes("")
        base = ARCHETYPE.build(root, random.Random(1))
        assert base.node_map == {}


class TestVerification:
    @pytest.mark.parametrize("form_string", all_test_forms())
    def test_containment_relation_matches(self, form_string):
        assert_verifies(ARCHETYPE, form_string)


class TestPredicateUnits:
    def test_nested_rect_passes(self):
        parent = Primitive(
            node_id="0",
            kind="rect",
            geom={"rect": (Fraction(0), Fraction(0), Fraction(1), Fraction(1))},
        )
        child = Primitive(
            node_id="0.0",
            kind="rect",
            geom={"rect": (Fraction(1, 4), Fraction(1, 4), Fraction(3, 4), Fraction(3, 4))},
        )
        assert ARCHETYPE.predicate(parent, child) is True

    def test_tight_case_does_not_silently_pass_a_near_miss(self):
        parent = Primitive(
            node_id="0",
            kind="rect",
            geom={"rect": (Fraction(0), Fraction(0), Fraction(1), Fraction(1))},
        )
        just_outside = Primitive(
            node_id="0.0",
            kind="rect",
            geom={
                "rect": (
                    Fraction(0),
                    Fraction(0),
                    Fraction(1) + Fraction(1, 10**6),
                    Fraction(1),
                )
            },
        )
        assert ARCHETYPE.predicate(parent, just_outside) is False

    def test_disjoint_regions_do_not_nest(self):
        left = Primitive(
            node_id="0",
            kind="rect",
            geom={"rect": (Fraction(0), Fraction(0), Fraction(1, 2), Fraction(1))},
        )
        right = Primitive(
            node_id="1",
            kind="rect",
            geom={"rect": (Fraction(1, 2), Fraction(0), Fraction(1), Fraction(1))},
        )
        assert ARCHETYPE.predicate(left, right) is False


class TestSvg:
    def test_to_svg_produces_well_formed_svg_document(self):
        import xml.dom.minidom as minidom

        root = form_to_nodes("(()(()()))")
        base = ARCHETYPE.build(root, random.Random(1))
        svg = map_rect.to_svg(base)
        assert svg.startswith("<svg")
        minidom.parseString(svg)
