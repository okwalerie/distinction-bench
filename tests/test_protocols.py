from __future__ import annotations

import json

import pytest

from lofbench.protocols import (
    PROTOCOLS,
    get_protocol,
    load_protocol_registry,
    write_protocol_registry,
)


def test_protocol_zoo_is_versioned_and_complete():
    assert set(PROTOCOLS) == {
        "reduce-infer-v1",
        "reduce-taught-v1",
        "transcribe-infer-v1",
        "transcribe-taught-v1",
    }
    assert all(spec.maximum_output_tokens == 512 for spec in PROTOCOLS.values())
    assert all("chain of thought" in spec.system_text for spec in PROTOCOLS.values())


def test_infer_protocol_has_no_reading_rule():
    text = get_protocol("reduce-infer-v1").render_user_text(reading_rule="secret legend")
    assert "secret legend" not in text


def test_taught_protocol_requires_and_uses_only_declared_rule():
    spec = get_protocol("reduce-taught-v1")
    with pytest.raises(ValueError, match="requires"):
        spec.render_user_text()
    rendered = spec.render_user_text(reading_rule="ovals are marks")
    assert "ovals are marks" in rendered


def test_registry_round_trip(tmp_path):
    path = tmp_path / "protocols.json"
    write_protocol_registry(path)
    loaded = load_protocol_registry(path)
    assert loaded == PROTOCOLS
    raw = json.loads(path.read_text())
    assert raw["schema_version"] == 1


def test_unknown_protocol_fails():
    with pytest.raises(ValueError, match="unknown protocol"):
        get_protocol("reduce-something")
