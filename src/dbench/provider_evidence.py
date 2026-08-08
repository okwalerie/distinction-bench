"""Immutable provider evidence and its sole normalized projection."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

OPENROUTER_CHAT_SOURCE = "openrouter.chat-completion.response.v1"
OPENROUTER_GENERATION_SOURCE = "openrouter.generation.response.v1"
OPENROUTER_ERROR_SOURCE = "openrouter.request.error.v1"
AGENT_CLI_SOURCE = "agent-cli.process.v1"


@dataclass(frozen=True)
class ProviderEvidenceSource:
    """One exactly retained source payload with a deterministic semantic label."""

    label: str
    payload_json: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProviderEvidenceSource:
        if set(value) != {"label", "payload_json"}:
            raise RuntimeError("provider evidence source schema is invalid")
        label = value["label"]
        payload_json = value["payload_json"]
        if not isinstance(label, str) or not isinstance(payload_json, str):
            raise RuntimeError("provider evidence source values are invalid")
        try:
            json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("provider evidence source is not exact json") from exc
        return cls(label=label, payload_json=payload_json)

    def payload(self) -> Any:
        return json.loads(self.payload_json)


@dataclass(frozen=True)
class ProviderEvidenceEnvelope:
    """Exact immutable evidence retained by one execution adapter call."""

    schema_version: int
    adapter_id: str
    request_started_at: str
    response_finished_at: str
    sources: tuple[ProviderEvidenceSource, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_id": self.adapter_id,
            "request_started_at": self.request_started_at,
            "response_finished_at": self.response_finished_at,
            "sources": [source.to_dict() for source in self.sources],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProviderEvidenceEnvelope:
        expected = {
            "schema_version",
            "adapter_id",
            "request_started_at",
            "response_finished_at",
            "sources",
        }
        if set(value) != expected or value["schema_version"] != 1:
            raise RuntimeError("provider evidence envelope schema is invalid")
        if not isinstance(value["adapter_id"], str) or not isinstance(value["sources"], list):
            raise RuntimeError("provider evidence envelope values are invalid")
        sources = tuple(ProviderEvidenceSource.from_dict(row) for row in value["sources"])
        labels = [source.label for source in sources]
        if len(labels) != len(set(labels)):
            raise RuntimeError("provider evidence source labels are not unique")
        for field in ("request_started_at", "response_finished_at"):
            if not isinstance(value[field], str):
                raise RuntimeError("provider evidence timing is invalid")
        return cls(
            schema_version=1,
            adapter_id=value["adapter_id"],
            request_started_at=value["request_started_at"],
            response_finished_at=value["response_finished_at"],
            sources=sources,
        )


@dataclass(frozen=True)
class AttemptProjection:
    """The only normalized interpretation of a provider evidence envelope."""

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


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} provider evidence is not an object")
    return value


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise RuntimeError(f"{label} provider evidence is invalid")
    return value


def _tokens(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"{label} provider evidence is invalid")
    return value


def _cost(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError("provider cost evidence is invalid")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise RuntimeError("provider cost evidence is invalid")
    return result


def _timing(envelope: ProviderEvidenceEnvelope) -> tuple[str, str, float]:
    try:
        started = datetime.fromisoformat(envelope.request_started_at)
        finished = datetime.fromisoformat(envelope.response_finished_at)
    except ValueError as exc:
        raise RuntimeError("provider evidence timing is invalid") from exc
    if started.tzinfo is None or finished.tzinfo is None or finished < started:
        raise RuntimeError("provider evidence timing is invalid")
    return (
        envelope.request_started_at,
        envelope.response_finished_at,
        (finished - started).total_seconds() * 1000,
    )


def _source_map(envelope: ProviderEvidenceEnvelope) -> dict[str, Any]:
    return {source.label: source.payload() for source in envelope.sources}


def _openrouter_projection(
    envelope: ProviderEvidenceEnvelope,
    run: Mapping[str, Any],
) -> AttemptProjection:
    sources = _source_map(envelope)
    started_at, finished_at, latency_ms = _timing(envelope)
    if set(sources) == {OPENROUTER_ERROR_SOURCE}:
        error = _object(sources[OPENROUTER_ERROR_SOURCE], "openrouter error")
        return AttemptProjection(
            status="transport_error",
            started_at=started_at,
            finished_at=finished_at,
            response_text="",
            provider_request_id="",
            resolved_model_id="",
            provider="openrouter",
            endpoint=_text(run.get("endpoint"), "planned endpoint"),
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            observed_cost_usd=0.0,
            latency_ms=latency_ms,
            error_type=_text(error.get("error_type"), "transport error type"),
        )
    if OPENROUTER_CHAT_SOURCE not in sources:
        raise RuntimeError("openrouter attempt lacks exact chat evidence")
    chat = _object(sources[OPENROUTER_CHAT_SOURCE], "openrouter chat")
    choices = chat.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise RuntimeError("openrouter chat evidence has no unique completion")
    message = _object(_object(choices[0], "openrouter choice").get("message"), "message")
    response_text = _text(message.get("content"), "completion", allow_empty=True)
    request_id = _text(chat.get("id"), "provider request id")
    resolved_model_id = _text(chat.get("model"), "resolved model").removeprefix(
        "openrouter/"
    )
    usage = _object(chat.get("usage"), "chat usage")
    details = usage.get("completion_tokens_details") or {}
    details = _object(details, "completion token details")
    input_tokens = _tokens(
        usage.get("prompt_tokens", usage.get("input_tokens")), "input tokens"
    )
    output_tokens = _tokens(
        usage.get("completion_tokens", usage.get("output_tokens")), "output tokens"
    )
    if OPENROUTER_GENERATION_SOURCE not in sources:
        allowed = {OPENROUTER_CHAT_SOURCE, OPENROUTER_ERROR_SOURCE}
        if not set(sources) <= allowed:
            raise RuntimeError("openrouter attempt has unexpected evidence sources")
        error_type = "missing_generation_evidence"
        if OPENROUTER_ERROR_SOURCE in sources:
            error = _object(sources[OPENROUTER_ERROR_SOURCE], "openrouter error")
            error_type = _text(error.get("error_type"), "generation lookup error type")
        return AttemptProjection(
            status="accounting_unknown",
            started_at=started_at,
            finished_at=finished_at,
            response_text=response_text,
            provider_request_id=request_id,
            resolved_model_id=resolved_model_id,
            provider="openrouter",
            endpoint=_text(run.get("endpoint"), "planned endpoint"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=_tokens(
                details.get("reasoning_tokens", 0), "reasoning tokens"
            ),
            observed_cost_usd=0.0,
            latency_ms=latency_ms,
            error_type=error_type,
        )
    if set(sources) != {OPENROUTER_CHAT_SOURCE, OPENROUTER_GENERATION_SOURCE}:
        raise RuntimeError("openrouter attempt has unexpected evidence sources")
    generation_wrapper = _object(
        sources[OPENROUTER_GENERATION_SOURCE], "openrouter generation"
    )
    generation = _object(generation_wrapper.get("data"), "openrouter generation data")
    if generation.get("id") not in (None, request_id):
        raise RuntimeError("generation evidence belongs to a different provider request")
    generation_model = generation.get("model") or generation.get("model_id")
    if generation_model is not None and str(generation_model).removeprefix(
        "openrouter/"
    ) != resolved_model_id:
        raise RuntimeError("generation evidence belongs to a different resolved model")
    provider_name = _text(generation.get("provider_name"), "provider name")
    catalog_row = _object(run.get("catalog_row"), "run catalog")
    selected = _object(catalog_row.get("selected_endpoint"), "selected endpoint")
    endpoint = (
        _text(selected.get("tag"), "selected endpoint tag")
        if provider_name == selected.get("provider_name")
        else provider_name
    )
    reasoning_tokens = details.get("reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = generation.get("native_tokens_reasoning", 0)
    try:
        observed_cost_usd = _cost(generation.get("total_cost"))
    except RuntimeError:
        return AttemptProjection(
            status="accounting_unknown",
            started_at=started_at,
            finished_at=finished_at,
            response_text=response_text,
            provider_request_id=request_id,
            resolved_model_id=resolved_model_id,
            provider="openrouter",
            endpoint=endpoint,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=_tokens(reasoning_tokens, "reasoning tokens"),
            observed_cost_usd=0.0,
            latency_ms=latency_ms,
            error_type="missing_provider_accounting",
        )
    return AttemptProjection(
        status="complete",
        started_at=started_at,
        finished_at=finished_at,
        response_text=response_text,
        provider_request_id=request_id,
        resolved_model_id=resolved_model_id,
        provider="openrouter",
        endpoint=endpoint,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=_tokens(reasoning_tokens, "reasoning tokens"),
        observed_cost_usd=observed_cost_usd,
        latency_ms=latency_ms,
        error_type="",
    )


def _agent_cli_projection(
    envelope: ProviderEvidenceEnvelope,
    run: Mapping[str, Any],
) -> AttemptProjection:
    sources = _source_map(envelope)
    if set(sources) != {AGENT_CLI_SOURCE}:
        raise RuntimeError("agent-cli attempt lacks exact process evidence")
    process = _object(sources[AGENT_CLI_SOURCE], "agent-cli process")
    started_at, finished_at, latency_ms = _timing(envelope)
    returncode = process.get("returncode")
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        raise RuntimeError("agent-cli return code evidence is invalid")
    command = _text(process.get("command"), "agent-cli command")
    error_type = "" if returncode == 0 else f"{command}_exit_{returncode}"
    response_text = _text(
        process.get("output_text", ""), "agent-cli output", allow_empty=True
    )
    return AttemptProjection(
        status="complete" if returncode == 0 else "transport_error",
        started_at=started_at,
        finished_at=finished_at,
        response_text=response_text,
        provider_request_id=_text(process.get("request_id", ""), "request id", allow_empty=True),
        resolved_model_id=(
            _text(process.get("resolved_model_id"), "resolved model")
            if returncode == 0
            else ""
        ),
        provider=command,
        endpoint=command,
        input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        observed_cost_usd=0.0,
        latency_ms=latency_ms,
        error_type=error_type,
    )


def project_provider_evidence(
    envelope: ProviderEvidenceEnvelope,
    run: Mapping[str, Any],
) -> AttemptProjection:
    """Purely derive every normalized attempt field from retained raw evidence."""
    if envelope.schema_version != 1:
        raise RuntimeError("unsupported provider evidence envelope")
    if envelope.adapter_id == "openrouter-direct-v1":
        return _openrouter_projection(envelope, run)
    if envelope.adapter_id == "agent-cli-v1":
        return _agent_cli_projection(envelope, run)
    raise RuntimeError(f"unsupported provider evidence adapter {envelope.adapter_id!r}")


def validate_openrouter_run_policy(run: Mapping[str, Any]) -> None:
    """Validate exact authenticated routing/catalog proof in the adapter layer."""
    routing = _object(run.get("routing_policy"), "routing policy")
    privacy = _object(run.get("privacy_policy"), "privacy policy")
    generation = _object(run.get("generation"), "generation policy")
    catalog = _object(run.get("catalog_row"), "provider catalog")
    selected = _object(catalog.get("selected_endpoint"), "selected endpoint")
    zdr_selected = _object(
        catalog.get("zdr_selected_endpoint"), "zdr selected endpoint"
    )
    endpoint_identity = (
        run.get("resolved_model_id"),
        selected.get("tag"),
        selected.get("provider_name"),
    )
    if (
        routing.get("order") != [run.get("endpoint")]
        or routing.get("allow_fallbacks") is not False
        or routing.get("data_collection") != "deny"
        or routing.get("zdr") is not True
        or privacy.get("data_collection") != "deny"
        or privacy.get("zdr") is not True
        or privacy.get("authenticated_zdr_catalog") is not True
        or generation.get("max_retries") != 0
        or catalog.get("authenticated") is not True
        or endpoint_identity
        != (
            zdr_selected.get("model_id"),
            zdr_selected.get("tag"),
            zdr_selected.get("provider_name"),
        )
        or selected.get("pricing") != zdr_selected.get("pricing")
    ):
        raise RuntimeError("run lacks exact authenticated ZDR routing proof")
