"""Build the public, bundle-only static benchmark gallery."""

from __future__ import annotations

import html
import json
import math
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pyarrow as pa
import pyarrow.parquet as pq

from lofbench.protocols import ProtocolSpec
from lofbench.publication import scan_publication
from lofbench.release_bundle import PublicationView
from lofbench.renderers.pipeline.spec import DialectSpec
from lofbench.suites import LoadedSuite

SAFE_DOWNLOADS = (
    "suite.json",
    "protocols.json",
    "human-trial.schema.json",
    "profiles.parquet",
    "effects.parquet",
)
PUBLIC_PROFILE_SCHEMA = pa.schema(
    [
        ("execution_surface", pa.string()),
        ("requested_model_id", pa.string()),
        ("resolved_model_id", pa.string()),
        ("protocol_id", pa.string()),
        ("reasoning", pa.string()),
        ("competence", pa.float64()),
        ("text_competence", pa.float64()),
        ("spatial_competence", pa.float64()),
        ("within_family_invariance", pa.float64()),
        ("cross_family_text_invariance", pa.float64()),
        ("cross_family_spatial_invariance", pa.float64()),
        ("invalid_output_rate", pa.float64()),
        ("coverage", pa.float64()),
        ("observed_trials", pa.int64()),
        ("expected_trials", pa.int64()),
        ("mean_latency_ms", pa.float64()),
        ("input_tokens", pa.int64()),
        ("output_tokens", pa.int64()),
        ("reasoning_tokens", pa.int64()),
        ("cost_usd", pa.float64()),
        ("dialect_accuracy", pa.string()),
        ("family_accuracy", pa.string()),
    ]
)
PUBLIC_EFFECT_SCHEMA = pa.schema(
    [
        ("execution_surface", pa.string()),
        ("requested_model_id", pa.string()),
        ("resolved_model_id", pa.string()),
        ("protocol_id", pa.string()),
        ("reasoning", pa.string()),
        ("family", pa.string()),
        ("archetype", pa.string()),
        ("plain_dialect_id", pa.string()),
        ("treatment_dialect_id", pa.string()),
        ("signed_paired_effect", pa.float64()),
        ("bootstrap_low", pa.float64()),
        ("bootstrap_high", pa.float64()),
        ("mcnemar_b", pa.int64()),
        ("mcnemar_c", pa.int64()),
        ("mcnemar_exact_p", pa.float64()),
        ("paired_forms", pa.int64()),
        ("coverage", pa.float64()),
    ]
)
_VERBATIM_DOWNLOADS = (
    "suite.json",
    "protocols.json",
    "human-trial.schema.json",
)
_ROOT_FILES = {
    "index.html",
    "forms.html",
    "atlas.html",
    "runs.html",
    "presentation.html",
    "human.html",
    "downloads.html",
    "CNAME",
}
_FORBIDDEN_PATH_NAMES = {
    "release.json",
    "runs.jsonl",
    "trials.parquet",
    "calls.parquet",
    "transcripts.jsonl",
    "request-started.jsonl",
    "ledger.jsonl",
}
_FORBIDDEN_HTML_MARKERS = (
    "/releases/download/",
    ".tar.gz",
    ".zip",
    "release.json",
    "runs.jsonl",
    "trials.parquet",
    "calls.parquet",
    "transcripts.jsonl",
    "request-started.jsonl",
    "ledger.jsonl",
    "exact endpoint",
    "catalog_row",
    "routing_policy",
    "provider_evidence",
    "provider_request_id",
    "response_text",
    "prompt_hash",
    "completion_evidence_sha256",
    "call_id",
    "trial_id",
    "run_id",
)
_OPAQUE_EXECUTION_ID = re.compile(rb"(?:run|trial|call)_[0-9a-f]{16,}")
_EXEMPLAR_DIFFICULTY = "2. medium"
_SUPPORTED_URL_ATTRIBUTES = frozenset({"href", "src"})
_UNSUPPORTED_URL_ATTRIBUTES = frozenset(
    {
        "action",
        "archive",
        "background",
        "cite",
        "classid",
        "codebase",
        "data",
        "dynsrc",
        "formaction",
        "icon",
        "imagesrcset",
        "longdesc",
        "lowsrc",
        "manifest",
        "ping",
        "poster",
        "profile",
        "srcdoc",
        "srcset",
        "usemap",
    }
)

_CSS = """
:root{color-scheme:light;--ink:#171713;--muted:#68685f;--line:#d7d6c9;--paper:#f7f6ee;
--accent:#5d3fc0}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);
font:16px/1.52 system-ui,sans-serif}main,header,footer{max-width:1120px;margin:auto;padding:1.2rem}
nav{display:flex;gap:1rem;flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:1rem}
a{color:var(--accent)}h1{font-size:clamp(2rem,6vw,4.5rem);line-height:1;margin:.8em 0 .25em}
h2{margin-top:2.2rem}.lede{max-width:70ch;font-size:1.2rem}.grid{display:grid;
grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem}
.card{border:1px solid var(--line);background:#fff;padding:1rem;border-radius:.35rem}
.metric{font-size:2rem;font-variant-numeric:tabular-nums}.muted{color:var(--muted)}
table{border-collapse:collapse;width:100%;background:#fff}th,td{padding:.5rem;
border:1px solid var(--line);text-align:left;vertical-align:top}
pre{white-space:pre-wrap;overflow-wrap:anywhere}.stimulus{min-height:12rem;display:grid;
place-items:center;background:#fff;border:1px solid var(--line);padding:1rem}
.stimulus img{max-width:100%;max-height:18rem}.tabs button{padding:.5rem 1rem;margin-right:.3rem}
[hidden]{display:none!important}.notice{border-left:.35rem solid #c08900;padding:.7rem 1rem;
background:#fff8df}code{font-size:.88em}.answer button{padding:.7rem 1rem;margin:.2rem}
.dialect-cells details{margin:.5rem 0}.exemplar .stimulus{min-height:9rem;max-height:18rem;
overflow:auto}.exemplar h3{margin-bottom:.25rem}
.chart-group{margin:1.5rem 0}.chart-figure{margin:1.5rem 0;padding:1rem;background:#fff;
border:1px solid var(--line);border-radius:.35rem;overflow-x:auto}.chart-figure svg{display:block;
width:100%;min-width:680px;height:auto}.chart-track{fill:#e6e5dd}.chart-competence{fill:#d55e00}
.chart-validity{fill:#0072b2}.chart-resource{fill:#5d3fc0}.chart-axis{stroke:#68685f;stroke-width:1}
.chart-label{fill:#171713;font:14px system-ui,sans-serif}.chart-value{fill:#171713;
font:bold 14px system-ui,sans-serif}.chart-values{font-variant-numeric:tabular-nums;margin-top:1rem}
.chart-values caption{text-align:left;font-weight:700;padding:.5rem 0}
footer{margin-top:4rem;border-top:1px solid var(--line);color:var(--muted)}
@media(max-width:720px){.chart-figure{padding:.5rem}.chart-figure svg{min-width:640px}}
@media print{body{background:#fff}.chart-figure{break-inside:avoid;border-color:#777}
nav{display:none}}
"""


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _nav(prefix: str = "") -> str:
    links = (
        ("index.html", "what is tested"),
        ("forms.html", "forms + protocols"),
        ("atlas.html", "dialect atlas"),
        ("runs.html", "models + runs"),
        ("presentation.html", "presentation"),
        ("human.html", "human pilot"),
        ("downloads.html", "downloads + citation"),
    )
    return (
        "<nav>"
        + "".join(f'<a href="{prefix}{href}">{_e(label)}</a>' for href, label in links)
        + "</nav>"
    )


def _page(title: str, body: str, *, prefix: str = "") -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; connect-src 'none'; form-action 'none'\">"
        f"<title>{_e(title)} · distinction benchmark</title><style>{_CSS}</style></head><body>"
        f"<header>{_nav(prefix)}</header><main>{body}</main>"
        "<footer>distinction benchmark · code: mit · suite and site content: cc by 4.0"
        "</footer></body></html>"
    )


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


@dataclass(frozen=True)
class _ChartProfile:
    execution_surface: str
    resolved_model_id: str
    protocol_id: str
    reasoning: str
    competence: float
    validity: float
    observed_trials: int
    expected_trials: int
    mean_latency_seconds: Decimal
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal

    @property
    def output_tokens_per_observation(self) -> Decimal | None:
        if self.observed_trials == 0:
            return None
        return _decimal_divide(Decimal(self.output_tokens), Decimal(self.observed_trials))


@dataclass(frozen=True)
class _ChartScope:
    model_count: int
    protocol_count: int
    profile_count: int
    observation_count: int
    sample_n: int | None = None
    sample_dialect_modality: str | None = None

    @property
    def is_matching_sample(self) -> bool:
        return self.sample_n is not None and self.sample_dialect_modality is not None


def _decimal_divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return numerator / denominator


def _decimal_multiply(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return left * right


def _decimal_sum(values: list[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return sum(values, Decimal("0"))


def _chart_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"published profile has invalid {field}")
    return value


def _chart_number(
    row: Mapping[str, Any],
    field: str,
    *,
    maximum: float | None = None,
) -> float:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"published profile has invalid {field}")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0 or (maximum is not None and parsed > maximum):
        raise RuntimeError(f"published profile has invalid {field}")
    return 0.0 if parsed == 0 else parsed


def _chart_count(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"published profile has invalid {field}")
    return value


def _chart_decimal(row: Mapping[str, Any], field: str) -> Decimal:
    value = row.get(field)
    parsed = _chart_number(row, field)
    return Decimal("0") if parsed == 0 else Decimal(str(value))


def _chart_projection(rows: tuple[Mapping[str, Any], ...]) -> tuple[_ChartProfile, ...]:
    projected = []
    for row in rows:
        observed = _chart_count(row, "observed_trials")
        expected = _chart_count(row, "expected_trials")
        _chart_count(row, "reasoning_tokens")
        if observed > expected:
            raise RuntimeError("published profile has observed_trials above expected_trials")
        projected.append(
            _ChartProfile(
                execution_surface=_chart_text(row, "execution_surface"),
                resolved_model_id=_chart_text(row, "resolved_model_id"),
                protocol_id=_chart_text(row, "protocol_id"),
                reasoning=_chart_text(row, "reasoning"),
                competence=_chart_number(row, "competence", maximum=1),
                validity=1 - _chart_number(row, "invalid_output_rate", maximum=1),
                observed_trials=observed,
                expected_trials=expected,
                mean_latency_seconds=_decimal_divide(
                    _chart_decimal(row, "mean_latency_ms"), Decimal("1000")
                ),
                input_tokens=_chart_count(row, "input_tokens"),
                output_tokens=_chart_count(row, "output_tokens"),
                cost_usd=_chart_decimal(row, "cost_usd"),
            )
        )
    return tuple(
        sorted(
            projected,
            key=lambda row: (
                row.execution_surface,
                row.resolved_model_id,
                row.protocol_id,
                row.reasoning,
            ),
        )
    )


def _chart_scope(
    publication: PublicationView,
    profiles: tuple[_ChartProfile, ...],
) -> _ChartScope:
    models = {profile.resolved_model_id for profile in profiles}
    protocols = [profile.protocol_id for profile in profiles]
    scope = _ChartScope(
        model_count=len(models),
        protocol_count=len(set(protocols)),
        profile_count=len(profiles),
        observation_count=sum(profile.observed_trials for profile in profiles),
    )
    contract = publication.sample_contract
    if not isinstance(contract, Mapping) or not profiles:
        return scope
    contract_protocols = contract.get("protocol_ids")
    sample_n = contract.get("trials_per_run")
    dialect_id = contract.get("dialect_id")
    execution_surface = contract.get("execution_surface")
    dialect = publication.suite.specs.get(dialect_id) if isinstance(dialect_id, str) else None
    if (
        not isinstance(contract_protocols, list)
        or not contract_protocols
        or any(not isinstance(value, str) or not value for value in contract_protocols)
        or isinstance(sample_n, bool)
        or not isinstance(sample_n, int)
        or sample_n <= 0
        or dialect is None
        or not isinstance(execution_surface, str)
        or not execution_surface
        or len(models) != 1
        or len(contract_protocols) != len(profiles)
        or sorted(contract_protocols) != sorted(protocols)
        or any(
            profile.observed_trials != sample_n
            or profile.expected_trials != sample_n
            or profile.execution_surface != execution_surface
            for profile in profiles
        )
    ):
        return scope
    return _ChartScope(
        model_count=scope.model_count,
        protocol_count=scope.protocol_count,
        profile_count=scope.profile_count,
        observation_count=scope.observation_count,
        sample_n=sample_n,
        sample_dialect_modality="text" if dialect.modality == "text" else "spatial",
    )


def _chart_empty_state() -> str:
    return "<p class=notice>no admitted aggregate profiles; charts are not available.</p>"


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def _svg_bar(*, x: float, y: float, width: float, css_class: str) -> str:
    return (
        f'<rect class=chart-track x="{x:.1f}" y="{y:.1f}" width="500.0" height="18" rx="2"/>'
        f'<rect class="{css_class}" x="{x:.1f}" y="{y:.1f}" '
        f'width="{width:.1f}" height="18" rx="2"/>'
    )


def _outcome_figure(profiles: tuple[_ChartProfile, ...], *, id_prefix: str) -> str:
    height = 92 + 62 * len(profiles)
    rows = []
    table_rows = []
    for index, profile in enumerate(profiles):
        y = 66 + index * 62
        label = f"{profile.protocol_id} · n={profile.observed_trials}"
        rows.append(
            f'<text class=chart-label x="10" y="{y + 14}">{_e(label)}</text>'
            + _svg_bar(
                x=300,
                y=y,
                width=500 * profile.competence,
                css_class="chart-competence",
            )
            + f'<text class=chart-value x="810" y="{y + 14}">'
            f"{profile.competence:.0%}</text>"
        )
        table_rows.append(
            "<tr>"
            f"<td><code>{_e(profile.protocol_id)}</code></td>"
            f"<td>{_e(profile.resolved_model_id)}</td>"
            f"<td>{profile.competence:.1%}</td>"
            f"<td>{profile.observed_trials}</td><td>{profile.expected_trials}</td></tr>"
        )
    title_id = f"{id_prefix}-outcome-title"
    desc_id = f"{id_prefix}-outcome-desc"
    return (
        '<figure class=chart-figure data-chart="outcome-by-protocol">'
        "<h3>outcome by protocol</h3>"
        f'<svg role="img" aria-labelledby="{title_id} {desc_id}" '
        f'viewBox="0 0 900 {height}">'
        f'<title id="{title_id}">competence by protocol</title>'
        f'<desc id="{desc_id}">horizontal bars show competence, with percentages and '
        "observation counts written directly on every row.</desc>"
        '<text class=chart-label x="300" y="32">0%</text>'
        '<text class=chart-label x="775" y="32">100%</text>' + "".join(rows) + "</svg>"
        "<table class=chart-values><caption>exact outcome values</caption><thead><tr>"
        "<th>protocol</th><th>model</th><th>competence</th><th>observed n</th>"
        f"<th>expected n</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table>"
        "</figure>"
    )


def _validity_figure(profiles: tuple[_ChartProfile, ...], *, id_prefix: str) -> str:
    height = 106 + 92 * len(profiles)
    rows = []
    table_rows = []
    for index, profile in enumerate(profiles):
        y = 68 + index * 92
        rows.append(
            f'<text class=chart-label x="10" y="{y + 13}">'
            f"{_e(profile.protocol_id)} · n={profile.observed_trials}</text>"
            + _svg_bar(x=300, y=y, width=500 * profile.validity, css_class="chart-validity")
            + f'<text class=chart-value x="810" y="{y + 14}">validity '
            f"{profile.validity:.0%}</text>"
            + _svg_bar(
                x=300,
                y=y + 28,
                width=500 * profile.competence,
                css_class="chart-competence",
            )
            + f'<text class=chart-value x="810" y="{y + 42}">correctness '
            f"{profile.competence:.0%}</text>"
        )
        table_rows.append(
            "<tr>"
            f"<td><code>{_e(profile.protocol_id)}</code></td>"
            f"<td>{profile.validity:.1%}</td><td>{profile.competence:.1%}</td>"
            f"<td>{profile.observed_trials}</td></tr>"
        )
    title_id = f"{id_prefix}-validity-title"
    desc_id = f"{id_prefix}-validity-desc"
    return (
        '<figure class=chart-figure data-chart="valid-versus-correct">'
        "<h3>valid output versus correct result</h3>"
        f'<svg role="img" aria-labelledby="{title_id} {desc_id}" '
        f'viewBox="0 0 1040 {height}">'
        f'<title id="{title_id}">validity and correctness by protocol</title>'
        f'<desc id="{desc_id}">paired, directly labelled bars distinguish the rate of '
        "schema-valid output from competence for each protocol.</desc>"
        '<text class=chart-label x="300" y="32">0%</text>'
        '<text class=chart-label x="775" y="32">100%</text>' + "".join(rows) + "</svg>"
        "<table class=chart-values><caption>exact validity and correctness values</caption>"
        "<thead><tr><th>protocol</th><th>valid output</th><th>correct result</th>"
        f"<th>observed n</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table>"
        "</figure>"
    )


def _resource_figure(profiles: tuple[_ChartProfile, ...], *, id_prefix: str) -> str:
    costs = [_decimal_multiply(profile.cost_usd, Decimal("100")) for profile in profiles]
    latencies = [profile.mean_latency_seconds for profile in profiles]
    outputs = [profile.output_tokens_per_observation for profile in profiles]
    output_values = [value for value in outputs if value is not None]
    maxima = (max(costs), max(latencies), max(output_values, default=Decimal("0")))
    panels = (
        ("cost (cents)", costs, maxima[0], lambda value: f"{value:.4f}¢"),
        ("mean latency (seconds)", latencies, maxima[1], lambda value: f"{value:.3f}s"),
        (
            "output tokens / observation",
            outputs,
            maxima[2],
            lambda value: "not available" if value is None else f"{value:.1f}",
        ),
    )
    panel_markup = []
    for panel_index, (heading, values, maximum, formatter) in enumerate(panels):
        x = 280 + panel_index * 300
        panel_markup.append(f'<text class=chart-value x="{x}" y="34">{_e(heading)}</text>')
        panel_markup.append(f'<line class=chart-axis x1="{x}" y1="48" x2="{x + 205}" y2="48"/>')
        for row_index, value in enumerate(values):
            y = 74 + row_index * 58
            width = (
                Decimal("0")
                if value is None or maximum == 0
                else _decimal_multiply(_decimal_divide(value, maximum), Decimal("190"))
            )
            panel_markup.append(
                f'<rect class=chart-track x="{x:.1f}" y="{y:.1f}" '
                'width="190.0" height="18" rx="2"/>'
                f'<rect class=chart-resource x="{x:.1f}" y="{y:.1f}" '
                f'width="{width:.1f}" height="18" rx="2"/>'
                f'<text class=chart-label x="{x}" y="{y + 38}">{_e(formatter(value))}</text>'
            )
    labels = "".join(
        f'<text class=chart-label x="10" y="{88 + index * 58}">'
        f"{_e(profile.protocol_id)} · n={profile.observed_trials}</text>"
        for index, profile in enumerate(profiles)
    )
    table_rows = []
    for profile in profiles:
        output_per_observation = profile.output_tokens_per_observation
        output_display = (
            "not available"
            if output_per_observation is None
            else _decimal_text(output_per_observation)
        )
        table_rows.append(
            "<tr>"
            f"<td><code>{_e(profile.protocol_id)}</code></td>"
            f"<td>{_decimal_text(profile.cost_usd)}</td>"
            f"<td>{_decimal_text(profile.mean_latency_seconds)}</td>"
            f"<td>{output_display}</td>"
            f"<td>{profile.output_tokens}</td>"
            f"<td>{profile.observed_trials}</td></tr>"
        )
    total_cost = _decimal_sum([profile.cost_usd for profile in profiles])
    total_observations = sum(profile.observed_trials for profile in profiles)
    total_input = sum(profile.input_tokens for profile in profiles)
    total_output = sum(profile.output_tokens for profile in profiles)
    height = 118 + 58 * len(profiles)
    title_id = f"{id_prefix}-resource-title"
    desc_id = f"{id_prefix}-resource-desc"
    return (
        '<figure class=chart-figure data-chart="resource-footprint">'
        "<h3>resource footprint</h3>"
        "<p>profile-row totals: "
        f"<strong>{total_observations} observations</strong>; "
        f"<strong>${_decimal_text(total_cost)}</strong>; "
        f"<strong>{total_input:,} input tokens</strong>; "
        f"<strong>{total_output:,} output tokens</strong>.</p>"
        f'<svg role="img" aria-labelledby="{title_id} {desc_id}" '
        f'viewBox="0 0 1180 {height}">'
        f'<title id="{title_id}">resource footprint by protocol</title>'
        f'<desc id="{desc_id}">three independently scaled panels show aggregate cost in '
        "cents, mean latency in seconds, and output tokens per observation.</desc>"
        + labels
        + "".join(panel_markup)
        + "</svg>"
        "<table class=chart-values><caption>exact resource values</caption><thead><tr>"
        "<th>protocol</th><th>cost usd</th><th>mean latency seconds</th>"
        "<th>output tokens / observation</th><th>output tokens</th><th>observed n</th>"
        "</tr></thead>"
        f"<tbody>{''.join(table_rows)}</tbody></table></figure>"
    )


def _sample_comparison_copy(
    profiles: tuple[_ChartProfile, ...],
    scope: _ChartScope,
) -> str:
    if not scope.is_matching_sample:
        return ""
    by_protocol = {profile.protocol_id: profile for profile in profiles}
    pairs = (
        ("reduce-infer-v1", "reduce-taught-v1", "the two reduce protocols"),
        ("transcribe-infer-v1", "transcribe-taught-v1", "the two transcription protocols"),
    )
    equal_pairs = [
        label
        for left, right, label in pairs
        if left in by_protocol
        and right in by_protocol
        and by_protocol[left].competence == by_protocol[right].competence
    ]
    if not equal_pairs:
        return ""
    observed = " and ".join(f"{label} observed the same result" for label in equal_pairs)
    return (
        f"<p>{observed}. this descriptive equality does not show that teaching had no effect; "
        "the sample is too small for a causal conclusion.</p>"
    )


def _counted(count: int, noun: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {noun}{suffix}"


def _chart_scope_notice(scope: _ChartScope) -> str:
    if scope.is_matching_sample:
        return (
            "<p class=notice><strong>smoke test:</strong> "
            f"{scope.model_count} model, 1 {_e(scope.sample_dialect_modality)} dialect, "
            f"{scope.protocol_count} protocols, and n={scope.sample_n} per protocol. this "
            "sample cannot estimate dialect sensitivity, invariance, or controlled effects."
            "</p>"
        )
    return (
        "<p class=notice><strong>aggregate scope:</strong> "
        f"{_counted(scope.model_count, 'resolved model')}, "
        f"{_counted(scope.protocol_count, 'protocol')}, "
        f"{_counted(scope.observation_count, 'observation')} across "
        f"{_counted(scope.profile_count, 'profile')}. "
        "these charts are descriptive; interpret uncertainty from the release design.</p>"
    )


def _chart_group(
    profiles: tuple[_ChartProfile, ...],
    scope: _ChartScope,
    *,
    id_prefix: str,
    outcome_only: bool = False,
) -> str:
    if not profiles:
        return (
            f'<section class=chart-group data-chart-group="{_e(id_prefix)}">'
            f"{_chart_empty_state()}</section>"
        )
    figures = _outcome_figure(profiles, id_prefix=id_prefix)
    if not outcome_only:
        figures += _validity_figure(profiles, id_prefix=id_prefix)
        figures += _resource_figure(profiles, id_prefix=id_prefix)
    return (
        f'<section class=chart-group data-chart-group="{_e(id_prefix)}">'
        f"{_chart_scope_notice(scope)}{figures}</section>"
    )


def site_tree_checksums(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_site_tree(expected: Path, rebuilt: Path) -> None:
    """Require exact path and byte equality for a regenerated sealed site."""
    if site_tree_checksums(expected) != site_tree_checksums(rebuilt):
        raise RuntimeError("rebuilt site tree does not match the sealed bundle site")


class _LocalReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []
        self.unsupported_attributes: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            normalized = name.casefold()
            if normalized in _SUPPORTED_URL_ATTRIBUTES:
                if value is not None:
                    self.references.append(value)
                continue
            if (
                normalized in _UNSUPPORTED_URL_ATTRIBUTES
                or normalized.endswith(":href")
                or normalized.endswith(":src")
            ):
                self.unsupported_attributes.append(name)


def _spatial_assets(publication: PublicationView) -> dict[str, str]:
    cells = [cell for cell in publication.suite.cells if cell["modality"] != "text"]
    assets = {f"assets/{cell['asset_path']}": cell["model_payload_sha256"] for cell in cells}
    if len(assets) != len(cells) or any(
        not path.startswith("assets/stimuli/image/")
        or ".." in Path(path).parts
        or Path(path).is_absolute()
        for path in assets
    ):
        raise RuntimeError("frozen spatial cells do not define unique safe image assets")
    return assets


def _expected_site_files(publication: PublicationView) -> set[str]:
    dialects = {f"dialects/{dialect_id}.html" for dialect_id in publication.suite.specs}
    downloads = {f"downloads/{name}" for name in SAFE_DOWNLOADS}
    return _ROOT_FILES | dialects | downloads | set(_spatial_assets(publication))


def _expected_site_directories(expected_files: set[str]) -> set[str]:
    directories: set[str] = set()
    for relative in expected_files:
        parent = Path(relative).parent
        while parent != Path("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _verify_local_references(root: Path, html_paths: list[Path]) -> None:
    for path in html_paths:
        parser = _LocalReferenceParser()
        parser.feed(path.read_text())
        if parser.unsupported_attributes:
            raise RuntimeError(
                f"unsupported url-bearing attribute in {path.name}: "
                f"{parser.unsupported_attributes[0]}"
            )
        for reference in parser.references:
            if reference != reference.strip() or any(ord(char) < 32 for char in reference):
                raise RuntimeError(f"unsafe site reference in {path.name}: {reference}")
            decoded = reference
            for _depth in range(8):
                parsed = urlsplit(decoded)
                if parsed.scheme or parsed.netloc:
                    raise RuntimeError(f"external site reference in {path.name}: {reference}")
                next_value = unquote(decoded)
                if next_value == decoded:
                    break
                decoded = next_value
            else:
                raise RuntimeError(f"over-encoded site reference in {path.name}: {reference}")
            parsed = urlsplit(decoded)
            if parsed.scheme or parsed.netloc:
                raise RuntimeError(f"external site reference in {path.name}: {reference}")
            local = unquote(parsed.path)
            if not local:
                continue
            if "\\" in local or local.startswith("/"):
                raise RuntimeError(f"unsafe local site reference in {path.name}: {reference}")
            target = (path.parent / local).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError as exc:
                raise RuntimeError(
                    f"local site reference escapes output in {path.name}: {reference}"
                ) from exc
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                raise RuntimeError(f"broken local site reference in {path.name}: {reference}")


def _aggregate_table(
    rows: tuple[Mapping[str, Any], ...],
    schema: pa.Schema,
) -> pa.Table:
    projected = [{field.name: row.get(field.name) for field in schema} for row in rows]
    return pa.Table.from_pylist(projected, schema=schema)


def _public_aggregate_tables(publication: PublicationView) -> dict[str, pa.Table]:
    return {
        "profiles.parquet": _aggregate_table(publication.profiles, PUBLIC_PROFILE_SCHEMA),
        "effects.parquet": _aggregate_table(publication.effects, PUBLIC_EFFECT_SCHEMA),
    }


def _private_execution_values(publication: PublicationView) -> set[bytes]:
    run_values = {
        value for run in publication.runs for value in (run.run_id, run.endpoint) if value
    }
    trial_values = {
        str(row[field])
        for row in publication.trials
        for field in (
            "trial_id",
            "run_id",
            "prompt_hash",
            "response_text",
            "completion_evidence_sha256",
        )
        if row.get(field)
    }
    return {value.encode() for value in run_values | trial_values if len(value) >= 8}


def _verify_public_aggregate_downloads(publication: PublicationView, root: Path) -> None:
    private_values = _private_execution_values(publication)
    for name, expected in _public_aggregate_tables(publication).items():
        actual = pq.read_table(root / "downloads" / name)
        if not actual.schema.equals(expected.schema, check_metadata=True):
            raise RuntimeError(f"public aggregate schema is not exact: {name}")
        if not actual.equals(expected):
            raise RuntimeError(f"public aggregate values do not match validated rows: {name}")
        for field in actual.schema:
            if not (pa.types.is_string(field.type) or pa.types.is_large_string(field.type)):
                continue
            for value in actual[field.name].to_pylist():
                if value is None:
                    continue
                payload = value.encode()
                if _OPAQUE_EXECUTION_ID.search(payload) or any(
                    private in payload for private in private_values
                ):
                    raise RuntimeError(f"private execution detail appears in {name}")


def _verify_forbidden_content(publication: PublicationView, html_paths: list[Path]) -> None:
    private_values = _private_execution_values(publication)
    for path in html_paths:
        payload = path.read_bytes()
        unescaped = html.unescape(payload.decode()).encode()
        lowered = unescaped.lower()
        marker = next(
            (value for value in _FORBIDDEN_HTML_MARKERS if value.encode() in lowered), None
        )
        if marker is not None:
            raise RuntimeError(f"forbidden public output marker in {path.name}: {marker}")
        if _OPAQUE_EXECUTION_ID.search(unescaped) or any(
            value in unescaped for value in private_values
        ):
            raise RuntimeError(f"private execution detail appears in {path.name}")


def verify_public_site(publication: PublicationView, root: Path) -> None:
    """Require the exact public projection, resolvable links, and no private evidence."""
    if not root.is_dir():
        raise RuntimeError("public site output is not a directory")
    expected_files = _expected_site_files(publication)
    expected_directories = _expected_site_directories(expected_files)
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise RuntimeError(f"public site contains a symlink: {relative}")
        if path.is_file():
            actual_files.add(relative)
        elif path.is_dir():
            actual_directories.add(relative)
        else:
            raise RuntimeError(f"public site contains a special file: {relative}")
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        unexpected = sorted(actual_files - expected_files)
        raise RuntimeError(
            f"public site file set is not exact: missing={missing}, unexpected={unexpected}"
        )
    if actual_directories != expected_directories:
        missing = sorted(expected_directories - actual_directories)
        unexpected = sorted(actual_directories - expected_directories)
        raise RuntimeError(
            f"public site directory set is not exact: missing={missing}, unexpected={unexpected}"
        )
    if any(Path(relative).name in _FORBIDDEN_PATH_NAMES for relative in actual_files):
        raise RuntimeError("public site contains a forbidden evidence artifact")
    for name in _VERBATIM_DOWNLOADS:
        if (root / "downloads" / name).read_bytes() != (publication.root / name).read_bytes():
            raise RuntimeError(f"public download does not match validated bundle input: {name}")
    _verify_public_aggregate_downloads(publication, root)
    for relative, digest in _spatial_assets(publication).items():
        payload = (root / relative).read_bytes()
        if sha256(payload).hexdigest() != digest:
            raise RuntimeError(f"public spatial asset does not match frozen cell: {relative}")
    html_paths = sorted(root.rglob("*.html"))
    _verify_local_references(root, html_paths)
    _verify_forbidden_content(publication, html_paths)
    scan_publication(root)


def _overview(
    publication: PublicationView,
    suite: LoadedSuite,
    protocols: dict[str, ProtocolSpec],
    chart_profiles: tuple[_ChartProfile, ...],
    chart_scope: _ChartScope,
) -> str:
    runs = publication.runs
    trials = len(publication.trials)
    families = {spec.family for spec in suite.specs.values()}
    metrics = (
        (len(suite.forms), "frozen abstract forms"),
        (len(suite.specs), "documented dialects"),
        (len(families), "representation families"),
        (len(protocols), "task protocols"),
        (len(runs), "admitted runs"),
        (trials, "admitted trials"),
    )
    cards = "".join(
        f"<div class=card><div class=metric>{number}</div><div>{_e(label)}</div></div>"
        for number, label in metrics
    )
    exemplars = _dialect_exemplar_cards(suite)
    if chart_scope.is_matching_sample:
        caveat = (
            f"this is an authenticated {chart_scope.protocol_count}-protocol by "
            f"n={chart_scope.sample_n} smoke test, not a model ranking."
        )
        outcome_heading = "sample outcome"
        outcome_copy = (
            f"in this one-{chart_scope.sample_dialect_modality}-dialect sample, competence "
            "equals observed accuracy. percentages are descriptive, not a model comparison."
        )
    else:
        caveat = "results shown here are aggregate views of admitted bundle records."
        outcome_heading = "aggregate outcomes"
        outcome_copy = (
            "competence values are descriptive aggregates. release design and coverage "
            "determine which comparisons they can support."
        )
    return (
        "<h1>distinction benchmark</h1>"
        "<p class=lede>an inspectable benchmark of whether a model can preserve laws of form "
        "structure and reduce it across genuinely different representational dialects.</p>"
        f"<p class=notice>release <strong>{_e(publication.release_id)}</strong>: "
        f"{_e(caveat)}</p>"
        f"<section class=grid>{cards}</section><h2>the claim</h2>"
        "<p>competence and representational invariance are reported separately. a model can be "
        "consistently wrong; that does not make it competent. plain and treated dialect effects "
        "are paired only within an archetype.</p>"
        f"<h2>{_e(outcome_heading)}</h2><p>{_e(outcome_copy)}</p>"
        f"{_chart_group(chart_profiles, chart_scope, id_prefix='home', outcome_only=True)}"
        "<h2>every dialect, one frozen form</h2>"
        "<p>the same medium probe form is rendered once in each documented dialect; open a card "
        "for its reading rule, provenance, limitations, and all 400 frozen cells.</p>"
        f"<section class=grid>{exemplars}</section>"
    )


def _forms(suite: LoadedSuite, protocols: dict[str, ProtocolSpec]) -> str:
    by_difficulty: dict[str, list[dict[str, Any]]] = {}
    for form in suite.forms:
        by_difficulty.setdefault(form["difficulty"], []).append(form)
    rows = "".join(
        f"<tr><td>{_e(name)}</td><td>{len(forms)}</td>"
        f"<td>{sum(f['normal_value'] == 'marked' for f in forms)}</td>"
        f"<td>{sum(f['normal_value'] == 'unmarked' for f in forms)}</td></tr>"
        for name, forms in by_difficulty.items()
    )
    memberships = {
        form["abstract_form_id"]: sorted(
            name for name, ids in suite.form_sets.items() if form["abstract_form_id"] in ids
        )
        for form in suite.forms
    }
    form_rows = "".join(
        "<tr>"
        f"<td><code>{_e(form['abstract_form_id'])}</code></td>"
        f"<td><code>{_e(form['reference_transcription'])}</code></td>"
        f"<td>{_e(form['normal_value'])}</td><td>{_e(form['difficulty'])}</td>"
        f"<td>{_e(', '.join(memberships[form['abstract_form_id']]))}</td></tr>"
        for form in suite.forms
    )
    protocol_cards = "".join(
        "<article class=card>"
        f"<h3><code>{_e(spec.protocol_id)}</code></h3>"
        f"<p><strong>system prompt</strong></p><pre>{_e(spec.system_text)}</pre>"
        f"<p><strong>user prompt template</strong></p><pre>{_e(spec.user_template)}</pre>"
        f"<p class=muted>answer: {_e(spec.answer_kind)} · scorer: {_e(spec.scorer_id)}@"
        f"{_e(spec.scorer_version)} · dialect legend: {_e(spec.includes_dialect_legend)}</p>"
        "<details><summary>exact response schema</summary>"
        f"<pre>{_e(json.dumps(spec.response_json_schema, indent=2))}</pre></details>"
        "</article>"
        for spec in protocols.values()
    )
    form_sets = "".join(
        f"<tr><td><code>{_e(name)}</code></td><td>{len(ids)}</td>"
        f"<td>{_e(', '.join(ids[:8]))}{' …' if len(ids) > 8 else ''}</td></tr>"
        for name, ids in sorted(suite.form_sets.items())
    )
    return (
        "<h1>forms + protocols</h1><p class=lede>the abstract form, visual dialect, task protocol, "
        "model endpoint, and execution surface are separate frozen variables.</p>"
        "<h2>balanced form table</h2><table><thead><tr><th>difficulty</th><th>forms</th>"
        f"<th>marked</th><th>unmarked</th></tr></thead><tbody>{rows}</tbody></table>"
        f"<h2>frozen form sets</h2><table><thead><tr><th>set</th><th>forms</th>"
        f"<th>first ids</th></tr></thead><tbody>{form_sets}</tbody></table>"
        "<h2>protocol registry</h2><p class=notice><strong>possible confounds:</strong> infer "
        "protocols measure convention inference "
        "and task performance together; taught protocols expose the reading rule but may measure "
        "instruction use. image dialects additionally confound raster perception and layout.</p>"
        f"<section class=grid>{protocol_cards}</section>"
        "<h2>every form and its set membership</h2><table><thead><tr><th>form id</th>"
        "<th>reference form</th><th>normal value</th><th>difficulty</th><th>sets</th></tr>"
        f"</thead><tbody>{form_rows}</tbody></table>"
    )


def _stimulus_markup(cell: dict[str, Any], *, prefix: str = "") -> str:
    if cell["modality"] != "text":
        return (
            f'<img loading=lazy src="{prefix}assets/{_e(cell["asset_path"])}" '
            f'alt="frozen stimulus {_e(cell["abstract_form_id"])}">'
        )
    return f"<pre>{_e(cell['model_payload'])}</pre>"


def _cell_markup(cell: dict[str, Any], *, prefix: str = "") -> str:
    stimulus = _stimulus_markup(cell, prefix=prefix)
    return (
        f"<details><summary><code>{_e(cell['abstract_form_id'])}</code></summary>"
        f"<div class=stimulus>{stimulus}</div><p class=muted>sha256 "
        f"<code>{_e(cell['model_payload_sha256'])}</code><br>symbolic hash "
        f"<code>{_e(cell['symbolic_payload_hash'])}</code></p></details>"
    )


def _dialect_exemplars(suite: LoadedSuite) -> tuple[str, dict[str, dict[str, Any]]]:
    probe_ids = set(suite.form_sets["probe"])
    candidates = [
        form["abstract_form_id"]
        for form in suite.forms
        if form["abstract_form_id"] in probe_ids and form["difficulty"] == _EXEMPLAR_DIFFICULTY
    ]
    if len(candidates) != 1:
        raise RuntimeError("probe set must contain exactly one medium exemplar form")
    exemplar_form_id = candidates[0]
    cells = {
        cell["dialect_id"]: cell
        for cell in suite.cells
        if cell["abstract_form_id"] == exemplar_form_id
    }
    if set(cells) != set(suite.specs):
        raise RuntimeError("dialect exemplars do not cover the frozen registry")
    return exemplar_form_id, cells


def _dialect_exemplar_card(
    *,
    dialect_id: str,
    spec: DialectSpec,
    cell: dict[str, Any],
    exemplar_form_id: str,
    cell_count: int | None,
) -> str:
    description = f"<p>{_e(spec.description)}</p>" if cell_count is not None else ""
    count = f" · {cell_count} cells" if cell_count is not None else ""
    link_text = (
        "inspect every frozen cell" if cell_count is not None else "reading rule + all frozen cells"
    )
    return (
        '<article class="card exemplar" '
        f'data-dialect-exemplar="{_e(dialect_id)}">'
        f"<h3>{_e(spec.label)}</h3><p><code>{_e(dialect_id)}</code></p>"
        f"<div class=stimulus>{_stimulus_markup(cell)}</div>"
        f"<p class=muted>same frozen form · <code>{_e(exemplar_form_id)}</code></p>"
        f"{description}<p class=muted>{_e(spec.modality)} · {_e(spec.family)} · "
        f"{_e(spec.archetype)}{count}</p>"
        f'<a href="dialects/{_e(dialect_id)}.html">{link_text}</a></article>'
    )


def _dialect_exemplar_cards(suite: LoadedSuite) -> str:
    exemplar_form_id, cells = _dialect_exemplars(suite)
    return "".join(
        _dialect_exemplar_card(
            dialect_id=dialect_id,
            spec=spec,
            cell=cells[dialect_id],
            exemplar_form_id=exemplar_form_id,
            cell_count=None,
        )
        for dialect_id, spec in sorted(suite.specs.items())
    )


def _atlas(out: Path, suite: LoadedSuite) -> str:
    cells_by_dialect: dict[str, list[dict[str, Any]]] = {}
    for cell in suite.cells:
        cells_by_dialect.setdefault(cell["dialect_id"], []).append(cell)
    cards = []
    exemplar_form_id, exemplars = _dialect_exemplars(suite)
    for dialect_id, spec in suite.specs.items():
        cells = cells_by_dialect[dialect_id]
        cards.append(
            _dialect_exemplar_card(
                dialect_id=dialect_id,
                spec=spec,
                cell=exemplars[dialect_id],
                exemplar_form_id=exemplar_form_id,
                cell_count=len(cells),
            )
        )
        cell_markup = "".join(_cell_markup(cell, prefix="../") for cell in cells)
        limitations = "".join(f"<li>{_e(item)}</li>" for item in spec.limitations)
        body = (
            f"<h1>{_e(spec.label)}</h1><p><code>{_e(dialect_id)}</code></p>"
            f"<p class=lede>{_e(spec.description)}</p><div class=grid>"
            f"<article class=card><h2>reading rule</h2><p>{_e(spec.reading_rule)}</p></article>"
            "<article class=card><h2>identity</h2>"
            f"<pre>{_e(json.dumps(spec.to_dict(), indent=2))}</pre>"
            "</article></div><h2>limitations</h2>"
            f"<ul>{limitations}</ul><h2>all frozen cells</h2>"
            f"<section class=dialect-cells>{cell_markup}</section>"
        )
        _write(out / "dialects" / f"{dialect_id}.html", _page(spec.label, body, prefix="../"))
    return (
        "<h1>dialect atlas</h1><p class=lede>every dialect declares its reading rule, provenance, "
        "modality, limits, structural family, and exact frozen payload hashes.</p>"
        f"<section class=grid>{''.join(cards)}</section>"
    )


def _records_table(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    if not rows:
        return "<p class=notice>no admitted records for this table.</p>"
    heading = "".join(f"<th>{_e(column.replace('_', ' '))}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_e(row.get(column, ''))}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>"


def _profile_aggregate(profile: dict[str, Any], field: str) -> dict[str, float]:
    value = profile.get(field)
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"published profile has invalid {field}") from exc
    if not isinstance(parsed, dict) or any(
        not isinstance(key, str) or isinstance(child, bool) or not isinstance(child, (int, float))
        for key, child in parsed.items()
    ):
        raise RuntimeError(f"published profile has invalid {field}")
    return {key: float(child) for key, child in parsed.items()}


def _profile_label(profile: dict[str, Any]) -> str:
    return f"{profile['resolved_model_id']} · {profile['protocol_id']}"


def _coverage_matrices(
    suite: LoadedSuite,
    profiles: list[dict[str, Any]],
) -> tuple[str, str]:
    columns = sorted(
        profiles,
        key=lambda row: (
            str(row["execution_surface"]),
            str(row["resolved_model_id"]),
            str(row["protocol_id"]),
            str(row["reasoning"]),
        ),
    )
    headings = "".join(f"<th>{_e(_profile_label(profile))}</th>" for profile in columns)
    dialect_aggregates = [_profile_aggregate(profile, "dialect_accuracy") for profile in columns]
    family_aggregates = [_profile_aggregate(profile, "family_accuracy") for profile in columns]
    dialect_rows = []
    for dialect_id, spec in sorted(suite.specs.items()):
        values = [
            "<td>not run</td>"
            if dialect_id not in aggregate
            else f"<td>{aggregate[dialect_id]:.0%}</td>"
            for aggregate in dialect_aggregates
        ]
        dialect_rows.append(
            f"<tr><td><code>{_e(dialect_id)}</code></td><td>{_e(spec.family)}</td>"
            f"{''.join(values)}</tr>"
        )
    families = sorted({spec.family for spec in suite.specs.values()})
    family_rows = []
    for family in families:
        values = [
            "<td>not run</td>" if family not in aggregate else f"<td>{aggregate[family]:.0%}</td>"
            for aggregate in family_aggregates
        ]
        family_rows.append(f"<tr><td>{_e(family)}</td>{''.join(values)}</tr>")
    dialect = (
        "<table><thead><tr><th>dialect</th><th>family</th>"
        f"{headings}</tr></thead><tbody>{''.join(dialect_rows)}</tbody></table>"
    )
    family = (
        f"<table><thead><tr><th>family</th>{headings}</tr></thead>"
        f"<tbody>{''.join(family_rows)}</tbody></table>"
    )
    return dialect, family


def _reasoning_contrasts(profiles: list[dict[str, Any]]) -> str:
    if not profiles:
        return "<p class=notice>not run: no admitted profile.</p>"
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for profile in profiles:
        key = (
            str(profile["execution_surface"]),
            str(profile["resolved_model_id"]),
            str(profile["protocol_id"]),
        )
        groups.setdefault(key, []).append(profile)
    rows = []
    for key, values in sorted(groups.items()):
        contrast = (
            "admitted contrast"
            if len({str(value["reasoning"]) for value in values}) > 1
            else "not run: no second reasoning setting"
        )
        for value in values:
            rows.append(
                "<tr>"
                f"<td>{_e(key[0])}</td><td>{_e(key[1])}</td><td>{_e(key[2])}</td>"
                f"<td><code>{_e(value['reasoning'])}</code></td>"
                f"<td>{_e(value.get('competence'))}</td>"
                f"<td>{_e(value.get('within_family_invariance'))}</td>"
                f"<td>{_e(value.get('mean_latency_ms'))}</td>"
                f"<td>{_e(value.get('input_tokens'))}/{_e(value.get('output_tokens'))}/"
                f"{_e(value.get('reasoning_tokens'))}</td>"
                f"<td>{float(value.get('cost_usd') or 0):.8f}</td>"
                f"<td>{_e(contrast)}</td></tr>"
            )
    return (
        "<table><thead><tr><th>surface</th><th>model</th><th>protocol</th><th>reasoning</th>"
        "<th>competence</th><th>invariance</th><th>mean latency ms</th>"
        "<th>input/output/reasoning tokens</th><th>cost usd</th>"
        "<th>contrast status</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _runs_page(
    publication: PublicationView,
    profiles: list[dict[str, Any]],
    effects: list[dict[str, Any]],
    chart_profiles: tuple[_ChartProfile, ...],
    chart_scope: _ChartScope,
) -> str:
    suite = publication.suite
    profile_table = _records_table(
        profiles,
        (
            "execution_surface",
            "requested_model_id",
            "resolved_model_id",
            "protocol_id",
            "reasoning",
            "competence",
            "within_family_invariance",
            "text_competence",
            "spatial_competence",
            "invalid_output_rate",
            "coverage",
            "observed_trials",
            "expected_trials",
        ),
    )
    resource_table = _records_table(
        profiles,
        (
            "execution_surface",
            "resolved_model_id",
            "protocol_id",
            "mean_latency_ms",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cost_usd",
        ),
    )
    effect_table = _records_table(
        effects,
        (
            "resolved_model_id",
            "protocol_id",
            "family",
            "plain_dialect_id",
            "treatment_dialect_id",
            "signed_paired_effect",
            "bootstrap_low",
            "bootstrap_high",
            "mcnemar_exact_p",
        ),
    )
    dialect_matrix, family_matrix = _coverage_matrices(suite, profiles)
    if chart_scope.is_matching_sample:
        scope = (
            f"this authenticated sample is a {chart_scope.protocol_count}-protocol by "
            f"n={chart_scope.sample_n} smoke test ({chart_scope.observation_count} trial "
            f"observations summarized into {chart_scope.profile_count} aggregate profile "
            "rows), not a model ranking."
        )
    else:
        scope = "all tables are aggregates recomputed from admitted bundle records."
    comparison_copy = _sample_comparison_copy(chart_profiles, chart_scope)
    return (
        "<h1>models + aggregate results</h1><p class=lede>api and agent-mediated execution "
        "surfaces remain separate experimental conditions. only recomputed aggregate rows "
        "are published here.</p>"
        f"<p class=notice>{_e(scope)}</p>"
        "<h2>presentation charts</h2>"
        "<p>all plotted values come from the validated public aggregate profile rows.</p>"
        f"{comparison_copy}"
        f"{_chart_group(chart_profiles, chart_scope, id_prefix='results')}"
        "<h2>how these rows are derived</h2><p>validated scored observations are grouped by "
        "model, protocol, execution surface, and reasoning setting. competence balances "
        "families; coverage compares observed with planned observations; invariance measures "
        "agreement across representations. individual exchanges are not part of this site.</p>"
        "<h2>competence + invariance profiles</h2>"
        f"{profile_table}"
        "<h2>aggregate resource use</h2>"
        f"{resource_table}"
        "<h2>controlled dialect effects</h2>"
        f"{effect_table}"
        "<h2>dialect matrix</h2><p>every frozen dialect is explicit; absent cells say not run.</p>"
        f"{dialect_matrix}"
        "<h2>family matrix</h2><p>family aggregates are shown only where admitted trials exist.</p>"
        f"{family_matrix}"
        "<h2>reasoning contrasts</h2><p>competence, invariance, latency, and tokens stay "
        "separate across reasoning settings.</p>"
        f"{_reasoning_contrasts(profiles)}"
    )


def _presentation(profiles: tuple[_ChartProfile, ...], scope: _ChartScope) -> str:
    if scope.is_matching_sample:
        heading = "sample presentation"
        lede = (
            "three views of the admitted aggregate sample: outcome, valid output versus "
            "correct result, and resource footprint."
        )
        detail = (
            f"all values are projected only from the public profile rows. this authenticated "
            f"sample covers {scope.model_count} model and 1 {scope.sample_dialect_modality} "
            f"dialect, with {scope.protocol_count} protocols and n={scope.sample_n} per "
            "protocol. competence therefore equals observed accuracy here."
        )
    else:
        heading = "aggregate presentation"
        lede = (
            "available aggregate views of outcome, valid output versus correct result, and "
            "resource footprint."
        )
        detail = (
            "all values are projected only from admitted public profile rows. no "
            "sample-specific design or result is inferred without a matching sample contract."
        )
    return (
        f"<h1>{_e(heading)}</h1><p class=lede>{_e(lede)}</p><p>{_e(detail)}</p>"
        f"{_sample_comparison_copy(profiles, scope)}"
        f"{_chart_group(profiles, scope, id_prefix='presentation')}"
    )


def _human(suite: LoadedSuite, protocols: dict[str, ProtocolSpec], release_id: str) -> str:
    protocol_ids = list(protocols)
    form_ids = suite.form_sets["probe"]
    dialect_ids = sorted(suite.specs)
    chosen: list[dict[str, Any]] = []
    cell_map = {(c["abstract_form_id"], c["dialect_id"]): c for c in suite.cells}
    for index in range(len(protocol_ids) * 3):
        form_id = form_ids[index % len(form_ids)]
        dialect_id = dialect_ids[index % len(dialect_ids)]
        cell = cell_map[(form_id, dialect_id)]
        spec = suite.specs[dialect_id]
        protocol = protocols[protocol_ids[index % len(protocol_ids)]]
        chosen.append(
            {
                "trial": index + 1,
                "abstract_form_id": form_id,
                "dialect_id": dialect_id,
                "protocol_id": protocol.protocol_id,
                "answer_kind": protocol.answer_kind,
                "prompt": protocol.render_user_text(reading_rule=spec.reading_rule),
                "modality": cell["modality"],
                "payload": cell.get("model_payload"),
                "asset": f"assets/{cell['asset_path']}",
            }
        )
    payload = json.dumps(chosen, ensure_ascii=False).replace("</", "<\\/")
    return (
        f"<h1>human pilot</h1><p class=lede>{len(chosen)} local-only stimuli, balanced across "
        f"{len(protocol_ids)} frozen protocols. nothing is posted; export stays on your device.</p>"
        "<p class=notice>this informal pilot is not an admitted model run. use a pseudonymous "
        "participant code; no data leaves this page. every exported HumanTrialRecord conforms "
        'to the <a href="downloads/human-trial.schema.json">published schema</a>.</p>'
        "<div class=card><label>participant code <input id=participant required minlength=3 "
        'maxlength=64 pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,63}"></label> '
        "<label>laws of form familiarity <select id=familiarity><option value=none>none</option>"
        "<option value=some>some</option><option value=expert>expert</option>"
        "</select></label></div>"
        "<div id=pilot class=card></div><div class=answer id=answer></div>"
        "<label>confidence <select id=confidence><option value=low>low</option>"
        "<option value=medium selected>medium</option><option value=high>high</option>"
        "</select></label> "
        "<button id=next>save answer + next</button> "
        "<button id=download disabled>download json</button>"
        f"<script>const stimuli={payload};const release={json.dumps(release_id)};"
        "let i=0;let started=performance.now();const rows=[];"
        "const pilot=document.querySelector('#pilot'),answer=document.querySelector('#answer');"
        "function render(){if(i>=stimuli.length){pilot.textContent='pilot complete';"
        "answer.innerHTML='';document.querySelector('#next').disabled=true;"
        "document.querySelector('#download').disabled=false;return}"
        "const s=stimuli[i];pilot.innerHTML=`<p><strong>${s.trial} / ${stimuli.length}</strong> · "
        "<code>${s.protocol_id}</code> · <code>${s.dialect_id}</code></p><p>${s.prompt}</p>`+"
        "(s.modality!=='text'?`<div class=stimulus><img src=\"${s.asset}\" "
        'alt="frozen stimulus"></div>`:`<div class=stimulus><pre></pre></div>`);'
        "if(s.modality==='text')pilot.querySelector('pre').textContent=s.payload;"
        "answer.innerHTML=s.answer_kind==='normal_value'?"
        "'<button data-v=marked>marked</button> <button data-v=unmarked>unmarked</button>':"
        "'<textarea rows=6 cols=60 aria-label=transcription></textarea>';"
        "answer.querySelectorAll('[data-v]').forEach(b=>b.onclick=()=>{"
        "answer.dataset.value=b.dataset.v})}document.querySelector('#next').onclick=()=>{"
        "const s=stimuli[i];const participant=document.querySelector('#participant').value.trim();"
        "const familiarity_band=document.querySelector('#familiarity').value;"
        "if(!/^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/.test(participant))return;"
        "const value=s.answer_kind==='normal_value'?"
        "answer.dataset.value:(answer.querySelector('textarea')?.value||'');"
        "if(!value)return;rows.push({participant_code:participant,familiarity_band,"
        "abstract_form_id:s.abstract_form_id,dialect_id:s.dialect_id,protocol_id:s.protocol_id,"
        "answer:s.answer_kind==='normal_value'?value:null,"
        "transcription:s.answer_kind!=='normal_value'?value:null,confidence:"
        "document.querySelector('#confidence').value,elapsed_ms:Math.round(performance.now()-started)});"
        "answer.dataset.value='';i++;started=performance.now();render()};"
        "document.querySelector('#download').onclick=()=>{"
        "const blob=new Blob([JSON.stringify({schema_version:1,release_id:release,records:rows},"
        "null,2)],{type:'application/json'});"
        "const a=document.createElement('a');a.href=URL.createObjectURL(blob);"
        "a.download='distinction-human-pilot.json';"
        "a.click();URL.revokeObjectURL(a.href)};render()</script>"
    )


def _downloads(publication: PublicationView, downloads: Path) -> str:
    rows = "".join(
        f'<tr><td><a href="downloads/{_e(name)}">{_e(name)}</a></td>'
        f"<td>{(downloads / name).stat().st_size}</td>"
        f"<td><code>{sha256((downloads / name).read_bytes()).hexdigest()}</code></td></tr>"
        for name in SAFE_DOWNLOADS
    )
    citation = (
        f"distinction benchmark contributors ({publication.release_id}). "
        "distinction benchmark: laws of form representation invariance evaluation."
    )
    return (
        "<h1>downloads + citation</h1><p class=lede>these public files define the test and "
        "support the aggregate claims displayed on this website.</p>"
        "<p class=notice>the complete audit authority remains offline. individual execution "
        "records and provider exchanges are deliberately not published.</p>"
        "<h2>public benchmark data</h2><p>the registries and human schema are exact authority "
        "copies. profile and effect downloads are fixed-schema aggregate projections of the "
        "validated rows used on this page.</p>"
        f"<table><thead><tr><th>artifact</th><th>bytes</th><th>sha256</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        "<h2>caveats</h2><ul><li>sample releases are protocol smoke tests, not rankings.</li>"
        "<li>untaught runs combine dialect inference with task performance.</li>"
        "<li>image runs include raster perception and layout as possible confounds.</li>"
        "<li>the checksums above cover the exact public data files shown here.</li></ul>"
        f"<h2>suggested citation</h2><pre>{_e(citation)}</pre>"
        "<p>code is mit licensed. the frozen suite, stimuli, documentation, and site content are "
        "licensed cc by 4.0.</p>"
    )


def build_site(publication: PublicationView, out: Path) -> None:
    try:
        out.resolve().relative_to((publication.root / "site").resolve())
        inside_bundle_site = True
    except ValueError:
        inside_bundle_site = False
    if publication.status != "sealed" and not inside_bundle_site:
        raise RuntimeError("a working bundle may only build its own site/ directory")
    if not publication.stimuli_materialized:
        raise RuntimeError("public site generation requires all frozen stimuli materialized")
    if out.exists():
        if any(out.iterdir()):
            raise FileExistsError(f"site output is not empty: {out}")
    else:
        out.mkdir(parents=True)

    suite = publication.suite
    protocols = publication.protocols
    profiles = [dict(row) for row in publication.profiles]
    effects = [dict(row) for row in publication.effects]
    chart_profiles = _chart_projection(publication.profiles)
    chart_scope = _chart_scope(publication, chart_profiles)
    downloads = out / "downloads"
    downloads.mkdir()
    for name in _VERBATIM_DOWNLOADS:
        shutil.copyfile(publication.root / name, downloads / name)
    for name, table in _public_aggregate_tables(publication).items():
        pq.write_table(table, downloads / name, compression="zstd")
    _write(
        out / "index.html",
        _page(
            "what is tested",
            _overview(publication, suite, protocols, chart_profiles, chart_scope),
        ),
    )
    _write(out / "forms.html", _page("forms + protocols", _forms(suite, protocols)))
    _write(out / "atlas.html", _page("dialect atlas", _atlas(out, suite)))
    _write(
        out / "runs.html",
        _page(
            "models + runs",
            _runs_page(publication, profiles, effects, chart_profiles, chart_scope),
        ),
    )
    _write(
        out / "presentation.html",
        _page("presentation", _presentation(chart_profiles, chart_scope)),
    )
    _write(
        out / "human.html",
        _page("human pilot", _human(suite, protocols, publication.release_id)),
    )
    _write(
        out / "downloads.html",
        _page("downloads + citation", _downloads(publication, downloads)),
    )
    for relative in sorted(_spatial_assets(publication)):
        source = publication.root / relative.removeprefix("assets/")
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (out / "CNAME").write_text("distinction.valeriekim.ca\n")
    verify_public_site(publication, out)
