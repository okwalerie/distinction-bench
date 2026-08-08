"""Provider-neutral evidence contracts shared by execution and publication."""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

AttemptStatus = Literal[
    "complete",
    "provider_error",
    "transport_error",
    "accounting_unknown",
]

AUDIT_SAFE_RESPONSE_HEADERS = frozenset(
    {"content-type", "x-generation-id", "x-request-id", "x-openrouter-request-id"}
)


def sanitize_response_headers(
    rows: Any,
) -> tuple[tuple[str, str], ...]:
    """Retain only deterministic response metadata safe for publication."""
    sanitized: list[tuple[str, str]] = []
    try:
        for row in rows:
            if len(row) != 2 or not all(isinstance(item, str) for item in row):
                continue
            name, value = row
            normalized = name.lower()
            if normalized in AUDIT_SAFE_RESPONSE_HEADERS:
                sanitized.append((normalized, value))
    except TypeError:
        return ()
    return tuple(sanitized)


def validate_response_headers(rows: Any) -> tuple[tuple[str, str], ...]:
    """Reject publication data containing unapproved response metadata."""
    try:
        headers = tuple(tuple(row) for row in rows)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("provider evidence response headers are invalid") from exc
    if any(
        len(row) != 2
        or not all(isinstance(item, str) for item in row)
        or row[0] != row[0].lower()
        or row[0] not in AUDIT_SAFE_RESPONSE_HEADERS
        for row in headers
    ):
        raise RuntimeError("provider evidence response headers are invalid")
    return headers


@dataclass(frozen=True)
class ProviderEvidenceSource:
    """One exact immutable provider exchange or local process event."""

    label: str
    sequence: int
    request_started_at: str
    response_finished_at: str
    request_method: str
    request_url: str
    http_status: int | None
    response_headers: tuple[tuple[str, str], ...]
    raw_body_base64: str
    body_text: str
    text_decoding: Literal["utf-8", "invalid_utf8"]
    json_parse_outcome: Literal["parsed", "invalid", "empty"]
    transport_error: str = ""
    transport_error_message: str = ""

    @staticmethod
    def _body_projection(raw_body: bytes) -> tuple[str, str, str]:
        if not raw_body:
            return "", "utf-8", "empty"
        try:
            body_text = raw_body.decode("utf-8")
            text_decoding = "utf-8"
        except UnicodeDecodeError:
            body_text = raw_body.decode("utf-8", errors="replace")
            text_decoding = "invalid_utf8"
        try:
            json.loads(body_text)
            json_outcome = "parsed"
        except (json.JSONDecodeError, ValueError):
            json_outcome = "invalid"
        return body_text, text_decoding, json_outcome

    @classmethod
    def capture_http(
        cls,
        *,
        label: str,
        sequence: int,
        request_started_at: str,
        response_finished_at: str,
        request_method: str,
        request_url: str,
        http_status: int | None,
        response_headers: tuple[tuple[str, str], ...],
        raw_body: bytes,
        transport_error: str = "",
        transport_error_message: str = "",
    ) -> ProviderEvidenceSource:
        body_text, text_decoding, json_outcome = cls._body_projection(raw_body)
        return cls(
            label=label,
            sequence=sequence,
            request_started_at=request_started_at,
            response_finished_at=response_finished_at,
            request_method=request_method,
            request_url=request_url,
            http_status=http_status,
            response_headers=sanitize_response_headers(response_headers),
            raw_body_base64=base64.b64encode(raw_body).decode("ascii"),
            body_text=body_text,
            text_decoding=text_decoding,
            json_parse_outcome=json_outcome,
            transport_error=transport_error,
            transport_error_message=transport_error_message,
        )

    @classmethod
    def capture_event(
        cls,
        *,
        label: str,
        request_started_at: str,
        response_finished_at: str,
        operation: str,
        payload: Mapping[str, Any],
    ) -> ProviderEvidenceSource:
        raw = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        return cls.capture_http(
            label=label,
            sequence=1,
            request_started_at=request_started_at,
            response_finished_at=response_finished_at,
            request_method="PROCESS",
            request_url=operation,
            http_status=None,
            response_headers=(),
            raw_body=raw,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "sequence": self.sequence,
            "request_started_at": self.request_started_at,
            "response_finished_at": self.response_finished_at,
            "request_method": self.request_method,
            "request_url": self.request_url,
            "http_status": self.http_status,
            "response_headers": [list(row) for row in self.response_headers],
            "raw_body_base64": self.raw_body_base64,
            "body_text": self.body_text,
            "text_decoding": self.text_decoding,
            "json_parse_outcome": self.json_parse_outcome,
            "transport_error": self.transport_error,
            "transport_error_message": self.transport_error_message,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProviderEvidenceSource:
        expected = {
            "label",
            "sequence",
            "request_started_at",
            "response_finished_at",
            "request_method",
            "request_url",
            "http_status",
            "response_headers",
            "raw_body_base64",
            "body_text",
            "text_decoding",
            "json_parse_outcome",
            "transport_error",
            "transport_error_message",
        }
        if set(value) != expected:
            raise RuntimeError("provider evidence source schema is invalid")
        if not all(
            isinstance(value[field], str)
            for field in (
                "label",
                "request_started_at",
                "response_finished_at",
                "request_method",
                "request_url",
                "raw_body_base64",
                "body_text",
                "text_decoding",
                "json_parse_outcome",
                "transport_error",
                "transport_error_message",
            )
        ):
            raise RuntimeError("provider evidence source values are invalid")
        if isinstance(value["sequence"], bool) or not isinstance(value["sequence"], int):
            raise RuntimeError("provider evidence source sequence is invalid")
        status = value["http_status"]
        if status is not None and (isinstance(status, bool) or not isinstance(status, int)):
            raise RuntimeError("provider evidence http status is invalid")
        try:
            raw_body = base64.b64decode(value["raw_body_base64"], validate=True)
            headers = validate_response_headers(value["response_headers"])
        except (ValueError, TypeError) as exc:
            raise RuntimeError("provider evidence raw body or headers are invalid") from exc
        body_text, text_decoding, json_outcome = cls._body_projection(raw_body)
        if (
            body_text != value["body_text"]
            or text_decoding != value["text_decoding"]
            or json_outcome != value["json_parse_outcome"]
        ):
            raise RuntimeError("provider evidence raw body contradicts its projections")
        try:
            started = datetime.fromisoformat(value["request_started_at"])
            finished = datetime.fromisoformat(value["response_finished_at"])
        except ValueError as exc:
            raise RuntimeError("provider evidence source timing is invalid") from exc
        if started.tzinfo is None or finished.tzinfo is None or finished < started:
            raise RuntimeError("provider evidence source timing is invalid")
        return cls(
            label=value["label"],
            sequence=value["sequence"],
            request_started_at=value["request_started_at"],
            response_finished_at=value["response_finished_at"],
            request_method=value["request_method"],
            request_url=value["request_url"],
            http_status=status,
            response_headers=headers,
            raw_body_base64=value["raw_body_base64"],
            body_text=body_text,
            text_decoding=text_decoding,
            json_parse_outcome=json_outcome,
            transport_error=value["transport_error"],
            transport_error_message=value["transport_error_message"],
        )

    def payload(self) -> Any:
        if self.json_parse_outcome != "parsed":
            return None
        return json.loads(self.body_text)


@dataclass(frozen=True)
class ProviderEvidenceEnvelope:
    """Exact immutable evidence retained by one execution-adapter call."""

    schema_version: int
    adapter_id: str
    sources: tuple[ProviderEvidenceSource, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_id": self.adapter_id,
            "sources": [source.to_dict() for source in self.sources],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProviderEvidenceEnvelope:
        expected = {
            "schema_version",
            "adapter_id",
            "sources",
        }
        if set(value) != expected or value["schema_version"] != 2:
            raise RuntimeError("provider evidence envelope schema is invalid")
        if not isinstance(value["adapter_id"], str) or not isinstance(value["sources"], list):
            raise RuntimeError("provider evidence envelope values are invalid")
        sources = tuple(ProviderEvidenceSource.from_dict(row) for row in value["sources"])
        identities = [(source.label, source.sequence) for source in sources]
        if len(identities) != len(set(identities)):
            raise RuntimeError("provider evidence source identities are not unique")
        return cls(
            schema_version=2,
            adapter_id=value["adapter_id"],
            sources=sources,
        )


@dataclass(frozen=True)
class AttemptProjection:
    """The sole normalized interpretation of a provider evidence envelope."""

    status: AttemptStatus
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
    provider_latency_ms: float
    error_type: str


EvidenceProjector = Callable[[ProviderEvidenceEnvelope, Mapping[str, Any]], AttemptProjection]
AccountingRecoverer = Callable[
    [ProviderEvidenceEnvelope, Mapping[str, Any]], ProviderEvidenceEnvelope
]


def fail_closed_projection(
    evidence: ProviderEvidenceEnvelope,
    run: Mapping[str, Any],
    error_type: str,
) -> AttemptProjection:
    """Return a durable unknown-accounting projection after an adapter defect."""
    source = evidence.sources[0] if evidence.sources else None
    latency = 0.0
    if source is not None:
        try:
            latency = (
                datetime.fromisoformat(source.response_finished_at)
                - datetime.fromisoformat(source.request_started_at)
            ).total_seconds() * 1000
            if not math.isfinite(latency) or latency < 0:
                latency = 0.0
        except (TypeError, ValueError, OverflowError):
            latency = 0.0
    return AttemptProjection(
        status="accounting_unknown",
        started_at=(
            source.request_started_at
            if source is not None and isinstance(source.request_started_at, str)
            else ""
        ),
        finished_at=(
            source.response_finished_at
            if source is not None and isinstance(source.response_finished_at, str)
            else ""
        ),
        response_text="",
        provider_request_id="",
        resolved_model_id="",
        provider=str(run.get("provider", "")),
        endpoint=str(run.get("endpoint", "")),
        input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        observed_cost_usd=0.0,
        latency_ms=latency,
        provider_latency_ms=0.0,
        error_type=error_type,
    )
