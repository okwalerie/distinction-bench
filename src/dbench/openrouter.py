"""Authenticated OpenRouter discovery and Inspect execution adapter."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from typing import Any

from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset

from lofbench.run_models import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionSpec,
    TrialExecutor,
)

_API_ROOT = "https://openrouter.ai/api/v1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _authenticated_json(url: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


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
            sdk_version=(
                f"inspect-ai={version('inspect-ai')};openai={version('openai')}"
            ),
            reasoning={"effort": "default"},
            generation={"temperature": 0, "max_tokens": 512, "max_retries": 0},
            billing_channel="openrouter-limited-key",
            cohort=cohort,
            max_transport_attempts=max_transport_attempts,
            catalog_retrieved_at=self.retrieved_at,
            catalog_row=self.catalog_row,
        )


def fetch_openrouter_endpoint(
    model_id: str,
    *,
    api_key: str,
    required_modality: str | None = None,
) -> EndpointSelection:
    """Select the cheapest exact endpoint proven present in the ZDR catalog."""
    quoted = urllib.parse.quote(model_id, safe="/")
    model_payload = _authenticated_json(f"{_API_ROOT}/models/{quoted}/endpoints", api_key)
    zdr_payload = _authenticated_json(f"{_API_ROOT}/endpoints/zdr", api_key)
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
            price = float(pricing["prompt"]) + float(pricing["completion"])
        except (TypeError, ValueError):
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


class OpenRouterInspectExecutor(TrialExecutor):
    """One-call Inspect adapter pinned to an admitted OpenRouter endpoint."""

    def __init__(self, *, api_key: str) -> None:
        if not api_key:
            raise ValueError("OpenRouter execution requires an API key")
        self.api_key = api_key

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        from inspect_ai import eval as inspect_eval

        if request.run.generation.get("max_retries") != 0:
            raise RuntimeError("direct-api runs must disable model and SDK retries")
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
                model_args={
                    "api_key": self.api_key,
                    "provider": request.run.routing_policy,
                    "max_retries": 0,
                },
                display="none",
                log_dir=str(request.log_dir),
                log_samples=True,
                retry_on_error=0,
                max_retries=0,
            )
        except Exception as exc:
            return ExecutionResult(
                transport_error=True,
                error_type=type(exc).__name__,
                latency_ms=(time.monotonic() - started) * 1000,
            )
        if len(logs) != 1 or logs[0].status != "success" or not logs[0].samples:
            return ExecutionResult(
                transport_error=True,
                error_type=logs[0].status if logs else "missing_log",
                latency_ms=(time.monotonic() - started) * 1000,
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
        generation: dict[str, Any] = {}
        if request_id and (cost is None or not provider):
            query = urllib.parse.urlencode({"id": request_id})
            try:
                generation = _authenticated_json(
                    f"{_API_ROOT}/generation?{query}",
                    self.api_key,
                ).get("data", {})
            except Exception:  # noqa: BLE001 - accounting fails closed downstream
                generation = {}
        cost = cost if cost is not None else generation.get("total_cost")
        provider = provider or generation.get("provider_name", "")
        expected_provider = request.run.catalog_row.get("selected_endpoint", {}).get(
            "provider_name"
        )
        observed_endpoint = request.run.endpoint if provider == expected_provider else provider
        return ExecutionResult(
            response_text=event.output.completion,
            resolved_model_id=event.output.model.removeprefix("openrouter/"),
            endpoint=observed_endpoint,
            input_tokens=(usage.input_tokens if usage else generation.get("native_tokens_prompt")),
            output_tokens=(
                usage.output_tokens if usage else generation.get("native_tokens_completion")
            ),
            reasoning_tokens=(
                (usage.reasoning_tokens or 0)
                if usage
                else generation.get("native_tokens_reasoning", 0)
            ),
            observed_cost_usd=float(cost) if cost is not None else None,
            latency_ms=sample.total_time * 1000,
            provider_request_id=request_id,
            transcript=sample.model_dump(mode="json", exclude={"attachments"}),
        )
