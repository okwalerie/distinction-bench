"""Public suite generation, identity, and rerun gates."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace

import pytest

from lofbench.core import DIFFICULTY_CONFIGS, normal_value, string_depth, string_to_form
from lofbench.renderers import get_renderer
from lofbench.renderers.runtime import visual_runtime_available
from lofbench.suites import (
    DEFAULT_SUITE_VERSION,
    EXCLUDED_DIALECT_IDS,
    _dialect_sets,
    _form_sets,
    compute_cells,
    freeze_suite,
    frozen_dialect_specs,
    generate_form_table,
    load_suite,
    verify_suite,
)


def test_visual_gate_requires_optional_package_when_native_cairo_is_present():
    def missing_package(_name: str) -> object:
        raise ModuleNotFoundError("cairosvg")

    assert not visual_runtime_available(
        find_library=lambda _name: "libcairo.so",
        import_module=missing_package,
    )


def _small_form(transcription: str = "(()())") -> dict:
    return {
        "abstract_form_id": "lof_test",
        "abstract_form": string_to_form(transcription),
        "reference_transcription": transcription,
        "normal_value": normal_value(transcription),
        "difficulty": "1. easy",
        "depth": string_depth(transcription),
        "mark_count": transcription.count("("),
    }


class TestGenerateFormTable:
    def test_balanced_bounds_and_counts(self):
        forms = generate_form_table(seed=1, per_tier=8)
        assert len(forms) == 40
        configs = {
            name: (min_d, max_d, max_m) for name, min_d, max_d, _width, max_m in DIFFICULTY_CONFIGS
        }
        for tier, tier_forms in _group_by(forms, "difficulty").items():
            min_d, max_d, max_marks = configs[tier]
            assert Counter(form["normal_value"] for form in tier_forms) == {
                "marked": 4,
                "unmarked": 4,
            }
            assert all(min_d <= form["depth"] <= max_d for form in tier_forms)
            assert all(form["mark_count"] <= max_marks for form in tier_forms)

    def test_content_ids_are_stable_across_requested_counts(self):
        small = generate_form_table(seed=42, per_tier=4)
        large = generate_form_table(seed=42, per_tier=8)
        assert {form["abstract_form_id"] for form in small} <= {
            form["abstract_form_id"] for form in large
        }

    def test_ids_and_transcriptions_are_unique_and_deterministic(self):
        first = generate_form_table(seed=7, per_tier=6)
        second = generate_form_table(seed=7, per_tier=6)
        assert first == second
        assert len({form["abstract_form_id"] for form in first}) == len(first)
        assert len({form["reference_transcription"] for form in first}) == len(first)

    def test_abstract_form_and_normal_value_are_exact(self):
        for form in generate_form_table(seed=9, per_tier=4):
            assert form["abstract_form"] == string_to_form(form["reference_transcription"])
            assert form["normal_value"] == normal_value(form["reference_transcription"])

    def test_rejects_odd_per_tier(self):
        with pytest.raises(ValueError, match="positive even"):
            generate_form_table(per_tier=5)


def _group_by(rows: list[dict], key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row[key], []).append(row)
    return grouped


class TestDialectManifest:
    def test_public_names_and_documentation(self):
        specs = frozen_dialect_specs()
        assert len(specs) == 29
        assert len({spec.family for spec in specs.values()}) == 13
        assert "circle" not in specs
        assert EXCLUDED_DIALECT_IDS == frozenset({"circle"})
        assert "parens.reference-v1" in specs
        assert "pattern.plain-v1" in specs
        assert "enclosure.plain-v1" in specs
        assert not any("canonical" in dialect_id for dialect_id in specs)
        for spec in specs.values():
            assert spec.label and spec.reading_rule and spec.description
            assert spec.modality in {"text", "image"}
            assert spec.model_format and spec.provenance and spec.citation
            assert spec.limitations

    def test_explicit_dialect_sets(self):
        sets = _dialect_sets(frozen_dialect_specs())
        assert len(sets["all"]) == 29
        assert len(sets["text"]) == 20
        assert len(sets["spatial"]) == 9
        assert len(sets["core"]) == 17
        assert len(sets["agent"]) == 13
        assert sets["probe_text"] == [
            "parens.reference-v1",
            "prose.containment-plain-v1",
        ]
        assert sets["probe_multimodal"] == [
            "parens.reference-v1",
            "enclosure.plain-v1",
        ]

    def test_every_public_id_resolves(self):
        for dialect_id in frozen_dialect_specs():
            assert get_renderer(dialect_id).name == dialect_id


class TestFormSets:
    def test_exact_counts_and_balance(self):
        forms = generate_form_table(seed=11, per_tier=80)
        sets = _form_sets(forms)
        assert len(sets["full"]) == 400
        assert len(sets["core"]) == 120
        assert len(sets["agent"]) == 40
        assert len(sets["probe"]) == 5
        by_id = {form["abstract_form_id"]: form for form in forms}
        for set_name, per_value in (("core", 60), ("agent", 20)):
            counts = Counter(by_id[form_id]["normal_value"] for form_id in sets[set_name])
            assert counts == {"marked": per_value, "unmarked": per_value}
        probe_counts = Counter(by_id[form_id]["normal_value"] for form_id in sets["probe"])
        assert probe_counts == {"marked": 3, "unmarked": 2}


class TestCells:
    def test_text_cell_records_exact_payload_hashes(self):
        specs = frozen_dialect_specs("test")
        subset = {key: specs[key] for key in ("parens.reference-v1", "pattern.plain-v1")}
        cells, collisions = compute_cells([_small_form()], subset)
        assert len(cells) == 2
        assert collisions == []
        assert all(len(cell["model_payload_sha256"]) == 64 for cell in cells)
        assert all(cell["model_payload"] for cell in cells)
        assert all(cell["structure_verified"] for cell in cells)
        assert all(cell["roundtrip_ok"] for cell in cells)

    def test_duplicate_payload_is_collision_evidence(self):
        spec = frozen_dialect_specs("test")["parens.reference-v1"]
        duplicate = replace(spec, dialect_id="parens.duplicate-v1")
        _cells, collisions = compute_cells(
            [_small_form()],
            {spec.dialect_id: spec, duplicate.dialect_id: duplicate},
        )
        assert len(collisions) == 1
        assert collisions[0]["dialect_ids"] == [
            "parens.duplicate-v1",
            "parens.reference-v1",
        ]


@pytest.mark.requires_visual_runtime
class TestFreezeLoadAndVerify:
    def test_small_round_trip(self, tmp_path):
        path = tmp_path / "test-suite.json"
        frozen = freeze_suite("test-suite", seed=17, per_tier=2, out_path=path)
        loaded = load_suite("test-suite", path=path)
        assert loaded.suite_version == "test-suite"
        assert len(loaded.forms) == 10
        assert len(loaded.specs) == 29
        assert len(loaded.cells) == 290
        assert frozen["collisions"] == []
        verify_suite("test-suite", path=path)

    def test_tamper_fails_rerun_gate(self, tmp_path):
        path = tmp_path / "test-suite.json"
        freeze_suite("test-suite", seed=19, per_tier=2, out_path=path)
        data = json.loads(path.read_text())
        data["cells"][0]["model_payload_sha256"] = "0" * 64
        path.write_text(json.dumps(data))
        with pytest.raises(RuntimeError, match="rerun gate failed"):
            verify_suite("test-suite", path=path)

    def test_committed_v1_suite(self):
        suite = load_suite(DEFAULT_SUITE_VERSION)
        assert len(suite.forms) == 400
        assert len(suite.specs) == 29
        assert len(suite.cells) == 11_600
        verify_suite(DEFAULT_SUITE_VERSION)
