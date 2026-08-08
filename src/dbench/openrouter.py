"""Authenticated OpenRouter discovery and direct evidence-retaining adapter."""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from typing import Any

from dbench.provider_evidence import (
    OPENROUTER_CHAT_SOURCE,
    OPENROUTER_ERROR_SOURCE,
    OPENROUTER_GENERATION_SOURCE,
    ProviderEvidenceEnvelope,
    ProviderEvidenceSource,
)
from lofbench.protocols import get_protocol
from lofbench.run_models import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionSpec,
    TrialExecutor,
)

_API_ROOT = "https://openrouter.ai/api/v1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _authenticated_json(
    url: str,
    api_key: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    body = (
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        if payload is not None
        else None
    )
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        raw = response.read().decode("utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("provider response is not a json object")
    return value, raw


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

    def execution_spec(
        self,
        *,
        cohort: str,
        max_transport_attempts: int,
    ) -> ExecutionSpec:
        return ExecutionSpec(
            requested_model_id=self.model_id,
            resolved_model_id=self.model_id,
            execution_surface="direct_api",
            provider="openrouter",
            endpoint=self.endpoint_tag,
            routing_policy=self.routing_policy,
            privacy_policy={
                "data_collection": "deny",
                "zdr": True,
                "authenticated_zdr_catalog": True,
            },
            sdk_version=f"lofbench={version('lofbench')};transport=stdlib-urllib",
            reasoning={"effort": "default"},
            generation={"temperature": 0, "max_tokens": 512, "max_retries": 0},
            billing_channel="openrouter-limited-key",
            cohort=cohort,
            max_transport_attempts=max_transport_attempts,
            pricing=_normalized_pricing(
                self.pricing,
                image_required="image" in self.architecture.get("input_modalities", []),
            ),
            catalog_retrieved_at=self.retrieved_at,
            catalog_row=self.catalog_row,
        )


def _normalized_pricing(
    pricing: dict[str, str],
    *,
    image_required: bool,
) -> dict[str, float]:
    """Translate provider catalog pricing into the provider-neutral run contract."""
    try:
        prompt = float(pricing["prompt"])
        completion = float(pricing["completion"])
        image = float(pricing["image"]) if image_required else float(pricing.get("image", 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("endpoint has unknown prompt/completion/image pricing") from exc
    normalized = {"prompt": prompt, "completion": completion, "image": image}
    if any(not math.isfinite(value) or value < 0 for value in normalized.values()):
        raise RuntimeError("endpoint has invalid prompt/completion/image pricing")
    return normalized


def fetch_openrouter_endpoint(
    model_id: str,
    *,
    api_key: str,
    required_modality: str | None = None,
) -> EndpointSelection:
    """Select the cheapest exact endpoint proven present in the ZDR catalog."""
    quoted = urllib.parse.quote(model_id, safe="/")
    model_payload, _model_raw = _authenticated_json(
        f"{_API_ROOT}/models/{quoted}/endpoints", api_key
    )
    zdr_payload, _zdr_raw = _authenticated_json(f"{_API_ROOT}/endpoints/zdr", api_key)
    model = model_payload.get("data", {})
    if model.get("id") != model_id:
        raise RuntimeError(f"openrouter did not return exact requested model {model_id!r}")
    input_modalities = set(model.get("architecture", {}).get("input_modalities", []))
    if required_modality and required_modality not in input_modalities:
        raise RuntimeError(
            f"openrouter model {model_id!r} does not declare {required_modality!r} input"
        )
    zdr_rows = {
        (row.get("model_id"), row.get("tag"), row.get("provider_name")): row
        for row in zdr_payload.get("data", [])
    }
    candidates: list[tuple[float, str, dict[str, Any], dict[str, Any]]] = []
    for endpoint in model.get("endpoints", []):
        identity = (model_id, endpoint.get("tag"), endpoint.get("provider_name"))
        zdr_row = zdr_rows.get(identity)
        if zdr_row is None or endpoint.get("status") != 0 or zdr_row.get("status") != 0:
            continue
        supported = set(endpoint.get("supported_parameters", []))
        zdr_supported = set(zdr_row.get("supported_parameters", []))
        if not ({"response_format", "structured_outputs"} & supported & zdr_supported):
            continue
        pricing = endpoint.get("pricing", {})
        if pricing.get("prompt") is None or pricing.get("completion") is None:
            continue
        if pricing != zdr_row.get("pricing"):
            continue
        try:
            normalized = _normalized_pricing(
                pricing,
                image_required=required_modality == "image",
            )
            price = normalized["prompt"] + normalized["completion"]
        except RuntimeError:
            continue
        candidates.append((price, str(endpoint["tag"]), endpoint, zdr_row))
    if not candidates:
        raise RuntimeError(f"no active exact ZDR structured-output endpoint for {model_id}")
    _price, _tag, selected, zdr_selected = min(candidates, key=lambda item: (item[0], item[1]))
    retrieved_at = _now()
    return EndpointSelection(
        model_id=model_id,
        endpoint_tag=selected["tag"],
        provider_name=selected["provider_name"],
        pricing=dict(selected["pricing"]),
        supported_parameters=tuple(selected.get("supported_parameters", [])),
        architecture=dict(model.get("architecture", {})),
        retrieved_at=retrieved_at,
        catalog_row={
            "authenticated": True,
            "retrieved_at": retrieved_at,
            "model": model,
            "selected_endpoint": selected,
            "zdr_selected_endpoint": zdr_selected,
        },
    )


def _message_payload(request: ExecutionRequest) -> list[dict[str, Any]]:
    protocol = get_protocol(request.run.protocol_id)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": protocol.system_text}
    ]
    if isinstance(request.sample.input, str):
        messages.append({"role": "user", "content": request.sample.input})
        return messages
    for message in request.sample.input:
        content: list[dict[str, Any]] = []
        for item in message.content:
            if getattr(item, "type", "") == "text":
                content.append({"type": "text", "text": item.text})
            elif getattr(item, "type", "") == "image":
                content.append(
                    {"type": "image_url", "image_url": {"url": item.image}}
                )
            else:
                raise RuntimeError("unsupported public sample content")
        messages.append({"role": message.role, "content": content})
    return messages


class OpenRouterExecutor(TrialExecutor):
    """Direct one-call OpenRouter adapter retaining exact provider responses."""

    def __init__(self, *, api_key: str) -> None:
        if not api_key:
            raise ValueError("OpenRouter execution requires an API key")
        self.api_key = api_key

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.run.generation.get("max_retries") != 0:
            raise RuntimeError("direct-api runs must disable model and SDK retries")
        protocol = get_protocol(request.run.protocol_id)
        started_at = _now()
        response_finished_at = started_at
        sources: list[ProviderEvidenceSource] = []
        try:
            chat, chat_raw = _authenticated_json(
                f"{_API_ROOT}/chat/completions",
                self.api_key,
                method="POST",
                payload={
                    "model": request.run.requested_model_id,
                    "messages": _message_payload(request),
                    "provider": request.run.routing_policy,
                    "temperature": request.run.generation["temperature"],
                    "max_tokens": request.run.generation["max_tokens"],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": protocol.protocol_id,
                            "strict": True,
                            "schema": protocol.response_json_schema,
                        },
                    },
                },
            )
            response_finished_at = _now()
            sources.append(
                ProviderEvidenceSource(
                    label=OPENROUTER_CHAT_SOURCE,
                    payload_json=chat_raw,
                )
            )
            request_id = chat.get("id")
            if not isinstance(request_id, str) or not request_id:
                raise RuntimeError("chat response omitted provider request id")
            query = urllib.parse.urlencode({"id": request_id})
            _generation, generation_raw = _authenticated_json(
                f"{_API_ROOT}/generation?{query}", self.api_key
            )
            sources.append(
                ProviderEvidenceSource(
                    label=OPENROUTER_GENERATION_SOURCE,
                    payload_json=generation_raw,
                )
            )
        except Exception as exc:
            if not sources:
                response_finished_at = _now()
            sources.append(
                ProviderEvidenceSource(
                    label=OPENROUTER_ERROR_SOURCE,
                    payload_json=json.dumps(
                        {"error_type": type(exc).__name__},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
        return ExecutionResult(
            provider_evidence=ProviderEvidenceEnvelope(
                schema_version=1,
                adapter_id="openrouter-direct-v1",
                request_started_at=started_at,
                response_finished_at=response_finished_at,
                sources=tuple(sources),
            )
        )
