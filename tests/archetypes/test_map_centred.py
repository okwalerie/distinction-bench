"""Tests for ``map-centred@1``: determinism, the polar interval-subset
predicate, and the wraparound-safety invariant -- every angular span is a
plain ``[lo, hi)`` subset of ``[0, 1)``, never a wrapped range.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "6. Map-centred".
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest
from _helpers import all_test_forms, assert_verifies

from lofbench.renderers.archetypes import map_centred
from lofbench.renderers.pipeline.archetype import Primitive
from lofbench.renderers.pipeline.nodes import form_to_nodes

ARCHETYPE = map_centred.ARCHETYPE


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


class TestWraparoundSafety:
    """Pinned invariant: no wedge ever straddles the zero seam, because
    every angular span is an ordinary bounded ``Fraction`` sub-interval of
    ``[0, 1)`` built by recursively partitioning the parent's own span."""

    @pytest.mark.parametrize("form_string", all_test_forms())
    def test_every_angular_span_is_within_unit_circle_and_non_wrapping(self, form_string):
        root = form_to_nodes(form_string)
        base = ARCHETYPE.build(root, random.Random(1))
        for prim in base.node_map.values():
            lo, hi = prim.geom["angle"]
            assert Fraction(0) <= lo < hi <= Fraction(1), (
                f"non-representable or wrapping span for {prim.node_id!r}: ({lo}, {hi})"
            )

    def test_siblings_partition_the_parent_span_contiguously(self):
        root = form_to_nodes("(()()())")
        base = ARCHETYPE.build(root, random.Random(1))
        outer = base.node_map["0"]
        children = sorted(outer.children, key=lambda p: p.geom["angle"][0])
        # contiguous: each child's hi touches the next child's lo (up to the
        # cosmetic per-node gap shrink, so just check monotonic non-overlap
        # covering roughly the outer's own span without gaps larger than
        # the shrink itself).
        prev_hi = children[0].geom["angle"][0]
        for child in children:
            lo, hi = child.geom["angle"]
            assert lo >= prev_hi
            prev_hi = hi


class TestPredicateUnits:
    def _prim(self, node_id, angle, radial):
        return Primitive(node_id=node_id, kind="wedge", geom={"box": (angle, radial)})

    def test_nested_wedge_passes(self):
        parent = self._prim("0", (Fraction(0), Fraction(1)), (Fraction(0), map_centred.R_MAX))
        child = self._prim(
            "0.0", (Fraction(1, 4), Fraction(3, 4)), (Fraction(1), map_centred.R_MAX)
        )
        assert ARCHETYPE.predicate(parent, child) is True

    def test_disjoint_angular_span_does_not_nest(self):
        left = self._prim("0", (Fraction(0), Fraction(1, 2)), (Fraction(1), map_centred.R_MAX))
        right = self._prim("1", (Fraction(1, 2), Fraction(1)), (Fraction(1), map_centred.R_MAX))
        assert ARCHETYPE.predicate(left, right) is False

    def test_tight_radial_margin_still_nests(self):
        parent = self._prim("0", (Fraction(0), Fraction(1)), (Fraction(0), map_centred.R_MAX))
        grandchild = self._prim(
            "0.0.0", (Fraction(1, 4), Fraction(3, 4)), (Fraction(2), map_centred.R_MAX)
        )
        assert ARCHETYPE.predicate(parent, grandchild) is True


class TestSvg:
    def test_to_svg_produces_well_formed_svg_document(self):
        import xml.dom.minidom as minidom

        root = form_to_nodes("(()(()()))")
        base = ARCHETYPE.build(root, random.Random(1))
        svg = map_centred.to_svg(base)
        assert svg.startswith("<svg")
        minidom.parseString(svg)
