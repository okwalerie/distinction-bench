"""The sealed release-bundle publication boundary."""

from __future__ import annotations

import base64
import json
import math
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from lofbench.human_trials import HUMAN_TRIAL_EXPORT_SCHEMA, write_human_trial_schema
from lofbench.metrics import EMPTY_METRIC_SCHEMA, derive_metric_tables
from lofbench.protocols import ProtocolSpec, load_protocol_registry, write_protocol_registry
from lofbench.publication import scan_publication
from lofbench.records import CallRecord, LedgerEvent, RunManifest, TrialRecord
from lofbench.renderers.pipeline.composed import ComposedRenderer
from lofbench.run_models import call_id_for, trial_id_for
from lofbench.suites import DEFAULT_SUITE_VERSION, SUITES_DIR, LoadedSuite, load_suite

BUNDLE_SCHEMA_VERSION = 4
_ROOT_FILES = {
    "human-trial.schema.json",
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
        spend_caps_usd: dict[str, Any],
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
        write_human_trial_schema(root / "human-trial.schema.json")
        (root / "runs.jsonl").write_text("")
        (root / "transcripts.jsonl").write_text("")
        (root / "ledger.jsonl").write_text("")
        _write_rows(root / "trials.parquet", [], pa.schema([("trial_id", pa.string())]))
        _write_rows(root / "calls.parquet", [], pa.schema([("call_id", pa.string())]))
        _write_rows(root / "profiles.parquet", [], EMPTY_METRIC_SCHEMA)
        _write_rows(root / "effects.parquet", [], EMPTY_METRIC_SCHEMA)
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
            "spend_caps_usd": spend_caps_usd,
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
        self._validate_trial_provenance(run, suite, protocols[run.protocol_id], rows)
        trial_ids = [row["trial_id"] for row in rows]
        if len(trial_ids) != len(set(trial_ids)):
            raise RuntimeError("run contains duplicate trial ids")
        if set(trial_ids) != set(run.expected_trial_ids):
            raise RuntimeError("run is incomplete for its declared trial ids")
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
        existing_calls = _read_rows(self.root / "calls.parquet")
        existing_ledger = [
            LedgerEvent.from_dict(json.loads(line))
            for line in (self.root / "ledger.jsonl").read_text().splitlines()
            if line.strip()
        ]
        self._validate_release_accounting(
            [*existing_runs, admitted],
            existing_trials + rows,
            existing_calls + [call.to_dict() for call in call_values],
            [*existing_ledger, *ledger_values],
        )
        with (self.root / "runs.jsonl").open("a") as handle:
            handle.write(_json_line(admitted.to_dict()))
        _write_rows(self.root / "trials.parquet", existing_trials + rows)
        call_rows = existing_calls + [call.to_dict() for call in call_values]
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
    def _validate_trial_provenance(
        run: RunManifest,
        suite: LoadedSuite,
        protocol: ProtocolSpec,
        trials: list[dict[str, Any]],
    ) -> None:
        if run.run_id != run.authoritative_run_id():
            raise RuntimeError("run_id does not match the authoritative execution identity")
        if set(run.pricing) != {"prompt", "completion", "image"} or any(
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in run.pricing.values()
        ):
            raise RuntimeError("run lacks normalized prompt/completion/image pricing")
        if run.form_set not in suite.form_sets:
            raise RuntimeError(f"run uses unknown frozen form set {run.form_set!r}")
        if run.dialect_set not in suite.specs:
            raise RuntimeError(f"run uses unknown frozen dialect {run.dialect_set!r}")
        form_ids = tuple(suite.form_sets[run.form_set])
        authoritative_trial_ids = tuple(
            trial_id_for(run.run_id, form_id, run.dialect_set) for form_id in form_ids
        )
        if run.expected_trial_ids != authoritative_trial_ids:
            raise RuntimeError("run expected trial ids do not match its frozen form set")
        forms = {form["abstract_form_id"]: form for form in suite.forms}
        cells = {
            (cell["abstract_form_id"], cell["dialect_id"]): cell for cell in suite.cells
        }
        dialect = suite.specs[run.dialect_set]
        allowed_forms = set(form_ids)
        for trial in trials:
            form_id = trial["abstract_form_id"]
            if form_id not in allowed_forms:
                raise RuntimeError("trial abstract_form_id is not in the run's frozen form set")
            form = forms[form_id]
            cell = cells[(form_id, run.dialect_set)]
            parse_status, prediction, correct = protocol.parse_answer(
                trial["response_text"],
                expected_normal_value=form["normal_value"],
                expected_tree=form["abstract_form"],
            )
            authorities = {
                "trial_id": trial_id_for(run.run_id, form_id, run.dialect_set),
                "run_id": run.run_id,
                "suite_version": run.suite_version,
                "dialect_id": run.dialect_set,
                "protocol_id": run.protocol_id,
                "execution_surface": run.execution_surface,
                "requested_model_id": run.requested_model_id,
                "resolved_model_id": run.resolved_model_id,
                "provider": run.provider,
                "endpoint": run.endpoint,
                "prompt_hash": protocol.prompt_hash(
                    reading_rule=dialect.reading_rule,
                    model_payload_sha256=cell["model_payload_sha256"],
                ),
                "symbolic_payload_hash": cell["symbolic_payload_hash"],
                "model_payload_sha256": cell["model_payload_sha256"],
                "normal_value": form["normal_value"],
                "parse_status": parse_status,
                "prediction": prediction,
                "correct": correct,
            }
            for field, expected in authorities.items():
                if trial[field] != expected:
                    raise RuntimeError(f"trial provenance mismatch for {field}")

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

    def _validate_release_accounting(
        self,
        runs: list[RunManifest],
        trials: list[dict[str, Any]],
        call_rows: list[dict[str, Any]],
        ledger_events: list[LedgerEvent],
    ) -> None:
        admitted = {run.run_id: run for run in runs if run.status == "admitted"}
        if {row["run_id"] for row in trials} != set(admitted):
            raise RuntimeError("release trials do not close over the admitted run set")
        expected_calls: dict[str, tuple[str, str, int]] = {}
        for trial in trials:
            if trial["attempt_count"] < 1:
                raise RuntimeError("admitted trial has no provider attempt")
            for attempt in range(1, trial["attempt_count"] + 1):
                call_id = call_id_for(trial["trial_id"], attempt)
                expected_calls[call_id] = (trial["trial_id"], trial["run_id"], attempt)
        calls = [CallRecord(**row) for row in call_rows]
        if any(
            not math.isfinite(value) or value < 0
            for call in calls
            for value in (call.reserved_cost_usd, call.observed_cost_usd)
        ):
            raise RuntimeError("release contains an invalid provider cost")
        calls_by_id = {call.call_id: call for call in calls}
        if len(calls_by_id) != len(calls) or set(calls_by_id) != set(expected_calls):
            raise RuntimeError("release call records do not equal the derived provider-attempt set")
        request_ids = [call.provider_request_id for call in calls if call.provider_request_id]
        if len(request_ids) != len(set(request_ids)):
            raise RuntimeError("release contains duplicate provider request ids")
        for call_id, expected in expected_calls.items():
            call = calls_by_id[call_id]
            if (call.trial_id, call.run_id, call.attempt) != expected:
                raise RuntimeError("release call identity does not match its derived call id")

        ledger_by_call: dict[str, list[LedgerEvent]] = {}
        for event in ledger_events:
            ledger_by_call.setdefault(event.call_id, []).append(event)
        if set(ledger_by_call) != set(expected_calls):
            raise RuntimeError("release ledger does not equal the derived provider-attempt set")
        for call_id, events in ledger_by_call.items():
            if len(events) != 2 or [event.event_type for event in events] != [
                "reserved",
                "settled",
            ]:
                raise RuntimeError("release call lacks one ordered reservation and settlement")
            call = calls_by_id[call_id]
            reserved, settled = events
            run = admitted[call.run_id]
            expected_identity = (call.trial_id, call.run_id, run.cohort)
            if any(
                (event.trial_id, event.run_id, event.cohort) != expected_identity
                for event in events
            ):
                raise RuntimeError("release ledger identity does not match its call")
            if reserved.amount_usd != call.reserved_cost_usd:
                raise RuntimeError("release reservation amount does not match its call")
            if settled.amount_usd != call.observed_cost_usd:
                raise RuntimeError("release settlement amount does not match its call")

        caps = self.manifest.get("spend_caps_usd")
        if not isinstance(caps, dict) or set(caps) != {"global", "cohorts"}:
            raise RuntimeError("release lacks authoritative global and cohort spend caps")
        global_cap = caps["global"]
        cohort_caps = caps["cohorts"]
        if (
            not isinstance(global_cap, (int, float))
            or not math.isfinite(global_cap)
            or global_cap <= 0
        ):
            raise RuntimeError("release global spend cap is invalid")
        observed = sum(call.observed_cost_usd for call in calls)
        if observed > global_cap + 1e-12:
            raise RuntimeError("release observed spend exceeds its global cap")
        for cohort in {run.cohort for run in admitted.values()}:
            cap = cohort_caps.get(cohort) if isinstance(cohort_caps, dict) else None
            if (
                not isinstance(cap, (int, float))
                or not math.isfinite(cap)
                or cap < 0
            ):
                raise RuntimeError(f"release lacks a valid cap for cohort {cohort!r}")
            cohort_observed = sum(
                call.observed_cost_usd
                for call in calls
                if admitted[call.run_id].cohort == cohort
            )
            if cohort_observed > cap + 1e-12:
                raise RuntimeError(f"release observed spend exceeds cohort {cohort!r} cap")

    def runs(self) -> list[RunManifest]:
        """Return the typed run manifests admitted to this bundle."""
        return [
            RunManifest.from_dict(json.loads(line))
            for line in (self.root / "runs.jsonl").read_text().splitlines()
            if line.strip()
        ]

    def _verified_metric_rows(
        self,
        *,
        suite: LoadedSuite,
        runs: list[RunManifest],
        trials: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        expected_profiles, expected_effects = derive_metric_tables(
            trials,
            runs=runs,
            suite=suite,
        )
        published_profiles = pq.read_table(self.root / "profiles.parquet")
        published_effects = pq.read_table(self.root / "effects.parquet")
        if not published_profiles.equals(expected_profiles):
            raise RuntimeError("published profiles do not recompute from admitted trials")
        if not published_effects.equals(expected_effects):
            raise RuntimeError("published effects do not recompute from admitted trials")
        return published_profiles.to_pylist(), published_effects.to_pylist()

    def verified_metric_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return derived rows only after recomputing them from admitted evidence."""
        suite = load_suite(version=self.manifest["suite_version"], path=self.root / "suite.json")
        return self._verified_metric_rows(
            suite=suite,
            runs=self.runs(),
            trials=_read_rows(self.root / "trials.parquet"),
        )

    def _artifact_paths(self) -> list[Path]:
        return sorted(
            path for path in self.root.rglob("*") if path.is_file() and path.name != "release.json"
        )

    def validate(self) -> None:
        if set(path.name for path in self.root.iterdir() if path.is_file()) != _ROOT_FILES:
            raise RuntimeError("release root files do not match the bundle schema")
        if json.loads((self.root / "human-trial.schema.json").read_text()) != (
            HUMAN_TRIAL_EXPORT_SCHEMA
        ):
            raise RuntimeError("human trial schema does not match the authoritative schema")
        suite = load_suite(version=self.manifest["suite_version"], path=self.root / "suite.json")
        protocols = load_protocol_registry(self.root / "protocols.json")
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
                run_trials = [row for row in trial_rows if row["run_id"] == run.run_id]
                self._validate_trial_provenance(
                    run,
                    suite,
                    protocols[run.protocol_id],
                    run_trials,
                )
                self._validate_run_accounting(
                    run,
                    run_trials,
                    [CallRecord(**row) for row in call_rows if row["run_id"] == run.run_id],
                    [event for event in ledger_rows if event.run_id == run.run_id],
                )
        self._validate_release_accounting(runs, trial_rows, call_rows, ledger_rows)
        self._verified_metric_rows(suite=suite, runs=runs, trials=trial_rows)
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
            if len(trials) != contract["total_attempts"]:
                raise RuntimeError("sealed sample does not contain exactly twenty trials")
            if len(calls) != contract["total_attempts"]:
                raise RuntimeError("sealed sample does not contain exactly twenty attempts")
            approval = self.manifest.get("paid_run_approval") or {}
            spend_caps = self.manifest.get("spend_caps_usd") or {}
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
                or spend_caps.get("global") != 30.0
                or spend_caps.get("cohorts", {}).get("sample") != 30.0
            ):
                raise RuntimeError("sealed sample lacks exact paid approval or spend caps")
            profiles = _read_rows(self.root / "profiles.parquet")
            if (
                len(profiles) != len(expected_protocols)
                or {row["protocol_id"] for row in profiles} != expected_protocols
                or any(row["coverage"] != 1.0 for row in profiles)
            ):
                raise RuntimeError("sealed sample requires complete derived profiles")
            site = self.root / "site"
            required_site_files = {
                "index.html",
                "forms.html",
                "atlas.html",
                "runs.html",
                "human.html",
                "downloads.html",
                "CNAME",
                "downloads/human-trial.schema.json",
            }
            missing = [
                relative for relative in required_site_files if not (site / relative).is_file()
            ]
            if missing:
                raise RuntimeError(f"sealed sample site is missing required artifacts: {missing}")
            required_markers = {
                "runs.html": (
                    "containment tree and frozen form",
                    "actual rendered stimulus",
                    "reading rule, protocol, and exact prompt",
                    "target and recorded response",
                    "parse and scorer identity",
                    "profile contribution",
                    "dialect matrix",
                    "family matrix",
                    "reasoning contrasts",
                    "not run",
                    "exact endpoint + routing provenance",
                    "input/output/reasoning tokens",
                ),
                "human.html": (
                    "HumanTrialRecord",
                    "familiarity_band",
                    "elapsed_ms",
                    "human-trial.schema.json",
                ),
                "forms.html": ("system prompt", "possible confounds", "frozen form sets"),
                "downloads.html": ("sha256", "caveats", "human-trial.schema.json"),
            }
            for relative, markers in required_markers.items():
                page = (site / relative).read_text()
                absent = [marker for marker in markers if marker not in page]
                if absent:
                    raise RuntimeError(
                        f"sealed sample site {relative} lacks required evidence: {absent}"
                    )

    def seal(
        self,
        *,
        repository_root: Path,
        publication_environment: Mapping[str, str] | None = None,
    ) -> None:
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
        scan_publication(self.root, environment=publication_environment)
        self.manifest["status"] = "sealed"
        self.manifest["sealed_at"] = _utc_now()
        self.manifest["files"] = {
            path.relative_to(self.root).as_posix(): _file_digest(path)
            for path in self._artifact_paths()
        }
        self._write_manifest()
        self.validate()
