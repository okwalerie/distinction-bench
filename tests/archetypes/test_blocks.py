"""Tests for ``blocks@1``: determinism and the exact 3-D interval-subset
predicate across a representative generation set.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "3. Blocks".
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest
from _helpers import all_test_forms, assert_verifies

from lofbench.renderers.archetypes import blocks
from lofbench.renderers.pipeline.archetype import Primitive
from lofbench.renderers.pipeline.nodes import form_to_nodes

ARCHETYPE = blocks.ARCHETYPE


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
    """Hand-labelled boxes, including a deliberately tight grandparent-to-
    grandchild margin case, per the plan's requirement that the predicate
    not silently pass a near miss."""

    def _box(self, x, z_lo):
        return (x, (blocks.Y_LO, blocks.Y_HI), (Fraction(z_lo), blocks.Z_TOP))

    def test_nested_box_passes(self):
        parent = Primitive(
            node_id="0", kind="block", geom={"box": self._box((Fraction(0), Fraction(1)), 0)}
        )
        child = Primitive(
            node_id="0.0",
            kind="block",
            geom={"box": self._box((Fraction(0, 1), Fraction(1, 2)), 1)},
        )
        assert ARCHETYPE.predicate(parent, child) is True

    def test_grandparent_grandchild_tight_margin_still_nests(self):
        parent = Primitive(
            node_id="0", kind="block", geom={"box": self._box((Fraction(0), Fraction(1)), 0)}
        )
        grandchild = Primitive(
            node_id="0.0.0",
            kind="block",
            geom={"box": self._box((Fraction(499, 1000), Fraction(501, 1000)), 2)},
        )
        assert ARCHETYPE.predicate(parent, grandchild) is True

    def test_disjoint_sibling_boxes_do_not_nest(self):
        left = Primitive(
            node_id="0", kind="block", geom={"box": self._box((Fraction(0), Fraction(1, 2)), 1)}
        )
        right = Primitive(
            node_id="1", kind="block", geom={"box": self._box((Fraction(1, 2), Fraction(1)), 1)}
        )
        assert ARCHETYPE.predicate(left, right) is False
        assert ARCHETYPE.predicate(right, left) is False

    def test_z_axis_out_of_range_fails_even_with_matching_footprint(self):
        parent = Primitive(
            node_id="0", kind="block", geom={"box": self._box((Fraction(0), Fraction(1)), 5)}
        )
        # Same footprint, but z starts *before* the parent's -- not a descendant.
        not_child = Primitive(
            node_id="0.0", kind="block", geom={"box": self._box((Fraction(0), Fraction(1)), 4)}
        )
        assert ARCHETYPE.predicate(parent, not_child) is False


class TestSvg:
    def test_to_svg_produces_well_formed_svg_document(self):
        import xml.dom.minidom as minidom

        root = form_to_nodes("(()(()()))")
        base = ARCHETYPE.build(root, random.Random(1))
        svg = blocks.to_svg(base)
        assert svg.startswith("<svg")
        minidom.parseString(svg)
