from __future__ import annotations

import io
import json
import urllib.request

import pytest
from test_runner import _task_and_run

from dbench.openrouter import OpenRouterExecutor, fetch_openrouter_endpoint
from dbench.provider_evidence import (
    OPENROUTER_CHAT_SOURCE,
    OPENROUTER_ERROR_SOURCE,
    OPENROUTER_GENERATION_SOURCE,
    project_provider_evidence,
)
from lofbench.run_models import ExecutionRequest


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _catalogs():
    model = {
        "data": {
            "id": "example/vision-model",
            "architecture": {"input_modalities": ["text", "image"]},
            "endpoints": [
                {
                    "tag": "not-zdr",
                    "provider_name": "Cheap But Ineligible",
                    "status": 0,
                    "supported_parameters": ["response_format"],
                    "pricing": {"prompt": "0.00000001", "completion": "0.00000001"},
                },
                {
                    "tag": "eligible",
                    "provider_name": "Exact Provider",
                    "status": 0,
                    "supported_parameters": ["response_format"],
                    "pricing": {
                        "prompt": "0.000001",
                        "completion": "0.000002",
                        "image": "0.000003",
                    },
                },
                {
                    "tag": "inactive",
                    "provider_name": "Inactive Provider",
                    "status": -1,
                    "supported_parameters": ["structured_outputs"],
                    "pricing": {"prompt": "0.0000001", "completion": "0.0000001"},
                },
            ],
        }
    }
    zdr = {
        "data": [
            {
                "model_id": "example/vision-model",
                "tag": "eligible",
                "provider_name": "Exact Provider",
                "status": 0,
                "supported_parameters": ["response_format"],
                "pricing": {
                    "prompt": "0.000001",
                    "completion": "0.000002",
                    "image": "0.000003",
                },
            },
            {
                "model_id": "different/model",
                "tag": "not-zdr",
                "provider_name": "Cheap But Ineligible",
                "status": 0,
                "supported_parameters": ["response_format"],
                "pricing": {"prompt": "0.00000001", "completion": "0.00000001"},
            },
            {
                "model_id": "example/vision-model",
                "tag": "inactive",
                "provider_name": "Inactive Provider",
                "status": 0,
                "supported_parameters": ["structured_outputs"],
                "pricing": {"prompt": "0.0000001", "completion": "0.0000001"},
            },
        ]
    }
    return model, zdr


def test_endpoint_selection_authenticates_and_intersects_exact_zdr_rows(monkeypatch):
    model, zdr = _catalogs()
    requests: list[urllib.request.Request] = []

    def urlopen(request, timeout):
        assert timeout == 30
        requests.append(request)
        payload = zdr if request.full_url.endswith("/endpoints/zdr") else model
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    selection = fetch_openrouter_endpoint(
        "example/vision-model", api_key="opaque-test-key", required_modality="image"
    )
    assert selection.endpoint_tag == "eligible"
    assert selection.provider_name == "Exact Provider"
    assert selection.routing_policy == {
        "order": ["eligible"],
        "allow_fallbacks": False,
        "data_collection": "deny",
        "zdr": True,
    }
    assert selection.catalog_row["authenticated"] is True
    assert selection.execution_spec(cohort="sample", max_transport_attempts=1).pricing == {
        "prompt": 0.000001,
        "completion": 0.000002,
        "image": 0.000003,
    }
    assert len(requests) == 2
    assert all(
        request.get_header("Authorization") == "Bearer opaque-test-key" for request in requests
    )


def test_endpoint_selection_fails_when_exact_model_endpoint_is_not_zdr(monkeypatch):
    model, _zdr = _catalogs()

    def urlopen(request, timeout):
        assert timeout == 30
        payload = {"data": []} if request.full_url.endswith("/endpoints/zdr") else model
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="no active exact ZDR"):
        fetch_openrouter_endpoint(
            "example/vision-model", api_key="opaque-test-key", required_modality="image"
        )


def test_endpoint_selection_requires_declared_input_modality(monkeypatch):
    model, zdr = _catalogs()
    model["data"]["architecture"]["input_modalities"] = ["text"]

    def urlopen(request, timeout):
        assert timeout == 30
        payload = zdr if request.full_url.endswith("/endpoints/zdr") else model
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="does not declare 'image' input"):
        fetch_openrouter_endpoint(
            "example/vision-model", api_key="opaque-test-key", required_modality="image"
        )


def test_multimodal_endpoint_requires_explicit_image_pricing(monkeypatch):
    model, zdr = _catalogs()
    model["data"]["endpoints"][1]["pricing"].pop("image")
    zdr["data"][0]["pricing"].pop("image")

    def urlopen(request, timeout):
        assert timeout == 30
        payload = zdr if request.full_url.endswith("/endpoints/zdr") else model
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="no active exact ZDR"):
        fetch_openrouter_endpoint(
            "example/vision-model", api_key="opaque-test-key", required_modality="image"
        )


def test_direct_executor_retains_exact_chat_and_generation_evidence(monkeypatch, tmp_path):
    task, run = _task_and_run()
    requests: list[urllib.request.Request] = []

    chat = {
        "id": "request-1",
        "model": "example/model",
        "choices": [{"message": {"content": '{"value":"marked"}'}}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "completion_tokens_details": {"reasoning_tokens": 1},
        },
    }
    generation = {
        "data": {
            "id": "request-1",
            "model": "example/model",
            "provider_name": "Example Provider",
            "total_cost": 0.001,
        }
    }

    def urlopen(request, timeout):
        assert timeout == 30
        requests.append(request)
        payload = generation if "/generation?" in request.full_url else chat
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    result = OpenRouterExecutor(api_key="opaque-test-key").execute(
        ExecutionRequest(
            run=run,
            task=task,
            sample=list(task.dataset.samples)[0],
            attempt=1,
            log_dir=tmp_path,
        )
    )
    labels = {source.label for source in result.provider_evidence.sources}
    assert labels == {OPENROUTER_CHAT_SOURCE, OPENROUTER_GENERATION_SOURCE}
    assert result.provider_evidence.sources[0].payload_json == json.dumps(chat)
    assert result.provider_evidence.sources[1].payload_json == json.dumps(generation)
    projection = project_provider_evidence(result.provider_evidence, run.to_dict())
    assert projection.response_text == '{"value":"marked"}'
    assert projection.provider_request_id == "request-1"
    assert projection.input_tokens == 10
    assert projection.output_tokens == 2
    assert projection.reasoning_tokens == 1
    assert projection.observed_cost_usd == 0.001
    assert len(requests) == 2
    request_payload = json.loads(requests[0].data)
    assert request_payload["provider"] == run.routing_policy
    assert request_payload["model"] == run.requested_model_id
    assert "max_retries" not in request_payload


def test_direct_executor_projects_generation_lookup_error_from_retained_evidence(
    monkeypatch, tmp_path
):
    task, run = _task_and_run()
    chat = {
        "id": "request-1",
        "model": "example/model",
        "choices": [{"message": {"content": '{"value":"marked"}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    calls = 0

    def urlopen(_request, timeout):
        nonlocal calls
        assert timeout == 30
        calls += 1
        if calls == 2:
            raise RuntimeError("generation lookup unavailable")
        return _Response(json.dumps(chat).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    result = OpenRouterExecutor(api_key="opaque-test-key").execute(
        ExecutionRequest(
            run=run,
            task=task,
            sample=list(task.dataset.samples)[0],
            attempt=1,
            log_dir=tmp_path,
        )
    )
    assert {source.label for source in result.provider_evidence.sources} == {
        OPENROUTER_CHAT_SOURCE,
        OPENROUTER_ERROR_SOURCE,
    }
    projection = project_provider_evidence(result.provider_evidence, run.to_dict())
    assert projection.status == "accounting_unknown"
    assert projection.error_type == "RuntimeError"
