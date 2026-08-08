"""Typed run, trial, and call records shared by runners and release bundles."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import blake2b, sha256
from typing import Any, Literal

from lofbench.provider_evidence import ProviderEvidenceEnvelope

RunStatus = Literal[
    "planned",
    "probed",
    "ambiguous",
    "complete",
    "admitted",
    "rejected",
]


def deterministic_id(prefix: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{blake2b(canonical.encode(), digest_size=12).hexdigest()}"


EXECUTION_SPEC_FIELDS = (
    "requested_model_id",
    "resolved_model_id",
    "execution_surface",
    "provider",
    "endpoint",
    "routing_policy",
    "privacy_policy",
    "sdk_version",
    "reasoning",
    "generation",
    "billing_channel",
    "cohort",
    "max_transport_attempts",
    "pricing",
)

RUN_IDENTITY_FIELDS = (
    "suite_version",
    "form_set",
    "dialect_id",
    "protocol_id",
    *EXECUTION_SPEC_FIELDS,
    "authority",
)


def run_execution_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the sole authoritative identity material for a benchmark run."""
    return {field: value[field] for field in RUN_IDENTITY_FIELDS}


def execution_spec_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the provider-neutral execution projection used by run authority."""
    return {field: value[field] for field in EXECUTION_SPEC_FIELDS}


def run_id_for(value: Mapping[str, Any]) -> str:
    return deterministic_id("run", run_execution_identity(value))


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    suite_version: str
    form_set: str
    dialect_id: str
    protocol_id: str
    requested_model_id: str
    resolved_model_id: str
    execution_surface: str
    provider: str
    endpoint: str
    routing_policy: dict[str, Any]
    privacy_policy: dict[str, Any]
    sdk_version: str
    reasoning: dict[str, Any]
    generation: dict[str, Any]
    billing_channel: str
    cohort: str
    max_transport_attempts: int
    pricing: dict[str, float]
    authority: dict[str, str]
    expected_trial_ids: tuple[str, ...]
    status: RunStatus = "planned"
    attempts: int = 0
    token_usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    rejection_reason: str = ""
    catalog_retrieved_at: str = ""
    catalog_row: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["expected_trial_ids"] = list(self.expected_trial_ids)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunManifest:
        return cls(**{**value, "expected_trial_ids": tuple(value["expected_trial_ids"])})

    def authoritative_run_id(self) -> str:
        return run_id_for(self.to_dict())

    @classmethod
    def plan(cls, **kwargs: Any) -> RunManifest:
        return cls(run_id=run_id_for(kwargs), **kwargs)


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    run_id: str
    suite_version: str
    abstract_form_id: str
    dialect_id: str
    protocol_id: str
    execution_surface: str
    requested_model_id: str
    resolved_model_id: str
    provider: str
    endpoint: str
    prompt_hash: str
    symbolic_payload_hash: str
    model_payload_sha256: str
    parse_status: str
    attempt_count: int
    latency_ms: float
    provider_latency_ms: float
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    observed_cost_usd: float
    prediction: str
    normal_value: str
    correct: bool
    response_text: str = ""
    error_type: str = ""
    completion_evidence_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CallRecord:
    call_id: str
    trial_id: str
    run_id: str
    attempt: int
    started_at: str
    finished_at: str
    status: str
    reserved_cost_usd: float
    observed_cost_usd: float
    resolved_model_id: str
    provider: str
    endpoint: str
    latency_ms: float
    provider_latency_ms: float
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    provider_request_id: str = ""
    error_type: str = ""
    response_sha256: str = ""
    evidence_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RequestStartedRecord:
    """Durable intent written immediately before one external execution attempt."""

    call_id: str
    trial_id: str
    run_id: str
    attempt: int
    request_sha256: str
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RequestStartedRecord:
        expected = {
            "call_id",
            "trial_id",
            "run_id",
            "attempt",
            "request_sha256",
            "started_at",
        }
        if set(value) != expected:
            raise RuntimeError("request-started record schema is invalid")
        if not all(
            isinstance(value[field], str)
            for field in ("call_id", "trial_id", "run_id", "request_sha256", "started_at")
        ):
            raise RuntimeError("request-started record values are invalid")
        if (
            isinstance(value["attempt"], bool)
            or not isinstance(value["attempt"], int)
            or value["attempt"] < 1
        ):
            raise RuntimeError("request-started attempt is invalid")
        digest = value["request_sha256"]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise RuntimeError("request-started hash is invalid")
        try:
            started = datetime.fromisoformat(value["started_at"])
        except (ValueError, OverflowError) as exc:
            raise RuntimeError("request-started timestamp is invalid") from exc
        if started.tzinfo is None:
            raise RuntimeError("request-started timestamp is invalid")
        return cls(**value)


@dataclass(frozen=True)
class AttemptEvidence:
    """Immutable raw provider evidence linked to one benchmark attempt."""

    evidence_sha256: str
    call_id: str
    trial_id: str
    run_id: str
    attempt: int
    revision: int
    predecessor_evidence_sha256: str
    provider_evidence: ProviderEvidenceEnvelope

    def digest_material(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "trial_id": self.trial_id,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "revision": self.revision,
            "predecessor_evidence_sha256": self.predecessor_evidence_sha256,
            "provider_evidence": self.provider_evidence.to_dict(),
        }

    def authoritative_digest(self) -> str:
        canonical = json.dumps(
            self.digest_material(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"evidence_sha256": self.evidence_sha256, **self.digest_material()}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AttemptEvidence:
        return cls(
            evidence_sha256=value["evidence_sha256"],
            call_id=value["call_id"],
            trial_id=value["trial_id"],
            run_id=value["run_id"],
            attempt=value["attempt"],
            revision=value["revision"],
            predecessor_evidence_sha256=value["predecessor_evidence_sha256"],
            provider_evidence=ProviderEvidenceEnvelope.from_dict(value["provider_evidence"]),
        )

    @classmethod
    def capture(cls, **kwargs: Any) -> AttemptEvidence:
        kwargs.setdefault("revision", 0)
        kwargs.setdefault("predecessor_evidence_sha256", "")
        if isinstance(kwargs.get("provider_evidence"), dict):
            kwargs = {
                **kwargs,
                "provider_evidence": ProviderEvidenceEnvelope.from_dict(
                    kwargs["provider_evidence"]
                ),
            }
        provisional = cls(evidence_sha256="", **kwargs)
        return cls(evidence_sha256=provisional.authoritative_digest(), **kwargs)

    def revise(self, provider_evidence: ProviderEvidenceEnvelope) -> AttemptEvidence:
        return self.capture(
            call_id=self.call_id,
            trial_id=self.trial_id,
            run_id=self.run_id,
            attempt=self.attempt,
            revision=self.revision + 1,
            predecessor_evidence_sha256=self.evidence_sha256,
            provider_evidence=provider_evidence,
        )


@dataclass(frozen=True)
class LedgerEvent:
    event_type: Literal["reserved", "settled"]
    call_id: str
    trial_id: str
    run_id: str
    cohort: str
    amount_usd: float
    at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LedgerEvent:
        if "type" in value:
            value = {**value, "event_type": value["type"]}
            value.pop("type")
        return cls(**value)
