"""Typed run, trial, and call records shared by runners and release bundles."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from hashlib import blake2b, sha256
from typing import Any, Literal

RunStatus = Literal["planned", "probed", "complete", "admitted", "rejected"]


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
    "dialect_set",
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
    dialect_set: str
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
class AttemptEvidence:
    """The deterministic public evidence projection for one provider attempt."""

    evidence_sha256: str
    call_id: str
    trial_id: str
    run_id: str
    attempt: int
    status: str
    started_at: str
    finished_at: str
    response_text: str
    provider_request_id: str
    resolved_model_id: str
    provider: str
    endpoint: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    observed_cost_usd: float
    latency_ms: float
    error_type: str
    raw_transcript: dict[str, Any]

    def digest_material(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("evidence_sha256")
        return value

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
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AttemptEvidence:
        return cls(**value)

    @classmethod
    def capture(cls, **kwargs: Any) -> AttemptEvidence:
        provisional = cls(evidence_sha256="", **kwargs)
        return cls(evidence_sha256=provisional.authoritative_digest(), **kwargs)


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
