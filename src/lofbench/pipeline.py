"""Results data pipeline: inspect_ai eval logs -> tidy, site-ready artifacts.

One entry point reads ``.eval`` logs from a directory and writes versioned
tidy artifacts: a per-scored-form ``items`` table, a per-API-call ``calls``
table, a paired-comparison ``sensitivity`` table, and a ``transcripts`` JSONL
extract. DB-6 (site) and DB-8 (release skill) read these artifacts; they do
not read logs directly.

    uv run python -m lofbench.pipeline logs/ data/ --suite-version pilot-v0

Grounded in the 48 real pre-DB-4 logs in ``logs/`` as of 2026-07-04. See
``.lattice/plans/task_01KWQKYVADQNEXNR6HSBPEVFBV.md`` for the full design,
and ``.lattice/notes/rendering-architecture-2026-07-04.md`` for the
post-DB-4 ``render_metadata`` provenance schema this pipeline is already
shaped to consume once DB-4 lands (it has no real log to test against yet,
so that branch is deliberately the secondary path today).

Every real log today is pilot data: no log has non-empty ``render_metadata``,
a ``suite_version``, a ``form_id``, or a ``dialect_id`` stamped by a
renderer. The record builder tries per-sample ``render_metadata`` first and
falls back to the run-level ``get_log_metadata()`` dialect string when
``render_metadata`` is empty or absent -- composite samples omit the key
entirely (not ``{}``), so the lookup is None-safe:
``sample.metadata.get("render_metadata") or {}``, never a bare key access.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import random
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from hashlib import blake2b
from pathlib import Path
from typing import Any

import pandas as pd
from inspect_ai.log import EvalLog, EvalLogInfo, list_eval_logs, read_eval_log

from lofbench.analysis import get_log_metadata
from lofbench.pricing import PRICING_TABLE_VERSION, compute_cost_usd

SCHEMA_VERSION = "pipeline-schema-v1"
DEFAULT_SUITE_VERSION = "pilot-v0"
DEFAULT_MAX_LOG_MB = 400.0

# Historical ids remain readable for pilot-v0. Public v1 calls its neutral
# arm a reference transcription rather than privileging a canonical notation.
CANONICAL_DIALECT_IDS = frozenset(
    {"canonical", "parens.canonical", "parens.reference-v1"}
)

# Fallback family/modality/format derivation from the raw `renderer` name,
# used only when per-sample render_metadata is absent (i.e. every real log
# today). Keyed on the renderer registry name in lofbench.renderers.
_RENDERER_FALLBACK: dict[str, tuple[str, str, str]] = {
    # renderer name -> (family, modality, format)
    "canonical": ("parens", "text", "text"),
    "noisy_parens": ("parens", "text", "text"),
    "sexpr": ("pattern", "text", "text"),
    "nested_list": ("parens", "text", "text"),
    "circle": ("enclosure", "spatial", "image"),
    "svg_circle": ("enclosure", "spatial", "image"),
}


# =============================================================================
# Record schemas
# =============================================================================


@dataclass
class CallRecord:
    """One row per (sample, epoch) -- one API call."""

    call_id: str
    model: str
    dialect_id: str
    family: str
    modality: str
    format: str
    suite_version: str
    reasoning_setting: str | None
    thinking_tokens: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    group_size: int
    all_correct: bool | None
    epoch: int
    task_name: str
    log_path: str
    pricing_table_version: str
    schema_version: str = SCHEMA_VERSION


@dataclass
class ItemRecord:
    """One row per scored form, exploded out of a call."""

    call_id: str
    item_index: int
    form_id: str
    form_string: str
    dialect_id: str
    family: str
    modality: str
    format: str
    suite_version: str
    model: str
    reasoning_setting: str | None
    thinking_tokens: int
    difficulty: str
    depth: int | None
    steps: int | None
    target: str
    predicted: str
    correct: bool
    epoch: int
    applied_injectors: list[str] | None
    schema_version: str = SCHEMA_VERSION


@dataclass
class TranscriptRecord:
    """One JSON object per scored form, for walkthrough pages."""

    call_id: str
    item_index: int
    form_id: str
    form_string: str
    dialect_id: str
    model: str
    correct: bool
    target: str
    predicted: str
    full_completion_text: str
    rendered_input: str | None
    is_image: bool
    schema_version: str = SCHEMA_VERSION


@dataclass
class SensitivityRow:
    """One row per (model, reasoning_setting, dialect_id) contrast."""

    model: str
    reasoning_setting: str | None
    dialect_id: str
    suite_version: str
    n_paired: int
    n_distinct_forms: int
    accuracy_canonical: float
    accuracy_treatment: float
    paired_drop: float
    mcnemar_b: int
    mcnemar_c: int
    mcnemar_p: float
    bootstrap_ci_low: float
    bootstrap_ci_high: float
    coverage: float
    schema_version: str = SCHEMA_VERSION


@dataclass
class SkipRecord:
    """A log that was not processed, and why. Never a silent drop."""

    path: str
    reason: str


@dataclass
class ParseFailure:
    """A composite answer that matched neither known schema. Counted, not dropped."""

    call_id: str
    item_index: int
    reason: str


@dataclass
class PipelineSummary:
    """Printed/returned reconciliation report. Every count here must add up."""

    logs_seen: int = 0
    logs_processed: int = 0
    logs_skipped: list[SkipRecord] = field(default_factory=list)
    calls_written: int = 0
    calls_with_no_scored_items: int = 0
    items_written: int = 0
    items_parse_failures: list[ParseFailure] = field(default_factory=list)
    transcripts_written: int = 0
    sensitivity_rows: int = 0

    def as_text(self) -> str:
        lines = [
            f"logs seen:              {self.logs_seen}",
            f"logs processed:         {self.logs_processed}",
            f"logs skipped:           {len(self.logs_skipped)}",
        ]
        for s in self.logs_skipped:
            lines.append(f"  - {s.path}: {s.reason}")
        lines.append(f"calls written:          {self.calls_written}")
        lines.append(
            f"  calls with no scored items (errored samples): {self.calls_with_no_scored_items}"
        )
        lines.append(f"items written:          {self.items_written}")
        lines.append(
            f"  items with unrecognised answer schema (parse failures, "
            f"counted not dropped): {len(self.items_parse_failures)}"
        )
        lines.append(f"transcripts written:    {self.transcripts_written}")
        lines.append(f"sensitivity rows:       {self.sensitivity_rows}")
        return "\n".join(lines)


# =============================================================================
# Log iteration
# =============================================================================


@dataclass
class LoadedLog:
    path: str
    log: EvalLog | None
    skip_reason: str | None
    size_mb: float


def _log_path_str(info: EvalLogInfo) -> str:
    name = info.name
    return name[len("file://") :] if name.startswith("file://") else name


def iter_logs(log_dir: str, max_log_mb: float = DEFAULT_MAX_LOG_MB) -> Iterator[LoadedLog]:
    """Yield one log at a time; never hold every EvalLog in memory at once.

    Logs above ``max_log_mb`` are skipped rather than fully loaded -- this is
    a deliberate safety valve for arbitrarily large future runs, not a claim
    that today's largest checked-in log (~614MB) cannot be loaded (it loads
    in under 30s / under 1GB RSS as of this writing; the valve exists for
    logs that won't be so forgiving). A skipped log is reported, never
    silently absent from the summary.
    """
    for info in list_eval_logs(log_dir):
        path = _log_path_str(info)
        size_mb = (info.size or 0) / 1_000_000
        if size_mb > max_log_mb:
            try:
                header = read_eval_log(info, header_only=True)
                status = header.status
            except Exception as e:  # noqa: BLE001 - report, never crash the run
                yield LoadedLog(path, None, f"header read failed: {e}", size_mb)
                continue
            yield LoadedLog(
                path,
                None,
                f"skipped: {size_mb:.1f}MB exceeds max_log_mb={max_log_mb} (status={status})",
                size_mb,
            )
            continue
        try:
            log = read_eval_log(info, header_only=False)
        except Exception as e:  # noqa: BLE001 - report, never crash the run
            yield LoadedLog(path, None, f"failed to read: {e}", size_mb)
            continue
        # Cancelled/errored runs can report log.results is None, but
        # log.samples is still fully populated with whatever partial data was
        # captured before cancellation/error (confirmed against the real
        # cancelled 586MB log: log.results is None, but 2055 partial samples
        # load fine). extract_calls/extract_items/extract_transcripts all
        # iterate log.samples directly and never touch log.results, so
        # cancelled/errored runs need no special-casing here.
        yield LoadedLog(path, log, None, size_mb)


# =============================================================================
# Provenance / dialect derivation
# =============================================================================


def stable_form_id(form_string: str) -> str:
    """Deterministic id derived purely from the form string.

    Pre-DB-4 logs never carry a suite ``form_id``. This is not a claim that
    pairing should join on this hash instead of the form string -- pairing
    still prefers form_string per the plan -- but it gives every item a
    short, stable, content-addressed id that is automatically identical
    across any two logs that happen to render the same form string, which is
    exactly the property a pairing join needs.
    """
    digest = blake2b(form_string.encode("utf-8"), digest_size=8).hexdigest()
    return f"form:{digest}"


def reasoning_setting_of(log: EvalLog) -> str | None:
    """Normalise the model's reasoning configuration to one comparable string.

    Providers expose this two different ways in these logs: an effort level
    string (gemini-3-*: "high"/"medium"/"low"/"minimal") or a reasoning token
    budget (opus, gpt-5.2, gemini-2.5-flash: an int, including 0 meaning
    reasoning explicitly disabled). Effort string wins when both could
    theoretically be present; otherwise the token budget is reported as
    "tokens:<n>" so reasoning-toggle pairs (same model, different budget) are
    distinguishable arms rather than colliding into one "model" bucket, per
    the plan review's finding that mixing reasoning settings in the
    sensitivity grouping key corrupts the paired drop.
    """
    gc = log.eval.model_generate_config
    if gc is None:
        return None
    effort = getattr(gc, "reasoning_effort", None) or getattr(gc, "effort", None)
    if effort:
        return str(effort)
    tokens = getattr(gc, "reasoning_tokens", None)
    if tokens is not None:
        return f"tokens:{tokens}"
    return None


def _family_modality_format(renderer_name: str) -> tuple[str, str, str]:
    return _RENDERER_FALLBACK.get(renderer_name, (renderer_name, "text", "text"))


@dataclass
class Provenance:
    dialect_id: str
    family: str
    modality: str
    format: str
    suite_version: str
    form_id: str
    applied_injectors: list[str] | None


def resolve_provenance(
    sample_metadata: dict[str, Any], form_string: str, log_meta: dict[str, Any]
) -> Provenance:
    """Per-sample provenance, None-safe against composite samples that omit
    ``render_metadata`` entirely (single samples carry ``{}``; both are
    falsy, so ``.get(...) or {}`` handles both without a bare-key KeyError).
    """
    render_meta = sample_metadata.get("render_metadata") or {}

    dialect_id = render_meta.get("dialect_id") or log_meta["dialect"]
    family, modality, fmt = _family_modality_format(log_meta["renderer"])
    family = render_meta.get("family") or family
    modality = render_meta.get("modality") or modality
    fmt = render_meta.get("format") or fmt
    suite_version = render_meta.get("suite_version") or DEFAULT_SUITE_VERSION
    form_id = render_meta.get("form_id") or stable_form_id(form_string)

    injectors = render_meta.get("injectors")
    applied_injectors = [i["name"] for i in injectors if i.get("applied")] if injectors else None

    return Provenance(
        dialect_id=dialect_id,
        family=family,
        modality=modality,
        format=fmt,
        suite_version=suite_version,
        form_id=form_id,
        applied_injectors=applied_injectors,
    )


def _call_id(log: EvalLog, sample_id: Any, epoch: int) -> str:
    return f"{log.eval.task_id}:{sample_id}:{epoch}"


# =============================================================================
# Composite dual-schema answer parsing
# =============================================================================


def parse_composite_answer(answer_str: str | None, n: int) -> tuple[list[str], bool]:
    """Parse a composite scorer's ``answer`` string under either production schema.

    28+ of the 48 real logs use the current scorer's
    ``{"results": [...], "canonicals": [...]}``; 11+ use an earlier scorer
    version's ``{"items": [...], "total_marked": ...}`` (removed from the
    source tree, still in old logs). Both are a per-item marked/unmarked
    list positionally aligned with ``metadata["targets"]``.

    Returns ``(predicted, ok)``. On any parse failure or an unrecognised
    third schema, ``ok`` is False and ``predicted`` is ``["unknown"] * n`` --
    counted as a parse failure by the caller, never silently dropped.
    """
    if not answer_str:
        return ["unknown"] * n, False
    try:
        data = ast.literal_eval(answer_str)
    except (ValueError, SyntaxError):
        return ["unknown"] * n, False
    if not isinstance(data, dict):
        return ["unknown"] * n, False

    if "results" in data and "canonicals" in data:
        predicted = data["results"]
    elif "items" in data and "total_marked" in data:
        predicted = data["items"]
    else:
        return ["unknown"] * n, False  # unrecognised (third) schema

    if not isinstance(predicted, list) or len(predicted) != n:
        return ["unknown"] * n, False
    return [str(p) for p in predicted], True


# =============================================================================
# Extraction
# =============================================================================


def extract_calls(log: EvalLog, summary: PipelineSummary | None = None) -> list[CallRecord]:
    """One row per (sample, epoch) -- one API call, cost/tokens live here only."""
    log_meta = get_log_metadata(log)
    reasoning_setting = reasoning_setting_of(log)
    calls: list[CallRecord] = []

    for sample in log.samples or []:
        metadata = sample.metadata or {}
        group_size = metadata.get("group_size", 1)

        usage = sample.output.usage if sample.output else None
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        reasoning_tokens = getattr(usage, "reasoning_tokens", None) or 0
        cache_read = getattr(usage, "input_tokens_cache_read", None) or 0
        cache_write = getattr(usage, "input_tokens_cache_write", None) or 0

        cost_usd = compute_cost_usd(
            log_meta["model"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        if cost_usd is None:
            print(
                f"WARNING: no pricing entry for model {log_meta['model']!r}; cost_usd=None",
                file=sys.stderr,
            )

        all_correct: bool | None = None
        if sample.scores:
            score = next(iter(sample.scores.values()))
            if isinstance(score.value, dict):
                all_correct = bool(score.value.get("all_correct"))
            elif isinstance(score.value, str):
                all_correct = score.value == "C"
        else:
            if summary is not None:
                summary.calls_with_no_scored_items += 1

        # form_string is not known at the call level (it's per-item for
        # composite); resolve_provenance only needs it for form_id, which
        # calls don't carry, so pass "" -- dialect/family/etc are run-level
        # here regardless of form content.
        prov = resolve_provenance(metadata, "", log_meta)

        calls.append(
            CallRecord(
                call_id=_call_id(log, sample.id, sample.epoch),
                model=log_meta["model"],
                dialect_id=prov.dialect_id,
                family=prov.family,
                modality=prov.modality,
                format=prov.format,
                suite_version=prov.suite_version,
                reasoning_setting=reasoning_setting,
                thinking_tokens=log_meta["thinking_tokens"],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost_usd=cost_usd,
                group_size=group_size,
                all_correct=all_correct,
                epoch=sample.epoch,
                task_name=log.eval.task or "",
                log_path=_log_path_str_from_log(log),
                pricing_table_version=PRICING_TABLE_VERSION,
            )
        )
    return calls


def _log_path_str_from_log(log: EvalLog) -> str:
    location = getattr(log, "location", None)
    return str(location) if location else (log.eval.task_id or "")


def extract_items(log: EvalLog, summary: PipelineSummary | None = None) -> list[ItemRecord]:
    """Explode each call into one row per scored form.

    ``single_lof_task``: one row per call (score.value == "C"/"I").
    ``composite_lof_task``: ``group_size`` rows via the dual answer-schema
    parser, joined positionally to ``metadata["targets"]`` and
    ``metadata["original_expressions"]``.
    """
    log_meta = get_log_metadata(log)
    reasoning_setting = reasoning_setting_of(log)
    is_composite = "composite" in (log.eval.task or "")
    items: list[ItemRecord] = []

    for sample in log.samples or []:
        metadata = sample.metadata or {}
        call_id = _call_id(log, sample.id, sample.epoch)
        score = next(iter(sample.scores.values())) if sample.scores else None

        if is_composite:
            targets = metadata.get("targets", [])
            original_expressions = metadata.get("original_expressions", [])
            n = len(targets)
            answer_str = score.answer if score else None
            predicted_list, parse_ok = parse_composite_answer(answer_str, n)
            # An unscored sample (errored run: no score at all) is not schema
            # drift -- it is already counted via calls_with_no_scored_items,
            # and its items still flow through below as predicted="unknown".
            # Only a present-but-unrecognised answer is a parse failure.
            if not parse_ok and answer_str is not None and summary is not None:
                summary.items_parse_failures.append(
                    ParseFailure(
                        call_id=call_id, item_index=-1, reason="unrecognised answer schema"
                    )
                )
            for i in range(n):
                target = targets[i]
                predicted = predicted_list[i] if i < len(predicted_list) else "unknown"
                form_string = original_expressions[i] if i < len(original_expressions) else ""
                prov = resolve_provenance(metadata, form_string, log_meta)
                items.append(
                    ItemRecord(
                        call_id=call_id,
                        item_index=i,
                        form_id=prov.form_id,
                        form_string=form_string,
                        dialect_id=prov.dialect_id,
                        family=prov.family,
                        modality=prov.modality,
                        format=prov.format,
                        suite_version=prov.suite_version,
                        model=log_meta["model"],
                        reasoning_setting=reasoning_setting,
                        thinking_tokens=log_meta["thinking_tokens"],
                        difficulty=metadata.get("difficulty", "unknown"),
                        depth=None,
                        steps=None,
                        target=target,
                        predicted=predicted,
                        correct=(predicted == target),
                        epoch=sample.epoch,
                        applied_injectors=prov.applied_injectors,
                    )
                )
        else:
            form_string = metadata.get("original_form", "")
            predicted = score.answer if (score and score.answer) else "unknown"
            target = sample.target if isinstance(sample.target, str) else str(sample.target)
            prov = resolve_provenance(metadata, form_string, log_meta)
            items.append(
                ItemRecord(
                    call_id=call_id,
                    item_index=0,
                    form_id=prov.form_id,
                    form_string=form_string,
                    dialect_id=prov.dialect_id,
                    family=prov.family,
                    modality=prov.modality,
                    format=prov.format,
                    suite_version=prov.suite_version,
                    model=log_meta["model"],
                    reasoning_setting=reasoning_setting,
                    thinking_tokens=log_meta["thinking_tokens"],
                    difficulty=metadata.get("difficulty", "unknown"),
                    depth=metadata.get("depth"),
                    steps=metadata.get("steps"),
                    target=target,
                    predicted=predicted,
                    correct=(predicted == target),
                    epoch=sample.epoch,
                    applied_injectors=prov.applied_injectors,
                )
            )
    return items


def extract_transcripts(log: EvalLog) -> list[TranscriptRecord]:
    """Full completion text plus correctness, one JSON object per scored form."""
    is_composite = "composite" in (log.eval.task or "")
    log_meta = get_log_metadata(log)
    out: list[TranscriptRecord] = []

    for sample in log.samples or []:
        metadata = sample.metadata or {}
        call_id = _call_id(log, sample.id, sample.epoch)
        completion = sample.output.completion if sample.output else ""
        score = next(iter(sample.scores.values())) if sample.scores else None
        render_meta = metadata.get("render_metadata") or {}
        fallback_format = _family_modality_format(log_meta["renderer"])[2]
        is_image = (render_meta.get("format") or fallback_format) == "image"
        rendered_input = (
            None if is_image else (metadata.get("expression") or metadata.get("expressions"))
        )

        if is_composite:
            targets = metadata.get("targets", [])
            original_expressions = metadata.get("original_expressions", [])
            n = len(targets)
            predicted_list, _ok = parse_composite_answer(score.answer if score else None, n)
            for i in range(n):
                target = targets[i]
                predicted = predicted_list[i] if i < len(predicted_list) else "unknown"
                form_string = original_expressions[i] if i < len(original_expressions) else ""
                prov = resolve_provenance(metadata, form_string, log_meta)
                out.append(
                    TranscriptRecord(
                        call_id=call_id,
                        item_index=i,
                        form_id=prov.form_id,
                        form_string=form_string,
                        dialect_id=prov.dialect_id,
                        model=log_meta["model"],
                        correct=(predicted == target),
                        target=target,
                        predicted=predicted,
                        full_completion_text=completion,
                        rendered_input=rendered_input,
                        is_image=is_image,
                    )
                )
        else:
            form_string = metadata.get("original_form", "")
            predicted = score.answer if (score and score.answer) else "unknown"
            target = sample.target if isinstance(sample.target, str) else str(sample.target)
            prov = resolve_provenance(metadata, form_string, log_meta)
            out.append(
                TranscriptRecord(
                    call_id=call_id,
                    item_index=0,
                    form_id=prov.form_id,
                    form_string=form_string,
                    dialect_id=prov.dialect_id,
                    model=log_meta["model"],
                    correct=(predicted == target),
                    target=target,
                    predicted=predicted,
                    full_completion_text=completion,
                    rendered_input=rendered_input,
                    is_image=is_image,
                )
            )
    return out


# =============================================================================
# Sensitivity statistics
# =============================================================================


def _binom_cdf_le(k: int, n: int) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, i) for i in range(0, k + 1)) / (2**n)


def _binom_sf_ge(k: int, n: int) -> float:
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.comb(n, i) for i in range(k, n + 1)) / (2**n)


def mcnemar_exact_p(b: int, c: int) -> float:
    """Exact binomial McNemar p-value for discordant pair counts b, c.

    p = 2 * min(P(X <= min(b,c)), P(X >= max(b,c))) under X ~ Binomial(b+c, 0.5).
    No scipy/statsmodels dependency, per pyproject's "prefer stdlib" constraint
    and analysis.py's existing math.comb precedent for the composite MAE baseline.
    """
    n = b + c
    if n == 0:
        return 1.0
    lo, hi = min(b, c), max(b, c)
    p = 2 * min(_binom_cdf_le(lo, n), _binom_sf_ge(hi, n))
    return min(p, 1.0)


def bootstrap_ci(
    canonical_correct: list[bool],
    treatment_correct: list[bool],
    seed: int,
    n_resamples: int = 2000,
) -> tuple[float, float]:
    """95% bootstrap CI on paired_drop, resampling distinct-form pairs with replacement."""
    n = len(canonical_correct)
    if n == 0:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    drops = []
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        ac = sum(canonical_correct[i] for i in idx) / n
        at = sum(treatment_correct[i] for i in idx) / n
        drops.append(ac - at)
    drops.sort()
    lo_i = max(0, min(n_resamples - 1, round(0.025 * (n_resamples - 1))))
    hi_i = max(0, min(n_resamples - 1, round(0.975 * (n_resamples - 1))))
    return (drops[lo_i], drops[hi_i])


def compute_sensitivity(items_df: pd.DataFrame, seed: int = 20260704) -> pd.DataFrame:
    """Paired accuracy drop per (model, reasoning_setting, dialect_id) against
    that model's canonical-dialect arm at the same reasoning setting.

    Pairing key: (form_id, model, reasoning_setting), canonical arm vs each
    other dialect_id present for that (model, reasoning_setting,
    suite_version). ``form_id`` here is a pure hash of ``form_string`` (see
    ``stable_form_id``), so it is exactly equivalent to joining on
    form_string directly for pilot data.

    n_paired is the raw joined-pair count (can include duplicate-trivial-form
    and multi-epoch multiplicity); n_distinct_forms is the count after
    de-duplicating each arm to one row per distinct form_string (first
    occurrence) before joining, per the design note's pseudo-replication
    rule -- McNemar and the bootstrap CI run on the de-duplicated set.
    """
    if items_df.empty:
        return pd.DataFrame()

    rows: list[SensitivityRow] = []

    for suite_version, sv_df in items_df.groupby("suite_version"):
        for (model, reasoning_setting), grp in sv_df.groupby(
            ["model", "reasoning_setting"], dropna=False
        ):
            canon = grp[grp["dialect_id"].isin(CANONICAL_DIALECT_IDS)]
            if canon.empty:
                continue
            other_dialects = [
                d for d in grp["dialect_id"].unique() if d not in CANONICAL_DIALECT_IDS
            ]
            for treatment_dialect in other_dialects:
                treat = grp[grp["dialect_id"] == treatment_dialect]
                if treat.empty:
                    continue

                canon_counts = canon["form_id"].value_counts()
                treat_counts = treat["form_id"].value_counts()
                shared_ids = canon_counts.index.intersection(treat_counts.index)
                n_paired = int((canon_counts[shared_ids] * treat_counts[shared_ids]).sum())
                if n_paired == 0:
                    continue

                canon_dedup = canon.drop_duplicates("form_string", keep="first").set_index(
                    "form_id"
                )
                treat_dedup = treat.drop_duplicates("form_string", keep="first").set_index(
                    "form_id"
                )
                common_ids = canon_dedup.index.intersection(treat_dedup.index)
                if len(common_ids) == 0:
                    continue
                canon_al = canon_dedup.loc[common_ids]
                treat_al = treat_dedup.loc[common_ids]

                n_distinct_forms = len(common_ids)
                canon_correct = canon_al["correct"].tolist()
                treat_correct = treat_al["correct"].tolist()

                accuracy_canonical = sum(canon_correct) / n_distinct_forms
                accuracy_treatment = sum(treat_correct) / n_distinct_forms
                paired_drop = accuracy_canonical - accuracy_treatment

                b = sum(1 for cc, tc in zip(canon_correct, treat_correct) if cc and not tc)
                c = sum(1 for cc, tc in zip(canon_correct, treat_correct) if (not cc) and tc)
                mcnemar_p = mcnemar_exact_p(b, c)

                ci_low, ci_high = bootstrap_ci(canon_correct, treat_correct, seed=seed)

                # Pre-DB-4: no real log carries injector provenance, so this is
                # always the 1.0 branch today. Once DB-4 lands, applied_injectors
                # becomes a real per-item vector and coverage reflects it.
                treat_injectors = treat_al["applied_injectors"]
                if treat_injectors.apply(lambda x: x is None).all():
                    coverage = 1.0
                else:
                    coverage = treat_injectors.apply(lambda x: bool(x)).mean()

                rows.append(
                    SensitivityRow(
                        model=model,
                        reasoning_setting=reasoning_setting,
                        dialect_id=treatment_dialect,
                        suite_version=suite_version,
                        n_paired=n_paired,
                        n_distinct_forms=n_distinct_forms,
                        accuracy_canonical=accuracy_canonical,
                        accuracy_treatment=accuracy_treatment,
                        paired_drop=paired_drop,
                        mcnemar_b=b,
                        mcnemar_c=c,
                        mcnemar_p=mcnemar_p,
                        bootstrap_ci_low=ci_low,
                        bootstrap_ci_high=ci_high,
                        coverage=coverage,
                    )
                )

    return pd.DataFrame([asdict(r) for r in rows])


# =============================================================================
# Artifact writing
# =============================================================================


def _records_to_df(records: list, record_cls: type) -> pd.DataFrame:
    """Records to DataFrame, preserving the schema (columns) even when empty.

    Downstream consumers (DB-6) should never see a completely columnless
    parquet file just because a run happened to produce zero rows.
    """
    if not records:
        return pd.DataFrame(columns=[f.name for f in record_cls.__dataclass_fields__.values()])
    return pd.DataFrame([asdict(r) for r in records])


def write_tidy(
    items: list[ItemRecord],
    calls: list[CallRecord],
    out_dir: Path,
    suite_version: str,
    seed: int = 20260704,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Write items.parquet, calls.parquet, sensitivity.parquet under out_dir/<suite_version>/."""
    version_dir = out_dir / suite_version
    version_dir.mkdir(parents=True, exist_ok=True)

    items_df = _records_to_df(items, ItemRecord)
    calls_df = _records_to_df(calls, CallRecord)
    sensitivity_df = (
        compute_sensitivity(items_df, seed=seed) if not items_df.empty else pd.DataFrame()
    )

    # applied_injectors is a list-or-None column; parquet needs a consistent
    # object dtype, which pandas already gives it, but stringify for safety
    # across pyarrow versions rather than relying on nested-list inference.
    if not items_df.empty:
        items_df = items_df.copy()
        items_df["applied_injectors"] = items_df["applied_injectors"].apply(
            lambda v: json.dumps(v) if v is not None else None
        )

    items_df.to_parquet(version_dir / "items.parquet", index=False)
    calls_df.to_parquet(version_dir / "calls.parquet", index=False)
    if sensitivity_df.empty:
        sensitivity_df = _records_to_df([], SensitivityRow)
    sensitivity_df.to_parquet(version_dir / "sensitivity.parquet", index=False)

    return items_df, calls_df, sensitivity_df


def write_transcripts(
    transcripts: list[TranscriptRecord], out_dir: Path, suite_version: str
) -> Path:
    version_dir = out_dir / suite_version
    version_dir.mkdir(parents=True, exist_ok=True)
    path = version_dir / "transcripts.jsonl"
    with open(path, "w") as f:
        for t in transcripts:
            f.write(json.dumps(asdict(t)) + "\n")
    return path


# =============================================================================
# Orchestration
# =============================================================================


def run_pipeline(
    log_dir: str,
    out_dir: str,
    suite_version: str = DEFAULT_SUITE_VERSION,
    max_log_mb: float = DEFAULT_MAX_LOG_MB,
) -> PipelineSummary:
    """Read every log in log_dir, write tidy artifacts under out_dir/<suite_version>/."""
    summary = PipelineSummary()
    all_items: list[ItemRecord] = []
    all_calls: list[CallRecord] = []
    all_transcripts: list[TranscriptRecord] = []

    for loaded in iter_logs(log_dir, max_log_mb=max_log_mb):
        summary.logs_seen += 1
        if loaded.log is None:
            summary.logs_skipped.append(
                SkipRecord(path=loaded.path, reason=loaded.skip_reason or "unknown")
            )
            continue
        summary.logs_processed += 1

        calls = extract_calls(loaded.log, summary=summary)
        items = extract_items(loaded.log, summary=summary)
        transcripts = extract_transcripts(loaded.log)

        all_calls.extend(calls)
        all_items.extend(items)
        all_transcripts.extend(transcripts)

    out_path = Path(out_dir)
    items_df, calls_df, sensitivity_df = write_tidy(all_items, all_calls, out_path, suite_version)
    write_transcripts(all_transcripts, out_path, suite_version)

    summary.calls_written = len(calls_df)
    summary.items_written = len(items_df)
    summary.transcripts_written = len(all_transcripts)
    summary.sensitivity_rows = len(sensitivity_df)

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lofbench.pipeline",
        description="Build tidy site-ready artifacts from inspect_ai eval logs.",
    )
    parser.add_argument("log_dir", help="Directory of .eval logs (read one at a time)")
    parser.add_argument(
        "out_dir", help="Output directory; artifacts land under out_dir/<suite-version>/"
    )
    parser.add_argument("--suite-version", default=DEFAULT_SUITE_VERSION)
    parser.add_argument(
        "--max-log-mb",
        type=float,
        default=DEFAULT_MAX_LOG_MB,
        help="Skip (and report) logs larger than this many MB rather than fully loading them",
    )
    args = parser.parse_args(argv)

    summary = run_pipeline(args.log_dir, args.out_dir, args.suite_version, args.max_log_mb)
    print(summary.as_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
