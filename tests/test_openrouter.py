from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from dataclasses import replace

import pytest
from inspect_ai import Task
from inspect_ai.dataset import MemoryDataset
from test_runner import _ledger, _task_and_run

from dbench.openrouter import OpenRouterExecutor, fetch_openrouter_endpoint
from dbench.provider_evidence import (
    OPENROUTER_CHAT_SOURCE,
    OPENROUTER_GENERATION_SOURCE,
    project_openrouter_evidence,
)
from lofbench.orchestration import RunOrchestrator
from lofbench.provider_evidence import ProviderEvidenceEnvelope, ProviderEvidenceSource
from lofbench.run_models import ExecutionRequest


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _BrokenResponse:
    status = 200
    headers = {"x-provider-trace": "trace-1"}

    def __init__(self):
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _size):
        self.reads += 1
        if self.reads == 1:
            return b'{"id":'
        raise OSError("response stream failed")


def _projection_evidence(
    *,
    chat_changes=None,
    generation_changes=None,
) -> tuple[ProviderEvidenceEnvelope, object]:
    _task, run = _task_and_run()
    chat = {
        "id": "request-1",
        "model": "example/model",
        "provider": "Example Provider",
        "choices": [{"message": {"content": '{"value":"marked"}'}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "completion_tokens_details": {"reasoning_tokens": 1},
            "cost": 0.001,
        },
    }
    generation = {
        "data": {
            "id": "request-1",
            "model": "example/model",
            "provider_name": "Example Provider",
            "native_tokens_prompt": 10,
            "native_tokens_completion": 2,
            "native_tokens_reasoning": 1,
            "total_cost": 0.001,
            "latency": 10.0,
            "finish_reason": "stop",
            "cancelled": False,
        }
    }
    if chat_changes is not None:
        chat_changes(chat)
    if generation_changes is not None:
        generation_changes(generation)
    sources = (
        ProviderEvidenceSource.capture_http(
            label=OPENROUTER_CHAT_SOURCE,
            sequence=1,
            request_started_at="2026-08-08T00:00:00.000000+00:00",
            response_finished_at="2026-08-08T00:00:00.020000+00:00",
            request_method="POST",
            request_url="https://openrouter.ai/api/v1/chat/completions",
            http_status=200,
            response_headers=(("content-type", "application/json"),),
            raw_body=json.dumps(chat).encode(),
        ),
        ProviderEvidenceSource.capture_http(
            label=OPENROUTER_GENERATION_SOURCE,
            sequence=1,
            request_started_at="2026-08-08T00:00:00.021000+00:00",
            response_finished_at="2026-08-08T00:00:00.025000+00:00",
            request_method="GET",
            request_url="https://openrouter.ai/api/v1/generation?id=request-1",
            http_status=200,
            response_headers=(("content-type", "application/json"),),
            raw_body=json.dumps(generation).encode(),
        ),
    )
    return ProviderEvidenceEnvelope(
        schema_version=2,
        adapter_id="openrouter-direct-v2",
        sources=sources,
    ), run


def _set_nested(root, *path_and_value):
    *path, value = path_and_value
    cursor = root
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value


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
        "provider": "Example Provider",
        "choices": [
            {
                "message": {"content": '{"value":"marked"}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "completion_tokens_details": {"reasoning_tokens": 1},
            "cost": 0.001,
        },
    }
    generation = {
        "data": {
            "id": "request-1",
            "model": "example/model",
            "provider_name": "Example Provider",
            "total_cost": 0.001,
            "native_tokens_prompt": 10,
            "native_tokens_completion": 2,
            "native_tokens_reasoning": 1,
            "latency": 10.0,
            "finish_reason": "stop",
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
    assert result.provider_evidence.sources[0].body_text == json.dumps(chat)
    assert result.provider_evidence.sources[1].body_text == json.dumps(generation)
    projection = project_openrouter_evidence(result.provider_evidence, run.to_dict())
    assert projection.status == "complete"
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


@pytest.mark.parametrize(
    ("chat_changes", "generation_changes", "expected_status", "expected_error"),
    [
        (
            lambda row: row.update({"error": {"type": "provider_busy"}}),
            None,
            "provider_error",
            "provider_provider_busy",
        ),
        (
            lambda row: _set_nested(row, "choices", 0, "finish_reason", "error"),
            lambda row: _set_nested(row, "data", "finish_reason", "error"),
            "provider_error",
            "provider_finish_error",
        ),
        (
            None,
            lambda row: _set_nested(row, "data", "cancelled", True),
            "provider_error",
            "provider_cancelled",
        ),
        (
            None,
            lambda row: row.update({"error": {"type": "generation_failed"}}),
            "accounting_unknown",
            "missing_generation_accounting",
        ),
        (
            None,
            lambda row: _set_nested(row, "data", "finish_reason", "length"),
            "accounting_unknown",
            "contradictory_finish_reason",
        ),
        (
            None,
            lambda row: _set_nested(row, "data", "native_tokens_prompt", 11),
            "accounting_unknown",
            "contradictory_token_usage",
        ),
        (
            None,
            lambda row: _set_nested(row, "data", "total_cost", 0.002),
            "accounting_unknown",
            "contradictory_cost",
        ),
        (
            None,
            lambda row: _set_nested(row, "data", "id", "request-forged"),
            "accounting_unknown",
            "contradictory_request_id",
        ),
        (
            None,
            lambda row: _set_nested(row, "data", "model", "forged/model"),
            "accounting_unknown",
            "contradictory_model",
        ),
        (
            None,
            lambda row: _set_nested(row, "data", "provider_name", "Other Provider"),
            "accounting_unknown",
            "contradictory_provider",
        ),
        (
            lambda row: row.update({"model": "forged/model"}),
            lambda row: _set_nested(row, "data", "model", "forged/model"),
            "accounting_unknown",
            "contradictory_planned_model",
        ),
        (
            lambda row: row.update({"provider": "Other Provider"}),
            lambda row: _set_nested(row, "data", "provider_name", "Other Provider"),
            "accounting_unknown",
            "contradictory_planned_provider",
        ),
        (
            None,
            lambda row: _set_nested(row, "data", "latency", 2000.0),
            "accounting_unknown",
            "contradictory_latency",
        ),
    ],
)
def test_projection_closes_all_cross_source_semantics(
    chat_changes, generation_changes, expected_status, expected_error
):
    evidence, run = _projection_evidence(
        chat_changes=chat_changes,
        generation_changes=generation_changes,
    )
    projection = project_openrouter_evidence(evidence, run.to_dict())
    assert projection.status == expected_status
    assert projection.error_type == expected_error


def test_projection_rejects_a_source_projection_that_contradicts_raw_bytes():
    evidence, run = _projection_evidence()
    forged = replace(
        evidence,
        sources=(
            replace(evidence.sources[0], body_text='{"id":"forged"}'),
            evidence.sources[1],
        ),
    )
    projection = project_openrouter_evidence(forged, run.to_dict())
    assert projection.status == "accounting_unknown"
    assert projection.error_type == ("provider evidence raw body contradicts its projections")


def test_direct_executor_projects_generation_lookup_error_from_retained_evidence(
    monkeypatch, tmp_path
):
    task, run = _task_and_run()
    chat = {
        "id": "request-1",
        "model": "example/model",
        "provider": "Example Provider",
        "choices": [
            {
                "message": {"content": '{"value":"marked"}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    calls = 0

    def urlopen(_request, timeout):
        nonlocal calls
        assert timeout == 30
        calls += 1
        if calls > 1:
            raise RuntimeError("generation lookup unavailable")
        return _Response(json.dumps(chat).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    result = OpenRouterExecutor(api_key="opaque-test-key", sleeper=lambda _delay: None).execute(
        ExecutionRequest(
            run=run,
            task=task,
            sample=list(task.dataset.samples)[0],
            attempt=1,
            log_dir=tmp_path,
        )
    )
    assert [source.label for source in result.provider_evidence.sources] == [
        OPENROUTER_CHAT_SOURCE,
        OPENROUTER_GENERATION_SOURCE,
        OPENROUTER_GENERATION_SOURCE,
        OPENROUTER_GENERATION_SOURCE,
    ]
    assert [source.sequence for source in result.provider_evidence.sources[1:]] == [1, 2, 3]
    assert all(
        source.transport_error == "RuntimeError" for source in result.provider_evidence.sources[1:]
    )
    projection = project_openrouter_evidence(result.provider_evidence, run.to_dict())
    assert projection.status == "accounting_unknown"
    assert projection.error_type == "missing_generation_accounting"


@pytest.mark.parametrize(
    ("response", "expected_status", "expected_outcome", "expected_text"),
    [
        ("http", 429, "parsed", '{"error":{"type":"rate_limit"}}'),
        ("malformed", 200, "invalid", "not-json"),
    ],
)
def test_direct_executor_retains_non_success_and_malformed_chat_bodies(
    monkeypatch, tmp_path, response, expected_status, expected_outcome, expected_text
):
    task, run = _task_and_run()

    def urlopen(request, timeout):
        assert timeout == 30
        if response == "http":
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "limited",
                {"retry-after": "1"},
                io.BytesIO(expected_text.encode()),
            )
        return _Response(b"not-json")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    evidence = (
        OpenRouterExecutor(api_key="opaque-test-key", sleeper=lambda _delay: None)
        .execute(
            ExecutionRequest(
                run=run,
                task=task,
                sample=list(task.dataset.samples)[0],
                attempt=1,
                log_dir=tmp_path,
            )
        )
        .provider_evidence
    )
    source = evidence.sources[0]
    assert source.http_status == expected_status
    assert source.json_parse_outcome == expected_outcome
    assert source.body_text == expected_text
    projection = project_openrouter_evidence(evidence, run.to_dict())
    assert projection.status == ("provider_error" if response == "http" else "accounting_unknown")


def test_direct_executor_retains_http_metadata_and_partial_bytes_on_read_failure(
    monkeypatch, tmp_path
):
    task, run = _task_and_run()
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda _request, timeout: _BrokenResponse() if timeout == 30 else None,
    )
    evidence = (
        OpenRouterExecutor(api_key="opaque-test-key", sleeper=lambda _delay: None)
        .execute(
            ExecutionRequest(
                run=run,
                task=task,
                sample=list(task.dataset.samples)[0],
                attempt=1,
                log_dir=tmp_path,
            )
        )
        .provider_evidence
    )
    source = evidence.sources[0]
    assert source.http_status == 200
    assert source.response_headers == (("x-provider-trace", "trace-1"),)
    assert source.raw_body_base64 == "eyJpZCI6"
    assert source.body_text == '{"id":'
    assert source.json_parse_outcome == "invalid"
    assert source.transport_error == "OSError"
    assert project_openrouter_evidence(evidence, run.to_dict()).status == "accounting_unknown"


def test_resume_recovers_billed_chat_accounting_without_resending_inference(monkeypatch, tmp_path):
    full_task, full_run = _task_and_run()
    sample = list(full_task.dataset.samples)[0]
    task = Task(
        dataset=MemoryDataset(samples=[sample]),
        solver=full_task.solver,
        scorer=full_task.scorer,
        config=full_task.config,
        metadata=full_task.metadata,
    )
    run = replace(full_run, expected_trial_ids=full_run.expected_trial_ids[:1])
    chat = {
        "id": "request-1",
        "model": "example/model",
        "provider": "Example Provider",
        "choices": [
            {
                "message": {"content": '{"value":"marked"}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "completion_tokens_details": {"reasoning_tokens": 1},
            "cost": 0.001,
        },
    }
    generation = {
        "data": {
            "id": "request-1",
            "model": "example/model",
            "provider_name": "Example Provider",
            "native_tokens_prompt": 10,
            "native_tokens_completion": 2,
            "native_tokens_reasoning": 1,
            "total_cost": 0.001,
            "latency": 10.0,
            "finish_reason": "stop",
        }
    }
    chat_requests = 0
    generation_requests = 0

    def urlopen(request, timeout):
        nonlocal chat_requests, generation_requests
        assert timeout == 30
        if "/generation?" not in request.full_url:
            chat_requests += 1
            return _Response(json.dumps(chat).encode())
        generation_requests += 1
        if generation_requests <= 3:
            raise TimeoutError("accounting lookup timed out")
        return _Response(json.dumps(generation).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    executor = OpenRouterExecutor(api_key="opaque-test-key", sleeper=lambda _delay: None)
    ledger = _ledger(tmp_path)
    orchestrator = RunOrchestrator(
        tmp_path / "run-state",
        executor,
        ledger=ledger,
        evidence_projector=project_openrouter_evidence,
        accounting_recoverer=executor.recover_accounting,
    )
    with pytest.raises(RuntimeError, match="accounting is unknown"):
        orchestrator.execute(run, task, approve_paid_run=True, max_spend_usd=30.0)
    assert ledger.totals()[0] == 0.0
    assert ledger.totals()[1] > 0.0
    completed = orchestrator.execute(run, task, approve_paid_run=True, max_spend_usd=30.0)
    assert completed.status == "complete"
    assert completed.attempts == 1
    assert chat_requests == 1
    assert generation_requests == 4
    assert ledger.totals() == pytest.approx((0.001, 0.0))
    call_rows = [
        json.loads(line) for line in (tmp_path / "run-state/calls.jsonl").read_text().splitlines()
    ]
    evidence_rows = [
        json.loads(line)
        for line in (tmp_path / "run-state/transcripts.jsonl").read_text().splitlines()
    ]
    assert len({row["call_id"] for row in call_rows}) == 1
    assert [row["status"] for row in call_rows] == ["accounting_unknown", "complete"]
    assert [row["revision"] for row in evidence_rows] == [0, 1]
