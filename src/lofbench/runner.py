"""Guarded, resumable orchestration for public benchmark runs."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset, Sample

from lofbench.records import CallRecord, RunManifest, TrialRecord, deterministic_id
from lofbench.release_bundle import ReleaseBundle
from lofbench.scorers import parse_protocol_answer
from lofbench.tasks.single import single_lof_task

GLOBAL_OPENROUTER_CAP_USD = 30.0
COHORT_CAPS_USD = {"broad": 18.0, "frontier": 7.5, "reserve": 4.5}
MAX_TRANSPORT_ATTEMPTS = 3


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _append_fsynced(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_env_file(path: Path) -> dict[str, str]:
    """Load a strict key-value env file without logging its contents."""
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(f"env file must be mode 0600 or stricter, found {mode:o}")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError("env file contains a non-assignment line")
        key, value = stripped.split("=", 1)
        if not key or not key.replace("_", "").isalnum():
            raise ValueError("env file contains an invalid variable name")
        values[key] = value
    return values


@dataclass(frozen=True)
class EndpointSelection:
    model_id: str
    endpoint_tag: str
    provider_name: str
    pricing: dict[str, str]
    supported_parameters: tuple[str, ...]
    architecture: dict[str, Any]
    retrieved_at: str
    catalog_row: dict[str, Any]

    @property
    def routing_policy(self) -> dict[str, Any]:
        return {
            "order": [self.endpoint_tag],
            "allow_fallbacks": False,
            "data_collection": "deny",
            "zdr": True,
        }


def fetch_openrouter_endpoint(model_id: str) -> EndpointSelection:
    """Pin the cheapest active exact endpoint with structured-output support."""
    quoted = urllib.parse.quote(model_id, safe="/")
    url = f"https://openrouter.ai/api/v1/models/{quoted}/endpoints"
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed HTTPS host
        payload = json.load(response)
    model = payload.get("data", {})
    if model.get("id") != model_id:
        raise RuntimeError(f"openrouter did not return exact requested model {model_id!r}")
    candidates = []
    for endpoint in model.get("endpoints", []):
        supported = set(endpoint.get("supported_parameters", []))
        pricing = endpoint.get("pricing", {})
        if endpoint.get("status") != 0:
            continue
        if not ({"response_format", "structured_outputs"} & supported):
            continue
        if pricing.get("prompt") is None or pricing.get("completion") is None:
            continue
        try:
            price = float(pricing["prompt"]) + float(pricing["completion"])
        except (TypeError, ValueError):
            continue
        candidates.append((price, endpoint["tag"], endpoint))
    if not candidates:
        raise RuntimeError(f"no active structured-output endpoint for {model_id}")
    _price, _tag, selected = min(candidates, key=lambda item: (item[0], item[1]))
    return EndpointSelection(
        model_id=model_id,
        endpoint_tag=selected["tag"],
        provider_name=selected["provider_name"],
        pricing=dict(selected["pricing"]),
        supported_parameters=tuple(selected.get("supported_parameters", [])),
        architecture=dict(model.get("architecture", {})),
        retrieved_at=_now(),
        catalog_row={"model": model, "selected_endpoint": selected},
    )


def trial_id_for(run_id: str, abstract_form_id: str, dialect_id: str) -> str:
    return deterministic_id(
        "trial",
        {
            "run_id": run_id,
            "abstract_form_id": abstract_form_id,
            "dialect_id": dialect_id,
        },
    )


@dataclass(frozen=True)
class ExecutionRequest:
    run: RunManifest
    task: Task
    sample: Sample
    attempt: int
    log_dir: Path


@dataclass(frozen=True)
class ExecutionResult:
    response_text: str = ""
    resolved_model_id: str = ""
    endpoint: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    observed_cost_usd: float | None = None
    latency_ms: float = 0.0
    provider_request_id: str = ""
    transcript: dict[str, Any] | None = None
    transport_error: bool = False
    error_type: str = ""


class TrialExecutor(ABC):
    @abstractmethod
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError


class InMemoryExecutor(TrialExecutor):
    """Scripted adapter for budget, retry, and resume tests."""

    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = list(results)
        self.calls: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(request)
        if not self.results:
            raise RuntimeError("in-memory executor has no scripted result")
        return self.results.pop(0)


class InspectExecutor(TrialExecutor):
    """One-call Inspect/OpenRouter adapter with the frozen task configuration."""

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        from inspect_ai import eval as inspect_eval

        task = Task(
            dataset=MemoryDataset(samples=[request.sample], name="one_public_trial"),
            solver=request.task.solver,
            scorer=request.task.scorer,
            config=request.task.config,
            metadata=request.task.metadata,
        )
        started = time.monotonic()
        try:
            logs = inspect_eval(
                task,
                model=f"openrouter/{request.run.requested_model_id}",
                model_args={"provider": request.run.routing_policy},
                display="none",
                log_dir=str(request.log_dir),
                log_samples=True,
                retry_on_error=0,
            )
        except Exception as exc:  # provider/network errors are recorded and retried
            return ExecutionResult(
                transport_error=True,
                error_type=type(exc).__name__,
                latency_ms=(time.monotonic() - started) * 1000,
                observed_cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
            )
        if len(logs) != 1 or logs[0].status != "success" or not logs[0].samples:
            error_type = logs[0].status if logs else "missing_log"
            return ExecutionResult(
                transport_error=True,
                error_type=error_type,
                latency_ms=(time.monotonic() - started) * 1000,
                observed_cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
            )
        sample = logs[0].samples[0]
        model_events = [event for event in sample.events if event.event == "model"]
        if not model_events:
            return ExecutionResult(transport_error=True, error_type="missing_model_event")
        event = model_events[-1]
        raw = event.call.response if event.call else {}
        usage = event.output.usage
        raw_usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        cost = raw_usage.get("cost")
        provider = raw.get("provider", "") if isinstance(raw, dict) else ""
        request_id = raw.get("id", "") if isinstance(raw, dict) else ""
        resolved = event.output.model.removeprefix("openrouter/")
        return ExecutionResult(
            response_text=event.output.completion,
            resolved_model_id=resolved,
            endpoint=provider,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            reasoning_tokens=(usage.reasoning_tokens or 0) if usage else None,
            observed_cost_usd=float(cost) if cost is not None else None,
            latency_ms=sample.total_time * 1000,
            provider_request_id=request_id,
            transcript=sample.model_dump(mode="json", exclude={"attachments"}),
        )


class _CliExecutor(TrialExecutor):
    command: str

    def _argv(self, request: ExecutionRequest, schema_path: Path, output_path: Path) -> list[str]:
        raise NotImplementedError

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not isinstance(request.sample.input, str):
            return ExecutionResult(error_type="agent_cli_text_only", transport_error=True)
        with tempfile.TemporaryDirectory(prefix="lofbench-agent-") as directory:
            root = Path(directory)
            schema_path = root / "schema.json"
            output_path = root / "output.json"
            protocol = request.task.config.response_schema
            schema_path.write_text(json.dumps(protocol.json_schema if protocol else {}))
            started = time.monotonic()
            completed = subprocess.run(
                self._argv(request, schema_path, output_path),
                cwd=root,
                text=True,
                capture_output=True,
                timeout=300,
                check=False,
            )
            if completed.returncode != 0 or not output_path.exists():
                return ExecutionResult(
                    transport_error=True,
                    error_type=f"{self.command}_exit_{completed.returncode}",
                    observed_cost_usd=0.0,
                    input_tokens=0,
                    output_tokens=0,
                    reasoning_tokens=0,
                )
            return ExecutionResult(
                response_text=output_path.read_text(),
                resolved_model_id=request.run.resolved_model_id,
                endpoint=self.command,
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                observed_cost_usd=0.0,
                latency_ms=(time.monotonic() - started) * 1000,
                transcript={"surface": self.command, "stderr": completed.stderr},
            )


class CodexCliExecutor(_CliExecutor):
    command = "codex"

    def _argv(self, request: ExecutionRequest, schema_path: Path, output_path: Path) -> list[str]:
        return [
            "codex",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--model",
            request.run.requested_model_id,
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            request.sample.input,
        ]


class ClaudeCliExecutor(_CliExecutor):
    command = "claude"

    def _argv(self, request: ExecutionRequest, schema_path: Path, output_path: Path) -> list[str]:
        return [
            "claude",
            "-p",
            request.sample.input,
            "--model",
            request.run.requested_model_id,
            "--tools",
            "",
            "--json-schema",
            schema_path.read_text(),
            "--output-format",
            "json",
        ]


class SpendLedger:
    def __init__(self, path: Path, *, global_cap: float, cohort_cap: float) -> None:
        self.path = path
        self.global_cap = global_cap
        self.cohort_cap = cohort_cap

    def totals(self) -> tuple[float, float]:
        reserved: dict[str, float] = {}
        observed = 0.0
        for event in _read_jsonl(self.path):
            if event["type"] == "reserved":
                reserved[event["call_id"]] = float(event["amount_usd"])
            elif event["type"] == "settled":
                reserved.pop(event["call_id"], None)
                observed += float(event["amount_usd"])
        return observed, sum(reserved.values())

    def reserve(self, call_id: str, amount: float) -> None:
        observed, outstanding = self.totals()
        limit = min(self.global_cap, self.cohort_cap)
        if observed + outstanding + amount > limit + 1e-12:
            raise RuntimeError(f"spend reservation would exceed ${limit:.2f} cap")
        _append_fsynced(
            self.path,
            {"type": "reserved", "call_id": call_id, "amount_usd": amount, "at": _now()},
        )

    def settle(self, call_id: str, amount: float) -> None:
        if amount < 0:
            raise RuntimeError("observed cost cannot be negative")
        _append_fsynced(
            self.path,
            {"type": "settled", "call_id": call_id, "amount_usd": amount, "at": _now()},
        )
        observed, _outstanding = self.totals()
        if observed > min(self.global_cap, self.cohort_cap) + 1e-12:
            raise RuntimeError("observed spend exceeded the declared cap")


class RunOrchestrator:
    def __init__(
        self,
        state_dir: Path,
        executor: TrialExecutor,
        *,
        global_cap_usd: float = GLOBAL_OPENROUTER_CAP_USD,
        cohort_cap_usd: float = GLOBAL_OPENROUTER_CAP_USD,
    ) -> None:
        self.state_dir = state_dir
        self.executor = executor
        self.ledger = SpendLedger(
            state_dir / "spend-ledger.jsonl",
            global_cap=global_cap_usd,
            cohort_cap=cohort_cap_usd,
        )

    def _reservation(self, run: RunManifest, sample: Sample) -> float:
        pricing = run.catalog_row.get("selected_endpoint", {}).get("pricing", {})
        try:
            prompt_price = float(pricing["prompt"])
            completion_price = float(pricing["completion"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("unknown endpoint pricing") from exc
        estimated_input = max(1, len(str(sample.input)) // 2)
        image_allowance = 2000 if sample.metadata["modality"] == "image" else 0
        image_price = float(pricing.get("image", prompt_price))
        estimate = (
            estimated_input * prompt_price
            + 512 * completion_price
            + image_allowance * image_price
        )
        return max(0.01, estimate * 1.25)

    def execute(
        self,
        run: RunManifest,
        task: Task,
        *,
        approve_paid_run: bool = False,
        max_spend_usd: float | None = None,
    ) -> RunManifest:
        if not approve_paid_run or max_spend_usd != GLOBAL_OPENROUTER_CAP_USD:
            return run
        self.state_dir.mkdir(parents=True, exist_ok=True)
        samples_by_trial = {
            trial_id_for(
                run.run_id,
                sample.metadata["abstract_form_id"],
                sample.metadata["dialect_id"],
            ): sample
            for sample in task.dataset.samples
        }
        if set(samples_by_trial) != set(run.expected_trial_ids):
            raise RuntimeError("task samples do not match the run's expected trial ids")
        existing = {
            row["trial_id"]: row for row in _read_jsonl(self.state_dir / "trials.jsonl")
        }
        prior_calls = _read_jsonl(self.state_dir / "calls.jsonl")
        total_usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
        total_cost = 0.0
        total_latency = 0.0
        attempt_total = 0
        for trial_id in run.expected_trial_ids:
            if trial_id in existing:
                continue
            sample = samples_by_trial[trial_id]
            completed = False
            attempts_used = sum(1 for row in prior_calls if row["trial_id"] == trial_id)
            for attempt in range(attempts_used + 1, MAX_TRANSPORT_ATTEMPTS + 1):
                call_id = deterministic_id("call", {"trial_id": trial_id, "attempt": attempt})
                reservation = self._reservation(run, sample)
                self.ledger.reserve(call_id, reservation)
                started_at = _now()
                result = self.executor.execute(
                    ExecutionRequest(
                        run=run,
                        task=task,
                        sample=sample,
                        attempt=attempt,
                        log_dir=self.state_dir / "inspect-logs",
                    )
                )
                attempt_total += 1
                if (
                    result.observed_cost_usd is None
                    or result.input_tokens is None
                    or result.output_tokens is None
                    or result.reasoning_tokens is None
                ):
                    raise RuntimeError("provider response omitted price or usage")
                self.ledger.settle(call_id, result.observed_cost_usd)
                call = CallRecord(
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=_now(),
                    status="transport_error" if result.transport_error else "complete",
                    reserved_cost_usd=reservation,
                    observed_cost_usd=result.observed_cost_usd,
                    provider_request_id=result.provider_request_id,
                    error_type=result.error_type,
                )
                _append_fsynced(self.state_dir / "calls.jsonl", call.to_dict())
                if result.transcript is not None:
                    _append_fsynced(
                        self.state_dir / "transcripts.jsonl",
                        {"trial_id": trial_id, "attempt": attempt, "data": result.transcript},
                    )
                if result.transport_error:
                    continue
                if result.resolved_model_id != run.resolved_model_id:
                    raise RuntimeError("provider resolved a different model id")
                allowed_endpoints = {
                    run.endpoint,
                    run.catalog_row.get("selected_endpoint", {}).get("provider_name"),
                }
                if result.endpoint not in allowed_endpoints:
                    raise RuntimeError("provider endpoint drift or fallback detected")
                parse_status, prediction, correct = parse_protocol_answer(
                    result.response_text,
                    protocol_id=run.protocol_id,
                    expected_normal_value=sample.metadata["normal_value"],
                    expected_tree=sample.metadata["abstract_form"],
                )
                trial = TrialRecord(
                    trial_id=trial_id,
                    run_id=run.run_id,
                    suite_version=run.suite_version,
                    abstract_form_id=sample.metadata["abstract_form_id"],
                    dialect_id=sample.metadata["dialect_id"],
                    protocol_id=run.protocol_id,
                    execution_surface=run.execution_surface,
                    requested_model_id=run.requested_model_id,
                    resolved_model_id=result.resolved_model_id,
                    provider=run.provider,
                    endpoint=result.endpoint,
                    prompt_hash=sample.metadata["prompt_hash"],
                    symbolic_payload_hash=sample.metadata["symbolic_payload_hash"],
                    model_payload_sha256=sample.metadata["model_payload_sha256"],
                    parse_status=parse_status,
                    attempt_count=attempt,
                    latency_ms=result.latency_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    reasoning_tokens=result.reasoning_tokens,
                    observed_cost_usd=result.observed_cost_usd,
                    prediction=prediction,
                    normal_value=sample.metadata["normal_value"],
                    correct=correct,
                    response_text=result.response_text,
                    error_type=result.error_type,
                )
                _append_fsynced(self.state_dir / "trials.jsonl", trial.to_dict())
                total_usage["input_tokens"] += result.input_tokens
                total_usage["output_tokens"] += result.output_tokens
                total_usage["reasoning_tokens"] += result.reasoning_tokens
                total_cost += result.observed_cost_usd
                total_latency += result.latency_ms
                completed = True
                break
            if not completed:
                continue
        all_trials = _read_jsonl(self.state_dir / "trials.jsonl")
        complete = {row["trial_id"] for row in all_trials} == set(run.expected_trial_ids)
        updated = replace(
            run,
            status="complete" if complete else "probed",
            attempts=run.attempts + attempt_total,
            token_usage={
                key: run.token_usage.get(key, 0) + value for key, value in total_usage.items()
            },
            latency_ms=run.latency_ms + total_latency,
            cost_usd=run.cost_usd + total_cost,
        )
        _write_json_atomic(self.state_dir / "run.json", updated.to_dict())
        return updated


def plan_run(
    task: Task,
    *,
    suite_version: str,
    form_set: str,
    dialect_set: str,
    protocol_id: str,
    model_id: str,
    selection: EndpointSelection,
    reasoning: dict[str, Any] | None = None,
) -> RunManifest:
    base = RunManifest.plan(
        suite_version=suite_version,
        form_set=form_set,
        dialect_set=dialect_set,
        protocol_id=protocol_id,
        requested_model_id=model_id,
        resolved_model_id=model_id,
        execution_surface="direct_api",
        provider="openrouter",
        endpoint=selection.endpoint_tag,
        routing_policy=selection.routing_policy,
        privacy_policy={"data_collection": "deny", "zdr": True},
        sdk_version="inspect-ai",
        reasoning=reasoning or {"effort": "default"},
        generation={"temperature": 0, "max_tokens": 512},
        billing_channel="openrouter-limited-key",
        expected_trial_ids=(),
        catalog_retrieved_at=selection.retrieved_at,
        catalog_row=selection.catalog_row,
    )
    trial_ids = tuple(
        trial_id_for(
            base.run_id,
            sample.metadata["abstract_form_id"],
            sample.metadata["dialect_id"],
        )
        for sample in task.dataset.samples
    )
    return replace(base, expected_trial_ids=trial_ids)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m lofbench.runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--release", type=Path, required=True)
    plan.add_argument("--state-root", type=Path, required=True)
    plan.add_argument("--release-id", required=True)
    plan.add_argument("--repository-url", required=True)
    plan.add_argument("--suite", type=Path, default=Path("suites/v1.json"))
    plan.add_argument("--model", required=True)
    plan.add_argument("--form-set", default="probe")
    plan.add_argument("--dialect", required=True)
    plan.add_argument("--protocol", action="append", required=True)
    for name in ("probe", "run", "resume"):
        command = subparsers.add_parser(name)
        command.add_argument("--release", type=Path, required=True)
        command.add_argument("--state-dir", type=Path, required=True)
        command.add_argument("--env-file", type=Path, required=True)
        command.add_argument("--approve-paid-run", action="store_true")
        command.add_argument("--max-spend-usd", type=float)
    status = subparsers.add_parser("status")
    status.add_argument("--state-dir", type=Path, required=True)
    admit = subparsers.add_parser("admit")
    admit.add_argument("--release", type=Path, required=True)
    admit.add_argument("--state-dir", type=Path, required=True)
    seal = subparsers.add_parser("seal")
    seal.add_argument("--release", type=Path, required=True)
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        if args.release.exists():
            raise FileExistsError("plan requires a new release directory")
        selection = fetch_openrouter_endpoint(args.model)
        tasks_and_runs: list[tuple[Task, RunManifest]] = []
        for protocol_id in args.protocol:
            task = single_lof_task(
                suite=str(args.suite),
                form_set=args.form_set,
                dialect=args.dialect,
                protocol=protocol_id,
            )
            run = plan_run(
                task,
                suite_version="v1",
                form_set=args.form_set,
                dialect_set=args.dialect,
                protocol_id=protocol_id,
                model_id=args.model,
                selection=selection,
            )
            tasks_and_runs.append((task, run))
        ReleaseBundle.create_working(
            args.release,
            release_id=args.release_id,
            repository_url=args.repository_url,
            repository_root=Path.cwd(),
            suite_path=args.suite,
            expected_run_ids=[run.run_id for _task, run in tasks_and_runs],
            paid_run_approval={
                "approved_by": "human:valerie",
                "approved_at": "2026-08-08",
                "openrouter_cap_usd": GLOBAL_OPENROUTER_CAP_USD,
                "scope": "v1.0.0-sample.1 then stop and report",
            },
        )
        cost_sheet = {
            "catalog_retrieved_at": selection.retrieved_at,
            "model_id": selection.model_id,
            "endpoint": selection.endpoint_tag,
            "provider": selection.provider_name,
            "pricing": selection.pricing,
            "runs": [],
        }
        for task, run in tasks_and_runs:
            state_dir = args.state_root / run.run_id
            _write_json_atomic(state_dir / "run.json", run.to_dict())
            estimator = RunOrchestrator(state_dir, InMemoryExecutor([]))
            projected = sum(estimator._reservation(run, sample) for sample in task.dataset.samples)
            cost_sheet["runs"].append(
                {
                    "run_id": run.run_id,
                    "protocol_id": run.protocol_id,
                    "trials": len(run.expected_trial_ids),
                    "conservative_reservation_usd": projected,
                }
            )
        _write_json_atomic(args.state_root / "cost-sheet.json", cost_sheet)
        print(
            json.dumps(
                {
                    "release_id": args.release_id,
                    "run_ids": [run.run_id for _task, run in tasks_and_runs],
                    "paid_calls": sum(len(run.expected_trial_ids) for _task, run in tasks_and_runs),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "status":
        run = json.loads((args.state_dir / "run.json").read_text())
        observed, reserved = SpendLedger(
            args.state_dir / "spend-ledger.jsonl",
            global_cap=GLOBAL_OPENROUTER_CAP_USD,
            cohort_cap=GLOBAL_OPENROUTER_CAP_USD,
        ).totals()
        print(
            json.dumps(
                {
                    "run_id": run["run_id"],
                    "status": run["status"],
                    "observed_spend_usd": observed,
                    "reserved_spend_usd": reserved,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "admit":
        bundle = ReleaseBundle.open(args.release)
        run = RunManifest.from_dict(json.loads((args.state_dir / "run.json").read_text()))
        trials = [TrialRecord(**row) for row in _read_jsonl(args.state_dir / "trials.jsonl")]
        calls = [CallRecord(**row) for row in _read_jsonl(args.state_dir / "calls.jsonl")]
        bundle.admit_run(
            run,
            trials,
            calls=calls,
            transcripts=_read_jsonl(args.state_dir / "transcripts.jsonl"),
        )
        return 0
    if args.command == "seal":
        ReleaseBundle.open(args.release).seal(repository_root=Path.cwd())
        return 0

    values = load_env_file(args.env_file)
    if "OPENROUTER_API_KEY" not in values:
        raise RuntimeError("env file does not define OPENROUTER_API_KEY")
    os.environ["OPENROUTER_API_KEY"] = values["OPENROUTER_API_KEY"]
    run = RunManifest.from_dict(json.loads((args.state_dir / "run.json").read_text()))
    task = single_lof_task(
        suite=str(args.release / "suite.json"),
        form_set=run.form_set,
        dialect=run.dialect_set,
        protocol=run.protocol_id,
    )
    updated = RunOrchestrator(args.state_dir, InspectExecutor()).execute(
        run,
        task,
        approve_paid_run=args.approve_paid_run,
        max_spend_usd=args.max_spend_usd,
    )
    print(json.dumps({"run_id": updated.run_id, "status": updated.status}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
