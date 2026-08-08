from __future__ import annotations

import base64

import pytest

from lofbench.provider_evidence import ProviderEvidenceSource


def test_http_evidence_round_trip_retains_malformed_body_status_headers_and_timing():
    raw = b"not-json\xff"
    source = ProviderEvidenceSource.capture_http(
        label="provider.lookup.response.v1",
        sequence=2,
        request_started_at="2026-08-08T00:00:00.000000+00:00",
        response_finished_at="2026-08-08T00:00:00.125000+00:00",
        request_method="GET",
        request_url="https://provider.invalid/generation?id=request-1",
        http_status=503,
        response_headers=(
            ("content-type", "application/json"),
            ("x-generation-id", "generation-1"),
            ("retry-after", "1"),
            ("x-request-id", "request-1"),
            ("authorization", "Bearer must-not-publish"),
            ("set-cookie", "session=must-not-publish"),
            ("x-api-key", "must-not-publish"),
        ),
        raw_body=raw,
    )

    assert source.raw_body_base64 == base64.b64encode(raw).decode("ascii")
    assert source.body_text == "not-json�"
    assert source.text_decoding == "invalid_utf8"
    assert source.json_parse_outcome == "invalid"
    assert source.response_headers == (
        ("content-type", "application/json"),
        ("x-generation-id", "generation-1"),
        ("x-request-id", "request-1"),
    )
    assert ProviderEvidenceSource.from_dict(source.to_dict()) == source


def test_http_evidence_rejects_a_forged_text_sibling():
    source = ProviderEvidenceSource.capture_http(
        label="provider.response.v1",
        sequence=1,
        request_started_at="2026-08-08T00:00:00+00:00",
        response_finished_at="2026-08-08T00:00:01+00:00",
        request_method="POST",
        request_url="https://provider.invalid/chat",
        http_status=200,
        response_headers=(),
        raw_body=b'{"id":"request-1"}',
    )
    forged = source.to_dict()
    forged["body_text"] = '{"id":"forged"}'
    with pytest.raises(RuntimeError, match="raw body"):
        ProviderEvidenceSource.from_dict(forged)


@pytest.mark.parametrize(
    "name",
    ["authorization", "set-cookie", "cookie", "proxy-authorization", "x-api-key", "x-unknown"],
)
def test_http_evidence_rejects_unapproved_published_response_headers(name):
    source = ProviderEvidenceSource.capture_http(
        label="provider.response.v1",
        sequence=1,
        request_started_at="2026-08-08T00:00:00+00:00",
        response_finished_at="2026-08-08T00:00:01+00:00",
        request_method="POST",
        request_url="https://provider.invalid/chat",
        http_status=200,
        response_headers=(),
        raw_body=b"{}",
    )
    forged = source.to_dict()
    forged["response_headers"] = [[name, "opaque-value"]]
    with pytest.raises(RuntimeError, match="response headers"):
        ProviderEvidenceSource.from_dict(forged)
