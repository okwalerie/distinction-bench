"""Tests for M6: the injector applicability admission gate.

See ``.lattice/notes/design-decisions-2026-07-04.md`` (Valerie's 4 July
amendment) and ``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md``
(DB-4, M6). Every injector already declares an ``applicability`` set (M1
data shape); this landing is the admission gate that actually reads it and
rejects an unsupported pairing at construction, before any render.
"""

from __future__ import annotations

import pytest

from lofbench.renderers.pipeline.admission import validate_applicability
from lofbench.renderers.pipeline.composed import ComposedRenderer
from lofbench.renderers.pipeline.injector import Injector
from lofbench.renderers.pipeline.registry import ARCHETYPE_REGISTRY, INJECTOR_REGISTRY
from lofbench.renderers.pipeline.spec import DialectSpec


class TestRejectsUnsupportedPairing:
    def test_parens_only_injector_rejected_against_a_spatial_archetype(self):
        # bracket_swap declares applicability={"parens"}; trees@1 is a
        # spatial, non-parens archetype -- the doc's own example of a
        # "rooms-only injector paired with the parens archetype" inverted
        # (no rooms injector exists yet to pair the other way, but the
        # admission check is symmetric in what it rejects).
        spec = DialectSpec(
            dialect_id="test.bad-pairing-v1",
            family="trees",
            archetype="trees@1",
            injectors=[("bracket_swap", {"mismatched": False})],
        )
        with pytest.raises(ValueError, match="not applicable"):
            ComposedRenderer(spec)

    def test_pattern_only_injector_rejected_against_parens(self):
        # preset declares applicability={"pattern"}; parens@1 is a
        # different archetype family entirely.
        spec = DialectSpec(
            dialect_id="test.bad-pairing-2-v1",
            family="parens",
            archetype="parens@1",
            injectors=[("preset", {"name": "lisp"})],
        )
        with pytest.raises(ValueError, match="not applicable"):
            ComposedRenderer(spec)

    def test_rejection_happens_at_construction_before_any_render(self):
        # The gate must fire in __init__, not lazily inside render() --
        # constructing the renderer alone must already raise.
        spec = DialectSpec(
            dialect_id="test.bad-pairing-3-v1",
            family="trees",
            archetype="trees@1",
            injectors=[("preset", {"name": "lisp"})],
        )
        with pytest.raises(ValueError):
            ComposedRenderer(spec)  # never gets to call .render(...)


class TestAcceptsSupportedPairing:
    def test_parens_injector_on_parens_archetype_constructs_fine(self):
        spec = DialectSpec(
            dialect_id="test.good-pairing-v1",
            family="parens",
            archetype="parens@1",
            injectors=[("bracket_swap", {"mismatched": False})],
        )
        renderer = ComposedRenderer(spec)  # must not raise
        result = renderer.render("(()())")
        assert result.metadata["structure_verified"] is True

    def test_generic_text_injector_on_any_text_archetype(self):
        # whitespace_jitter declares applicability={"text"} -- a whole
        # modality, per the amendment -- so it admits against pattern@1
        # too, not just parens@1.
        spec = DialectSpec(
            dialect_id="test.good-pairing-modality-v1",
            family="pattern",
            archetype="pattern@1",
            injectors=[("whitespace_jitter", {"amp": 1})],
        )
        ComposedRenderer(spec)  # must not raise

    def test_registry_key_style_applicability_admits(self):
        # DB-2's injectors declare applicability as the full registry key
        # (e.g. "rna_dotbracket@1"), not the bare archetype name -- the
        # gate must honour both declaration conventions.
        spec = DialectSpec(
            dialect_id="test.good-pairing-regkey-v1",
            family="biopolymer",
            archetype="rna_dotbracket@1",
            injectors=[("unpaired_filler", {})],
        )
        ComposedRenderer(spec)  # must not raise

    def test_no_injectors_never_rejected(self):
        spec = DialectSpec(dialect_id="test.no-injectors-v1", family="parens", archetype="parens@1")
        ComposedRenderer(spec)  # must not raise


class TestPermissiveOnUnknownRegistryKeys:
    """Construction-time fixtures with placeholder keys (no real archetype
    or injector registered) must not be forced to also register one --
    render() raises its own clear KeyError for a genuinely unknown key,
    and that is a distinct failure from an applicability mismatch."""

    def test_unknown_archetype_key_is_not_an_applicability_error(self):
        spec = DialectSpec(
            dialect_id="test.unknown-archetype-v1",
            family="test",
            archetype="does-not-exist@1",
            injectors=[("bracket_swap", {})],
        )
        # Construction succeeds (no ValueError from validate_applicability);
        # render() is where the unknown-archetype KeyError actually surfaces.
        renderer = ComposedRenderer(spec)
        with pytest.raises(KeyError):
            renderer.render("()")

    def test_unknown_injector_key_is_not_an_applicability_error(self):
        spec = DialectSpec(
            dialect_id="test.unknown-injector-v1",
            family="parens",
            archetype="parens@1",
            injectors=[("does-not-exist-injector", {})],
        )
        renderer = ComposedRenderer(spec)
        with pytest.raises(KeyError):
            renderer.render("()")


class TestValidateApplicabilityDirect:
    """Direct unit tests of the validator function itself, independent of
    ComposedRenderer construction."""

    def test_returns_none_for_empty_injectors(self):
        spec = DialectSpec(dialect_id="d", family="f", archetype="does-not-exist@1")
        assert validate_applicability(spec) is None

    def test_raises_with_dialect_id_and_applicability_in_message(self):
        spec = DialectSpec(
            dialect_id="test.direct-bad-v1",
            family="trees",
            archetype="trees@1",
            injectors=[("bracket_swap", {})],
        )
        with pytest.raises(ValueError) as excinfo:
            validate_applicability(spec)
        message = str(excinfo.value)
        assert "test.direct-bad-v1" in message
        assert "bracket_swap" in message
        assert "trees@1" in message


class _FixtureArchetype:
    name = "admission-fixture"
    version = "1"
    family = "admission-fixture-family"
    modality = "text"

    def build(self, root, rng):
        from lofbench.renderers.pipeline.archetype import BaseRender
        from lofbench.renderers.pipeline.nodes import nodes_to_form

        return BaseRender(modality="text", payload=nodes_to_form(root), node_map={})

    def predicate(self, parent, child):
        return False


class _AppliesToFamilyInjector:
    name = "admission-fixture-family-injector"
    version = "1"
    modalities = frozenset({"text"})
    applicability = frozenset({"admission-fixture-family"})

    def apply(self, base, root, rng, **params):
        return base


ARCHETYPE_REGISTRY.setdefault("admission-fixture@1", _FixtureArchetype())
INJECTOR_REGISTRY.setdefault("admission-fixture-family-injector", _AppliesToFamilyInjector())


class TestFamilyLevelApplicability:
    """Applicability may also match on family, not just name/registry-key/
    modality -- exercised here since none of the checked-in injectors
    happen to declare a family that differs from their archetype's name."""

    def test_fixture_injector_satisfies_injector_protocol(self):
        # Sanity, same convention as tests/test_verification.py's fixtures.
        assert isinstance(_AppliesToFamilyInjector(), Injector)

    def test_family_match_admits(self):
        spec = DialectSpec(
            dialect_id="test.family-match-v1",
            family="admission-fixture-family",
            archetype="admission-fixture@1",
            injectors=[("admission-fixture-family-injector", {})],
        )
        ComposedRenderer(spec)  # must not raise
