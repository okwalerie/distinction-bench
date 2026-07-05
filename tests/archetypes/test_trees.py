"""Tests for ``trees@1``: determinism, the combined structural + geometric
sanity predicate, and containment-relation verification across a
representative generation set.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "2. Trees" and
"Node ids and the containment predicate for trees and graph".
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest
from _helpers import all_test_forms, assert_verifies

from lofbench.renderers.archetypes import trees
from lofbench.renderers.archetypes._svg import Scene
from lofbench.renderers.pipeline.archetype import Primitive
from lofbench.renderers.pipeline.nodes import form_to_nodes

ARCHETYPE = trees.ARCHETYPE


class TestDeterminism:
    def test_two_fresh_renders_are_identical(self):
        root = form_to_nodes("((())(()()))")
        a = ARCHETYPE.build(root, random.Random(1))
        b = ARCHETYPE.build(root, random.Random(2))  # different seed: no rng consumed
        assert a.node_map == b.node_map

    def test_void_form_renders_empty_scene(self):
        root = form_to_nodes("")
        base = ARCHETYPE.build(root, random.Random(1))
        assert base.node_map == {}


class TestVerification:
    @pytest.mark.parametrize("form_string", all_test_forms())
    def test_containment_relation_matches(self, form_string):
        assert_verifies(ARCHETYPE, form_string)


class TestGeometricSanity:
    """The geometric sanity check the plan requires on top of the
    structural predicate: no overlapping node discs, every child strictly
    below its parent."""

    @pytest.mark.parametrize("form_string", all_test_forms())
    def test_generated_forms_pass_geometric_sanity(self, form_string):
        root = form_to_nodes(form_string)
        base = ARCHETYPE.build(root, random.Random(1))
        ok, msg = trees.geometric_sanity(base)
        assert ok, msg

    def test_detects_overlapping_discs(self):
        a = Primitive(
            node_id="0", kind="circle", geom={"x": Fraction(0), "y": Fraction(0), "r": Fraction(1)}
        )
        b = Primitive(
            node_id="1",
            kind="circle",
            geom={"x": Fraction(1, 10), "y": Fraction(0), "r": Fraction(1)},
        )
        base_node_map = {"0": a, "1": b}
        base = _fake_base(base_node_map)
        ok, msg = trees.geometric_sanity(base)
        assert ok is False
        assert "overlap" in msg

    def test_detects_child_not_below_parent(self):
        parent = Primitive(
            node_id="0",
            kind="circle",
            geom={"x": Fraction(0), "y": Fraction(1), "r": trees.RADIUS},
        )
        child = Primitive(
            node_id="0.0",
            kind="circle",
            geom={"x": Fraction(0), "y": Fraction(0), "r": trees.RADIUS},  # same y, not below
        )
        parent_with_child = Primitive(
            node_id="0",
            kind="circle",
            geom=parent.geom,
            children=(child,),
        )
        node_map = {"0": parent_with_child, "0.0": child}
        base = _fake_base(node_map)
        ok, msg = trees.geometric_sanity(base)
        assert ok is False
        assert "below" in msg

    def test_tight_but_not_overlapping_discs_pass(self):
        # Two discs exactly RADIUS*2 + tiny epsilon apart: does not overlap.
        r = trees.RADIUS
        a = Primitive(node_id="0", kind="circle", geom={"x": Fraction(0), "y": Fraction(0), "r": r})
        b = Primitive(
            node_id="1",
            kind="circle",
            geom={"x": r * 2 + Fraction(1, 1000), "y": Fraction(0), "r": r},
        )
        base = _fake_base({"0": a, "1": b})
        ok, _ = trees.geometric_sanity(base)
        assert ok is True


class TestSvg:
    def test_to_svg_produces_well_formed_svg_document(self):
        import xml.dom.minidom as minidom

        root = form_to_nodes("(()(()()))")
        base = ARCHETYPE.build(root, random.Random(1))
        svg = trees.to_svg(base)
        assert svg.startswith("<svg")
        minidom.parseString(svg)  # raises on malformed XML


def _fake_base(node_map: dict[str, Primitive]):
    from lofbench.renderers.pipeline.archetype import BaseRender

    return BaseRender(
        modality="spatial", payload=Scene(primitives=tuple(node_map.values())), node_map=node_map
    )
