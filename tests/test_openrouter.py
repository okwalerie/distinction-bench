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
    user = {
        "data": [
            {
                "id": "example/vision-model",
                "canonical_slug": "example/vision-model-20260808",
                "name": "Example Vision Model",
                "context_length": 123456,
            }
        ]
    }
    return model, zdr, user


def test_endpoint_selection_authenticates_and_intersects_exact_zdr_rows(monkeypatch):
    model, zdr, user = _catalogs()
    requests: list[urllib.request.Request] = []

    def urlopen(request, timeout):
        assert timeout == 30
        requests.append(request)
        payload = (
            zdr
            if request.full_url.endswith("/endpoints/zdr")
            else user
            if request.full_url.endswith("/models/user")
            else model
        )
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    selection = fetch_openrouter_endpoint(
        "example/vision-model", api_key="opaque-test-key", required_modality="image"
    )
    assert selection.endpoint_tag == "eligible"
    assert selection.provider_name == "Exact Provider"
    assert selection.model_id == "example/vision-model"
    assert selection.resolved_model_id == "example/vision-model-20260808"
    assert selection.routing_policy == {
        "order": ["eligible"],
        "allow_fallbacks": False,
        "data_collection": "deny",
        "zdr": True,
    }
    assert selection.catalog_row["authenticated"] is True
    assert selection.catalog_row["authenticated_user_model"] == user["data"][0]
    retrieval = selection.catalog_row["authenticated_user_models_retrieval"]
    assert retrieval["request_url"].endswith("/models/user")
    assert len(retrieval["raw_body_sha256"]) == 64
    execution = selection.execution_spec(cohort="sample", max_transport_attempts=1)
    assert execution.resolved_model_id == "example/vision-model-20260808"
    assert execution.pricing == {
        "prompt": 0.000001,
        "completion": 0.000002,
        "image": 0.000003,
    }
    assert len(requests) == 3
    assert all(
        request.get_header("Authorization") == "Bearer opaque-test-key" for request in requests
    )


@pytest.mark.parametrize(
    "user_rows",
    [
        [],
        [{"id": "example/vision-model", "canonical_slug": ""}],
        [{"id": "different/model", "canonical_slug": "different/model-1"}],
        [
            {"id": "example/vision-model", "canonical_slug": "example/vision-model-1"},
            {"id": "example/vision-model", "canonical_slug": "example/vision-model-2"},
        ],
    ],
)
def test_endpoint_selection_fails_closed_without_one_exact_canonical_user_row(
    monkeypatch, user_rows
):
    model, zdr, _user = _catalogs()

    def urlopen(request, timeout):
        assert timeout == 30
        payload = (
            zdr
            if request.full_url.endswith("/endpoints/zdr")
            else {"data": user_rows}
            if request.full_url.endswith("/models/user")
            else model
        )
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="user catalog"):
        fetch_openrouter_endpoint(
            "example/vision-model", api_key="opaque-test-key", required_modality="image"
        )


def test_endpoint_selection_fails_when_exact_model_endpoint_is_not_zdr(monkeypatch):
    model, _zdr, user = _catalogs()

    def urlopen(request, timeout):
        assert timeout == 30
        payload = (
            {"data": []}
            if request.full_url.endswith("/endpoints/zdr")
            else user
            if request.full_url.endswith("/models/user")
            else model
        )
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="no active exact ZDR"):
        fetch_openrouter_endpoint(
            "example/vision-model", api_key="opaque-test-key", required_modality="image"
        )


def test_endpoint_selection_requires_declared_input_modality(monkeypatch):
    model, zdr, user = _catalogs()
    model["data"]["architecture"]["input_modalities"] = ["text"]

    def urlopen(request, timeout):
        assert timeout == 30
        payload = (
            zdr
            if request.full_url.endswith("/endpoints/zdr")
            else user
            if request.full_url.endswith("/models/user")
            else model
        )
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="does not declare 'image' input"):
        fetch_openrouter_endpoint(
            "example/vision-model", api_key="opaque-test-key", required_modality="image"
        )


def test_multimodal_endpoint_requires_explicit_image_pricing(monkeypatch):
    model, zdr, user = _catalogs()
    model["data"]["endpoints"][1]["pricing"].pop("image")
    zdr["data"][0]["pricing"].pop("image")

    def urlopen(request, timeout):
        assert timeout == 30
        payload = (
            zdr
            if request.full_url.endswith("/endpoints/zdr")
            else user
            if request.full_url.endswith("/models/user")
            else model
        )
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


def test_projection_distinguishes_requested_alias_from_canonical_generation_model():
    evidence, run = _projection_evidence(
        chat_changes=lambda chat: chat.update(model="example/model"),
        generation_changes=lambda wrapper: wrapper["data"].update(model="example/model-20260808"),
    )
    run = replace(run, resolved_model_id="example/model-20260808")
    projection = project_openrouter_evidence(evidence, run.to_dict())
    assert projection.status == "complete"
    assert projection.resolved_model_id == "example/model-20260808"


def test_projection_rejects_alias_and_canonical_identity_substitutions_independently():
    bad_chat, run = _projection_evidence(
        chat_changes=lambda chat: chat.update(model="other/alias"),
        generation_changes=lambda wrapper: wrapper["data"].update(model="example/model-20260808"),
    )
    run = replace(run, resolved_model_id="example/model-20260808")
    assert project_openrouter_evidence(bad_chat, run.to_dict()).error_type == (
        "contradictory_requested_model"
    )

    bad_generation, run = _projection_evidence(
        generation_changes=lambda wrapper: wrapper["data"].update(model="other/canonical")
    )
    assert project_openrouter_evidence(bad_generation, run.to_dict()).error_type == (
        "contradictory_planned_model"
    )


def test_billed_http_error_uses_generation_id_header_for_exact_accounting(monkeypatch, tmp_path):
    task, run = _task_and_run()
    generation = {
        "data": {
            "id": "request-1",
            "model": "example/model",
            "provider_name": "Example Provider",
            "total_cost": 0.004,
            "native_tokens_prompt": 10,
            "native_tokens_completion": 2,
            "native_tokens_reasoning": 1,
            "latency": 0.0,
            "finish_reason": "error",
            "error": {"type": "provider_unavailable"},
        }
    }
    requests = 0

    def urlopen(request, timeout):
        nonlocal requests
        assert timeout == 30
        requests += 1
        if "/generation?" in request.full_url:
            return _Response(json.dumps(generation).encode())
        raise urllib.error.HTTPError(
            request.full_url,
            502,
            "provider unavailable",
            {"x-generation-id": "request-1", "set-cookie": "must-not-publish"},
            io.BytesIO(
                json.dumps({"error": {"metadata": {"error_type": "provider_unavailable"}}}).encode()
            ),
        )

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
    assert evidence.sources[0].response_headers == (("x-generation-id", "request-1"),)
    projection = project_openrouter_evidence(evidence, run.to_dict())
    assert projection.status == "provider_error"
    assert projection.provider_request_id == "request-1"
    assert projection.observed_cost_usd == pytest.approx(0.004)
    assert (projection.input_tokens, projection.output_tokens, projection.reasoning_tokens) == (
        10,
        2,
        1,
    )
    assert requests == 2


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
            lambda row: _set_nested(
                row,
                "choices",
                0,
                "error",
                {"metadata": {"error_type": "provider_unavailable"}},
            ),
            None,
            "provider_error",
            "provider_provider_unavailable",
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
            "provider_error",
            "provider_generation_failed",
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
            "contradictory_planned_model",
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
            "contradictory_requested_model",
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


def test_billed_chat_error_retains_exact_generation_accounting():
    evidence, run = _projection_evidence(
        chat_changes=lambda row: row.update({"error": {"type": "provider_busy"}})
    )
    projection = project_openrouter_evidence(evidence, run.to_dict())
    assert projection.status == "provider_error"
    assert projection.provider_request_id == "request-1"
    assert projection.resolved_model_id == "example/model"
    assert projection.input_tokens == 10
    assert projection.output_tokens == 2
    assert projection.reasoning_tokens == 1
    assert projection.observed_cost_usd == 0.001
    assert projection.provider_latency_ms == 10.0


def test_chat_error_without_generation_accounting_remains_unknown():
    evidence, run = _projection_evidence(
        chat_changes=lambda row: row.update({"error": {"type": "provider_busy"}})
    )
    evidence = replace(evidence, sources=evidence.sources[:1])
    projection = project_openrouter_evidence(evidence, run.to_dict())
    assert projection.status == "accounting_unknown"
    assert projection.observed_cost_usd == 0.0


def test_chat_body_and_generation_header_request_ids_must_agree():
    evidence, run = _projection_evidence()
    chat = replace(evidence.sources[0], response_headers=(("x-generation-id", "request-forged"),))
    projection = project_openrouter_evidence(
        replace(evidence, sources=(chat, evidence.sources[1])), run.to_dict()
    )
    assert projection.status == "accounting_unknown"
    assert projection.error_type == "contradictory_chat_request_id"


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_started_at", None),
        ("request_started_at", "not-a-timestamp"),
        ("response_finished_at", "not-a-timestamp"),
        ("response_finished_at", "2026-08-07T23:59:59+00:00"),
    ],
)
def test_projection_is_total_over_malformed_source_timing(field, value):
    evidence, run = _projection_evidence()
    forged_source = replace(evidence.sources[0], **{field: value})
    forged = replace(evidence, sources=(forged_source, evidence.sources[1]))
    projection = project_openrouter_evidence(forged, run.to_dict())
    assert projection.status == "accounting_unknown"
    assert projection.latency_ms == 0.0


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("data", "total_cost"), None),
        (("data", "total_cost"), "0.001"),
        (("data", "total_cost"), float("nan")),
        (("data", "total_cost"), float("inf")),
        (("data", "total_cost"), -0.001),
        (("data", "native_tokens_prompt"), None),
        (("data", "native_tokens_prompt"), "10"),
        (("data", "native_tokens_prompt"), float("nan")),
        (("data", "native_tokens_prompt"), -1),
        (("data", "latency"), None),
        (("data", "latency"), "10"),
        (("data", "latency"), float("nan")),
        (("data", "latency"), float("inf")),
        (("data", "latency"), -1.0),
    ],
)
def test_projection_is_total_over_invalid_accounting_scalars(path, value):
    evidence, run = _projection_evidence(
        generation_changes=lambda row: _set_nested(row, *path, value)
    )
    projection = project_openrouter_evidence(evidence, run.to_dict())
    assert projection.status == "accounting_unknown"


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
    assert projection.status == "accounting_unknown"


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
    assert source.response_headers == ()
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
