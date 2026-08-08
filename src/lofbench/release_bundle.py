"""The sealed release-bundle publication boundary."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from lofbench.protocols import load_protocol_registry, write_protocol_registry
from lofbench.records import CallRecord, RunManifest, TrialRecord
from lofbench.renderers.pipeline.composed import ComposedRenderer
from lofbench.suites import DEFAULT_SUITE_VERSION, SUITES_DIR, load_suite

BUNDLE_SCHEMA_VERSION = 1
_ROOT_FILES = {
    "release.json",
    "suite.json",
    "protocols.json",
    "runs.jsonl",
    "trials.parquet",
    "calls.parquet",
    "profiles.parquet",
    "effects.parquet",
    "transcripts.jsonl",
}
_EMPTY_DERIVED_SCHEMA = pa.schema([("run_id", pa.string())])


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json_line(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _file_digest(path: Path) -> dict[str, Any]:
    return {"sha256": sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def _git_state(root: Path) -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return commit, dirty


def _write_rows(path: Path, rows: list[dict[str, Any]], schema: pa.Schema | None = None) -> None:
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path, compression="zstd")


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return pq.read_table(path).to_pylist()


def _write_bytes_verified(path: Path, payload: bytes, expected_sha256: str) -> None:
    actual = sha256(payload).hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(
            f"stimulus hash drift for {path}: expected {expected_sha256}, got {actual}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


class ReleaseBundle:
    """A small mutation surface around a working or sealed release directory."""

    def __init__(self, root: Path, manifest: dict[str, Any]) -> None:
        self.root = root
        self.manifest = manifest

    @classmethod
    def create_working(
        cls,
        root: Path,
        *,
        release_id: str,
        repository_url: str,
        repository_root: Path,
        suite_path: Path | None = None,
        expected_run_ids: Iterable[str] = (),
        paid_run_approval: dict[str, Any] | None = None,
        materialize_stimuli: bool = False,
    ) -> ReleaseBundle:
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"release directory is not empty: {root}")
        root.mkdir(parents=True, exist_ok=True)
        commit, dirty = _git_state(repository_root)
        if dirty:
            raise RuntimeError("cannot create a release bundle from a dirty worktree")
        source_suite = suite_path or (SUITES_DIR / f"{DEFAULT_SUITE_VERSION}.json")
        suite = load_suite(path=source_suite)
        shutil.copyfile(source_suite, root / "suite.json")
        write_protocol_registry(root / "protocols.json")
        (root / "runs.jsonl").write_text("")
        (root / "transcripts.jsonl").write_text("")
        _write_rows(root / "trials.parquet", [], pa.schema([("trial_id", pa.string())]))
        _write_rows(root / "calls.parquet", [], pa.schema([("call_id", pa.string())]))
        _write_rows(root / "profiles.parquet", [], _EMPTY_DERIVED_SCHEMA)
        _write_rows(root / "effects.parquet", [], _EMPTY_DERIVED_SCHEMA)
        (root / "stimuli" / "text").mkdir(parents=True)
        (root / "stimuli" / "image").mkdir(parents=True)
        (root / "inspect").mkdir()
        (root / "site").mkdir()
        manifest = {
            "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
            "release_id": release_id,
            "status": "working",
            "repository_url": repository_url,
            "repository_commit": commit,
            "suite_version": suite.suite_version,
            "protocol_ids": sorted(load_protocol_registry(root / "protocols.json")),
            "created_at": _utc_now(),
            "sealed_at": None,
            "licenses": {
                "code": "MIT",
                "suite_and_site_content": "CC-BY-4.0",
            },
            "citations": ["George Spencer-Brown, Laws of Form (1969)"],
            "expected_run_ids": sorted(expected_run_ids),
            "admitted_run_ids": [],
            "paid_run_approval": paid_run_approval,
            "stimuli_materialized": False,
            "files": {},
        }
        (root / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
        bundle = cls(root, manifest)
        if materialize_stimuli:
            bundle.materialize_stimuli()
        return bundle

    @classmethod
    def open(cls, root: Path) -> ReleaseBundle:
        manifest = json.loads((root / "release.json").read_text())
        if manifest.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
            raise RuntimeError("unsupported release-bundle schema")
        return cls(root, manifest)

    def _require_working(self) -> None:
        if self.manifest["status"] != "working":
            raise RuntimeError("sealed release bundles are immutable")

    def _write_manifest(self) -> None:
        (self.root / "release.json").write_text(json.dumps(self.manifest, indent=2) + "\n")

    def materialize_stimuli(self) -> None:
        """Write every frozen model payload and verify it against ``suite.json``.

        Text payloads are already frozen in the suite. Spatial payloads are
        deterministically re-rendered here because storing every PNG twice in git
        would make the suite artifact needlessly heavy. A release may only claim
        materialization after all 11,600 frozen hashes have been checked.
        """
        self._require_working()
        suite = load_suite(version=self.manifest["suite_version"], path=self.root / "suite.json")
        forms = {form["abstract_form_id"]: form for form in suite.forms}
        text_rows: dict[str, list[dict[str, str]]] = {}
        for cell in suite.cells:
            relative = Path(cell["asset_path"])
            target = self.root / relative
            if cell["modality"] == "text":
                payload = cell["model_payload"].encode("utf-8")
                if sha256(payload).hexdigest() != cell["model_payload_sha256"]:
                    raise RuntimeError(
                        "frozen text hash drift for "
                        f"{cell['abstract_form_id']}/{cell['dialect_id']}"
                    )
                text_rows.setdefault(cell["dialect_id"], []).append(
                    {
                        "abstract_form_id": cell["abstract_form_id"],
                        "model_payload": cell["model_payload"],
                        "model_payload_sha256": cell["model_payload_sha256"],
                    }
                )
                continue

            form = forms[cell["abstract_form_id"]]
            rendered = ComposedRenderer(suite.specs[cell["dialect_id"]]).render(
                form["reference_transcription"]
            )
            prefix = "data:image/png;base64,"
            if not rendered.rendered.startswith(prefix):
                raise RuntimeError(f"spatial stimulus is not png: {relative}")
            payload = base64.b64decode(rendered.rendered.removeprefix(prefix), validate=True)
            _write_bytes_verified(target, payload, cell["model_payload_sha256"])

        for dialect_id, rows in sorted(text_rows.items()):
            target = self.root / "stimuli" / "text" / f"{dialect_id}.json"
            target.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "suite_version": suite.suite_version,
                        "dialect_id": dialect_id,
                        "cells": sorted(rows, key=lambda row: row["abstract_form_id"]),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            )
        self.manifest["stimuli_materialized"] = True
        self._write_manifest()

    def admit_run(
        self,
        run: RunManifest,
        trials: Iterable[TrialRecord],
        *,
        calls: Iterable[CallRecord] = (),
        transcripts: Iterable[dict[str, Any]] = (),
    ) -> None:
        self._require_working()
        suite = load_suite(version=self.manifest["suite_version"], path=self.root / "suite.json")
        protocols = load_protocol_registry(self.root / "protocols.json")
        if run.suite_version != suite.suite_version or run.suite_version == "pilot-v0":
            raise RuntimeError("run suite does not match this v1 bundle")
        if run.protocol_id not in protocols:
            raise RuntimeError(f"unknown protocol {run.protocol_id!r}")
        if run.status != "complete":
            raise RuntimeError("only complete runs can be admitted")
        rows = [trial.to_dict() for trial in trials]
        trial_ids = [row["trial_id"] for row in rows]
        if len(trial_ids) != len(set(trial_ids)):
            raise RuntimeError("run contains duplicate trial ids")
        if set(trial_ids) != set(run.expected_trial_ids):
            raise RuntimeError("run is incomplete for its declared trial ids")
        if any(row["run_id"] != run.run_id for row in rows):
            raise RuntimeError("trial run_id mismatch")
        if any(row["resolved_model_id"] != run.resolved_model_id for row in rows):
            raise RuntimeError("resolved model drift within run")

        existing_runs = self._runs()
        if run.run_id in {item.run_id for item in existing_runs}:
            raise RuntimeError(f"run {run.run_id} is already present")
        existing_trials = _read_rows(self.root / "trials.parquet")
        if set(trial_ids) & {row["trial_id"] for row in existing_trials}:
            raise RuntimeError("trial id already exists in bundle")

        admitted = replace(run, status="admitted")
        with (self.root / "runs.jsonl").open("a") as handle:
            handle.write(_json_line(admitted.to_dict()))
        _write_rows(self.root / "trials.parquet", existing_trials + rows)
        call_rows = _read_rows(self.root / "calls.parquet") + [call.to_dict() for call in calls]
        _write_rows(self.root / "calls.parquet", call_rows)
        with (self.root / "transcripts.jsonl").open("a") as handle:
            for transcript in transcripts:
                handle.write(_json_line(transcript))
        self.manifest["admitted_run_ids"] = sorted(
            [*self.manifest["admitted_run_ids"], run.run_id]
        )
        self._write_manifest()

    def _runs(self) -> list[RunManifest]:
        return [
            RunManifest.from_dict(json.loads(line))
            for line in (self.root / "runs.jsonl").read_text().splitlines()
            if line.strip()
        ]

    def _artifact_paths(self) -> list[Path]:
        return sorted(
            path
            for path in self.root.rglob("*")
            if path.is_file() and path.name != "release.json"
        )

    def validate(self) -> None:
        if set(path.name for path in self.root.iterdir() if path.is_file()) != _ROOT_FILES:
            raise RuntimeError("release root files do not match the bundle schema")
        load_suite(version=self.manifest["suite_version"], path=self.root / "suite.json")
        load_protocol_registry(self.root / "protocols.json")
        runs = self._runs()
        if len({run.run_id for run in runs}) != len(runs):
            raise RuntimeError("duplicate run ids")
        trial_rows = _read_rows(self.root / "trials.parquet")
        if len({row["trial_id"] for row in trial_rows}) != len(trial_rows):
            raise RuntimeError("duplicate trial ids")
        admitted = {run.run_id for run in runs if run.status == "admitted"}
        if admitted != set(self.manifest["admitted_run_ids"]):
            raise RuntimeError("admitted run manifest mismatch")
        by_run: dict[str, set[str]] = {}
        for row in trial_rows:
            by_run.setdefault(row["run_id"], set()).add(row["trial_id"])
        for run in runs:
            if run.status == "admitted" and by_run.get(run.run_id, set()) != set(
                run.expected_trial_ids
            ):
                raise RuntimeError(f"admitted run {run.run_id} is incomplete")
        if self.manifest.get("stimuli_materialized"):
            suite = load_suite(
                version=self.manifest["suite_version"], path=self.root / "suite.json"
            )
            text_payloads: dict[tuple[str, str], dict[str, str]] = {}
            for dialect_id, spec in suite.specs.items():
                if spec.modality != "text":
                    continue
                text_path = self.root / "stimuli" / "text" / f"{dialect_id}.json"
                if not text_path.is_file():
                    raise RuntimeError(f"missing materialized stimulus file {text_path}")
                payload = json.loads(text_path.read_text())
                for row in payload["cells"]:
                    text_payloads[(dialect_id, row["abstract_form_id"])] = row
            for cell in suite.cells:
                if cell["modality"] == "text":
                    row = text_payloads.get((cell["dialect_id"], cell["abstract_form_id"]))
                    if row is None or sha256(row["model_payload"].encode()).hexdigest() != cell[
                        "model_payload_sha256"
                    ]:
                        raise RuntimeError(
                            "missing or changed materialized text stimulus "
                            f"{cell['abstract_form_id']}/{cell['dialect_id']}"
                        )
                else:
                    path = self.root / cell["asset_path"]
                    if not path.is_file() or sha256(path.read_bytes()).hexdigest() != cell[
                        "model_payload_sha256"
                    ]:
                        raise RuntimeError(f"missing or changed materialized image stimulus {path}")
        if self.manifest["status"] == "sealed":
            actual_paths = {
                path.relative_to(self.root).as_posix(): path for path in self._artifact_paths()
            }
            if set(actual_paths) != set(self.manifest["files"]):
                raise RuntimeError("sealed bundle contains an unlisted or missing artifact")
            for relative, path in actual_paths.items():
                if _file_digest(path) != self.manifest["files"][relative]:
                    raise RuntimeError(f"checksum drift for {relative}")

    def seal(self, *, repository_root: Path) -> None:
        self._require_working()
        commit, dirty = _git_state(repository_root)
        if dirty:
            raise RuntimeError("cannot seal a release bundle from a dirty worktree")
        if commit != self.manifest["repository_commit"]:
            raise RuntimeError("repository commit changed since bundle creation")
        expected = set(self.manifest["expected_run_ids"])
        if expected and expected != set(self.manifest["admitted_run_ids"]):
            raise RuntimeError("not every expected run is admitted")
        self.validate()
        self.manifest["status"] = "sealed"
        self.manifest["sealed_at"] = _utc_now()
        self.manifest["files"] = {
            path.relative_to(self.root).as_posix(): _file_digest(path)
            for path in self._artifact_paths()
        }
        self._write_manifest()
        self.validate()
