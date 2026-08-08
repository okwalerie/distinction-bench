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
from lofbench.publication import scan_publication
from lofbench.records import CallRecord, LedgerEvent, RunManifest, TrialRecord
from lofbench.renderers.pipeline.composed import ComposedRenderer
from lofbench.suites import DEFAULT_SUITE_VERSION, SUITES_DIR, load_suite

BUNDLE_SCHEMA_VERSION = 2
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
    "ledger.jsonl",
}
_EMPTY_DERIVED_SCHEMA = pa.schema([("run_id", pa.string())])
_SAMPLE_PROTOCOLS = {
    "reduce-infer-v1",
    "reduce-taught-v1",
    "transcribe-infer-v1",
    "transcribe-taught-v1",
}


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
        sample_contract: dict[str, Any] | None = None,
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
        (root / "ledger.jsonl").write_text("")
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
            "sample_contract": sample_contract,
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

    def require_repository_state(self, repository_root: Path) -> None:
        """Require the exact clean commit captured when the bundle was planned."""
        commit, dirty = _git_state(repository_root)
        if dirty:
            raise RuntimeError("release operation requires a clean worktree")
        if commit != self.manifest["repository_commit"]:
            raise RuntimeError("release operation is not on the planned repository commit")

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
        ledger_events: Iterable[LedgerEvent] = (),
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
        call_values = list(calls)
        ledger_values = list(ledger_events)
        self._validate_run_accounting(run, rows, call_values, ledger_values)

        existing_runs = self.runs()
        if run.run_id in {item.run_id for item in existing_runs}:
            raise RuntimeError(f"run {run.run_id} is already present")
        existing_trials = _read_rows(self.root / "trials.parquet")
        if set(trial_ids) & {row["trial_id"] for row in existing_trials}:
            raise RuntimeError("trial id already exists in bundle")

        admitted = replace(run, status="admitted")
        with (self.root / "runs.jsonl").open("a") as handle:
            handle.write(_json_line(admitted.to_dict()))
        _write_rows(self.root / "trials.parquet", existing_trials + rows)
        call_rows = _read_rows(self.root / "calls.parquet") + [
            call.to_dict() for call in call_values
        ]
        _write_rows(self.root / "calls.parquet", call_rows)
        with (self.root / "ledger.jsonl").open("a") as handle:
            for event in ledger_values:
                handle.write(_json_line(event.to_dict()))
        with (self.root / "transcripts.jsonl").open("a") as handle:
            for transcript in transcripts:
                handle.write(_json_line(transcript))
        self.manifest["admitted_run_ids"] = sorted([*self.manifest["admitted_run_ids"], run.run_id])
        self._write_manifest()

    @staticmethod
    def _validate_run_accounting(
        run: RunManifest,
        trials: list[dict[str, Any]],
        calls: list[CallRecord],
        ledger_events: list[LedgerEvent],
    ) -> None:
        if len(calls) != run.attempts or not calls:
            raise RuntimeError("run attempt count does not match its call records")
        if len({call.call_id for call in calls}) != len(calls):
            raise RuntimeError("run contains duplicate call ids")
        if any(call.run_id != run.run_id for call in calls):
            raise RuntimeError("call run_id mismatch")
        provider_request_ids = [
            call.provider_request_id for call in calls if call.provider_request_id
        ]
        if len(provider_request_ids) != len(set(provider_request_ids)):
            raise RuntimeError("run contains duplicate provider request ids")
        by_trial: dict[str, list[CallRecord]] = {}
        for call in calls:
            by_trial.setdefault(call.trial_id, []).append(call)
        trial_by_id = {row["trial_id"]: row for row in trials}
        if set(by_trial) != set(trial_by_id):
            raise RuntimeError("call trial ids do not match admitted trials")
        for trial_id, trial_calls in by_trial.items():
            trial_calls.sort(key=lambda call: call.attempt)
            if [call.attempt for call in trial_calls] != list(range(1, len(trial_calls) + 1)):
                raise RuntimeError("call attempts are not contiguous")
            complete = [call for call in trial_calls if call.status == "complete"]
            if len(complete) != 1 or complete[0] is not trial_calls[-1]:
                raise RuntimeError("each admitted trial requires one final complete call")
            call = complete[0]
            trial = trial_by_id[trial_id]
            if trial["attempt_count"] != call.attempt:
                raise RuntimeError("trial attempt count does not match its complete call")
            for field in (
                "resolved_model_id",
                "endpoint",
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "observed_cost_usd",
            ):
                if trial[field] != getattr(call, field):
                    raise RuntimeError(f"trial/call accounting mismatch for {field}")
            if run.execution_surface == "direct_api" and not call.provider_request_id:
                raise RuntimeError("direct-api call is missing its provider request id")
        if abs(sum(call.observed_cost_usd for call in calls) - run.cost_usd) > 1e-12:
            raise RuntimeError("run cost does not match call records")
        for field in ("input_tokens", "output_tokens", "reasoning_tokens"):
            if sum(getattr(call, field) for call in calls) != run.token_usage.get(field, 0):
                raise RuntimeError(f"run usage does not match calls for {field}")
        events_by_call: dict[str, list[LedgerEvent]] = {}
        for event in ledger_events:
            events_by_call.setdefault(event.call_id, []).append(event)
        if set(events_by_call) != {call.call_id for call in calls}:
            raise RuntimeError("ledger call ids do not match run calls")
        calls_by_id = {call.call_id: call for call in calls}
        for call_id, events in events_by_call.items():
            if len(events) != 2 or [event.event_type for event in events] != [
                "reserved",
                "settled",
            ]:
                raise RuntimeError("each call requires one reservation and settlement")
            call = calls_by_id[call_id]
            reserved, settled = events
            if any(
                (event.run_id, event.trial_id, event.cohort)
                != (run.run_id, call.trial_id, run.cohort)
                for event in events
            ):
                raise RuntimeError("ledger identity does not match run call")
            if reserved.amount_usd != call.reserved_cost_usd:
                raise RuntimeError("ledger reservation does not match call")
            if settled.amount_usd != call.observed_cost_usd:
                raise RuntimeError("ledger settlement does not match call")

    def runs(self) -> list[RunManifest]:
        """Return the typed run manifests admitted to this bundle."""
        return [
            RunManifest.from_dict(json.loads(line))
            for line in (self.root / "runs.jsonl").read_text().splitlines()
            if line.strip()
        ]

    def _artifact_paths(self) -> list[Path]:
        return sorted(
            path for path in self.root.rglob("*") if path.is_file() and path.name != "release.json"
        )

    def validate(self) -> None:
        if set(path.name for path in self.root.iterdir() if path.is_file()) != _ROOT_FILES:
            raise RuntimeError("release root files do not match the bundle schema")
        load_suite(version=self.manifest["suite_version"], path=self.root / "suite.json")
        load_protocol_registry(self.root / "protocols.json")
        runs = self.runs()
        if len({run.run_id for run in runs}) != len(runs):
            raise RuntimeError("duplicate run ids")
        trial_rows = _read_rows(self.root / "trials.parquet")
        call_rows = _read_rows(self.root / "calls.parquet")
        ledger_rows = [
            LedgerEvent.from_dict(json.loads(line))
            for line in (self.root / "ledger.jsonl").read_text().splitlines()
            if line.strip()
        ]
        if len({row["trial_id"] for row in trial_rows}) != len(trial_rows):
            raise RuntimeError("duplicate trial ids")
        if len({row["call_id"] for row in call_rows}) != len(call_rows):
            raise RuntimeError("duplicate call ids")
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
            if run.status == "admitted":
                self._validate_run_accounting(
                    run,
                    [row for row in trial_rows if row["run_id"] == run.run_id],
                    [CallRecord(**row) for row in call_rows if row["run_id"] == run.run_id],
                    [event for event in ledger_rows if event.run_id == run.run_id],
                )
        self._validate_sample_contract(runs, trial_rows, call_rows)
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
                    if (
                        row is None
                        or sha256(row["model_payload"].encode()).hexdigest()
                        != cell["model_payload_sha256"]
                    ):
                        raise RuntimeError(
                            "missing or changed materialized text stimulus "
                            f"{cell['abstract_form_id']}/{cell['dialect_id']}"
                        )
                else:
                    path = self.root / cell["asset_path"]
                    if (
                        not path.is_file()
                        or sha256(path.read_bytes()).hexdigest() != cell["model_payload_sha256"]
                    ):
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

    def _validate_sample_contract(
        self,
        runs: list[RunManifest],
        trials: list[dict[str, Any]],
        calls: list[dict[str, Any]],
        *,
        sealing: bool = False,
    ) -> None:
        contract = self.manifest.get("sample_contract")
        if not contract:
            return
        if self.manifest["release_id"] != "v1.0.0-sample.1":
            raise RuntimeError("sample contract is attached to the wrong release id")
        expected_protocols = set(contract["protocol_ids"])
        if (
            expected_protocols != _SAMPLE_PROTOCOLS
            or contract["form_set"] != "probe"
            or contract["dialect_id"] != "enclosure.plain-v1"
            or contract["execution_surface"] != "direct_api"
            or contract["max_transport_attempts"] != 1
            or contract["trials_per_run"] != 5
            or contract["total_attempts"] != 20
        ):
            raise RuntimeError("sample contract does not declare the frozen 4 x 5 invariant")
        if len(runs) > len(expected_protocols):
            raise RuntimeError("sample bundle contains too many protocol runs")
        if runs:
            if not {run.protocol_id for run in runs} <= expected_protocols or len(
                {run.protocol_id for run in runs}
            ) != len(runs):
                raise RuntimeError("sample protocols do not match the contract")
            if any(
                run.form_set != contract["form_set"]
                or len(run.expected_trial_ids) != contract["trials_per_run"]
                or run.max_transport_attempts != contract["max_transport_attempts"]
                or run.execution_surface != contract["execution_surface"]
                or run.dialect_set != contract["dialect_id"]
                for run in runs
            ):
                raise RuntimeError("sample run shape does not match the contract")
            for field in (
                "dialect_set",
                "requested_model_id",
                "resolved_model_id",
                "endpoint",
                "provider",
            ):
                if len({getattr(run, field) for run in runs}) != 1:
                    raise RuntimeError(f"sample runs do not share one {field}")
            for run in runs:
                selected = run.catalog_row.get("selected_endpoint", {})
                zdr_selected = run.catalog_row.get("zdr_selected_endpoint", {})
                endpoint_identity = (
                    run.resolved_model_id,
                    selected.get("tag"),
                    selected.get("provider_name"),
                )
                if (
                    run.routing_policy.get("order") != [run.endpoint]
                    or run.routing_policy.get("allow_fallbacks") is not False
                    or run.routing_policy.get("data_collection") != "deny"
                    or run.routing_policy.get("zdr") is not True
                    or run.privacy_policy.get("data_collection") != "deny"
                    or run.privacy_policy.get("zdr") is not True
                    or run.privacy_policy.get("authenticated_zdr_catalog") is not True
                    or run.generation.get("max_retries") != 0
                    or run.catalog_row.get("authenticated") is not True
                    or endpoint_identity
                    != (
                        zdr_selected.get("model_id"),
                        zdr_selected.get("tag"),
                        zdr_selected.get("provider_name"),
                    )
                    or selected.get("pricing") != zdr_selected.get("pricing")
                ):
                    raise RuntimeError("sample run lacks exact authenticated ZDR routing proof")
        admitted = [run for run in runs if run.status == "admitted"]
        if admitted:
            expected_forms = set(
                load_suite(
                    version=self.manifest["suite_version"], path=self.root / "suite.json"
                ).form_sets["probe"]
            )
            forms_by_run = {
                run.run_id: {
                    row["abstract_form_id"] for row in trials if row["run_id"] == run.run_id
                }
                for run in admitted
            }
            if len({tuple(sorted(ids)) for ids in forms_by_run.values()}) != 1:
                raise RuntimeError("sample runs do not use the same frozen probe forms")
            if any(ids != expected_forms for ids in forms_by_run.values()):
                raise RuntimeError("sample runs do not use the exact frozen probe form set")
        if self.manifest["status"] == "sealed" or sealing:
            if len(admitted) != len(expected_protocols):
                raise RuntimeError("sealed sample requires four admitted runs")
            if len(calls) != contract["total_attempts"]:
                raise RuntimeError("sealed sample does not contain exactly twenty attempts")
            approval = self.manifest.get("paid_run_approval") or {}
            if (
                approval.get("release_id") != self.manifest["release_id"]
                or approval.get("max_spend_usd") != 30.0
                or approval.get("paid_calls") != contract["total_attempts"]
                or set(approval.get("run_ids", [])) != {run.run_id for run in admitted}
                or approval.get("model_id") != admitted[0].requested_model_id
                or approval.get("endpoint") != admitted[0].endpoint
                or approval.get("provider")
                != admitted[0].catalog_row["selected_endpoint"]["provider_name"]
                or not approval.get("approved_by")
                or not approval.get("scope")
            ):
                raise RuntimeError("sealed sample lacks exact human paid-run approval")
            profiles = _read_rows(self.root / "profiles.parquet")
            if (
                len(profiles) != len(expected_protocols)
                or {row["protocol_id"] for row in profiles} != expected_protocols
                or any(row["coverage"] != 1.0 for row in profiles)
            ):
                raise RuntimeError("sealed sample requires complete derived profiles")
            if not any((self.root / "site").iterdir()):
                raise RuntimeError("sealed sample requires its static site")

    def seal(self, *, repository_root: Path) -> None:
        self._require_working()
        self.require_repository_state(repository_root)
        expected = set(self.manifest["expected_run_ids"])
        if expected and expected != set(self.manifest["admitted_run_ids"]):
            raise RuntimeError("not every expected run is admitted")
        self.validate()
        self._validate_sample_contract(
            self.runs(),
            _read_rows(self.root / "trials.parquet"),
            _read_rows(self.root / "calls.parquet"),
            sealing=True,
        )
        scan_publication(self.root)
        self.manifest["status"] = "sealed"
        self.manifest["sealed_at"] = _utc_now()
        self.manifest["files"] = {
            path.relative_to(self.root).as_posix(): _file_digest(path)
            for path in self._artifact_paths()
        }
        self._write_manifest()
        self.validate()
