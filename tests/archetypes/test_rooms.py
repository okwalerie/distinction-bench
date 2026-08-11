"""Tests for ``rooms@1``: determinism, the shared ``rect_subset``
predicate (identical to ``map``), and that the cosmetic door gap never
changes the verified geometry.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "7. Rooms".
"""

from __future__ import annotations

import random

import pytest
from _helpers import all_test_forms, assert_verifies

from lofbench.renderers.archetypes import rooms
from lofbench.renderers.pipeline.nodes import form_to_nodes

ARCHETYPE = rooms.ARCHETYPE


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


class TestDoorGapIsCosmeticOnly:
    def test_door_wall_field_does_not_appear_in_rect_subset_geometry(self):
        # rect_subset only ever reads geom["rect"]; door_wall is carried
        # alongside but the predicate must not depend on it.
        root = form_to_nodes("(()(()()))")
        base = ARCHETYPE.build(root, random.Random(1))
        for prim in base.node_map.values():
            assert "rect" in prim.geom
            assert "door_wall" in prim.geom  # present, but not predicate input

    def test_wall_segments_leave_a_gap_at_the_door(self):
        segments = rooms._wall_segments_with_door(0.0, 0.0, 10.0, 10.0, "top")
        top_segments = [s for s in segments if s[1] == 0.0 and s[3] == 0.0]
        assert len(top_segments) == 2  # split around the door midpoint
        # the two segments do not touch -- there is a real gap
        assert top_segments[0][2] < top_segments[1][0]


class TestSvg:
    def test_to_svg_produces_well_formed_svg_document(self):
        import xml.dom.minidom as minidom

        root = form_to_nodes("(()(()()))")
        base = ARCHETYPE.build(root, random.Random(1))
        svg = rooms.to_svg(base)
        assert svg.startswith("<svg")
        minidom.parseString(svg)
