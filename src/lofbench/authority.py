"""Immutable registry authority and canonical run-authority projections."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

SUITE_REGISTRY_GIT_PATH = "src/lofbench/registries/suites-v1.json"
PROTOCOL_REGISTRY_GIT_PATH = "src/lofbench/registries/protocols-v1.json"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class RegistryBlob:
    path: str
    sha256: str
    git_blob: str
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RegistryBlob:
        return cls(**dict(value))


@dataclass(frozen=True)
class AuthorityManifest:
    source_commit: str
    suite_registry: RegistryBlob
    protocol_registry: RegistryBlob

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_commit": self.source_commit,
            "suite_registry": self.suite_registry.to_dict(),
            "protocol_registry": self.protocol_registry.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AuthorityManifest:
        return cls(
            source_commit=str(value["source_commit"]),
            suite_registry=RegistryBlob.from_dict(value["suite_registry"]),
            protocol_registry=RegistryBlob.from_dict(value["protocol_registry"]),
        )


@dataclass(frozen=True)
class RunAuthority:
    suite_registry_sha256: str
    protocol_registry_sha256: str
    selected_form_set_sha256: str
    selected_cells_sha256: str
    selected_protocol_sha256: str
    endpoint_catalog_sha256: str
    execution_spec_sha256: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RunAuthority:
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected or any(
            not isinstance(value[key], str) or len(value[key]) != 64 for key in expected
        ):
            raise RuntimeError("run authority digest set is invalid")
        return cls(**{key: str(value[key]) for key in expected})


def _git_output(repository_root: Path, *args: str, text: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=text,
    )
    return result.stdout


def git_commit(repository_root: Path) -> str:
    return str(_git_output(repository_root, "rev-parse", "HEAD", text=True)).strip()


def _git_blob(repository_root: Path, commit: str, path: str) -> tuple[bytes, str]:
    payload = _git_output(repository_root, "show", f"{commit}:{path}")
    blob = str(
        _git_output(repository_root, "rev-parse", f"{commit}:{path}", text=True)
    ).strip()
    assert isinstance(payload, bytes)
    return payload, blob


def authority_from_git(
    repository_root: Path,
    *,
    commit: str | None = None,
) -> tuple[AuthorityManifest, bytes, bytes]:
    source_commit = commit or git_commit(repository_root)
    suite_bytes, suite_blob = _git_blob(
        repository_root, source_commit, SUITE_REGISTRY_GIT_PATH
    )
    protocol_bytes, protocol_blob = _git_blob(
        repository_root, source_commit, PROTOCOL_REGISTRY_GIT_PATH
    )
    manifest = AuthorityManifest(
        source_commit=source_commit,
        suite_registry=RegistryBlob(
            path=SUITE_REGISTRY_GIT_PATH,
            sha256=sha256(suite_bytes).hexdigest(),
            git_blob=suite_blob,
            bytes=len(suite_bytes),
        ),
        protocol_registry=RegistryBlob(
            path=PROTOCOL_REGISTRY_GIT_PATH,
            sha256=sha256(protocol_bytes).hexdigest(),
            git_blob=protocol_blob,
            bytes=len(protocol_bytes),
        ),
    )
    return manifest, suite_bytes, protocol_bytes


def verify_authority_copies(
    manifest: AuthorityManifest,
    *,
    suite_bytes: bytes,
    protocol_bytes: bytes,
    repository_root: Path | None = None,
) -> None:
    for label, record, payload in (
        ("suite", manifest.suite_registry, suite_bytes),
        ("protocol", manifest.protocol_registry, protocol_bytes),
    ):
        if len(payload) != record.bytes or sha256(payload).hexdigest() != record.sha256:
            raise RuntimeError(f"{label} registry evidence does not match recorded authority")
    if repository_root is None:
        return
    source, source_suite, source_protocol = authority_from_git(
        repository_root, commit=manifest.source_commit
    )
    if source != manifest:
        raise RuntimeError("authority manifest does not match its recorded git tree")
    if source_suite != suite_bytes or source_protocol != protocol_bytes:
        raise RuntimeError("registry evidence bytes do not match the recorded git tree")


def derive_run_authority(
    *,
    suite_bytes: bytes,
    protocol_bytes: bytes,
    form_ids: Sequence[str],
    dialect_id: str,
    protocol_id: str,
    execution_spec: Mapping[str, Any],
    catalog_retrieved_at: str,
    catalog_row: Mapping[str, Any],
) -> RunAuthority:
    try:
        suite = json.loads(suite_bytes)
        protocol_registry = json.loads(protocol_bytes)
        forms_by_id = {row["abstract_form_id"]: row for row in suite["forms"]}
        cells_by_key = {
            (row["abstract_form_id"], row["dialect_id"]): row for row in suite["cells"]
        }
        forms = [forms_by_id[form_id] for form_id in form_ids]
        cells = [cells_by_key[(form_id, dialect_id)] for form_id in form_ids]
        dialect = suite["dialects"][dialect_id]
        protocol = protocol_registry["protocols"][protocol_id]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("run selection is absent from its registry authority") from exc
    return RunAuthority(
        suite_registry_sha256=sha256(suite_bytes).hexdigest(),
        protocol_registry_sha256=sha256(protocol_bytes).hexdigest(),
        selected_form_set_sha256=canonical_sha256(
            {"abstract_form_ids": list(form_ids), "forms": forms}
        ),
        selected_cells_sha256=canonical_sha256(
            {"dialect": dialect, "cells": cells}
        ),
        selected_protocol_sha256=canonical_sha256(protocol),
        endpoint_catalog_sha256=canonical_sha256(
            {"retrieved_at": catalog_retrieved_at, "catalog_row": dict(catalog_row)}
        ),
        execution_spec_sha256=canonical_sha256(dict(execution_spec)),
    )
