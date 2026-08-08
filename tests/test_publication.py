from __future__ import annotations

import json
from pathlib import Path

import pytest

from lofbench.publication import (
    archive_release,
    export_inspect_bundle,
    redact_mapping,
    scan_publication,
)


def test_redact_mapping_removes_named_fields_recursively():
    value = {"token": "secret", "nested": [{"api_key": "also secret", "ok": 1}]}
    assert redact_mapping(value, secret_fields=("token", "api_key")) == {
        "token": "[redacted]",
        "nested": [{"api_key": "[redacted]", "ok": 1}],
    }


def test_scan_reports_location_and_marker_but_never_secret(tmp_path: Path):
    secret = "test-secret-value"
    (tmp_path / "bad.txt").write_text(f"OPENROUTER_API_KEY={secret}")
    with pytest.raises(RuntimeError) as raised:
        scan_publication(tmp_path, secret_values=(secret,))
    assert "bad.txt" in str(raised.value)
    assert secret not in str(raised.value)


def test_inspect_export_is_compact_and_scanned(tmp_path: Path):
    release = tmp_path / "release"
    release.mkdir()
    for name in (
        "suite.json",
        "protocols.json",
        "runs.jsonl",
        "trials.parquet",
        "calls.parquet",
        "profiles.parquet",
        "effects.parquet",
        "transcripts.jsonl",
    ):
        (release / name).write_text("")
    (release / "release.json").write_text(json.dumps({"release_id": "test"}))
    output = export_inspect_bundle(release, tmp_path / "inspect.zip")
    assert output.is_file()


def test_release_archive_is_reproducible(tmp_path: Path):
    release = tmp_path / "release"
    release.mkdir()
    (release / "release.json").write_text('{"status":"sealed"}\n')
    first = archive_release(release, tmp_path / "first.tar.gz")
    second = archive_release(release, tmp_path / "second.tar.gz")
    assert first.read_bytes() == second.read_bytes()
