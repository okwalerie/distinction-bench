"""Tests for the lofsite package (DB-6, phase 1).

Covers: the sandbox validation guard, the all-dialect renderer fan-out, and
the app's routes (via starlette's TestClient, no live server needed).
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from lofsite.app import create_app
from lofsite.data import ArtifactStatus
from lofsite.rendering import render_all_dialects
from lofsite.validation import MAX_CHARS, MAX_DEPTH, validate_form_input


@pytest.fixture
def client():
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Validation guard
# ---------------------------------------------------------------------------


class TestValidateFormInput:
    def test_valid_form_round_trips_and_strips_whitespace(self):
        result = validate_form_input("( () () )")
        assert result.ok
        assert result.error is None
        assert result.form_string == "(()())"

    def test_empty_input_rejected(self):
        result = validate_form_input("")
        assert not result.ok
        assert result.form_string is None
        assert "Enter a form" in result.error

    def test_whitespace_only_rejected(self):
        result = validate_form_input("   \n\t  ")
        assert not result.ok

    def test_junk_characters_rejected(self):
        result = validate_form_input("(()) DROP TABLE forms;")
        assert not result.ok
        assert "Unexpected character" in result.error

    def test_unbalanced_missing_close_rejected(self):
        result = validate_form_input("(()")
        assert not result.ok
        assert "Unbalanced" in result.error

    def test_unbalanced_extra_close_rejected(self):
        result = validate_form_input("())")
        assert not result.ok
        assert "Unbalanced" in result.error
        assert "no matching" in result.error

    def test_oversized_input_rejected(self):
        huge = "(" * (MAX_CHARS + 1) + ")" * (MAX_CHARS + 1)
        result = validate_form_input(huge)
        assert not result.ok
        assert "too long" in result.error

    def test_oversized_boundary_accepted(self):
        # Exactly MAX_CHARS characters, balanced and shallow, must be accepted.
        form = "()" * (MAX_CHARS // 2)
        assert len(form) == MAX_CHARS
        result = validate_form_input(form)
        assert result.ok

    def test_over_deep_rejected(self):
        deep = "(" * (MAX_DEPTH + 1) + ")" * (MAX_DEPTH + 1)
        result = validate_form_input(deep)
        assert not result.ok
        assert "too deep" in result.error

    def test_exactly_max_depth_accepted(self):
        form = "(" * MAX_DEPTH + ")" * MAX_DEPTH
        result = validate_form_input(form)
        assert result.ok


# ---------------------------------------------------------------------------
# Rendering fan-out
# ---------------------------------------------------------------------------


class TestRenderAllDialects:
    def test_covers_every_registered_renderer(self):
        from lofbench.renderers import list_renderers

        fan_out = render_all_dialects("(()())")
        # "composed" cannot be zero-arg constructed (it needs a caller-
        # supplied DialectSpec) -- a degraded fan-out that silently drops a
        # renderer that *can* be zero-arg constructed must still fail this,
        # so assert both halves explicitly rather than just set equality.
        rendered_keys = {p.registry_key for p in fan_out}
        zero_arg_renderers = set(list_renderers()) - {"composed"}
        assert rendered_keys == zero_arg_renderers
        assert fan_out.skipped == ["composed"]

    def test_circle_panel_is_image_others_are_text(self):
        panels = render_all_dialects("(()())")
        by_key = {p.registry_key: p for p in panels}
        assert by_key["circle"].kind == "image"
        # DB-4 M5: circle is now the enclosure@1 composed dialect, emitted
        # through the pinned rasteriser as a PNG data URI -- most
        # multimodal providers reject the image/svg+xml mime type the
        # legacy SVGCircleRenderer used to emit.
        assert by_key["circle"].content.startswith("data:image/png;base64,")
        for key in ("canonical", "noisy_parens", "sexpr", "nested_list"):
            assert by_key[key].kind == "text"

    def test_circle_registry_key_equals_renderer_name(self):
        # DB-4 M5 fix: "circle" used to alias SVGCircleRenderer, whose own
        # .name was "svg_circle" -- a registry-key-vs-name asymmetry flagged
        # in plan review. "circle" is now a composed enclosure@1 named
        # dialect whose ComposedRenderer.name equals the registry key.
        panels = render_all_dialects("(())")
        circle = next(p for p in panels if p.registry_key == "circle")
        assert circle.renderer_name == "circle"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestRoutes:
    def test_index_ok(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Sandbox" in r.text

    def test_axioms_ok_and_mentions_axioms(self, client):
        r = client.get("/axioms")
        assert r.status_code == 200
        assert "Calling" in r.text
        assert "Crossing" in r.text

    def test_axioms_static_image_served(self, client):
        r = client.get("/axioms-cmy.png")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/")

    def test_charts_placeholder_when_no_artifact(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("lofsite.data.DATA_DIR", tmp_path / "nonexistent")
        r = client.get("/charts")
        assert r.status_code == 200
        assert "Awaiting suite v1 data" in r.text

    def test_matrix_placeholder_when_no_artifact(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("lofsite.data.DATA_DIR", tmp_path / "nonexistent")
        r = client.get("/matrix")
        assert r.status_code == 200
        assert "Awaiting suite v1 data" in r.text

    def test_walkthroughs_placeholder_when_no_artifact(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("lofsite.data.DATA_DIR", tmp_path / "nonexistent")
        r = client.get("/walkthroughs")
        assert r.status_code == 200
        assert "Awaiting suite v1 data" in r.text

    def test_render_valid_form_returns_all_dialect_panels(self, client):
        r = client.post("/render", data={"form_input": "(()())"})
        assert r.status_code == 200
        assert "canonical" in r.text
        assert "circle" in r.text
        assert "sexpr" in r.text

    def test_render_junk_input_rejected_no_renderer_runs(self, client):
        r = client.post("/render", data={"form_input": "(()) <script>alert(1)</script>"})
        assert r.status_code == 200
        assert "Unexpected character" in r.text

    def test_render_unbalanced_input_rejected(self, client):
        r = client.post("/render", data={"form_input": "((())"})
        assert r.status_code == 200
        assert "Unbalanced" in r.text

    def test_render_oversized_input_rejected(self, client):
        huge = "(" * 150 + ")" * 150
        r = client.post("/render", data={"form_input": huge})
        assert r.status_code == 200
        assert "too long" in r.text


# ---------------------------------------------------------------------------
# Data artifact status
# ---------------------------------------------------------------------------


def test_artifact_status_reports_absent_for_nonexistent_dir(tmp_path):
    from lofsite.data import items_status

    status = items_status(data_dir=tmp_path / "does-not-exist")
    assert isinstance(status, ArtifactStatus)
    assert status.available is False


def test_artifact_status_reports_present(tmp_path):
    from lofsite.data import ITEMS_ARTIFACT, items_status

    (tmp_path / ITEMS_ARTIFACT).write_bytes(b"")
    status = items_status(data_dir=tmp_path)
    assert status.available is True
