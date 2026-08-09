"""Provider-neutral, resumable execution of one immutable benchmark run."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
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
from lofbench.records import (
    AttemptEvidence,
    CallRecord,
    RequestStartedRecord,
    RunManifest,
    RunStatus,
    TrialRecord,
)
from lofbench.run_models import (
    ExecutionRequest,
    TrialExecutor,
    call_id_for,
    request_sha256_for,
    trial_id_for,
)
from lofbench.state_io import (
    FileLockUnavailableError,
    append_jsonl_fsynced,
    exclusive_file_lock,
    read_jsonl,
    write_json_atomic,
)

MAX_TRANSPORT_ATTEMPTS = 3
TransitionObserver = Callable[[str, str], None]


class RunAlreadyRunningError(RuntimeError):
    """Another process owns this run's execution state."""

    status = "already_running"


def _effective_calls(rows: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for row in rows:
        by_id[row["call_id"]] = row
    return list(by_id.values())


def _request_starts(rows: list[dict]) -> dict[str, RequestStartedRecord]:
    records = [RequestStartedRecord.from_dict(row) for row in rows]
    by_id = {record.call_id: record for record in records}
    if len(by_id) != len(records):
        raise RuntimeError("run state contains duplicate request-started records")
    return by_id


def _latest_evidence(rows: list[dict]) -> dict[str, AttemptEvidence]:
    chains: dict[str, list[AttemptEvidence]] = {}
    for row in rows:
        record = AttemptEvidence.from_dict(row)
        chains.setdefault(record.call_id, []).append(record)
    latest: dict[str, AttemptEvidence] = {}
    for call_id, chain in chains.items():
        chain.sort(key=lambda record: record.revision)
        predecessor = ""
        identity = None
        for revision, record in enumerate(chain):
            candidate_identity = (
                record.call_id,
                record.trial_id,
                record.run_id,
                record.attempt,
            )
            identity = identity or candidate_identity
            if (
                record.revision != revision
                or record.predecessor_evidence_sha256 != predecessor
                or record.authoritative_digest() != record.evidence_sha256
                or candidate_identity != identity
            ):
                raise RuntimeError("run evidence revision chain is invalid")
            predecessor = record.evidence_sha256
        latest[call_id] = chain[-1]
    return latest


def project_attempt(
    evidence: ProviderEvidenceEnvelope,
    run: RunManifest,
    projector: EvidenceProjector,
) -> AttemptProjection:
    """Apply one adapter projection and enforce the provider-neutral run identity."""
    try:
        projection = projector(evidence, run.to_dict())
    except Exception as exc:  # a projector defect must not erase a paid attempt
        return fail_closed_projection(evidence, run.to_dict(), f"projector_{type(exc).__name__}")
    if projection.status in {"complete", "provider_error"} and (
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


def call_record_from_projection(
    *,
    call_id: str,
    trial_id: str,
    run: RunManifest,
    attempt: int,
    reservation: float,
    projection: AttemptProjection,
    evidence: AttemptEvidence,
) -> CallRecord:
    """Derive the sole call record projection used by execution and migration."""
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


def trial_record_from_projection(
    *,
    run: RunManifest,
    sample: Sample,
    protocol: ProtocolSpec,
    projection: AttemptProjection,
    evidence: AttemptEvidence,
    attempt: int,
) -> TrialRecord:
    """Derive the sole scored trial projection used by execution and migration."""
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


class RunOrchestrator:
    def __init__(
        self,
        state_dir: Path,
        executor: TrialExecutor,
        *,
        ledger: SpendLedger,
        evidence_projector: EvidenceProjector,
        accounting_recoverer: AccountingRecoverer | None = None,
        transition_observer: TransitionObserver | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.executor = executor
        self.ledger = ledger
        self.evidence_projector = evidence_projector
        self.accounting_recoverer = accounting_recoverer
        self.transition_observer = transition_observer

    def _observe(self, transition: str, call_id: str = "") -> None:
        if self.transition_observer is not None:
            self.transition_observer(transition, call_id)

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
        return project_attempt(evidence, run, self.evidence_projector)

    def _write_run_state(
        self,
        run: RunManifest,
        *,
        status_override: RunStatus | None = None,
    ) -> RunManifest:
        trials = read_jsonl(self.state_dir / "trials.jsonl")
        calls = _effective_calls(read_jsonl(self.state_dir / "calls.jsonl"))
        complete = {row["trial_id"] for row in trials} == set(run.expected_trial_ids)
        observed = bool(calls or trials)
        updated = replace(
            run,
            status=status_override
            or ("complete" if complete else "probed" if observed else "planned"),
            attempts=len(calls),
            token_usage={
                key: sum(row[key] for row in calls)
                for key in ("input_tokens", "output_tokens", "reasoning_tokens")
            },
            latency_ms=sum(row["latency_ms"] for row in calls),
            cost_usd=sum(row["observed_cost_usd"] for row in calls),
        )
        write_json_atomic(self.state_dir / "run.json", updated.to_dict())
        self._observe("run_manifest")
        return updated

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
        try:
            with exclusive_file_lock(self.state_dir / ".run.lock", blocking=False):
                return self._execute_locked(run, task, allow_new_inference=True)
        except FileLockUnavailableError as exc:
            raise RunAlreadyRunningError(
                f"run {run.run_id} is already running in another process"
            ) from exc

    def reconcile(self, run: RunManifest, task: Task) -> RunManifest:
        """Validate and close durable local transitions without scheduling inference."""
        if run.run_id != run.authoritative_run_id():
            raise RuntimeError("run_id does not match the authoritative execution identity")
        if not 1 <= run.max_transport_attempts <= MAX_TRANSPORT_ATTEMPTS:
            raise RuntimeError("run declares an invalid transport-attempt limit")
        try:
            with exclusive_file_lock(self.state_dir / ".run.lock", blocking=False):
                return self._execute_locked(run, task, allow_new_inference=False)
        except FileLockUnavailableError as exc:
            raise RunAlreadyRunningError(
                f"run {run.run_id} is already running in another process"
            ) from exc

    def _execute_locked(
        self,
        run: RunManifest,
        task: Task,
        *,
        allow_new_inference: bool,
    ) -> RunManifest:
        """Reconcile and execute while the caller owns the run's single-writer lock."""
        is_paid = run.billing_channel != "subscription_unmetered"
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
        call_identity = {
            call_id_for(trial_id, attempt): (trial_id, attempt)
            for trial_id in run.expected_trial_ids
            for attempt in range(1, run.max_transport_attempts + 1)
        }
        request_hashes = {
            call_id: request_sha256_for(
                call_id=call_id,
                trial_id=trial_id,
                run_id=run.run_id,
                attempt=attempt,
                prompt_hash=samples_by_trial[trial_id].metadata["prompt_hash"],
                model_payload_sha256=samples_by_trial[trial_id].metadata["model_payload_sha256"],
            )
            for call_id, (trial_id, attempt) in call_identity.items()
        }
        inferred_here: set[str] = set()
        recovered_here: set[str] = set()

        while True:
            evidence_by_call = _latest_evidence(read_jsonl(self.state_dir / "transcripts.jsonl"))
            request_starts = _request_starts(read_jsonl(self.state_dir / "request-started.jsonl"))
            call_rows = _effective_calls(read_jsonl(self.state_dir / "calls.jsonl"))
            calls_by_id = {row["call_id"]: row for row in call_rows}
            trial_rows = read_jsonl(self.state_dir / "trials.jsonl")
            if len({row["trial_id"] for row in trial_rows}) != len(trial_rows):
                raise RuntimeError("run state contains duplicate trials")
            trials_by_id = {row["trial_id"]: row for row in trial_rows}
            if not set(trials_by_id) <= set(run.expected_trial_ids):
                raise RuntimeError("run state contains an unexpected trial")

            events_by_call: dict[str, list] = {}
            for event in self.ledger.events_for_run(run.run_id):
                events_by_call.setdefault(event.call_id, []).append(event)
            initiated = (
                set(events_by_call) | set(request_starts) | set(evidence_by_call) | set(calls_by_id)
            )
            if initiated - set(call_identity):
                raise RuntimeError("run state contains an unexpected call")

            # Close every deterministic transition already justified by durable
            # evidence before a new inference is considered.
            changed = False
            reserve_only: list[str] = []
            ambiguous: list[str] = []
            for call_id in call_identity:
                if call_id not in initiated:
                    continue
                trial_id, attempt = call_identity[call_id]
                events = events_by_call.get(call_id, [])
                if len(events) not in (1, 2) or events[0].event_type != "reserved":
                    raise RuntimeError("run ledger transition sequence is invalid")
                if len(events) == 2 and events[1].event_type != "settled":
                    raise RuntimeError("run ledger transition sequence is invalid")
                if any(
                    (event.call_id, event.trial_id, event.run_id, event.cohort)
                    != (call_id, trial_id, run.run_id, run.cohort)
                    for event in events
                ):
                    raise RuntimeError("run ledger identity is invalid")
                reservation = events[0].amount_usd
                request_start = request_starts.get(call_id)
                if request_start is not None and (
                    request_start.call_id != call_id
                    or request_start.trial_id != trial_id
                    or request_start.run_id != run.run_id
                    or request_start.attempt != attempt
                    or request_start.request_sha256 != request_hashes[call_id]
                ):
                    raise RuntimeError("run request-started identity is invalid")
                evidence = evidence_by_call.get(call_id)
                persisted_call = calls_by_id.get(call_id)
                if evidence is None:
                    if persisted_call is not None or len(events) == 2:
                        raise RuntimeError("run state contains a call without evidence")
                    if request_start is None:
                        reserve_only.append(call_id)
                    else:
                        ambiguous.append(call_id)
                    continue
                if request_start is None:
                    raise RuntimeError("run evidence has no request-started record")
                if (
                    evidence.call_id != call_id
                    or evidence.trial_id != trial_id
                    or evidence.run_id != run.run_id
                    or evidence.attempt != attempt
                    or evidence.authoritative_digest() != evidence.evidence_sha256
                ):
                    raise RuntimeError("run evidence identity is invalid")

                projection = self._project(evidence.provider_evidence, run)
                derived_call = call_record_from_projection(
                    call_id=call_id,
                    trial_id=trial_id,
                    run=run,
                    attempt=attempt,
                    reservation=reservation,
                    projection=projection,
                    evidence=evidence,
                )
                if persisted_call is None or (
                    persisted_call.get("evidence_sha256") != evidence.evidence_sha256
                ):
                    append_jsonl_fsynced(self.state_dir / "calls.jsonl", derived_call.to_dict())
                    self._observe("call", call_id)
                    changed = True
                    break
                if persisted_call != derived_call.to_dict():
                    raise RuntimeError("run call contradicts its provider evidence")

                if projection.status == "accounting_unknown":
                    if len(events) == 2:
                        raise RuntimeError("unknown accounting was settled")
                    if call_id in inferred_here:
                        self._write_run_state(run)
                        raise RuntimeError(
                            f"provider accounting is unknown: {projection.error_type}"
                        )
                    if self.accounting_recoverer is None or call_id in recovered_here:
                        self._write_run_state(run)
                        raise RuntimeError(
                            f"unresolved provider accounting: {projection.error_type}"
                        )
                    recovered_here.add(call_id)
                    recovered = self.accounting_recoverer(evidence.provider_evidence, run.to_dict())
                    if recovered == evidence.provider_evidence:
                        self._write_run_state(run)
                        raise RuntimeError(
                            f"unresolved provider accounting: {projection.error_type}"
                        )
                    revision = evidence.revise(recovered)
                    append_jsonl_fsynced(self.state_dir / "transcripts.jsonl", revision.to_dict())
                    self._observe("evidence", call_id)
                    changed = True
                    break

                if len(events) == 1:
                    self.ledger.settle(
                        call_id=call_id,
                        trial_id=trial_id,
                        run_id=run.run_id,
                        cohort=run.cohort,
                        amount=projection.observed_cost_usd,
                    )
                    self._observe("settled", call_id)
                    changed = True
                    break
                if events[1].amount_usd != projection.observed_cost_usd:
                    raise RuntimeError("run settlement contradicts its provider evidence")

                if projection.status == "complete":
                    trial = trial_record_from_projection(
                        run=run,
                        sample=samples_by_trial[trial_id],
                        protocol=protocol,
                        projection=projection,
                        evidence=evidence,
                        attempt=attempt,
                    )
                    persisted_trial = trials_by_id.get(trial_id)
                    if persisted_trial is None:
                        append_jsonl_fsynced(self.state_dir / "trials.jsonl", trial.to_dict())
                        self._observe("trial", call_id)
                        changed = True
                        break
                    if persisted_trial != trial.to_dict():
                        raise RuntimeError("run trial contradicts its completion evidence")
            if changed:
                continue

            if ambiguous:
                self._write_run_state(run, status_override="ambiguous")
                raise RuntimeError("request outcome is ambiguous; refusing to resend inference")

            if not allow_new_inference:
                if reserve_only:
                    raise RuntimeError("run has a reserved call requiring provider execution")
                return self._write_run_state(run)

            # A reservation is safe to execute only until the durable request
            # marker exists. Started-without-evidence is quarantined above.
            if reserve_only:
                call_id = reserve_only[0]
                trial_id, attempt = call_identity[call_id]
                request_start = RequestStartedRecord(
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    attempt=attempt,
                    request_sha256=request_hashes[call_id],
                    started_at=datetime.now(UTC).isoformat(),
                )
                append_jsonl_fsynced(
                    self.state_dir / "request-started.jsonl",
                    request_start.to_dict(),
                )
                self._observe("request_started", call_id)
                result = self.executor.execute(
                    ExecutionRequest(
                        run=run,
                        task=task,
                        sample=samples_by_trial[trial_id],
                        attempt=attempt,
                        log_dir=self.state_dir / "inspect-logs",
                        call_id=call_id,
                    )
                )
                self._observe("provider_returned", call_id)
                evidence = AttemptEvidence.capture(
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    attempt=attempt,
                    provider_evidence=result.provider_evidence,
                )
                append_jsonl_fsynced(self.state_dir / "transcripts.jsonl", evidence.to_dict())
                inferred_here.add(call_id)
                self._observe("evidence", call_id)
                continue

            # With reconciliation closed, reserve the next missing attempt before
            # allowing the provider adapter to act.
            scheduled = False
            for trial_id in run.expected_trial_ids:
                if trial_id in trials_by_id:
                    continue
                attempts = sorted(
                    attempt
                    for call_id, (candidate_trial, attempt) in call_identity.items()
                    if candidate_trial == trial_id and call_id in initiated
                )
                if attempts != list(range(1, len(attempts) + 1)):
                    raise RuntimeError("run attempt sequence is invalid")
                if len(attempts) >= run.max_transport_attempts:
                    continue
                attempt = len(attempts) + 1
                call_id = call_id_for(trial_id, attempt)
                reservation = self.reservation(run, samples_by_trial[trial_id]) if is_paid else 0.0
                self.ledger.reserve(
                    call_id=call_id,
                    trial_id=trial_id,
                    run_id=run.run_id,
                    cohort=run.cohort,
                    amount=reservation,
                )
                self._observe("reserved", call_id)
                scheduled = True
                break
            if scheduled:
                continue
            return self._write_run_state(run)
