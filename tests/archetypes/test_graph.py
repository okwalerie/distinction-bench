"""Tests for ``graph@1``: determinism, the edge-endpoint geometric sanity
check, and containment-relation verification across a representative
generation set.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "4. Graph".
"""

from __future__ import annotations

import random

import pytest
from _helpers import all_test_forms, assert_verifies

from lofbench.renderers.archetypes import graph
from lofbench.renderers.pipeline.nodes import form_to_nodes

ARCHETYPE = graph.ARCHETYPE


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


class TestGeometricSanity:
    """Every drawn edge's endpoints must coincide with the parent's and
    child's actual ring positions -- ring position alone is not the
    containment signal, the edge is."""

    @pytest.mark.parametrize("form_string", all_test_forms())
    def test_generated_forms_pass_geometric_sanity(self, form_string):
        root = form_to_nodes(form_string)
        base = ARCHETYPE.build(root, random.Random(1))
        ok, msg = graph.geometric_sanity(base)
        assert ok, msg

    def test_detects_edge_wired_to_wrong_position(self):
        root = form_to_nodes("(())")
        base = ARCHETYPE.build(root, random.Random(1))
        scene = base.payload
        edges = [p for p in scene.primitives if p.kind == "edge"]
        assert edges, "expected at least one edge for a nested form"
        bad_edge = edges[0]
        bad_geom = dict(bad_edge.geom)
        bad_geom["x1"] += 1000.0  # corrupt the recorded start point
        from dataclasses import replace

        corrupted = replace(bad_edge, geom=bad_geom)
        other_primitives = [p for p in scene.primitives if p is not bad_edge]
        from lofbench.renderers.archetypes._svg import Scene
        from lofbench.renderers.pipeline.archetype import BaseRender

        corrupted_scene = Scene(primitives=tuple(other_primitives) + (corrupted,))
        corrupted_base = BaseRender(
            modality="spatial", payload=corrupted_scene, node_map=corrupted_scene.node_map
        )
        ok, msg = graph.geometric_sanity(corrupted_base)
        assert ok is False
        assert "ring position" in msg


class TestSvg:
    def test_to_svg_produces_well_formed_svg_document(self):
        import xml.dom.minidom as minidom

        root = form_to_nodes("(()(()()))")
        base = ARCHETYPE.build(root, random.Random(1))
        svg = graph.to_svg(base)
        assert svg.startswith("<svg")
        minidom.parseString(svg)
