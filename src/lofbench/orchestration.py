"""Provider-neutral, resumable execution of one immutable benchmark run."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from inspect_ai import Task
from inspect_ai.dataset import Sample

from lofbench.accounting import DEFAULT_GLOBAL_CAP_USD, SpendLedger
from lofbench.protocols import get_protocol
from lofbench.publication import sanitize_public_mapping
from lofbench.records import AttemptEvidence, CallRecord, RunManifest, TrialRecord
from lofbench.run_models import ExecutionRequest, TrialExecutor, call_id_for, trial_id_for
from lofbench.state_io import append_jsonl_fsynced, read_jsonl, write_json_atomic

MAX_TRANSPORT_ATTEMPTS = 3


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunOrchestrator:
    def __init__(
        self,
        state_dir: Path,
        executor: TrialExecutor,
        *,
        ledger: SpendLedger,
    ) -> None:
        self.state_dir = state_dir
        self.executor = executor
        self.ledger = ledger

    @staticmethod
    def reservation(run: RunManifest, sample: Sample) -> float:
        pricing = run.pricing
        try:
            prompt_price = float(pricing["prompt"])
            completion_price = float(pricing["completion"])
            image_price = float(pricing["image"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("unknown endpoint pricing") from exc
        if any(
            not math.isfinite(value) or value < 0
            for value in (prompt_price, completion_price, image_price)
        ):
            raise RuntimeError("unknown endpoint pricing")
        estimated_input = max(1, len(str(sample.input)) // 2)
        image_allowance = 2000 if sample.metadata["modality"] != "text" else 0
        estimate = (
            estimated_input * prompt_price
            + run.generation["max_tokens"] * completion_price
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
        if run.run_id != run.authoritative_run_id():
            raise RuntimeError("run_id does not match the authoritative execution identity")
        is_paid = run.billing_channel != "subscription_unmetered"
        if is_paid and (not approve_paid_run or max_spend_usd != DEFAULT_GLOBAL_CAP_USD):
            return run
        if not 1 <= run.max_transport_attempts <= MAX_TRANSPORT_ATTEMPTS:
            raise RuntimeError("run declares an invalid transport-attempt limit")
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
        existing = {row["trial_id"]: row for row in read_jsonl(self.state_dir / "trials.jsonl")}
        prior_calls = read_jsonl(self.state_dir / "calls.jsonl")
        if any(row["status"] == "accounting_unknown" for row in prior_calls):
            raise RuntimeError("run has an unresolved provider-accounting failure")
        orphaned_complete = {
            row["trial_id"]
            for row in prior_calls
            if row["status"] == "complete" and row["trial_id"] not in existing
        }
        if orphaned_complete:
            raise RuntimeError("run has a completed call rejected by an identity gate")
        protocol = get_protocol(run.protocol_id)
        for trial_id in run.expected_trial_ids:
            if trial_id in existing:
                continue
            sample = samples_by_trial[trial_id]
            completed = False
            attempts_used = sum(1 for row in prior_calls if row["trial_id"] == trial_id)
            for attempt in range(attempts_used + 1, run.max_transport_attempts + 1):
                call_id = call_id_for(trial_id, attempt)
                reservation = self.reservation(run, sample) if is_paid else 0.0
                self.ledger.reserve(
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    cohort=run.cohort,
                    amount=reservation,
                )
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
                if (
                    result.observed_cost_usd is None
                    or result.input_tokens is None
                    or result.output_tokens is None
                    or result.reasoning_tokens is None
                ):
                    call = CallRecord(
                        call_id=call_id,
                        trial_id=trial_id,
                        run_id=run.run_id,
                        attempt=attempt,
                        started_at=started_at,
                        finished_at=_now(),
                        status="accounting_unknown",
                        reserved_cost_usd=reservation,
                        observed_cost_usd=0.0,
                        resolved_model_id=result.resolved_model_id,
                        provider=run.provider,
                        endpoint=result.endpoint,
                        latency_ms=result.latency_ms,
                        input_tokens=0,
                        output_tokens=0,
                        reasoning_tokens=0,
                        provider_request_id=result.provider_request_id,
                        error_type=result.error_type or "missing_provider_accounting",
                        response_sha256=sha256(result.response_text.encode()).hexdigest(),
                    )
                    append_jsonl_fsynced(self.state_dir / "calls.jsonl", call.to_dict())
                    raise RuntimeError("provider response omitted price or usage")
                if (
                    not isinstance(result.input_tokens, int)
                    or isinstance(result.input_tokens, bool)
                    or result.input_tokens < 0
                    or not isinstance(result.output_tokens, int)
                    or isinstance(result.output_tokens, bool)
                    or result.output_tokens < 0
                    or not isinstance(result.reasoning_tokens, int)
                    or isinstance(result.reasoning_tokens, bool)
                    or result.reasoning_tokens < 0
                    or not math.isfinite(result.observed_cost_usd)
                    or result.observed_cost_usd < 0
                    or not math.isfinite(result.latency_ms)
                    or result.latency_ms < 0
                ):
                    raise RuntimeError("provider response contains invalid usage, cost, or latency")
                if not isinstance(result.transcript, dict):
                    raise RuntimeError("provider response omitted complete attempt evidence")
                self.ledger.settle(
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    cohort=run.cohort,
                    amount=result.observed_cost_usd,
                )
                finished_at = _now()
                status = "transport_error" if result.transport_error else "complete"
                evidence = AttemptEvidence.capture(
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    attempt=attempt,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    response_text=result.response_text,
                    provider_request_id=result.provider_request_id,
                    resolved_model_id=result.resolved_model_id,
                    provider=run.provider,
                    endpoint=result.endpoint,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    reasoning_tokens=result.reasoning_tokens,
                    observed_cost_usd=result.observed_cost_usd,
                    latency_ms=result.latency_ms,
                    error_type=result.error_type,
                    raw_transcript=sanitize_public_mapping(result.transcript),
                )
                call = CallRecord(
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=finished_at,
                    status=status,
                    reserved_cost_usd=reservation,
                    observed_cost_usd=result.observed_cost_usd,
                    resolved_model_id=result.resolved_model_id,
                    provider=run.provider,
                    endpoint=result.endpoint,
                    latency_ms=result.latency_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    reasoning_tokens=result.reasoning_tokens,
                    provider_request_id=result.provider_request_id,
                    error_type=result.error_type,
                    response_sha256=sha256(result.response_text.encode()).hexdigest(),
                    evidence_sha256=evidence.evidence_sha256,
                )
                append_jsonl_fsynced(self.state_dir / "calls.jsonl", call.to_dict())
                append_jsonl_fsynced(
                    self.state_dir / "transcripts.jsonl",
                    evidence.to_dict(),
                )
                if result.transport_error:
                    continue
                if result.resolved_model_id != run.resolved_model_id:
                    raise RuntimeError("provider resolved a different model id")
                if result.endpoint != run.endpoint:
                    raise RuntimeError("provider endpoint drift or fallback detected")
                parse_status, prediction, correct = protocol.parse_answer(
                    result.response_text,
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
                    completion_evidence_sha256=evidence.evidence_sha256,
                )
                append_jsonl_fsynced(self.state_dir / "trials.jsonl", trial.to_dict())
                completed = True
                break
            if not completed:
                continue
        all_trials = read_jsonl(self.state_dir / "trials.jsonl")
        all_calls = read_jsonl(self.state_dir / "calls.jsonl")
        complete = {row["trial_id"] for row in all_trials} == set(run.expected_trial_ids)
        updated = replace(
            run,
            status="complete" if complete else "probed",
            attempts=len(all_calls),
            token_usage={
                key: sum(row[key] for row in all_calls)
                for key in ("input_tokens", "output_tokens", "reasoning_tokens")
            },
            latency_ms=sum(row["latency_ms"] for row in all_calls),
            cost_usd=sum(row["observed_cost_usd"] for row in all_calls),
        )
        write_json_atomic(self.state_dir / "run.json", updated.to_dict())
        return updated
