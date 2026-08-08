from __future__ import annotations

import io
import json
import urllib.request

import inspect_ai
import pytest
from test_runner import _task_and_run

from dbench.openrouter import OpenRouterInspectExecutor, fetch_openrouter_endpoint
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
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
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
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
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


def test_direct_executor_disables_inspect_and_sdk_retries(monkeypatch, tmp_path):
    task, run = _task_and_run()
    captured = {}

    def inspect_eval(*args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(inspect_ai, "eval", inspect_eval)
    result = OpenRouterInspectExecutor().execute(
        ExecutionRequest(
            run=run,
            task=task,
            sample=list(task.dataset.samples)[0],
            attempt=1,
            log_dir=tmp_path,
        )
    )
    assert result.transport_error is True
    assert captured["retry_on_error"] == 0
    assert captured["max_retries"] == 0
    assert captured["model_args"]["max_retries"] == 0
    assert captured["model_args"]["provider"] == run.routing_policy
