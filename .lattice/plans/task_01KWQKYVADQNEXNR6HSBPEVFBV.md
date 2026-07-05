# DB-5: Results data pipeline: inspect logs to tidy site-ready data

## Scope

Build one entry point that reads `inspect_ai` eval logs from `logs/` and writes versioned, tidy artifacts: a per-item sample table, a per-call table, a sensitivity-score table, and transcript extracts. DB-6 (site) and DB-8 (release skill) read these artifacts; they do not read logs directly.

This plan is grounded in the 48 real logs currently in `logs/` and in `src/lofbench/analysis.py`, `core.py`, `datasets/factory.py`, `tasks/single.py`, `tasks/composite.py`, `scorers/lof_scorer.py` as they exist today (pre-DB-4). It also reads `.lattice/notes/design-decisions-2026-07-04.md` and `.lattice/notes/rendering-architecture-2026-07-04.md`, which bind the future (suite v1) provenance schema. Both realities matter here: this pipeline must run today against pilot data, and must be ready to consume DB-4's new provenance without a rewrite.

## What the real logs actually contain (checked, not assumed)

48 `.eval` files, all pre-DB-4. Findings that shape the schema below:

1. **Two task shapes, one sample unit each.** `single_lof_task` samples are one form per sample (5 logs). `composite_lof_task` samples are one bundled call of `group_size` (4 or 8) forms per sample, scored as a group (39 logs, the overwhelming majority of real data). The frozen suite v1 (per the design-decisions note) runs one form per call — this pipeline must still explode the existing composite bundles into per-form rows to be useful against real data and to implement the DB-7 "call as a statistical cluster" rule.

2. **The composite scorer's answer string has two incompatible schemas in production logs.** Parsing `score.answer` with `ast.literal_eval` across all 39 successful composite logs gives two distinct key sets: 28 logs use `{'results': [...], 'canonicals': [...]}` (current `lof_scorer.py`), 11 logs use `{'items': [...], 'total_marked': ...}` (an earlier scorer version, no longer in the source tree). Both encode a per-item marked/unmarked list positionally aligned with `metadata['targets']` and `metadata['original_expressions']`. The pipeline must detect and handle both; a third, unrecognised shape must be counted as a parse failure and reported, never silently dropped.

3. **Sample ids are not stable pairing keys.** `generate_test_cases`/`generate_composite_test_cases` (`core.py`) build one `random.Random(seed)` stream, shuffle a `difficulty` list whose length equals `n` (or `n_groups`), then draw forms in order. Shuffling a list of length 10 vs length 1000 consumes the RNG differently, so `lof_001` (or `comp_001`) denotes a different underlying form depending on the run's `n`. Confirmed: the 48 logs span `n`/`n_groups` in {2, 5, 10, 30, 100, 200, 1000}. A join on `id` across two logs with different `n` silently pairs unrelated forms. The true pairing key available today is the form string itself: `metadata['original_form']` (single) or `metadata['original_expressions'][i]` (composite, per exploded item) — this is renderer-invariant because both dialect renders come from the same `case["input"]` string when `seed` matches. Post-DB-4, `form_id` from the frozen `suites/v1.json` table is the stable key and should be preferred when present; fall back to the form string only for pre-suite (pilot) logs.

4. **No cost field exists anywhere in a log.** `sample.output.usage` gives `input_tokens`, `output_tokens`, `total_tokens`, `input_tokens_cache_write`, `input_tokens_cache_read`, `reasoning_tokens` (confirmed via direct read). There is no pricing table anywhere in the repo (`grep` across `.py`/`.md`/`.org`/`.json` found nothing). This pipeline must own a small versioned pricing table (`$` per million input/output/cache-read/cache-write tokens, per model) and derive `cost_usd` per call. DB-7's budget sheet will want the same rates; this task creates the table, DB-7 can cite it.

5. **Provenance is currently run-level, not sample-level.** `get_log_metadata` (analysis.py) reads `log.eval.task_args` for `renderer`/`renderer_config`/`mismatched` — correct today because one log = one dialect for the whole run. Per-sample `render_metadata` is `{}` for every single-task renderer currently in the tree (confirmed on a real `canonical` sample), but on composite samples the key is absent entirely, not an empty dict (the composite branch in `factory.py`/`composite.py` never writes it at all — this is the drop DB-4's plan flags and fixes in its M3). The extraction code must treat both cases identically: a None-safe `sample.metadata.get('render_metadata') or {}` lookup, never a bare `sample.metadata['render_metadata']` that would raise `KeyError` on composite pilot samples. Post-DB-4, `render_metadata` becomes the per-sample provenance record (`suite_version`, `form_id`, `dialect_id`, `family`, per-injector `applied`/`resample_count`), because injector coverage is form-dependent and can vary within one run (resample-or-drop). **`get_log_metadata`'s rewrite for the new fields belongs to DB-4** (already noted as DB-4's scope in the rendering-architecture doc); this pipeline calls whatever `get_log_metadata` returns for run-level fields (model, thinking tokens, reasoning effort, epochs) and separately reads per-sample `metadata['render_metadata']` for join keys and coverage once DB-4 lands. Until then, `render_metadata` is `{}` and the pipeline falls back to the run-level `dialect` string `get_log_metadata` already derives.

6. **Cancelled and errored runs exist and must not crash the pipeline.** One log is `status="cancelled"` (1000-group Opus run, `log.results is None`) and one is `status="error"`. `analysis.py`'s existing `get_log_results` already falls back to computing from samples when `log.results` is `None` — reuse that fallback rather than re-deriving it.

7. **Log file sizes span 4KB to 614MB.** Large composite logs with big `group_size`/`n_groups`/reasoning-token budgets get large fast. The pipeline should read logs one at a time and write incrementally (or at least avoid holding every `EvalLog` in memory simultaneously) rather than `load_logs()`-then-process-all, which the existing helper does for small analysis notebooks but will not scale to a full release run.

## Sequencing against DB-4 and DB-1 (docs)

- DB-4 (two-layer rendering architecture) has not landed. No log in `logs/` has non-empty `render_metadata`, a `suite_version`, a `form_id`, or a `dialect_id`. Every real log today is pilot data (v0) per the design-decisions amendment ("suite v1 is a clean break... prior runs become pilot data").
- This plan's entry point, schema, and statistics are written to be correct against **either** vintage: pre-DB-4 logs (dialect/family derived from `get_log_metadata`'s run-level string, form string as pairing key) and post-DB-4 logs (dialect_id/family/form_id read from per-sample `render_metadata`, once DB-4 ships that field and `get_log_metadata`'s rewrite). The record builder tries `render_metadata` first per sample and falls back to the run-level `get_log_metadata()` dialect string when `render_metadata` is empty or absent (composite samples omit the key entirely; the lookup must be None-safe, e.g. `sample.metadata.get('render_metadata') or {}`, not a bare key access), tagging the row `suite_version="pilot-v0"` in the fallback case.
- Practical order of work: build and test the whole pipeline now against the 48 real pilot logs (this is what "smallest real log" testing below uses). No part of this plan is blocked on DB-4. When DB-4 lands, only the provenance-extraction function needs a new branch (already designed for below); the tidy schema, explosion logic, sensitivity statistics and artifact writers do not change.
- DB-1 in this repo's lattice is a docs-only task (refresh CLAUDE.md/README) and is unrelated to this plan; do not confuse it with the rendering-architecture note's narrative use of "DB-1" for task-layer changes — those changes are DB-4's in this repo's actual numbering (confirmed via `lattice show DB-4`).

## Architecture

One module, `src/lofbench/pipeline.py`, with one CLI entry point:

```
uv run python -m lofbench.pipeline logs/ data/ [--suite-version pilot-v0]
```

No `[project.scripts]` entry exists in `pyproject.toml` today and none is required by DB-8's description ("run the data pipeline"); a `python -m` invocation is one command and is enough. If DB-8's skill author later wants a `bench` verb, that is a one-line addition, not a redesign.

Pipeline stages, each a plain function so tests can call them independently:

1. `iter_logs(log_dir) -> Iterator[EvalLog]` — thin wrapper over `inspect_ai.log.list_eval_logs` + `read_eval_log`, one log resident at a time.
2. `extract_calls(log) -> list[CallRecord]` — one row per (sample id, epoch): run-level fields via `get_log_metadata(log)`, call-level fields (`cost_usd`, token counts, wall time) from `sample.output.usage`, `call_id = f"{log.eval.task_id}:{sample.id}:{sample.epoch}"`.
3. `extract_items(log, call) -> list[ItemRecord]` — explode each call into one row per scored form: for `single_lof_task`, one row (score.value == "C"); for `composite_lof_task`, `group_size` rows via the dual answer-schema parser in finding 2, each joined to `metadata['targets'][i]` and `metadata['original_expressions'][i]`, tagged with the shared `call_id`.
4. `write_tidy(items, calls, out_dir, suite_version)` — parquet artifacts.
5. `compute_sensitivity(items) -> DataFrame` — paired contrast per (model, dialect) against the canonical dialect, per the rules below.
6. `extract_transcripts(log) -> list[TranscriptRecord]` — full completion text plus correctness, written as JSONL (not parquet — this is unstructured text for a walkthrough page, not chart data).

## Schema

### `items` table (`data/<suite_version>/items.parquet`) — the tidy per-scored-form record

| column | type | source | notes |
|---|---|---|---|
| `call_id` | str | derived | groups exploded rows back to one API call; use for cluster bootstrap |
| `item_index` | int | position in call | 0 for single-task; 0..group_size-1 for composite |
| `form_id` | str | `render_metadata['form_id']` if present, else a stable hash of the form string | join key, see finding 3 |
| `form_string` | str | `original_form` / `original_expressions[i]` | always present; the fallback pairing key |
| `dialect_id` | str | `render_metadata['dialect_id']` if present, else `get_log_metadata(log)['dialect']` | e.g. `canonical`, `noisy-mismatch` |
| `family` | str | `render_metadata['family']` if present, else derived from `renderer` | grouping key |
| `modality` | str | `render_metadata['modality']` if present, else derived from `renderer` | `"text"` or `"spatial"`, per DB-6's requirement |
| `format` | str | `render_metadata['format']` if present, else derived from `renderer` | `"text"` or `"image"`, lets DB-6 pick string vs image display |
| `suite_version` | str | `render_metadata['suite_version']` if present, else `"pilot-v0"` | |
| `model` | str | `log.eval.model` | |
| `reasoning_setting` | str \| None | `get_log_metadata(log)` | shared field name with DB-6 and `calls`; do not use `reasoning_effort` |
| `thinking_tokens` | int | `get_log_metadata(log)` | |
| `difficulty` | str | sample metadata | |
| `depth` | int \| None | sample metadata (`single` only today) | |
| `steps` | int \| None | sample metadata (`single` only today) | |
| `target` | str | `marked`/`unmarked` | |
| `predicted` | str | parsed answer | `unknown` counts as incorrect, never dropped |
| `correct` | bool | `predicted == target` | |
| `epoch` | int | `sample.epoch` | |
| `applied_injectors` | list[str] \| None | `render_metadata['injectors']` where `applied: True`, else `None` | coverage-adjustment support |

### `calls` table (`data/<suite_version>/calls.parquet`) — one row per API call, for budget/cost rollups

| column | type | notes |
|---|---|---|
| `call_id` | str | join key back to `items` |
| `model`, `dialect_id`, `suite_version`, `reasoning_setting` | str | denormalised for convenience; `reasoning_setting` is the shared field name with `items` and DB-6, not `reasoning_effort` |
| `input_tokens`, `output_tokens`, `reasoning_tokens`, `cache_read_tokens`, `cache_write_tokens` | int | from `ModelUsage` |
| `cost_usd` | float | from the pricing table, see below |
| `group_size` | int | 1 for single-task, else the bundle size |
| `all_correct` | bool \| None | the scorer's own aggregate, kept for parity with existing `analysis.py` metrics |

**Rule, stated once and enforced by a test:** cost and token totals are summed over distinct `call_id` in `calls`, never over rows of `items`. Summing `items.cost_usd` (if that column existed) would overcount every composite call by `group_size`x. This is why `cost_usd`/tokens live only on `calls`.

This is the shared contract DB-6 relies on: DB-6 joins tokens and cost by reading the `calls` table and joining on `call_id`, never by reading a per-item cost field (there is none, by design, for exactly the double-counting reason above).

### `sensitivity` table (`data/<suite_version>/sensitivity.parquet`)

One row per (model, reasoning_setting, dialect_id) contrast against that model's canonical-dialect run at the same reasoning setting, computed per the rules below.

| column | type |
|---|---|
| `model`, `reasoning_setting`, `dialect_id`, `suite_version` | str |
| `n_paired` | int — distinct forms present in both the canonical and treatment arm, after de-dup |
| `n_distinct_forms` | int — distinct `form_string` count used for the CI, per the duplicate-forms rule in the design note |
| `accuracy_canonical`, `accuracy_treatment` | float |
| `paired_drop` | float — `accuracy_canonical - accuracy_treatment` |
| `mcnemar_b`, `mcnemar_c` | int — discordant pair counts (canonical-correct/treatment-wrong vs the reverse) |
| `mcnemar_p` | float — exact binomial McNemar p-value |
| `bootstrap_ci_low`, `bootstrap_ci_high` | float — 95% bootstrap CI on `paired_drop`, resampled over distinct forms (or over `call_id` clusters when the arm is composite-bundled) |
| `coverage` | float — fraction of items in this dialect where the relevant injector(s) had `applied: True`; `1.0` when `applied_injectors` is unavailable (pre-DB-4 logs, no injector concept yet) |

Every written artifact file (`items.parquet`, `calls.parquet`,
`sensitivity.parquet`, `transcripts.jsonl`) carries a `schema_version`
field stamped by the pipeline, distinct from `suite_version`. This lets a
schema change be diffable and lets DB-6 fail loudly on a mismatch, per
DB-6's own artifact-versioning requirement.

### `transcripts` (`data/<suite_version>/transcripts.jsonl`)

One JSON object per scored form: `call_id`, `item_index`, `form_id`, `form_string`, `dialect_id`, `model`, `correct`, `target`, `predicted`, `full_completion_text`, `rendered_input` (text) or a note that input was an image (do not inline image bytes into JSONL). DB-6 selects which to show; this pipeline extracts everything scoreable, it does not curate "the best" examples.

## Sensitivity score computation rules (binds to the design-decisions and rendering-architecture notes)

- **Pairing:** join `items` on `(form_id, model, reasoning_setting)` where one side has `dialect_id == "canonical"` (or `parens.canonical` post-DB-4) and the other has the treatment `dialect_id`. Use `form_string` as the join key when `form_id` is absent (pilot logs) — per finding 3, never join on `call_id`/sample `id` alone across logs with different `n`.
- **De-duplication:** `generate_form_string` can and does emit repeated trivial forms (`()`, `(())`) at low difficulty under distinct ids. Before computing McNemar/bootstrap, de-duplicate by distinct `form_string`, keeping one row per distinct form per arm (first occurrence), and report `n_distinct_forms` alongside raw `n_paired`. This follows the design note's pseudo-replication rule directly; do not skip it even though today's small `n` (some logs) makes duplicates rare — a 1000-form run will not be rare.
- **Coverage adjustment:** when `applied_injectors` shows an injector was not applied on some items (post-DB-4 resample-or-drop), exclude those items from that injector's paired contrast, or report the coverage-adjusted drop explicitly. Pre-DB-4 logs have no injector concept — treat `coverage = 1.0` and skip the exclusion.
- **McNemar:** implement the exact binomial form (no `scipy`/`statsmodels` — neither is installed nor a declared dependency; `pyproject.toml`'s constraint is "prefer stdlib, minimize deps," and `analysis.py` already hand-rolls binomial maths with `math.comb` for the composite MAE baseline — follow that precedent). `p = 2 * min(P(X <= min(b,c)), P(X >= max(b,c)))` under `X ~ Binomial(b+c, 0.5)`, using `math.comb`.
- **Bootstrap CI:** resample distinct forms (or `call_id` clusters, for composite-bundled data, per DB-7's "call as a statistical cluster" rule) with replacement, recompute `paired_drop` each resample, take the 2.5/97.5 percentiles. Use `random.Random(seed)` for reproducibility; no `numpy` bootstrap needed at this scale.
- **Pilot data is not compared across suite versions.** The pipeline computes `sensitivity` per `suite_version` independently. A `pilot-v0` row and a future `v1` row for the same model/dialect are not the same measurement and must never be diffed by downstream code; this is a reporting convention, not something the pipeline needs to block, but the plan records it so DB-6/DB-8 don't quietly do it.

## Pricing table

New small module, `src/lofbench/pricing.py`: a dict keyed by model id string to `$`/1M tokens for input, output, cache read, cache write, sourced from each provider's published rates at time of writing, with a `PRICING_TABLE_VERSION` string stamped into the `calls` table's provenance. Models seen in real logs today needing entries: `anthropic/claude-sonnet-4-20250514`, `anthropic/claude-sonnet-4-5-20250929`, `anthropic/claude-opus-4-5-20251101`, `openai/gpt-5.2`, `google/gemini-2.5-flash`, `google/gemini-3-flash-preview`, `google/gemini-3-pro-preview`, `google/gemini-3.0-pro`. An unknown model logs a warning and gets `cost_usd = None` (never a silent zero, which would look like a free run in a budget rollup).

## Test plan

- **Fixture tests (fast, run every CI pass):** a small synthetic `EvalLog`-shaped fixture (or the smallest real log, `2025-12-15T20-45-53..._single-lof-task_...eval` at 14KB / n=?, and the smallest composite log) covering: single-task explosion (trivial, 1 row per call), composite explosion under both answer schemas (`results`/`canonicals` and `items`/`total_marked`), an `unknown`-schema answer forced via a monkeypatched sample to prove it is counted, not dropped, a cancelled-run log (reuse `get_log_results`'s existing fallback path) and an all-`unknown` predicted case (structural: `unknown != target` always scores incorrect, never excluded).
- **Real-log integration test:** run the full pipeline against `logs/` as checked into this repo (48 files) and assert: total `items` rows equal `sum(group_size or 1)` over all successfully-parsed calls in all logs, minus a small, explicitly asserted set of known parse-failure rows (from finding 2's schema drift, if any log actually hits the third/unknown case — verify this empirically during implementation rather than assuming zero); total `calls` rows equal total (sample, epoch) pairs across all logs; the `canonical` vs `noisy_parens` sensitivity join for `google/gemini-2.5-flash` (which has both dialects, matching `n_groups=100`, in the fixture set) is non-empty and its `n_distinct_forms <= n_paired`.
- **Reconciliation acceptance test:** for one representative log, hand-count `group_size * n_groups` (or `n * epochs`) from `log.eval.task_args`/`log.eval.config` and assert the pipeline's row count for that log matches exactly (accounting for `cancelled`/`error` truncation, which must be counted, not hidden).
- Run `uv run pytest` and `uv run ruff check .` clean as part of the acceptance bar.

## Acceptance criteria

1. `uv run python -m lofbench.pipeline logs/ <tmp-out>` runs against the full checked-in `logs/` directory without crashing on the cancelled or errored log, and produces `items.parquet`, `calls.parquet`, `sensitivity.parquet`, `transcripts.jsonl` under `<tmp-out>/pilot-v0/`.
2. `items` row count and `calls` row count reconcile exactly with log totals as described in the test plan; any excluded/unparseable row is counted and reported in a printed summary, never silently dropped.
3. The canonical-vs-treatment pairing join (on `form_id` falling back to `form_string`) is non-empty for at least one real (model, dialect) pair in the checked-in logs.
4. `n_distinct_forms` is reported alongside `n_paired` in `sensitivity.parquet` and is `<=` it wherever duplicate trivial forms exist.
5. `calls.cost_usd` is populated (or explicitly `None` with a logged warning) for every model seen in the fixture logs; no accidental double counting when rolling up total spend (covered by the schema separation between `items` and `calls`).
6. `uv run pytest` and `uv run ruff check .` pass.

## Risks and open items for the implementer

- The exact smallest real log to use as the "small fixture" should be picked at implementation time by re-running the `list_eval_logs` scan in this plan (file sizes and sample counts are recorded above but re-verify before writing the test, in case another agent has added logs since this plan was written).
- If DB-4 lands mid-implementation, only `extract_items`'s provenance-reading branch needs a second code path (already reserved in the schema table above); do not block on DB-4, and do not duplicate `get_log_metadata`'s rewrite — call it.
- Pricing rates will drift; stamp `PRICING_TABLE_VERSION` so a rerun with updated rates is diffable, per the same content-addressing instinct the rendering-architecture note applies to renderers.
- The `render_metadata == {}` fallback path (pre-DB-4) is the one this plan spent the most verification effort on, precisely because it is what will actually run first. Do not treat the DB-4 provenance branch as the primary path during initial implementation — it has no real log to test against yet.

marker: plan-wave-2-20260704
