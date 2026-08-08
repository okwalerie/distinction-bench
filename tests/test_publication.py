from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lofbench.publication import (
    archive_release,
    export_inspect_bundle,
    redact_mapping,
    sanitize_public_mapping,
    scan_publication,
)


def test_redact_mapping_removes_named_fields_recursively():
    value = {"token": "secret", "nested": [{"api_key": "also secret", "ok": 1}]}
    assert redact_mapping(value, secret_fields=("token", "api_key")) == {
        "token": "[redacted]",
        "nested": [{"api_key": "[redacted]", "ok": 1}],
    }


def test_scan_reports_location_and_marker_but_never_secret(tmp_path: Path, monkeypatch):
    secret = "test-secret-value"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    (tmp_path / "bad.txt").write_text(f"OPENROUTER_API_KEY={secret}")
    with pytest.raises(RuntimeError) as raised:
        scan_publication(tmp_path)
    assert "bad.txt" in str(raised.value)
    assert secret not in str(raised.value)


def test_scan_finds_actual_environment_secret_inside_compressed_parquet(
    tmp_path: Path, monkeypatch
):
    secret = "opaque-secret-only-known-through-the-environment"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    pq.write_table(
        pa.Table.from_pylist([{"response": f"accidentally echoed {secret}"}]),
        tmp_path / "calls.parquet",
        compression="zstd",
    )
    with pytest.raises(RuntimeError) as raised:
        scan_publication(tmp_path)
    assert "calls.parquet" in str(raised.value)
    assert secret not in str(raised.value)


def test_public_sanitizer_drops_metadata_and_redacts_secret_values(monkeypatch):
    secret = "opaque-sanitizer-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    sanitized = sanitize_public_mapping(
        {
            "headers": {"Authorization": f"Bearer {secret}"},
            "nested": [{"response": f"echo {secret}", "ok": 1}],
        }
    )
    assert sanitized == {"nested": [{"response": "[redacted]", "ok": 1}]}


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_headers", {"Authorization": "Bearer nope"}),
        ("cwd", "/var/home/core/dev/distinction-bench"),
        ("api_key", "compressed-secret"),
    ],
)
def test_scan_rejects_unapproved_metadata_inside_parquet(tmp_path: Path, field, value):
    pq.write_table(pa.Table.from_pylist([{"ok": 1, field: value}]), tmp_path / "bad.parquet")
    with pytest.raises(RuntimeError, match="unapproved metadata"):
        scan_publication(tmp_path)


def test_inspect_export_is_compact_and_scanned(tmp_path: Path):
    release = tmp_path / "release"
    release.mkdir()
    (release / "suite.json").write_text("{}")
    (release / "protocols.json").write_text("{}")
    for name in ("runs.jsonl", "transcripts.jsonl", "ledger.jsonl"):
        (release / name).write_text("")
    for name in ("trials.parquet", "calls.parquet", "profiles.parquet", "effects.parquet"):
        pq.write_table(pa.table({"id": pa.array([], type=pa.string())}), release / name)
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
