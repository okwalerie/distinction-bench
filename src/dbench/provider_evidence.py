"""Application-owned projections of raw provider evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from lofbench.provider_evidence import (
    AttemptProjection,
    ProviderEvidenceEnvelope,
    ProviderEvidenceSource,
)

OPENROUTER_CHAT_SOURCE = "openrouter.chat-completion.response.v1"
OPENROUTER_GENERATION_SOURCE = "openrouter.generation.response.v1"
AGENT_CLI_SOURCE = "agent-cli.process.v1"

_ERROR_FINISH_REASONS = frozenset(
    {"cancelled", "canceled", "error", "failed", "length", "content_filter"}
)


def _elapsed(source: ProviderEvidenceSource) -> float:
    started = datetime.fromisoformat(source.request_started_at)
    finished = datetime.fromisoformat(source.response_finished_at)
    return (finished - started).total_seconds() * 1000


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"invalid_{label}")
    return value


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"invalid_{label}")
    return value


def _tokens(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid_{label}")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_{label}")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"invalid_{label}")
    return result


def _best_effort_chat(
    source: ProviderEvidenceSource | None,
) -> tuple[str, str, str, int, int, int]:
    try:
        chat = _object(source.payload() if source else None, "chat")
        choices = chat.get("choices")
        choice = _object(choices[0], "choice") if isinstance(choices, list) and choices else {}
        message = choice.get("message") if isinstance(choice, dict) else {}
        message = message if isinstance(message, dict) else {}
        usage = chat.get("usage") if isinstance(chat.get("usage"), dict) else {}
        details = (
            usage.get("completion_tokens_details")
            if isinstance(usage.get("completion_tokens_details"), dict)
            else {}
        )
        return (
            message.get("content") if isinstance(message.get("content"), str) else "",
            chat.get("id") if isinstance(chat.get("id"), str) else "",
            (
                chat.get("model", "").removeprefix("openrouter/")
                if isinstance(chat.get("model"), str)
                else ""
            ),
            usage.get("prompt_tokens")
            if isinstance(usage.get("prompt_tokens"), int)
            and not isinstance(usage.get("prompt_tokens"), bool)
            and usage["prompt_tokens"] >= 0
            else 0,
            usage.get("completion_tokens")
            if isinstance(usage.get("completion_tokens"), int)
            and not isinstance(usage.get("completion_tokens"), bool)
            and usage["completion_tokens"] >= 0
            else 0,
            details.get("reasoning_tokens")
            if isinstance(details.get("reasoning_tokens"), int)
            and not isinstance(details.get("reasoning_tokens"), bool)
            and details["reasoning_tokens"] >= 0
            else 0,
        )
    except (TypeError, ValueError, IndexError):
        return "", "", "", 0, 0, 0


def _openrouter_unknown(
    envelope: ProviderEvidenceEnvelope,
    run: Mapping[str, Any],
    error_type: str,
) -> AttemptProjection:
    chat = next(
        (source for source in envelope.sources if source.label == OPENROUTER_CHAT_SOURCE),
        None,
    )
    response, request_id, model, prompt, completion, reasoning = _best_effort_chat(chat)
    source = chat or (envelope.sources[0] if envelope.sources else None)
    started = source.request_started_at if source else ""
    finished = source.response_finished_at if source else ""
    latency = _elapsed(source) if source else 0.0
    endpoint = run.get("endpoint") if isinstance(run.get("endpoint"), str) else ""
    return AttemptProjection(
        status="accounting_unknown",
        started_at=started,
        finished_at=finished,
        response_text=response,
        provider_request_id=request_id,
        resolved_model_id=model,
        provider="openrouter",
        endpoint=endpoint,
        input_tokens=prompt,
        output_tokens=completion,
        reasoning_tokens=reasoning,
        observed_cost_usd=0.0,
        latency_ms=latency,
        provider_latency_ms=0.0,
        error_type=error_type,
    )


def _project_openrouter(
    envelope: ProviderEvidenceEnvelope,
    run: Mapping[str, Any],
) -> AttemptProjection:
    if envelope.schema_version != 2 or envelope.adapter_id != "openrouter-direct-v2":
        raise ValueError("unsupported_openrouter_evidence")
    chat_sources = [source for source in envelope.sources if source.label == OPENROUTER_CHAT_SOURCE]
    if len(chat_sources) != 1:
        raise ValueError("missing_unique_chat_exchange")
    chat_source = chat_sources[0]
    if chat_source.transport_error:
        raise ValueError(f"chat_{chat_source.transport_error}")
    if chat_source.http_status is None:
        raise ValueError("missing_chat_http_status")
    chat = _object(chat_source.payload(), "chat_json")
    if not 200 <= chat_source.http_status < 300 or "error" in chat:
        error = chat.get("error")
        error_name = (
            error.get("type")
            if isinstance(error, dict) and isinstance(error.get("type"), str)
            else f"http_{chat_source.http_status}"
        )
        return AttemptProjection(
            status="provider_error",
            started_at=chat_source.request_started_at,
            finished_at=chat_source.response_finished_at,
            response_text="",
            provider_request_id=(chat.get("id") if isinstance(chat.get("id"), str) else ""),
            resolved_model_id="",
            provider="openrouter",
            endpoint=_text(run.get("endpoint"), "planned_endpoint"),
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            observed_cost_usd=0.0,
            latency_ms=_elapsed(chat_source),
            provider_latency_ms=0.0,
            error_type=f"provider_{error_name}",
        )

    request_id = _text(chat.get("id"), "request_id")
    model = _text(chat.get("model"), "model").removeprefix("openrouter/")
    choices = chat.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("missing_unique_choice")
    choice = _object(choices[0], "choice")
    finish_reason = _text(choice.get("finish_reason"), "chat_finish_reason")
    message = _object(choice.get("message"), "message")
    response_text = _text(message.get("content"), "completion", allow_empty=True)
    usage = _object(chat.get("usage"), "chat_usage")
    details = usage.get("completion_tokens_details") or {}
    details = _object(details, "completion_details")
    input_tokens = _tokens(usage.get("prompt_tokens"), "chat_prompt_tokens")
    output_tokens = _tokens(usage.get("completion_tokens"), "chat_completion_tokens")
    reasoning_tokens = _tokens(details.get("reasoning_tokens", 0), "chat_reasoning_tokens")

    generation_sources = sorted(
        (source for source in envelope.sources if source.label == OPENROUTER_GENERATION_SOURCE),
        key=lambda source: source.sequence,
    )
    generation_source = next(
        (
            source
            for source in reversed(generation_sources)
            if not source.transport_error
            and source.http_status is not None
            and 200 <= source.http_status < 300
            and isinstance(source.payload(), dict)
            and isinstance(source.payload().get("data"), dict)
            and "error" not in source.payload()
        ),
        None,
    )
    if generation_source is None:
        raise ValueError("missing_generation_accounting")
    wrapper = _object(generation_source.payload(), "generation_wrapper")
    generation = _object(wrapper.get("data"), "generation")
    if generation.get("id") != request_id:
        raise ValueError("contradictory_request_id")
    generation_model = generation.get("model") or generation.get("model_id")
    if (
        not isinstance(generation_model, str)
        or generation_model.removeprefix("openrouter/") != model
    ):
        raise ValueError("contradictory_model")
    provider_name = _text(generation.get("provider_name"), "provider_name")
    chat_provider = chat.get("provider")
    if chat_provider is not None and chat_provider != provider_name:
        raise ValueError("contradictory_provider")
    catalog = _object(run.get("catalog_row"), "catalog")
    selected = _object(catalog.get("selected_endpoint"), "selected_endpoint")
    if model != run.get("resolved_model_id"):
        raise ValueError("contradictory_planned_model")
    if provider_name != selected.get("provider_name"):
        raise ValueError("contradictory_planned_provider")
    endpoint = _text(selected.get("tag"), "endpoint_tag")
    if endpoint != run.get("endpoint"):
        raise ValueError("contradictory_planned_endpoint")
    generation_input = _tokens(generation.get("native_tokens_prompt"), "generation_prompt_tokens")
    generation_output = _tokens(
        generation.get("native_tokens_completion"), "generation_completion_tokens"
    )
    generation_reasoning = _tokens(
        generation.get("native_tokens_reasoning", 0), "generation_reasoning_tokens"
    )
    if (generation_input, generation_output, generation_reasoning) != (
        input_tokens,
        output_tokens,
        reasoning_tokens,
    ):
        raise ValueError("contradictory_token_usage")
    cost = _number(generation.get("total_cost"), "generation_cost")
    chat_cost = usage.get("cost")
    if chat_cost is not None and not math.isclose(
        _number(chat_cost, "chat_cost"), cost, rel_tol=0, abs_tol=1e-9
    ):
        raise ValueError("contradictory_cost")
    provider_latency = _number(generation.get("latency"), "provider_latency")
    measured_latency = _elapsed(chat_source)
    latency_tolerance = max(1000.0, measured_latency * 0.25)
    if provider_latency > measured_latency + latency_tolerance:
        raise ValueError("contradictory_latency")
    generation_finish = _text(generation.get("finish_reason"), "generation_finish_reason")
    if generation_finish != finish_reason:
        raise ValueError("contradictory_finish_reason")
    cancelled = generation.get("cancelled", False)
    if not isinstance(cancelled, bool):
        raise ValueError("invalid_cancelled")
    provider_error = (
        cancelled
        or finish_reason in _ERROR_FINISH_REASONS
        or generation_finish in _ERROR_FINISH_REASONS
        or generation.get("error") not in (None, "", False)
    )
    if finish_reason != "stop" and not provider_error:
        provider_error = True
    return AttemptProjection(
        status="provider_error" if provider_error else "complete",
        started_at=chat_source.request_started_at,
        finished_at=chat_source.response_finished_at,
        response_text=response_text,
        provider_request_id=request_id,
        resolved_model_id=model,
        provider="openrouter",
        endpoint=endpoint,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        observed_cost_usd=cost,
        latency_ms=measured_latency,
        provider_latency_ms=provider_latency,
        error_type=(
            "provider_cancelled"
            if cancelled
            else f"provider_finish_{finish_reason}"
            if provider_error
            else ""
        ),
    )


def project_openrouter_evidence(
    envelope: ProviderEvidenceEnvelope,
    run: Mapping[str, Any],
) -> AttemptProjection:
    """Total OpenRouter projection: malformed evidence fails closed, never raises."""
    try:
        canonical = ProviderEvidenceEnvelope.from_dict(envelope.to_dict())
        return _project_openrouter(canonical, run)
    except (IndexError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        return _openrouter_unknown(envelope, run, str(exc) or type(exc).__name__)


def project_agent_cli_evidence(
    envelope: ProviderEvidenceEnvelope,
    run: Mapping[str, Any],
) -> AttemptProjection:
    """Total projection for exact subscription-cli process evidence."""
    provider = run.get("provider") if isinstance(run.get("provider"), str) else ""
    endpoint = run.get("endpoint") if isinstance(run.get("endpoint"), str) else ""
    try:
        envelope = ProviderEvidenceEnvelope.from_dict(envelope.to_dict())
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        return AttemptProjection(
            status="transport_error",
            started_at="",
            finished_at="",
            response_text="",
            provider_request_id="",
            resolved_model_id="",
            provider=provider,
            endpoint=endpoint,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            observed_cost_usd=0.0,
            latency_ms=0.0,
            provider_latency_ms=0.0,
            error_type=str(exc) or type(exc).__name__,
        )
    source = next(
        (source for source in envelope.sources if source.label == AGENT_CLI_SOURCE),
        None,
    )
    if source is None:
        return AttemptProjection(
            status="transport_error",
            started_at="",
            finished_at="",
            response_text="",
            provider_request_id="",
            resolved_model_id="",
            provider=provider,
            endpoint=endpoint,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            observed_cost_usd=0.0,
            latency_ms=0.0,
            provider_latency_ms=0.0,
            error_type="missing_process_evidence",
        )
    try:
        process = _object(source.payload(), "process")
        returncode = process.get("returncode")
        if isinstance(returncode, bool) or not isinstance(returncode, int):
            raise ValueError("invalid_returncode")
        command = _text(process.get("command"), "command")
        process_error = process.get("process_error", "")
        if not isinstance(process_error, str):
            raise ValueError("invalid_process_error")
        return AttemptProjection(
            status="complete" if returncode == 0 else "transport_error",
            started_at=source.request_started_at,
            finished_at=source.response_finished_at,
            response_text=_text(process.get("output_text", ""), "output", allow_empty=True),
            provider_request_id=_text(
                process.get("request_id", ""), "request_id", allow_empty=True
            ),
            resolved_model_id=(
                _text(process.get("resolved_model_id"), "model") if returncode == 0 else ""
            ),
            provider=command,
            endpoint=command,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            observed_cost_usd=0.0,
            latency_ms=_elapsed(source),
            provider_latency_ms=0.0,
            error_type=("" if returncode == 0 else process_error or f"{command}_exit_{returncode}"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return AttemptProjection(
            status="transport_error",
            started_at=source.request_started_at,
            finished_at=source.response_finished_at,
            response_text="",
            provider_request_id="",
            resolved_model_id="",
            provider=provider,
            endpoint=endpoint,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            observed_cost_usd=0.0,
            latency_ms=_elapsed(source),
            provider_latency_ms=0.0,
            error_type=str(exc) or type(exc).__name__,
        )


def projector_for_run(run: Mapping[str, Any]):
    surface = run.get("execution_surface")
    if surface == "direct_api":
        return project_openrouter_evidence
    if surface in {"codex_cli", "claude_cli"}:
        return project_agent_cli_evidence
    raise RuntimeError(f"unsupported execution surface {surface!r}")


def project_provider_evidence(
    envelope: ProviderEvidenceEnvelope,
    run: Mapping[str, Any],
) -> AttemptProjection:
    """Dispatch one retained envelope through its application-owned projector."""
    if envelope.adapter_id == "openrouter-direct-v2":
        return project_openrouter_evidence(envelope, run)
    if envelope.adapter_id == "agent-cli-v2":
        return project_agent_cli_evidence(envelope, run)
    return _openrouter_unknown(envelope, run, "unsupported_provider_evidence_adapter")


def validate_openrouter_run_policy(run: Mapping[str, Any]) -> None:
    """Validate exact authenticated routing/catalog proof in the adapter layer."""
    routing = _object(run.get("routing_policy"), "routing_policy")
    privacy = _object(run.get("privacy_policy"), "privacy_policy")
    generation = _object(run.get("generation"), "generation_policy")
    catalog = _object(run.get("catalog_row"), "provider_catalog")
    selected = _object(catalog.get("selected_endpoint"), "selected_endpoint")
    zdr_selected = _object(catalog.get("zdr_selected_endpoint"), "zdr_endpoint")
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
