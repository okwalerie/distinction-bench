"""Publication safety checks and derived reviewer exports."""

from __future__ import annotations

import gzip
import json
import shutil
import tarfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

_SECRET_MARKERS = {
    b"OPENROUTER_API_KEY=": "openrouter assignment",
    b"ANTHROPIC_API_KEY=": "anthropic assignment",
    b"GOOGLE_API_KEY=": "google assignment",
    b"sk-or-v1-": "openrouter key prefix",
    b"sk-ant-": "anthropic key prefix",
}


def redact_mapping(value: Mapping[str, Any], *, secret_fields: Iterable[str]) -> dict[str, Any]:
    """Return a JSON-safe copy with named secret fields removed at every depth."""
    secret_names = {name.lower() for name in secret_fields}

    def visit(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): "[redacted]" if str(key).lower() in secret_names else visit(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, tuple):
            return [visit(child) for child in item]
        return item

    return visit(value)


def scan_publication(root: Path, *, secret_values: Iterable[str] = ()) -> None:
    """Fail closed on recognizable credentials without echoing matched bytes."""
    values = [value.encode() for value in secret_values if value]
    findings: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        for marker, label in _SECRET_MARKERS.items():
            if marker in payload:
                findings.append(f"{relative}: {label}")
        if any(value in payload for value in values):
            findings.append(f"{relative}: supplied secret value")
    if findings:
        raise RuntimeError("publication secret scan failed: " + "; ".join(findings))


def export_inspect_bundle(release_root: Path, out: Path) -> Path:
    """Create a compact reviewer archive after the same release safety scan."""
    if out.exists():
        raise FileExistsError(f"inspect export already exists: {out}")
    scan_publication(release_root)
    selected = (
        "release.json",
        "suite.json",
        "protocols.json",
        "runs.jsonl",
        "trials.parquet",
        "calls.parquet",
        "profiles.parquet",
        "effects.parquet",
        "transcripts.jsonl",
    )
    temporary = out.parent / f".{out.name}.working"
    if temporary.exists():
        raise FileExistsError(f"inspect export staging directory exists: {temporary}")
    temporary.mkdir(parents=True)
    try:
        for name in selected:
            shutil.copyfile(release_root / name, temporary / name)
        (temporary / "README.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "purpose": "compact inspect/reviewer bundle",
                    "source_release": json.loads(
                        (release_root / "release.json").read_text()
                    )["release_id"],
                },
                indent=2,
            )
            + "\n"
        )
        archive = Path(
            shutil.make_archive(
                str(out.with_suffix("")),
                "zip",
                root_dir=temporary,
            )
        )
        if archive != out:
            archive.replace(out)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return out


def archive_release(release_root: Path, out: Path) -> Path:
    """Write a deterministic tar.gz of a validated, sealed release directory."""
    if out.exists():
        raise FileExistsError(f"release archive already exists: {out}")
    scan_publication(release_root)
    out.parent.mkdir(parents=True, exist_ok=True)

    def normalized(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        return info

    with out.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in sorted(release_root.rglob("*")):
                    archive.add(
                        path,
                        arcname=path.relative_to(release_root),
                        recursive=False,
                        filter=normalized,
                    )
    return out
