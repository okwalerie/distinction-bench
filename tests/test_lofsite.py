"""Tests for the lofsite package (DB-6, phase 1 + phase 2).

Covers: the sandbox validation guard, the all-dialect renderer fan-out
(including the phase-2 spatial NotImplementedError guard), the app's routes
(via starlette's TestClient, no live server needed), the phase-2 data-layer
schema_version checks, chart aggregation, and SVG rendering.
"""

from __future__ import annotations

import importlib
import json

import pandas as pd
import pytest

pytest.importorskip("fasthtml", reason="site extra is not installed")

from starlette.testclient import TestClient

from lofbench.pipeline import SCHEMA_VERSION
from lofbench.renderers.base import RenderedForm
from lofsite.app import create_app
from lofsite.data import ArtifactStatus
from lofsite.rendering import render_all_dialects
from lofsite.validation import MAX_CHARS, MAX_DEPTH, validate_form_input


@pytest.fixture
def client():
    return TestClient(create_app())


def test_app_construction_never_creates_a_cwd_session_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOFSITE_SECRET_KEY", raising=False)

    app_module = importlib.import_module("lofsite.app")
    app_module = importlib.reload(app_module)

    assert app_module.app.secret_key
    assert not (tmp_path / ".sesskey").exists()


def test_app_secret_precedence(monkeypatch):
    monkeypatch.setenv("LOFSITE_SECRET_KEY", "environment-secret")

    assert create_app(secret_key="argument-secret").secret_key == "argument-secret"
    assert create_app().secret_key == "environment-secret"


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


@pytest.mark.requires_visual_runtime
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
# Spatial fan-out guard (DB-9's lofsite side, phase 2)
#
# A sibling task may register spatial archetypes (trees, blocks, graph, ...)
# into the real renderer registry in parallel with this work. Rather than
# depend on that merge landing first, these tests monkeypatch
# `lofsite.rendering.list_renderers`/`get_renderer` with fakes standing in
# for both worlds the guard must handle: a dialect whose `.render()` raises
# NotImplementedError (the pipeline's documented pre-M5 behaviour for a
# spatial BaseRender reaching emit()), and a dialect that renders a PNG data
# URI once M5/DB-9 lands.
# ---------------------------------------------------------------------------


class _FakeTextRenderer:
    def render(self, form_string, rng=None):
        return RenderedForm(
            original=form_string,
            rendered=f"TEXT:{form_string}",
            renderer_name="fake_text",
            metadata={"format": "text"},
        )


class _FakeNotReadySpatialRenderer:
    """Stands in for a spatial dialect registered before M5/DB-9's
    rasteriser lands -- `.render()` reaches `emit()`'s spatial branch, which
    raises NotImplementedError by design."""

    def render(self, form_string, rng=None):
        raise NotImplementedError("Spatial emission lands in M5.")


class _FakePngSpatialRenderer:
    """Stands in for a spatial dialect once PNG rasterisation is landed."""

    def render(self, form_string, rng=None):
        return RenderedForm(
            original=form_string,
            rendered="data:image/png;base64,Zm9vYmFy",
            renderer_name="fake_png",
            metadata={"format": "image"},
        )


class TestSpatialFanOutGuard:
    def test_not_implemented_render_is_skipped_not_fatal(self, monkeypatch):
        fakes = {"fake_text": _FakeTextRenderer(), "fake_spatial": _FakeNotReadySpatialRenderer()}
        monkeypatch.setattr("lofsite.rendering.list_renderers", lambda: sorted(fakes))
        monkeypatch.setattr("lofsite.rendering.get_renderer", lambda key: fakes[key])

        fan_out = render_all_dialects("(()())")

        rendered_keys = {p.registry_key for p in fan_out}
        assert rendered_keys == {"fake_text"}
        assert fan_out.skipped == ["fake_spatial"]

    def test_png_data_uri_dialect_renders_as_image_panel(self, monkeypatch):
        fakes = {"fake_text": _FakeTextRenderer(), "fake_png": _FakePngSpatialRenderer()}
        monkeypatch.setattr("lofsite.rendering.list_renderers", lambda: sorted(fakes))
        monkeypatch.setattr("lofsite.rendering.get_renderer", lambda key: fakes[key])

        fan_out = render_all_dialects("(()())")

        by_key = {p.registry_key: p for p in fan_out}
        assert fan_out.skipped == []
        assert by_key["fake_png"].kind == "image"
        assert by_key["fake_png"].content.startswith("data:image/png;base64,")
        assert by_key["fake_text"].kind == "text"

    def test_mixed_construction_skip_and_render_skip_both_reported(self, monkeypatch):
        # Combines a construction-time skip (needs args) with a render-time
        # NotImplementedError skip, to prove the two paths compose rather
        # than one silently overwriting the other's bookkeeping.
        class _NeedsArgsRenderer:
            def __init__(self, spec):
                self.spec = spec

            def render(self, form_string, rng=None):  # pragma: no cover - never reached
                raise AssertionError("should never be constructed with zero args")

        def _get_renderer(key):
            if key == "needs_args":
                return _NeedsArgsRenderer()  # raises TypeError: missing 'spec'
            if key == "fake_spatial":
                return _FakeNotReadySpatialRenderer()
            return _FakeTextRenderer()

        monkeypatch.setattr(
            "lofsite.rendering.list_renderers",
            lambda: ["fake_spatial", "fake_text", "needs_args"],
        )
        monkeypatch.setattr("lofsite.rendering.get_renderer", _get_renderer)

        fan_out = render_all_dialects("(()())")

        assert {p.registry_key for p in fan_out} == {"fake_text"}
        assert sorted(fan_out.skipped) == ["fake_spatial", "needs_args"]


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


def test_default_suite_dir_joins_data_dir_and_suite_version(tmp_path, monkeypatch):
    """Phase-2 fix: the pipeline writes under out_dir/<suite_version>/, so the
    no-override default must join DATA_DIR with SUITE_VERSION, not treat
    DATA_DIR itself as the artifact directory (phase 1's bug -- it expected
    a nonexistent headline.parquet directly under DATA_DIR)."""
    from lofsite.data import ITEMS_ARTIFACT, items_status

    monkeypatch.setattr("lofsite.data.DATA_DIR", tmp_path)
    monkeypatch.setattr("lofsite.data.SUITE_VERSION", "pilot-v0")
    (tmp_path / "pilot-v0").mkdir()
    (tmp_path / "pilot-v0" / ITEMS_ARTIFACT).write_bytes(b"")

    status = items_status()
    assert status.available is True
    assert status.path == tmp_path / "pilot-v0" / ITEMS_ARTIFACT


# ---------------------------------------------------------------------------
# Schema_version checking (phase 2)
# ---------------------------------------------------------------------------


def _write_sensitivity_parquet(path, schema_version=SCHEMA_VERSION, n_rows=1):
    df = pd.DataFrame(
        {
            "model": ["m"] * n_rows,
            "reasoning_setting": [None] * n_rows,
            "dialect_id": ["noisy-balanced"] * n_rows,
            "suite_version": ["pilot-v0"] * n_rows,
            "n_paired": [10] * n_rows,
            "n_distinct_forms": [10] * n_rows,
            "accuracy_canonical": [0.5] * n_rows,
            "accuracy_treatment": [0.4] * n_rows,
            "paired_drop": [0.1] * n_rows,
            "mcnemar_b": [1] * n_rows,
            "mcnemar_c": [0] * n_rows,
            "mcnemar_p": [0.5] * n_rows,
            "bootstrap_ci_low": [-0.1] * n_rows,
            "bootstrap_ci_high": [0.3] * n_rows,
            "coverage": [1.0] * n_rows,
            "schema_version": [schema_version] * n_rows,
        }
    )
    df.to_parquet(path)


class TestSchemaVersionChecking:
    def test_load_sensitivity_absent_reports_not_found(self, tmp_path):
        from lofsite.data import load_sensitivity

        result = load_sensitivity(data_dir=tmp_path)
        assert result.frame is None
        assert "not found" in result.error

    def test_load_sensitivity_matching_schema_ok(self, tmp_path):
        from lofsite.data import SENSITIVITY_ARTIFACT, load_sensitivity

        _write_sensitivity_parquet(tmp_path / SENSITIVITY_ARTIFACT)
        result = load_sensitivity(data_dir=tmp_path)
        assert result.error is None
        assert result.frame is not None
        assert len(result.frame) == 1

    def test_load_sensitivity_schema_mismatch_refuses_to_render(self, tmp_path):
        from lofsite.data import SENSITIVITY_ARTIFACT, load_sensitivity

        _write_sensitivity_parquet(
            tmp_path / SENSITIVITY_ARTIFACT, schema_version="pipeline-schema-v2"
        )
        result = load_sensitivity(data_dir=tmp_path)
        assert result.frame is None
        assert "schema_version mismatch" in result.error
        assert "pipeline-schema-v2" in result.error

    def test_load_transcripts_schema_mismatch_refuses_to_render(self, tmp_path):
        from lofsite.data import TRANSCRIPTS_ARTIFACT, load_transcripts

        path = tmp_path / TRANSCRIPTS_ARTIFACT
        with path.open("w") as f:
            f.write(json.dumps({"schema_version": "some-other-version", "correct": True}) + "\n")
        result = load_transcripts(data_dir=tmp_path)
        assert result.frame is None
        assert "schema_version mismatch" in result.error

    def test_load_transcripts_matching_schema_ok(self, tmp_path):
        from lofsite.data import TRANSCRIPTS_ARTIFACT, load_transcripts

        path = tmp_path / TRANSCRIPTS_ARTIFACT
        with path.open("w") as f:
            f.write(json.dumps({"schema_version": SCHEMA_VERSION, "correct": True}) + "\n")
        result = load_transcripts(data_dir=tmp_path)
        assert result.error is None
        assert len(result.frame) == 1


# ---------------------------------------------------------------------------
# Chart aggregation (phase 2)
# ---------------------------------------------------------------------------


class TestChartsDataSensitivity:
    def test_sensitivity_rows_sorted_and_labelled(self):
        from lofsite.charts_data import sensitivity_rows

        df = pd.DataFrame(
            {
                "model": ["b-model", "a-model"],
                "reasoning_setting": [None, "high"],
                "dialect_id": ["noisy-mismatch", "noisy-balanced"],
                "paired_drop": [0.1, -0.2],
                "bootstrap_ci_low": [-0.05, -0.3],
                "bootstrap_ci_high": [0.25, -0.1],
                "mcnemar_p": [0.5, 0.0001],
                "n_distinct_forms": [10, 373],
                "coverage": [1.0, 1.0],
            }
        )
        rows = sensitivity_rows(df)
        assert [r.model for r in rows] == ["a-model", "b-model"]
        assert rows[0].label == "a-model · high · noisy-balanced"
        assert rows[1].label == "b-model · default · noisy-mismatch"

    def test_sensitivity_rows_empty_df(self):
        from lofsite.charts_data import sensitivity_rows

        assert sensitivity_rows(pd.DataFrame()) == []


class TestChartsDataMatrix:
    def test_accuracy_matrix_aggregates_and_reports_missing_combos(self):
        from lofsite.charts_data import accuracy_matrix

        items = pd.DataFrame(
            {
                "model": ["m1", "m1", "m2"],
                "dialect_id": ["canonical", "noisy-balanced", "canonical"],
                "correct": [True, False, True],
            }
        )
        matrix = accuracy_matrix(items)
        assert matrix.row_labels == ["m1", "m2"]
        assert matrix.col_labels == ["canonical", "noisy-balanced"]
        assert matrix.cells[("m1", "canonical")].accuracy == 1.0
        assert matrix.cells[("m1", "noisy-balanced")].accuracy == 0.0
        assert matrix.cells[("m2", "canonical")].n == 1
        # m2 never ran noisy-balanced -- honestly absent, not fabricated 0%.
        assert ("m2", "noisy-balanced") not in matrix.cells


class TestChartsDataWalkthroughs:
    def _transcripts_df(self):
        return pd.DataFrame(
            [
                {
                    "call_id": "c1",
                    "item_index": 0,
                    "form_id": "form:a",
                    "form_string": "()",
                    "dialect_id": "canonical",
                    "model": "m1",
                    "correct": True,
                    "target": "marked",
                    "predicted": "marked",
                    "full_completion_text": "the answer is marked",
                    "rendered_input": "()",
                    "is_image": False,
                },
                {
                    "call_id": "c2",
                    "item_index": 0,
                    "form_id": "form:b",
                    "form_string": "(())",
                    "dialect_id": "canonical",
                    "model": "m1",
                    "correct": False,
                    "target": "unmarked",
                    "predicted": "marked",
                    "full_completion_text": "the answer is marked",
                    "rendered_input": None,
                    "is_image": False,
                },
            ]
        )

    def test_pick_examples_returns_one_correct_one_incorrect(self):
        from lofsite.charts_data import pick_examples

        result = pick_examples(self._transcripts_df(), items_df=None)
        assert result["correct"].form_id == "form:a"
        assert result["incorrect"].form_id == "form:b"
        # No items artifact joined -- depth/difficulty degrade to None, never
        # a fabricated zero.
        assert result["correct"].depth is None
        assert result["correct"].difficulty is None

    def test_pick_examples_degrades_null_depth_gracefully(self):
        from lofsite.charts_data import pick_examples

        items = pd.DataFrame(
            [
                {"call_id": "c1", "item_index": 0, "depth": None, "difficulty": "unknown"},
                {"call_id": "c2", "item_index": 0, "depth": None, "difficulty": "unknown"},
            ]
        )
        result = pick_examples(self._transcripts_df(), items_df=items)
        assert result["correct"].depth is None
        assert result["correct"].difficulty == "unknown"

    def test_pick_examples_joins_real_depth_when_present(self):
        from lofsite.charts_data import pick_examples

        items = pd.DataFrame(
            [
                {"call_id": "c1", "item_index": 0, "depth": 4, "difficulty": "3. hard"},
                {"call_id": "c2", "item_index": 0, "depth": 2, "difficulty": "1. easy"},
            ]
        )
        result = pick_examples(self._transcripts_df(), items_df=items)
        assert result["correct"].depth == 4
        assert result["correct"].difficulty == "3. hard"

    def test_pick_examples_empty_transcripts(self):
        from lofsite.charts_data import pick_examples

        result = pick_examples(pd.DataFrame(), items_df=None)
        assert result == {"correct": None, "incorrect": None}


# ---------------------------------------------------------------------------
# SVG rendering (phase 2)
# ---------------------------------------------------------------------------


class TestSvgCharts:
    def test_sensitivity_svg_renders_p_value_and_escapes_lt(self):
        from lofsite.charts_data import SensitivityRow
        from lofsite.svg_charts import sensitivity_svg

        rows = [
            SensitivityRow(
                model="m1",
                reasoning_setting=None,
                dialect_id="noisy-balanced",
                paired_drop=-0.05,
                ci_low=-0.1,
                ci_high=0.0,
                mcnemar_p=0.0001,
                n_distinct_forms=42,
                coverage=1.0,
            )
        ]
        svg = sensitivity_svg(rows)
        assert "<svg" in svg
        assert "p&lt;0.001" in svg
        assert "n=42" in svg
        assert "var(--diverging-neg)" in svg  # negative drop -> "neg" colour role

    def test_sensitivity_svg_empty_rows(self):
        from lofsite.svg_charts import sensitivity_svg

        assert sensitivity_svg([]) == ""

    def test_accuracy_matrix_svg_marks_missing_combo_as_not_run(self):
        from lofsite.charts_data import MatrixCell, MatrixData
        from lofsite.svg_charts import accuracy_matrix_svg

        matrix = MatrixData(
            row_labels=["m1", "m2"],
            col_labels=["canonical", "noisy-balanced"],
            cells={("m1", "canonical"): MatrixCell(accuracy=0.75, n=100)},
        )
        svg = accuracy_matrix_svg(matrix)
        assert "75%" in svg
        assert "not run" in svg

    def test_accuracy_matrix_svg_empty(self):
        from lofsite.charts_data import MatrixData
        from lofsite.svg_charts import accuracy_matrix_svg

        assert accuracy_matrix_svg(MatrixData(row_labels=[], col_labels=[], cells={})) == ""


# ---------------------------------------------------------------------------
# Chart pages baking from a real synthetic artifact (phase 2)
# ---------------------------------------------------------------------------


class TestChartsPagesWithRealData:
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lofsite.data.DATA_DIR", tmp_path)
        monkeypatch.setattr("lofsite.data.SUITE_VERSION", "pilot-v0")
        d = tmp_path / "pilot-v0"
        d.mkdir()
        return d

    def test_charts_page_renders_real_sensitivity_row(self, tmp_path, monkeypatch):
        from lofsite.pages.charts import charts_page

        d = self._setup(tmp_path, monkeypatch)
        _write_sensitivity_parquet(d / "sensitivity.parquet")
        html = str(charts_page())
        assert "PILOT DATA" in html
        assert "pilot-v0" in html
        assert "the frozen suite v1" in html
        assert "<strong>not</strong>" in html
        assert "p=0.500" in html

    def test_matrix_page_renders_real_accuracy(self, tmp_path, monkeypatch):
        from lofsite.pages.charts import matrix_page

        d = self._setup(tmp_path, monkeypatch)
        items = pd.DataFrame(
            {
                "model": ["m1"],
                "dialect_id": ["canonical"],
                "correct": [True],
                "suite_version": ["pilot-v0"],
                "schema_version": [SCHEMA_VERSION],
            }
        )
        items.to_parquet(d / "items.parquet")
        html = str(matrix_page())
        assert "100%" in html
        assert "PILOT DATA" in html

    def test_charts_page_schema_mismatch_shows_error_not_crash(self, tmp_path, monkeypatch):
        from lofsite.pages.charts import charts_page

        d = self._setup(tmp_path, monkeypatch)
        _write_sensitivity_parquet(d / "sensitivity.parquet", schema_version="stale-v0")
        html = str(charts_page())
        assert "Cannot render this page" in html
        assert "schema_version mismatch" in html

    def test_walkthroughs_page_renders_real_transcript_and_degrades_depth(
        self, tmp_path, monkeypatch
    ):
        from lofsite.pages.charts import walkthroughs_page

        d = self._setup(tmp_path, monkeypatch)
        with (d / "transcripts.jsonl").open("w") as f:
            f.write(
                json.dumps(
                    {
                        "call_id": "c1",
                        "item_index": 0,
                        "form_id": "form:a",
                        "form_string": "()",
                        "dialect_id": "canonical",
                        "model": "m1",
                        "correct": True,
                        "target": "marked",
                        "predicted": "marked",
                        "full_completion_text": "marked",
                        "rendered_input": "()",
                        "is_image": False,
                        "schema_version": SCHEMA_VERSION,
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "call_id": "c2",
                        "item_index": 0,
                        "form_id": "form:b",
                        "form_string": "(())",
                        "dialect_id": "canonical",
                        "model": "m1",
                        "correct": False,
                        "target": "unmarked",
                        "predicted": "marked",
                        "full_completion_text": "marked",
                        "rendered_input": None,
                        "is_image": False,
                        "schema_version": SCHEMA_VERSION,
                    }
                )
                + "\n"
            )
        html = str(walkthroughs_page())
        assert "CORRECT" in html
        assert "INCORRECT" in html
        assert "not available" in html  # no items artifact -- depth degrades gracefully
