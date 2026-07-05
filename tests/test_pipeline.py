"""Tests for the M1 core abstractions of the composed rendering pipeline.

See ``.lattice/notes/rendering-architecture-2026-07-04.md`` and
``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md`` (DB-4, M1).
"""

from __future__ import annotations

import random

import pytest

from lofbench.core import generate_form_string
from lofbench.renderers import ComposedRenderer, get_renderer, list_renderers
from lofbench.renderers.pipeline.archetype import BaseRender, Primitive, induced_relation
from lofbench.renderers.pipeline.injector import Injector
from lofbench.renderers.pipeline.nodes import (
    containment_relation,
    form_to_nodes,
    iter_node_ids,
    nodes_to_form,
    relation_hash,
)
from lofbench.renderers.pipeline.verify import verify

# Renderer names the M5 migration has not touched yet.
LEGACY_RENDERER_NAMES = ["canonical", "noisy_parens", "circle", "nested_list", "sexpr"]
# Registry key equals renderer.name for every legacy name except "circle": the
# doc calls out the "circle" (registry key) vs "svg_circle" (renderer.name)
# asymmetry by name and defers fixing it to M5's registry-key-equals-name
# enforcement, alongside the SVGCircleRenderer -> enclosure@1 migration.
KEY_EQUALS_NAME_RESOLVED_IN_M5 = {"circle"}


class TestNodeRoundTrip:
    """nodes_to_form(form_to_nodes(s)) == s, the left-inverse the plan requires."""

    @pytest.mark.parametrize(
        "form_string",
        ["", "()", "()()", "(())", "(()())", "((()))", "(()()())", "(((())))"],
    )
    def test_hand_picked_forms_round_trip(self, form_string):
        root = form_to_nodes(form_string)
        assert nodes_to_form(root) == form_string

    def test_generated_forms_round_trip(self):
        rng = random.Random(2026)
        for _ in range(200):
            form_string = generate_form_string(rng=rng) or "()"
            root = form_to_nodes(form_string)
            assert nodes_to_form(root) == form_string


class TestNodeIds:
    """Structural path ids are stable and dialect-invariant."""

    def test_path_ids_match_doc_example_shape(self):
        # "(()())" -> one top mark "0" containing two children "0.0", "0.1"
        root = form_to_nodes("(()())")
        assert {n.id for n in root.children} == {"0"}
        outer = root.children[0]
        assert {c.id for c in outer.children} == {"0.0", "0.1"}

    def test_three_level_path_matches_doc_string(self):
        # third mark down: top mark 0, its child 1, that child's child 0
        root = form_to_nodes("(()(()))")
        outer = root.children[0]
        # outer's children: "0.0" (leaf), "0.1" (has a child "0.1.0")
        deep = [c for c in outer.children if c.id == "0.1"][0]
        assert deep.children[0].id == "0.1.0"

    def test_wrapper_root_excluded_from_ids(self):
        root = form_to_nodes("()()")
        assert "" not in set(iter_node_ids(root))


class TestContainmentRelation:
    """The one relation every dialect must preserve."""

    def test_siblings_have_no_relation(self):
        root = form_to_nodes("()()")
        assert containment_relation(root) == frozenset()

    def test_single_nesting(self):
        root = form_to_nodes("(())")
        assert containment_relation(root) == frozenset({("0", "0.0")})

    def test_transitive_closure_included(self):
        root = form_to_nodes("((()))")
        rel = containment_relation(root)
        assert ("0", "0.0") in rel
        assert ("0.0", "0.0.0") in rel
        assert ("0", "0.0.0") in rel  # transitive: grandparent contains grandchild

    def test_two_children_both_related_to_parent(self):
        root = form_to_nodes("(()())")
        assert containment_relation(root) == frozenset({("0", "0.0"), ("0", "0.1")})


class TestRelationHash:
    def test_stable_regardless_of_construction_order(self):
        rel_a = frozenset({("0", "0.0"), ("0", "0.1")})
        rel_b = frozenset({("0", "0.1"), ("0", "0.0")})
        assert relation_hash(rel_a) == relation_hash(rel_b)

    def test_differs_for_different_relations(self):
        rel_a = frozenset({("0", "0.0")})
        rel_b = frozenset({("0", "0.0"), ("0", "0.1")})
        assert relation_hash(rel_a) != relation_hash(rel_b)

    def test_empty_relation_is_stable(self):
        assert relation_hash(frozenset()) == relation_hash(frozenset())


class TestNodeMapExactlyOnce:
    def test_correct_node_map_covers_every_id_once(self):
        root = form_to_nodes("(()())")
        all_ids = list(iter_node_ids(root))
        assert len(all_ids) == len(set(all_ids)), "ids must not repeat"
        node_map = {nid: Primitive(node_id=nid, kind="token", geom={}) for nid in all_ids}
        assert set(node_map) == set(iter_node_ids(root))

    def test_missing_id_is_detectable(self):
        root = form_to_nodes("(()())")
        all_ids = set(iter_node_ids(root))
        broken_map = {nid: Primitive(node_id=nid, kind="token", geom={}) for nid in all_ids}
        broken_map.pop(next(iter(all_ids)))
        assert set(broken_map) != set(iter_node_ids(root))


class TestVerifySkeleton:
    """M1 ships one hash-equality check after the archetype builds."""

    def test_identity_text_render_verifies(self):
        form_string = "(()())"
        root = form_to_nodes(form_string)
        base = BaseRender(modality="text", payload=form_string, node_map={})
        ok, observed_hash = verify(base, root, pred=lambda p, c: False)
        assert ok is True
        assert observed_hash == relation_hash(containment_relation(root))

    def test_structure_breaking_text_render_fails_verify(self):
        root = form_to_nodes("(()())")
        # A payload that parses back to a different structure (one child, not two).
        broken_base = BaseRender(modality="text", payload="((()))", node_map={})
        ok, _ = verify(broken_base, root, pred=lambda p, c: False)
        assert ok is False

    def test_spatial_induced_relation_folds_predicate_over_all_pairs(self):
        # Three nested circles: a contains b contains c, by depth predicate.
        a = Primitive(node_id="0", kind="circle", geom={"depth": 0})
        b = Primitive(node_id="0.0", kind="circle", geom={"depth": 1})
        c = Primitive(node_id="0.0.0", kind="circle", geom={"depth": 2})
        base = BaseRender(
            modality="spatial",
            payload=a,
            node_map={"0": a, "0.0": b, "0.0.0": c},
        )

        def contains(parent: Primitive, child: Primitive) -> bool:
            return child.geom["depth"] > parent.geom["depth"]

        observed = induced_relation(base, contains)
        assert observed == frozenset({("0", "0.0"), ("0", "0.0.0"), ("0.0", "0.0.0")})

    def test_spatial_verify_matches_hand_built_identity_render(self):
        root = form_to_nodes("((()))")
        a = Primitive(node_id="0", kind="circle", geom={"depth": 0})
        b = Primitive(node_id="0.0", kind="circle", geom={"depth": 1})
        c = Primitive(node_id="0.0.0", kind="circle", geom={"depth": 2})
        base = BaseRender(modality="spatial", payload=a, node_map={"0": a, "0.0": b, "0.0.0": c})

        def contains(parent: Primitive, child: Primitive) -> bool:
            return child.geom["depth"] > parent.geom["depth"]

        ok, observed_hash = verify(base, root, contains)
        assert ok is True
        assert observed_hash == relation_hash(containment_relation(root))


class TestRegistryKeyEqualsName:
    """A continuous integration test that permanently kills the circle/svg_circle asymmetry."""

    @pytest.mark.parametrize("name", LEGACY_RENDERER_NAMES)
    def test_legacy_names_still_resolve(self, name):
        # M2 acceptance: "existing five names still resolve unchanged" --
        # key-equals-name is a separate, M5-scoped invariant (see below).
        get_renderer(name)

    @pytest.mark.parametrize(
        "name", [n for n in LEGACY_RENDERER_NAMES if n not in KEY_EQUALS_NAME_RESOLVED_IN_M5]
    )
    def test_legacy_names_key_equals_renderer_name(self, name):
        renderer = get_renderer(name)
        assert renderer.name == name

    def test_composed_is_registered_but_exempt(self):
        # "composed" is the ad-hoc research-path key; its true identity comes
        # from the caller-supplied spec.dialect_id, not the registry key.
        assert "composed" in list_renderers()


class TestComposedRendererConstruction:
    def test_construct_via_spec(self):
        from lofbench.renderers.pipeline.spec import DialectSpec

        spec = DialectSpec(dialect_id="test.ctor-v1", family="test", archetype="unused@1")
        renderer = ComposedRenderer(spec)
        assert renderer.name == "test.ctor-v1"
        assert renderer.spec is spec

    def test_construct_via_kwargs_ad_hoc_path(self):
        renderer = get_renderer(
            "composed",
            dialect_id="adhoc.ctor-v1",
            family="test",
            archetype="unused@1",
            injectors=[],
        )
        assert isinstance(renderer, ComposedRenderer)
        assert renderer.name == "adhoc.ctor-v1"


class _FixtureInjector:
    """A minimal Injector fixture -- just the data shape, no real transform."""

    name = "fixture_injector"
    version = "1"
    modalities = frozenset({"text"})
    applicability = frozenset({"parens"})

    def apply(self, base, root, rng, **params):
        return base


class TestInjectorApplicabilityShape:
    """M1 ships the applicability data shape; the M6 admission gate reads it later."""

    def test_fixture_satisfies_injector_shape(self):
        fixture = _FixtureInjector()
        assert isinstance(fixture, Injector)
        assert "parens" in fixture.applicability
        assert "rooms" not in fixture.applicability
