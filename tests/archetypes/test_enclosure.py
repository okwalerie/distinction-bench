"""Tests for ``enclosure@1``: DB-4's own M5 migration of
``svg_circle_renderer.py``.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, worked example
3, and ``.lattice/plans/task_01KWQKYV35TN175DJQSAEKK5AJ.md`` (DB-4, M5 item
5). Per the doc's own acceptance criterion, this is a *structural*
migration: the new platform-stable packer's drawn coordinates are not
required (or expected) to match the legacy ``SVGCircleRenderer``'s pixel
output -- only containment verification, determinism, and the epsilon
predicate's correctness are asserted. See
``TestStructuralEquivalenceVsLegacy`` for the explicit divergence note.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest
from _helpers import all_test_forms, assert_verifies

from lofbench.core import generate_form_string
from lofbench.renderers.archetypes import enclosure
from lofbench.renderers.pipeline.archetype import Primitive
from lofbench.renderers.pipeline.nodes import form_to_nodes
from lofbench.renderers.svg_circle_renderer import SVGCircleRenderer

ARCHETYPE = enclosure.ARCHETYPE


class TestDeterminism:
    def test_two_fresh_builds_are_identical(self):
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


class TestEpsilonPredicate:
    """Hand-labelled circle-in-circle scenes with explicit epsilons, per the
    doc's "the predicate is the trust boundary" admission rule -- including
    tangent and touching circles that must read as NOT contained."""

    def _circle(self, node_id, cx, cy, r) -> Primitive:
        geom = {"cx": Decimal(cx), "cy": Decimal(cy), "r": Decimal(r)}
        return Primitive(node_id=node_id, kind="circle", geom=geom)

    def test_comfortably_inside_passes(self):
        parent = self._circle("0", "0", "0", "1")
        child = self._circle("0.0", "0", "0", "0.5")
        assert ARCHETYPE.predicate(parent, child) is True

    def test_tangent_internally_touching_fails(self):
        # dist + r_child == r_parent exactly: margin is zero, strictly less
        # than EPS, so this must NOT verify as contained.
        parent = self._circle("0", "0", "0", "1")
        child = self._circle("0.0", "0.5", "0", "0.5")  # dist=0.5, +r_child(0.5)=1.0=r_parent
        assert ARCHETYPE.predicate(parent, child) is False

    def test_just_inside_eps_margin_passes(self):
        margin = enclosure.EPS * 10
        parent = self._circle("0", "0", "0", "1")
        # dist=0, r_child chosen so r_parent - (dist + r_child) == margin > EPS
        child = self._circle("0.0", "0", "0", str(Decimal("1") - margin))
        assert ARCHETYPE.predicate(parent, child) is True

    def test_just_outside_eps_margin_fails(self):
        # Margin smaller than EPS: must fail, not silently round through.
        margin = enclosure.EPS / 10
        parent = self._circle("0", "0", "0", "1")
        child = self._circle("0.0", "0", "0", str(Decimal("1") - margin))
        assert ARCHETYPE.predicate(parent, child) is False

    def test_overlapping_not_nested_fails(self):
        parent = self._circle("0", "0", "0", "1")
        child = self._circle("0.0", "1.5", "0", "1")  # far off to the side, overlapping only
        assert ARCHETYPE.predicate(parent, child) is False

    def test_disjoint_fails(self):
        parent = self._circle("0", "0", "0", "1")
        child = self._circle("0.0", "10", "10", "0.1")
        assert ARCHETYPE.predicate(parent, child) is False

    def test_decorative_primitive_never_contains_or_is_contained(self):
        parent = self._circle("0", "0", "0", "1")
        decorative_geom = {"cx": Decimal(0), "cy": Decimal(0), "r": Decimal("0.1")}
        decorative = Primitive(node_id=None, kind="circle", geom=decorative_geom)
        assert ARCHETYPE.predicate(parent, decorative) is False
        assert ARCHETYPE.predicate(decorative, parent) is False


class TestSvg:
    def test_to_svg_produces_well_formed_svg_document(self):
        import xml.dom.minidom as minidom

        root = form_to_nodes("(()(()()))")
        base = ARCHETYPE.build(root, random.Random(1))
        svg = enclosure.to_svg(base)
        assert svg.startswith("<svg")
        minidom.parseString(svg)  # raises on malformed XML

    def test_to_svg_forces_fill_none(self):
        # Model-facing stimuli force fill style to none -- colour cues that
        # mark isomorphisms are site pedagogy only (design-decisions note).
        root = form_to_nodes("(()(()()))")
        base = ARCHETYPE.build(root, random.Random(1))
        svg = enclosure.to_svg(base)
        assert 'fill="none"' in svg
        assert 'fill="black"' not in svg
        assert 'fill="white"' not in svg


class TestStructuralEquivalenceVsLegacy:
    """M5 acceptance: structural-equivalence-or-documented-divergence vs
    the legacy renderer on 50 seeded forms.

    The doc explicitly allows the migrated enclosure archetype's drawn
    coordinates to differ from ``SVGCircleRenderer``'s -- the requirement
    is verified containment and determinism, not pixel parity. This test
    documents that divergence directly: it asserts the *new* archetype
    verifies containment (via its own predicate) on every one of 50 seeded
    forms, and that the *legacy* renderer still runs unmodified (it is not
    deleted, per the plan's "no renderer is deleted until parity is
    tested"). It does not, and by design cannot, assert coordinate parity
    between the two -- the legacy renderer has no independent verification
    of its own geometry to compare against.
    """

    SEEDED_FORMS = [generate_form_string(rng=random.Random(i)) or "()" for i in range(50)]

    @pytest.mark.parametrize("form_string", SEEDED_FORMS)
    def test_new_archetype_verifies_containment(self, form_string):
        assert_verifies(ARCHETYPE, form_string)

    @pytest.mark.parametrize("form_string", SEEDED_FORMS)
    def test_legacy_renderer_still_runs_unmodified(self, form_string):
        # Documents that svg_circle_renderer.py is untouched and still
        # importable/constructible directly, even though it is no longer
        # wired to the "circle" registry key (see archetypes/__init__.py).
        legacy = SVGCircleRenderer()
        result = legacy.render(form_string)
        assert result.rendered.startswith("data:image/svg+xml")
        assert result.renderer_name == "svg_circle"
