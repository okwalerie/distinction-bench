"""Tests for the rendering showcase gallery (DB-12).

Covers: registry-iteration completeness (a monkeypatched extra dialect
appears with zero gallery edits), the seeded-variation acceptance criterion,
the static export's self-containment, the circle-alias accounting, the
spatial fallback captioning, the generator/suite sections, and the live
`/gallery` route.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fasthtml", reason="site extra is not installed")

from starlette.testclient import TestClient

from lofbench.core import DIFFICULTY_CONFIGS
from lofbench.renderers import list_renderers
from lofbench.suites import load_suite
from lofsite.app import create_app
from lofsite.export_gallery import build_static_document
from lofsite.gallery_data import (
    CIRCLE_ALIAS_ID,
    GalleryData,
    accounted_dialect_ids,
    build_gallery_data,
)


@pytest.fixture(scope="module")
def gallery_data() -> GalleryData:
    return build_gallery_data()


@pytest.fixture
def client():
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Registry-iteration completeness (acceptance criterion 1)
# ---------------------------------------------------------------------------


class TestRegistryAccounting:
    def test_every_registered_dialect_is_accounted_for(self, gallery_data):
        universe = set(list_renderers()) - {"composed"}
        accounted = accounted_dialect_ids(gallery_data)
        assert accounted == universe

    def test_circle_is_named_as_an_alias_not_a_separate_panel(self, gallery_data):
        # circle shares enclosure@1's archetype and empty injector list --
        # the gallery names it in a caption rather than rendering a second,
        # visually-identical panel for it.
        enclosure_group = next(
            g
            for g in gallery_data.families
            if g.family == "enclosure" and g.archetype == "enclosure@1"
        )
        panel_ids = {
            enclosure_group.canonical.dialect_id,
            *(v.dialect_id for v in enclosure_group.variations),
        }
        assert CIRCLE_ALIAS_ID not in panel_ids
        assert CIRCLE_ALIAS_ID in gallery_data.circle_note

    def test_new_dialect_registered_anywhere_appears_with_zero_gallery_edits(self, monkeypatch):
        """Monkeypatch an extra registered dialect straight into the live
        registries `gallery_data` reads (`list_renderers`/`DIALECT_SPECS`)
        and confirm it shows up in the accounting -- without touching any
        gallery module. This is the plan's binding constraint: a new
        dialect appears on the next request with zero gallery edits.
        """
        from lofbench.renderers import get_renderer as real_get_renderer
        from lofbench.renderers.pipeline.spec import DIALECT_SPECS, DialectSpec

        extra_spec = DialectSpec(
            dialect_id="fake.extra-v1", family="fake_family", archetype="parens@1"
        )
        monkeypatch.setitem(DIALECT_SPECS, "fake.extra-v1", extra_spec)
        monkeypatch.setattr(
            "lofsite.gallery_data.list_renderers",
            lambda: sorted([*list_renderers(), "fake.extra-v1"]),
        )

        def _fake_get_renderer(key, **kw):
            if key == "fake.extra-v1":
                return _FakeExtraRenderer()
            return real_get_renderer(key, **kw)

        monkeypatch.setattr("lofsite.gallery_data.get_renderer", _fake_get_renderer)

        from lofsite.gallery_data import build_gallery_data

        data = build_gallery_data()
        accounted = accounted_dialect_ids(data)
        assert "fake.extra-v1" in accounted
        # It joined a brand-new "fake_family" family as its own archetype
        # group (reusing parens@1's archetype key here only as a stand-in
        # value, never actually built through it since get_renderer is
        # faked) -- no code in gallery_data.py or pages/gallery.py names
        # "fake.extra-v1" or "fake_family" anywhere.
        fake_group = next(g for g in data.families if g.family == "fake_family")
        assert fake_group.canonical.dialect_id == "fake.extra-v1"


class _FakeExtraRenderer:
    def render(self, form_string, rng=None):
        from lofbench.renderers.base import RenderedForm

        return RenderedForm(
            original=form_string,
            rendered=f"FAKE:{form_string}",
            renderer_name="fake.extra-v1",
            metadata={"format": "text"},
        )


# ---------------------------------------------------------------------------
# Variation panels genuinely differ (acceptance criterion 2)
# ---------------------------------------------------------------------------


class TestVariationsGenuinelyDiffer:
    def test_seeded_archetype_variations_differ_from_canonical_and_each_other(self, gallery_data):
        checked_any = False
        for group in gallery_data.families:
            if group.is_fallback:
                continue
            checked_any = True
            contents = [group.canonical.content] + [v.content for v in group.variations]
            assert len(set(contents)) == len(contents), (
                f"{group.family}/{group.archetype}: variation content did not all differ"
            )
        assert checked_any, "expected at least one non-fallback archetype group"

    def test_spatial_fallback_uses_deeper_exemplar_not_a_fake_reseed(self, gallery_data):
        fallback_groups = [g for g in gallery_data.families if g.is_fallback]
        assert fallback_groups, "expected at least one spatial-style fallback group"
        for group in fallback_groups:
            assert len(group.variations) == 1
            fallback_panel = group.variations[0]
            assert group.canonical.content != fallback_panel.content
            assert "deeper" in fallback_panel.caption or "deeper" in group.note

    def test_legacy_seed_pair_dialects_genuinely_vary(self, gallery_data):
        seeded = [e for e in gallery_data.legacy if e.seed_pair is not None]
        assert {e.dialect_id for e in seeded} == {"canonical", "noisy_parens"}
        for entry in seeded:
            seed_a, seed_b = entry.seed_pair
            assert seed_a.content != seed_b.content

    def test_legacy_primary_panels_are_deterministic_across_builds(self):
        # Regression guard: `noisy_parens`'s default `render(rng=None)`
        # falls back to an unseeded `random.Random()` internally -- the
        # primary legacy panel must pass a fixed seed so two builds agree,
        # matching `build_gallery_data`/`build_static_document`'s claim to
        # be pure (same registries in, same output out).
        d1 = build_gallery_data()
        d2 = build_gallery_data()
        by_id_1 = {e.dialect_id: e.panel.content for e in d1.legacy}
        by_id_2 = {e.dialect_id: e.panel.content for e in d2.legacy}
        assert by_id_1 == by_id_2


# ---------------------------------------------------------------------------
# Generator section
# ---------------------------------------------------------------------------


class TestGeneratorSection:
    def test_exactly_five_samples_one_per_tier(self, gallery_data):
        assert len(gallery_data.generator_samples) == len(DIFFICULTY_CONFIGS)
        assert [s.tier for s in gallery_data.generator_samples] == [
            c[0] for c in DIFFICULTY_CONFIGS
        ]

    def test_every_sample_has_nonnegative_depth_and_steps(self, gallery_data):
        for sample in gallery_data.generator_samples:
            assert sample.depth >= 0
            assert sample.steps >= 0
            assert sample.form_string is not None


# ---------------------------------------------------------------------------
# Suite v1 section
# ---------------------------------------------------------------------------


class TestSuiteSection:
    def test_headline_numbers_match_loaded_suite_exactly(self, gallery_data):
        suite = load_suite()
        assert gallery_data.suite_summary.n_forms == len(suite.forms)
        assert gallery_data.suite_summary.n_dialects == len(suite.specs)
        assert gallery_data.suite_summary.n_cells == len(suite.cells)

    def test_sample_has_twelve_cells(self, gallery_data):
        assert len(gallery_data.suite_summary.sample_cells) == 12


# ---------------------------------------------------------------------------
# Static export
# ---------------------------------------------------------------------------


class TestStaticExport:
    def test_starts_with_doctype(self):
        doc = build_static_document()
        assert doc.startswith("<!doctype html>")

    def test_contains_at_least_one_image_payload(self):
        doc = build_static_document()
        assert "data:image/png;base64," in doc

    def test_no_external_resource_loads(self):
        doc = build_static_document()
        assert '<script src="http' not in doc
        assert "<link" not in doc or 'href="http' not in doc
        assert '<img src="http' not in doc

    def test_write_and_read_back_is_byte_identical(self, tmp_path):
        doc = build_static_document()
        out = tmp_path / "gallery.html"
        out.write_text(doc)
        assert out.read_text() == doc


# ---------------------------------------------------------------------------
# Pages / routes
# ---------------------------------------------------------------------------


class TestGalleryPageAndRoute:
    def test_gallery_page_renders_without_raising(self):
        from lofsite.pages.gallery import gallery_page

        html = str(gallery_page())
        assert "Rendering gallery" in html

    def test_gallery_route_returns_200(self, client):
        r = client.get("/gallery")
        assert r.status_code == 200
        assert "Rendering gallery" in r.text

    def test_gallery_content_build_completes_under_budget(self):
        from lofsite.gallery_data import build_gallery_data

        start = time.time()
        build_gallery_data()
        elapsed = time.time() - start
        # Loose regression guard against an accidental N^2 render loop, per
        # the plan's "Rendering cost and caching decision" -- not a strict
        # perf target.
        assert elapsed < 3.0
