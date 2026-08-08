"""Publication safety checks and derived reviewer exports."""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tarfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

_SECRET_MARKERS = {
    b"OPENROUTER_API_KEY=": "openrouter assignment",
    b"ANTHROPIC_API_KEY=": "anthropic assignment",
    b"GOOGLE_API_KEY=": "google assignment",
    b"sk-or-v1-": "openrouter key prefix",
    b"sk-ant-": "anthropic key prefix",
    b"Authorization:": "authorization header",
    b'authorization"': "authorization metadata",
    b"Bearer ": "bearer credential",
    b"x-api-key": "api-key header",
    b"/var/home/": "host home path",
    b"/home/": "host home path",
    b"C:\\Users\\": "host home path",
}
_DENIED_METADATA_FIELDS = {
    "api_key",
    "authorization",
    "cookies",
    "cwd",
    "env",
    "environment",
    "headers",
    "home",
    "request_headers",
    "token",
}
_SECRET_ENV_NAMES = (
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
)


def _secret_source(environment: Mapping[str, str] | None) -> Mapping[str, str]:
    if environment is None:
        return os.environ
    return {**os.environ, **environment}


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


def sanitize_public_mapping(
    value: Mapping[str, Any],
    *,
    secret_env_names: Iterable[str] = _SECRET_ENV_NAMES,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Drop denied metadata and redact secret-bearing scalar values for publication."""
    source = _secret_source(environment)
    secret_values = [secret for name in secret_env_names if (secret := source.get(name))]
    home = str(Path.home())
    if home not in {"", "/"}:
        secret_values.append(home)

    def visit(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): visit(child)
                for key, child in item.items()
                if str(key).lower() not in _DENIED_METADATA_FIELDS
            }
        if isinstance(item, (list, tuple)):
            return [visit(child) for child in item]
        if isinstance(item, str):
            payload = item.encode()
            if any(secret in item for secret in secret_values) or any(
                marker in payload for marker in _SECRET_MARKERS
            ):
                return "[redacted]"
        return item

    return visit(value)


def _structured_findings(
    value: Any,
    *,
    location: str,
    secret_values: tuple[bytes, ...],
) -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            child_location = f"{location}.{name}"
            if name.lower() in _DENIED_METADATA_FIELDS:
                findings.append(f"{child_location}: unapproved metadata field")
            findings.extend(
                _structured_findings(
                    child,
                    location=child_location,
                    secret_values=secret_values,
                )
            )
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            findings.extend(
                _structured_findings(
                    child,
                    location=f"{location}[{index}]",
                    secret_values=secret_values,
                )
            )
    elif isinstance(value, str):
        payload = value.encode()
        if any(secret in payload for secret in secret_values) or any(
            marker in payload for marker in _SECRET_MARKERS
        ):
            findings.append(f"{location}: secret or host path in structured value")
    return findings


def scan_publication(
    root: Path,
    *,
    secret_env_names: Iterable[str] = _SECRET_ENV_NAMES,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Fail closed on credentials, host paths, and unapproved metadata."""
    source = _secret_source(environment)
    values = tuple(value.encode() for name in secret_env_names if (value := source.get(name)))
    home = str(Path.home())
    if home not in {"", "/"}:
        values = (*values, home.encode())
    findings: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        for marker, label in _SECRET_MARKERS.items():
            if marker in payload:
                findings.append(f"{relative}: {label}")
        if any(value in payload for value in values):
            findings.append(f"{relative}: environment secret or home path")
        try:
            if path.suffix == ".json":
                structured: Any = json.loads(path.read_text())
            elif path.suffix == ".jsonl":
                structured = [
                    json.loads(line) for line in path.read_text().splitlines() if line.strip()
                ]
            elif path.suffix == ".parquet":
                structured = pq.read_table(path).to_pylist()
            else:
                structured = None
        except Exception as exc:  # noqa: BLE001 - malformed public data fails closed
            findings.append(f"{relative}: structured scan failed ({type(exc).__name__})")
            continue
        if structured is not None:
            findings.extend(
                _structured_findings(
                    structured,
                    location=relative,
                    secret_values=values,
                )
            )
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
        "human-trial.schema.json",
        "runs.jsonl",
        "trials.parquet",
        "calls.parquet",
        "profiles.parquet",
        "effects.parquet",
        "transcripts.jsonl",
        "ledger.jsonl",
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
                    "source_release": json.loads((release_root / "release.json").read_text())[
                        "release_id"
                    ],
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
