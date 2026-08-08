"""Authenticated OpenRouter discovery and direct evidence-retaining adapter."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from typing import Any

from dbench.provider_evidence import (
    OPENROUTER_CHAT_SOURCE,
    OPENROUTER_GENERATION_SOURCE,
    project_openrouter_evidence,
)
from lofbench.protocols import get_protocol
from lofbench.provider_evidence import ProviderEvidenceEnvelope, ProviderEvidenceSource
from lofbench.run_models import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionSpec,
    TrialExecutor,
)

_API_ROOT = "https://openrouter.ai/api/v1"
_GENERATION_LOOKUP_ATTEMPTS = 3
_GENERATION_BACKOFF_SECONDS = (0.1, 0.25)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _headers(response: Any) -> tuple[tuple[str, str], ...]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ()
    rows = headers.raw_items() if hasattr(headers, "raw_items") else headers.items()
    return tuple((str(name), str(value)) for name, value in rows)


def _generation_id(source: ProviderEvidenceSource) -> str:
    payload = source.payload()
    body_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(body_id, str) or not body_id:
        body_id = None
    header_ids = [value for name, value in source.response_headers if name == "x-generation-id"]
    if len(set(header_ids)) > 1:
        return ""
    header_id = header_ids[0] if header_ids else None
    if body_id is not None and header_id is not None and body_id != header_id:
        return ""
    return body_id or header_id or ""


def _read_response(response: Any) -> tuple[bytes, Exception | None]:
    chunks: list[bytes] = []
    try:
        while chunk := response.read(65536):
            chunks.append(chunk)
    except Exception as exc:
        return b"".join(chunks), exc
    return b"".join(chunks), None


def _request_exchange(
    url: str,
    api_key: str,
    *,
    label: str,
    sequence: int,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> ProviderEvidenceSource:
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
    started_at = _now()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            status = getattr(response, "status", None)
            if status is None and hasattr(response, "getcode"):
                status = response.getcode()
            status = 200 if status is None else int(status)
            headers = _headers(response)
            raw, read_error = _read_response(response)
        return ProviderEvidenceSource.capture_http(
            label=label,
            sequence=sequence,
            request_started_at=started_at,
            response_finished_at=_now(),
            request_method=method,
            request_url=url,
            http_status=status,
            response_headers=headers,
            raw_body=raw,
            transport_error=(type(read_error).__name__ if read_error else ""),
            transport_error_message=(str(read_error) if read_error else ""),
        )
    except urllib.error.HTTPError as exc:
        raw, read_error = _read_response(exc)
        return ProviderEvidenceSource.capture_http(
            label=label,
            sequence=sequence,
            request_started_at=started_at,
            response_finished_at=_now(),
            request_method=method,
            request_url=url,
            http_status=exc.code,
            response_headers=_headers(exc),
            raw_body=raw,
            transport_error=(type(read_error).__name__ if read_error else ""),
            transport_error_message=(str(read_error) if read_error else ""),
        )
    except Exception as exc:
        return ProviderEvidenceSource.capture_http(
            label=label,
            sequence=sequence,
            request_started_at=started_at,
            response_finished_at=_now(),
            request_method=method,
            request_url=url,
            http_status=None,
            response_headers=(),
            raw_body=b"",
            transport_error=type(exc).__name__,
            transport_error_message=str(exc),
        )


def _authenticated_json(url: str, api_key: str) -> tuple[dict[str, Any], str]:
    source = _request_exchange(
        url,
        api_key,
        label="openrouter.catalog.response.v1",
        sequence=1,
    )
    value = source.payload()
    if (
        source.http_status is None
        or not 200 <= source.http_status < 300
        or not isinstance(value, dict)
    ):
        raise RuntimeError("provider catalog response is not a successful json object")
    return value, source.body_text


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
    messages: list[dict[str, Any]] = [{"role": "system", "content": protocol.system_text}]
    if isinstance(request.sample.input, str):
        messages.append({"role": "user", "content": request.sample.input})
        return messages
    for message in request.sample.input:
        content: list[dict[str, Any]] = []
        for item in message.content:
            if getattr(item, "type", "") == "text":
                content.append({"type": "text", "text": item.text})
            elif getattr(item, "type", "") == "image":
                content.append({"type": "image_url", "image_url": {"url": item.image}})
            else:
                raise RuntimeError("unsupported public sample content")
        messages.append({"role": message.role, "content": content})
    return messages


class OpenRouterExecutor(TrialExecutor):
    """Direct one-call OpenRouter adapter retaining exact provider responses."""

    def __init__(self, *, api_key: str, sleeper: Callable[[float], None] = time.sleep) -> None:
        if not api_key:
            raise ValueError("OpenRouter execution requires an API key")
        self.api_key = api_key
        self.sleeper = sleeper

    def _generation_sources(
        self,
        request_id: str,
        *,
        first_sequence: int = 1,
    ) -> tuple[ProviderEvidenceSource, ...]:
        query = urllib.parse.urlencode({"id": request_id})
        rows: list[ProviderEvidenceSource] = []
        for offset in range(_GENERATION_LOOKUP_ATTEMPTS):
            source = _request_exchange(
                f"{_API_ROOT}/generation?{query}",
                self.api_key,
                label=OPENROUTER_GENERATION_SOURCE,
                sequence=first_sequence + offset,
            )
            rows.append(source)
            payload = source.payload()
            if (
                source.http_status is not None
                and 200 <= source.http_status < 300
                and isinstance(payload, dict)
                and isinstance(payload.get("data"), dict)
                and "error" not in payload
            ):
                break
            if offset < _GENERATION_LOOKUP_ATTEMPTS - 1:
                try:
                    self.sleeper(_GENERATION_BACKOFF_SECONDS[offset])
                except Exception:
                    break
        return tuple(rows)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.run.generation.get("max_retries") != 0:
            raise RuntimeError("direct-api runs must disable model and SDK retries")
        protocol = get_protocol(request.run.protocol_id)
        chat_source = _request_exchange(
            f"{_API_ROOT}/chat/completions",
            self.api_key,
            label=OPENROUTER_CHAT_SOURCE,
            sequence=1,
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
        sources = [chat_source]
        request_id = _generation_id(chat_source)
        if request_id:
            sources.extend(self._generation_sources(request_id))
        return ExecutionResult(
            provider_evidence=ProviderEvidenceEnvelope(
                schema_version=2,
                adapter_id="openrouter-direct-v2",
                sources=tuple(sources),
            )
        )

    def recover_accounting(
        self,
        evidence: ProviderEvidenceEnvelope,
        _run: Mapping[str, Any],
    ) -> ProviderEvidenceEnvelope:
        chat = next(
            (source for source in evidence.sources if source.label == OPENROUTER_CHAT_SOURCE),
            None,
        )
        request_id = _generation_id(chat) if chat is not None else ""
        if not request_id:
            return evidence
        prior = [
            source for source in evidence.sources if source.label == OPENROUTER_GENERATION_SOURCE
        ]
        recovered = self._generation_sources(
            request_id,
            first_sequence=max((source.sequence for source in prior), default=0) + 1,
        )
        return ProviderEvidenceEnvelope(
            schema_version=2,
            adapter_id=evidence.adapter_id,
            sources=(*evidence.sources, *recovered),
        )

    @staticmethod
    def project(
        evidence: ProviderEvidenceEnvelope,
        run: Mapping[str, Any],
    ):
        return project_openrouter_evidence(evidence, run)
