"""Provider-neutral execution contracts and immutable run planning."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from inspect_ai import Task
from inspect_ai.dataset import Sample

from lofbench.records import RunManifest, deterministic_id


def trial_id_for(run_id: str, abstract_form_id: str, dialect_id: str) -> str:
    return deterministic_id(
        "trial",
        {
            "run_id": run_id,
            "abstract_form_id": abstract_form_id,
            "dialect_id": dialect_id,
        },
    )


@dataclass(frozen=True)
class ExecutionSpec:
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
    catalog_retrieved_at: str = ""
    catalog_row: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExecutionRequest:
    run: RunManifest
    task: Task
    sample: Sample
    attempt: int
    log_dir: Path


@dataclass(frozen=True)
class ExecutionResult:
    response_text: str = ""
    resolved_model_id: str = ""
    endpoint: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    observed_cost_usd: float | None = None
    latency_ms: float = 0.0
    provider_request_id: str = ""
    transcript: dict[str, Any] | None = None
    transport_error: bool = False
    error_type: str = ""


class TrialExecutor(ABC):
    @abstractmethod
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError


class InMemoryExecutor(TrialExecutor):
    """Scripted adapter for budget, retry, and resume tests."""

    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = list(results)
        self.calls: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(request)
        if not self.results:
            raise RuntimeError("in-memory executor has no scripted result")
        return self.results.pop(0)


def plan_run(
    task: Task,
    *,
    suite_version: str,
    form_set: str,
    dialect_set: str,
    protocol_id: str,
    execution: ExecutionSpec,
) -> RunManifest:
    base = RunManifest.plan(
        suite_version=suite_version,
        form_set=form_set,
        dialect_set=dialect_set,
        protocol_id=protocol_id,
        requested_model_id=execution.requested_model_id,
        resolved_model_id=execution.resolved_model_id,
        execution_surface=execution.execution_surface,
        provider=execution.provider,
        endpoint=execution.endpoint,
        routing_policy=execution.routing_policy,
        privacy_policy=execution.privacy_policy,
        sdk_version=execution.sdk_version,
        reasoning=execution.reasoning,
        generation=execution.generation,
        billing_channel=execution.billing_channel,
        cohort=execution.cohort,
        max_transport_attempts=execution.max_transport_attempts,
        expected_trial_ids=(),
        catalog_retrieved_at=execution.catalog_retrieved_at,
        catalog_row=execution.catalog_row or {},
    )
    trial_ids = tuple(
        trial_id_for(
            base.run_id,
            sample.metadata["abstract_form_id"],
            sample.metadata["dialect_id"],
        )
        for sample in task.dataset.samples
    )
    return replace(base, expected_trial_ids=trial_ids)
