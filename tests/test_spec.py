"""Tests for M2: registry widening, DialectSpec, and named-dialect wiring.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, "Pipeline spec
format", and ``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md`` (DB-4, M2).
"""

from __future__ import annotations

import pytest

from lofbench.renderers import ComposedRenderer, get_renderer
from lofbench.renderers.pipeline.archetype import BaseRender
from lofbench.renderers.pipeline.nodes import nodes_to_form
from lofbench.renderers.pipeline.registry import ARCHETYPE_REGISTRY
from lofbench.renderers.pipeline.spec import (
    DIALECT_SPECS,
    DialectSpec,
    register_named_dialect,
)


class TestDialectSpecRoundTrip:
    def test_from_dict_to_dict_round_trip(self):
        spec = DialectSpec(
            dialect_id="parens.mismatched-jitter-v1",
            family="parens",
            archetype="parens@1",
            injectors=[
                ("whitespace_jitter", {"amp": 2}),
                ("bracket_swap", {"mismatched": True}),
            ],
            style={"px": 512},
            suite_version="v1",
        )
        rebuilt = DialectSpec.from_dict(spec.to_dict())
        assert rebuilt == spec

    def test_from_dict_fills_in_defaults(self):
        spec = DialectSpec.from_dict({"dialect_id": "d", "family": "f", "archetype": "a@1"})
        assert spec.injectors == []
        assert spec.style == {}
        assert spec.suite_version == "adhoc"

    def test_to_dict_is_json_shaped(self):
        spec = DialectSpec("d", "f", "a@1", [("inj", {"amp": 1})], {"px": 8}, "v1")
        d = spec.to_dict()
        assert d["injectors"] == [["inj", {"amp": 1}]]
        assert d["style"] == {"px": 8}
        assert d["suite_version"] == "v1"


class TestSeedDigest:
    def test_deterministic_for_equal_specs(self):
        spec = DialectSpec("d", "f", "a@1", [("inj", {"amp": 1})], {"px": 8}, "v1")
        same = DialectSpec("d", "f", "a@1", [("inj", {"amp": 1})], {"px": 8}, "v1")
        assert spec.seed_digest() == same.seed_digest()

    def test_changes_with_injector_params(self):
        base = DialectSpec("d", "f", "a@1", [("inj", {"amp": 1})])
        changed = DialectSpec("d", "f", "a@1", [("inj", {"amp": 2})])
        assert base.seed_digest() != changed.seed_digest()

    def test_changes_with_injector_order(self):
        # order is semantic, per the doc -- a resequenced injector list must
        # not collide onto the same digest.
        forward = DialectSpec("d", "f", "a@1", [("x", {}), ("y", {})])
        backward = DialectSpec("d", "f", "a@1", [("y", {}), ("x", {})])
        assert forward.seed_digest() != backward.seed_digest()

    def test_changes_with_suite_version(self):
        v1 = DialectSpec("d", "f", "a@1", suite_version="v1")
        v2 = DialectSpec("d", "f", "a@1", suite_version="v2")
        assert v1.seed_digest() != v2.seed_digest()

    def test_changes_with_style(self):
        plain = DialectSpec("d", "f", "a@1", style={})
        styled = DialectSpec("d", "f", "a@1", style={"px": 512})
        assert plain.seed_digest() != styled.seed_digest()


LEGACY_RENDERER_NAMES = ["canonical", "noisy_parens", "circle", "nested_list", "sexpr"]


class TestLegacyNamesUnaffectedByWidening:
    @pytest.mark.parametrize("name", LEGACY_RENDERER_NAMES)
    def test_still_resolves(self, name):
        # M2's acceptance is only that resolution still works after the
        # registry value type widens -- "circle" is migrated in M5-rest to
        # the enclosure@1 composed dialect (see tests/test_pipeline.py's
        # TestRegistryKeyEqualsName for the key-equals-name assertion).
        get_renderer(name)


class _IdentityTextArchetype:
    """A trivial text archetype fixture: emits the form string unchanged."""

    name = "test-identity"
    version = "1"
    family = "test"
    modality = "text"

    def build(self, root, rng):
        return BaseRender(modality="text", payload=nodes_to_form(root), node_map={})

    def predicate(self, parent, child):
        return False  # unused for text modality


ARCHETYPE_REGISTRY.setdefault("test-identity@1", _IdentityTextArchetype())


class TestNamedDialectFactory:
    """The mechanism worked example 4 describes: one archetype, one registry line."""

    def test_named_dialect_resolves_via_get_renderer(self):
        spec = DialectSpec(
            dialect_id="test.identity-v1", family="test", archetype="test-identity@1"
        )
        register_named_dialect(spec)
        try:
            renderer = get_renderer("test.identity-v1")
            assert isinstance(renderer, ComposedRenderer)
            assert renderer.name == "test.identity-v1"
        finally:
            DIALECT_SPECS.pop("test.identity-v1", None)

    def test_kwargs_forward_into_style_without_mutating_checked_in_spec(self):
        spec = DialectSpec(
            dialect_id="test.identity-style-v1", family="test", archetype="test-identity@1"
        )
        register_named_dialect(spec)
        try:
            plain = get_renderer("test.identity-style-v1")
            assert plain.spec.style == {}

            styled = get_renderer("test.identity-style-v1", px=64)
            assert styled.spec.style == {"px": 64}

            # forwarding builds a new spec; the checked-in one is untouched.
            assert DIALECT_SPECS["test.identity-style-v1"].style == {}
        finally:
            DIALECT_SPECS.pop("test.identity-style-v1", None)

    def test_render_end_to_end_through_named_dialect(self):
        spec = DialectSpec(
            dialect_id="test.identity-render-v1", family="test", archetype="test-identity@1"
        )
        register_named_dialect(spec)
        try:
            renderer = get_renderer("test.identity-render-v1")
            result = renderer.render("(()())")
            assert result.rendered == "(()())"
            assert result.metadata["structure_verified"] is True
        finally:
            DIALECT_SPECS.pop("test.identity-render-v1", None)
