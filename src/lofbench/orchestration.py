"""Provider-neutral, resumable execution of one immutable benchmark run."""

from __future__ import annotations

import math
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from inspect_ai import Task
from inspect_ai.dataset import Sample

from lofbench.accounting import DEFAULT_GLOBAL_CAP_USD, SpendLedger
from lofbench.protocols import ProtocolSpec, get_protocol
from lofbench.provider_evidence import (
    AccountingRecoverer,
    AttemptProjection,
    EvidenceProjector,
    ProviderEvidenceEnvelope,
    fail_closed_projection,
)
from lofbench.records import AttemptEvidence, CallRecord, RunManifest, TrialRecord
from lofbench.run_models import ExecutionRequest, TrialExecutor, call_id_for, trial_id_for
from lofbench.state_io import append_jsonl_fsynced, read_jsonl, write_json_atomic

MAX_TRANSPORT_ATTEMPTS = 3


def _effective_calls(rows: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for row in rows:
        by_id[row["call_id"]] = row
    return list(by_id.values())


def _latest_evidence(rows: list[dict]) -> dict[str, AttemptEvidence]:
    latest: dict[str, AttemptEvidence] = {}
    for row in rows:
        record = AttemptEvidence.from_dict(row)
        prior = latest.get(record.call_id)
        if prior is None or record.revision > prior.revision:
            latest[record.call_id] = record
    return latest


class RunOrchestrator:
    def __init__(
        self,
        state_dir: Path,
        executor: TrialExecutor,
        *,
        ledger: SpendLedger,
        evidence_projector: EvidenceProjector,
        accounting_recoverer: AccountingRecoverer | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.executor = executor
        self.ledger = ledger
        self.evidence_projector = evidence_projector
        self.accounting_recoverer = accounting_recoverer

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

    def _project(
        self,
        evidence: ProviderEvidenceEnvelope,
        run: RunManifest,
    ) -> AttemptProjection:
        try:
            projection = self.evidence_projector(evidence, run.to_dict())
        except Exception as exc:  # a projector defect must not erase a paid attempt
            return fail_closed_projection(
                evidence, run.to_dict(), f"projector_{type(exc).__name__}"
            )
        if projection.status == "complete" and (
            projection.resolved_model_id != run.resolved_model_id
            or projection.endpoint != run.endpoint
            or projection.provider != run.provider
        ):
            return replace(
                projection,
                status="accounting_unknown",
                error_type="provider_identity_contradiction",
            )
        return projection

    @staticmethod
    def _call(
        *,
        call_id: str,
        trial_id: str,
        run: RunManifest,
        attempt: int,
        reservation: float,
        projection: AttemptProjection,
        evidence: AttemptEvidence,
    ) -> CallRecord:
        return CallRecord(
            call_id=call_id,
            trial_id=trial_id,
            run_id=run.run_id,
            attempt=attempt,
            started_at=projection.started_at,
            finished_at=projection.finished_at,
            status=projection.status,
            reserved_cost_usd=reservation,
            observed_cost_usd=projection.observed_cost_usd,
            resolved_model_id=projection.resolved_model_id,
            provider=projection.provider,
            endpoint=projection.endpoint,
            latency_ms=projection.latency_ms,
            provider_latency_ms=projection.provider_latency_ms,
            input_tokens=projection.input_tokens,
            output_tokens=projection.output_tokens,
            reasoning_tokens=projection.reasoning_tokens,
            provider_request_id=projection.provider_request_id,
            error_type=projection.error_type,
            response_sha256=sha256(projection.response_text.encode()).hexdigest(),
            evidence_sha256=evidence.evidence_sha256,
        )

    def _append_attempt(
        self,
        evidence: AttemptEvidence,
        call: CallRecord,
    ) -> None:
        append_jsonl_fsynced(self.state_dir / "transcripts.jsonl", evidence.to_dict())
        append_jsonl_fsynced(self.state_dir / "calls.jsonl", call.to_dict())

    @staticmethod
    def _trial(
        *,
        run: RunManifest,
        sample: Sample,
        protocol: ProtocolSpec,
        projection: AttemptProjection,
        evidence: AttemptEvidence,
        attempt: int,
    ) -> TrialRecord:
        parse_status, prediction, correct = protocol.parse_answer(
            projection.response_text,
            expected_normal_value=sample.metadata["normal_value"],
            expected_tree=sample.metadata["abstract_form"],
        )
        return TrialRecord(
            trial_id=evidence.trial_id,
            run_id=run.run_id,
            suite_version=run.suite_version,
            abstract_form_id=sample.metadata["abstract_form_id"],
            dialect_id=sample.metadata["dialect_id"],
            protocol_id=run.protocol_id,
            execution_surface=run.execution_surface,
            requested_model_id=run.requested_model_id,
            resolved_model_id=projection.resolved_model_id,
            provider=projection.provider,
            endpoint=projection.endpoint,
            prompt_hash=sample.metadata["prompt_hash"],
            symbolic_payload_hash=sample.metadata["symbolic_payload_hash"],
            model_payload_sha256=sample.metadata["model_payload_sha256"],
            parse_status=parse_status,
            attempt_count=attempt,
            latency_ms=projection.latency_ms,
            provider_latency_ms=projection.provider_latency_ms,
            input_tokens=projection.input_tokens,
            output_tokens=projection.output_tokens,
            reasoning_tokens=projection.reasoning_tokens,
            observed_cost_usd=projection.observed_cost_usd,
            prediction=prediction,
            normal_value=sample.metadata["normal_value"],
            correct=correct,
            response_text=projection.response_text,
            error_type=projection.error_type,
            completion_evidence_sha256=evidence.evidence_sha256,
        )

    def _write_run_state(self, run: RunManifest) -> RunManifest:
        trials = read_jsonl(self.state_dir / "trials.jsonl")
        calls = _effective_calls(read_jsonl(self.state_dir / "calls.jsonl"))
        complete = {row["trial_id"] for row in trials} == set(run.expected_trial_ids)
        updated = replace(
            run,
            status="complete" if complete else "probed",
            attempts=len(calls),
            token_usage={
                key: sum(row[key] for row in calls)
                for key in ("input_tokens", "output_tokens", "reasoning_tokens")
            },
            latency_ms=sum(row["latency_ms"] for row in calls),
            cost_usd=sum(row["observed_cost_usd"] for row in calls),
        )
        write_json_atomic(self.state_dir / "run.json", updated.to_dict())
        return updated

    def _recover_unknown_calls(
        self,
        run: RunManifest,
        samples_by_trial: dict[str, Sample],
        protocol: ProtocolSpec,
    ) -> None:
        calls = _effective_calls(read_jsonl(self.state_dir / "calls.jsonl"))
        unknown = [row for row in calls if row["status"] == "accounting_unknown"]
        if not unknown:
            return
        if self.accounting_recoverer is None:
            self._write_run_state(run)
            raise RuntimeError("run has unresolved provider accounting")
        evidence_by_call = _latest_evidence(read_jsonl(self.state_dir / "transcripts.jsonl"))
        existing_trials = {row["trial_id"] for row in read_jsonl(self.state_dir / "trials.jsonl")}
        for row in unknown:
            prior = evidence_by_call[row["call_id"]]
            try:
                recovered_envelope = self.accounting_recoverer(
                    prior.provider_evidence, run.to_dict()
                )
            except Exception as exc:
                self._write_run_state(run)
                raise RuntimeError("provider accounting recovery failed") from exc
            revised = prior.revise(recovered_envelope)
            append_jsonl_fsynced(self.state_dir / "transcripts.jsonl", revised.to_dict())
            projection = self._project(recovered_envelope, run)
            call = self._call(
                call_id=row["call_id"],
                trial_id=row["trial_id"],
                run=run,
                attempt=row["attempt"],
                reservation=row["reserved_cost_usd"],
                projection=projection,
                evidence=revised,
            )
            append_jsonl_fsynced(self.state_dir / "calls.jsonl", call.to_dict())
            if projection.status == "accounting_unknown":
                self._write_run_state(run)
                raise RuntimeError("provider accounting remains unresolved")
            self.ledger.settle(
                call_id=call.call_id,
                trial_id=call.trial_id,
                run_id=run.run_id,
                cohort=run.cohort,
                amount=projection.observed_cost_usd,
            )
            if projection.status == "complete" and call.trial_id not in existing_trials:
                trial = self._trial(
                    run=run,
                    sample=samples_by_trial[call.trial_id],
                    protocol=protocol,
                    projection=projection,
                    evidence=revised,
                    attempt=call.attempt,
                )
                append_jsonl_fsynced(self.state_dir / "trials.jsonl", trial.to_dict())
                existing_trials.add(call.trial_id)

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
        protocol = get_protocol(run.protocol_id)
        self._recover_unknown_calls(run, samples_by_trial, protocol)
        existing = {row["trial_id"]: row for row in read_jsonl(self.state_dir / "trials.jsonl")}
        prior_calls = _effective_calls(read_jsonl(self.state_dir / "calls.jsonl"))
        orphaned_complete = {
            row["trial_id"]
            for row in prior_calls
            if row["status"] == "complete" and row["trial_id"] not in existing
        }
        if orphaned_complete:
            raise RuntimeError("run has a completed call rejected by an identity gate")
        for trial_id in run.expected_trial_ids:
            if trial_id in existing:
                continue
            sample = samples_by_trial[trial_id]
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
                result = self.executor.execute(
                    ExecutionRequest(
                        run=run,
                        task=task,
                        sample=sample,
                        attempt=attempt,
                        log_dir=self.state_dir / "inspect-logs",
                    )
                )
                evidence = AttemptEvidence.capture(
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    attempt=attempt,
                    provider_evidence=result.provider_evidence,
                )
                append_jsonl_fsynced(self.state_dir / "transcripts.jsonl", evidence.to_dict())
                projection = self._project(result.provider_evidence, run)
                call = self._call(
                    call_id=call_id,
                    trial_id=trial_id,
                    run=run,
                    attempt=attempt,
                    reservation=reservation,
                    projection=projection,
                    evidence=evidence,
                )
                append_jsonl_fsynced(self.state_dir / "calls.jsonl", call.to_dict())
                if projection.status == "accounting_unknown":
                    self._write_run_state(run)
                    raise RuntimeError(f"provider accounting is unknown: {projection.error_type}")
                self.ledger.settle(
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    cohort=run.cohort,
                    amount=projection.observed_cost_usd,
                )
                if projection.status != "complete":
                    continue
                trial = self._trial(
                    run=run,
                    sample=sample,
                    protocol=protocol,
                    projection=projection,
                    evidence=evidence,
                    attempt=attempt,
                )
                append_jsonl_fsynced(self.state_dir / "trials.jsonl", trial.to_dict())
                break
        return self._write_run_state(run)
