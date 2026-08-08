"""The sealed release-bundle publication boundary."""

from __future__ import annotations

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
            "files": {},
        }
        (root / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return cls(root, manifest)

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
