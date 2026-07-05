---
name: bench-release
description: Walk a distinction-bench operator through a model-drop release — cost sheet and approval gate, eval grid, data pipeline, site refresh, sanity check, writeup draft. Triggers on "run a release", "new model dropped", "release run for distinction-bench".
---

# bench-release

This skill is a checklist and command reference, not automation. It runs when a new
model ships and someone decides to spend money finding out how it does. Follow the
steps in order. Step 2 is a hard stop: do not call any paid API before a human types
an explicit go.

Every command below was run against the merged code on 2026-07-05, most against a
free mock model, to confirm the flags and paths are real. Where a fact changed since
this skill was drafted, re-check it — do not trust this file over the code.

## 0. Preconditions (hard stop before anything else)

Check all of these before step 1. If any fails, stop and fix it first.

- **Working tree is clean.** `git status --short` prints nothing. A release run should
  not mix in uncommitted renderer or task changes.
- **Provider API keys are set** for every provider this run's tiers will use
  (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, and so on — match the
  `--model` strings you plan to use in step 3).
- **The frozen suite verifies.** Run:

  ```bash
  uv run python -m lofbench.suites --verify-only
  ```

  This defaults to suite version `v1` and re-renders every one of its cells, checking
  each against the payload hash frozen in `suites/v1.json`. A clean run prints
  `Suite 'v1' rerun gate: OK.` A failure means an un-versioned renderer edit changed a
  stimulus — do not proceed; fix the renderer or re-freeze the suite first.

  `suites/v1.json` itself exists in this repo and holds 120 forms across 5 difficulty
  tiers and 29 dialects (`python3 -c "import json; d=json.load(open('suites/v1.json'));
  print(d['header'])"` shows the header). That part of the old "no frozen suite"
  problem is fixed.

- **Read this next warning before step 3. It is the single most important fact in this
  skill.** Having a verified `suites/v1.json` does **not** mean `inspect eval` runs
  suite v1 today. There is no `--suite` flag and no code path that feeds the frozen
  form table or the frozen, `suite_version`-threaded dialect specs into
  `single_lof_task`. Confirmed by running it: `single_lof_task`'s `n`/`seed` generate
  forms through `lofbench.core.generate_test_cases`, a different generator from the
  one that built the frozen table (`lofbench.suites.generate_form_table`), so a form
  id like `lof_001` means a different form string in each path. Every sample metadata
  stamps `suite_version: "adhoc"` regardless of which dialect you pick — even when you
  pass a dialect id straight out of `suites/v1.json`, e.g. `parens.canonical` — because
  the live `DIALECT_SPECS` registry `get_renderer` resolves against is built with the
  dataclass default `suite_version="adhoc"`, not the suite-version-threaded copy
  `load_suite` reconstructs. **So: every eval run possible today is pilot-grade,
  dialect-name-and-count-matched to v1 by convention, not identity-verified against
  v1's payload hashes.** Say this plainly in the release writeup (step 7) rather than
  letting a reader assume "suite v1" appears anywhere in an actual log's metadata. This
  is a gap for a follow-up task (a suite-runner CLI), not something to route around
  here.

- **Confirm pricing coverage before spending, not during.** `src/lofbench/pricing.py`'s
  `PRICING_TABLE` prices calls by the exact `--model` string. It currently has no
  entry for Haiku (the measurement doc's tier-1 pinned model) or for Qwen3-235B on
  OpenRouter (the tier-3 pinned model) — confirmed by grep against the table's keys.
  `compute_cost_usd` returns `None`, never a silent `0.0`, for a model it cannot price
  (confirmed by running the pipeline against a log from an unpriced mock model: it
  printed `WARNING: no pricing entry for model 'mockllm/model'; cost_usd=None` and
  left every affected row's cost blank rather than zero). A blank cost is invisible in
  a budget rollup, so add the missing `ModelRates` row, with a dated source comment
  per the file's existing convention, for any tier-1 or tier-3 model before running
  it. Adding rows for the Sonnet, Opus and Gemini 3 Flash tiers is not needed — they
  are already there.

## 1. Confirm the model

Write down, for the model that just shipped:

- The exact `--model` string `inspect eval` will use (provider-qualified, e.g.
  `anthropic/claude-haiku-4-5-<date>`, `google/gemini-3-flash-preview`,
  `openrouter/<org>/<model>`).
- Which reasoning control the provider exposes: `--reasoning-tokens` (an integer
  budget — Anthropic models only, confirmed against `inspect eval --help`) or
  `--reasoning-effort` (`none`/`minimal`/`low`/`medium`/`high`/`xhigh` — other
  providers, same `--help` output).
- Which of the four measurement-doc tiers (section 8) it belongs in: cheap closed
  (tier 1), flagship (tier 2), open-weight via OpenRouter (tier 3), or a
  reasoning-toggle pair over an existing tier's model (tier 4).

Tier 4 (reasoning on/off pairs) is fully wired end to end today: `reasoning_setting_of`
in `pipeline.py` reads the run's reasoning config off the eval log and stamps a
`reasoning_setting` column on `items`, `calls` and `sensitivity` alike. Treat tier 4 as
runnable once the precondition step is clear — no gap to flag there.

## 2. Produce the cost sheet and get approval (the hard gate)

**Do not send a single request to a paid API before this step closes with an explicit
yes.**

1. Re-check every price in `docs/measurement-design.md` section 9's price table
   against the provider's current published rate — prices drift, and the doc says so.
2. Adapt section 9's reproducible Python snippet: swap in this run's real per-token
   input/output prices, and the actual dialect count and form count you intend to run
   (not the worked example's numbers, unless you are running that exact configuration).
3. Produce a line-item table in this shape, one row per tier/model/reasoning-setting
   combination you plan to run:

   | Line | Model (pricing) | Reasoning | Calls | $/call | Line cost |
   |------|-----------------|-----------|-------|--------|-----------|

4. Sum to a total. **Stop here. Print the total. Ask the operator to type an explicit
   go or no-go.** A silent default, a "proceeding unless you object," or any path that
   lets the run start without a typed yes fails this step — redo it if that happens.
5. If the total exceeds 150 US dollars (the stretch cap from the design-decisions
   note), refuse to proceed. Cut scope (fewer forms, fewer dialects, drop a tier) and
   recompute before asking again. Do not round down or "call it close enough."
6. If any planned model is missing from `PRICING_TABLE` (see step 0), add its
   `ModelRates` row now, before computing the sheet that depends on it.

## 3. Run the evaluation grid (batch APIs)

Read the step-0 warning again before running anything here: none of this cites
suite v1 in a verified sense yet. Pick dialect ids from `suites/v1.json`'s 29 entries
by name and match its per-tier form counts by convention, and record that choice in
the writeup.

One `inspect eval` invocation per (tier, dialect) cell. `single_lof_task` accepts
`n`, `seed`, `renderer`, `render_seed`, `renderer_config` via `-T`; there is no other
task-specific flag. One fully worked example per tier, confirmed to load and score
against a free mock model before this skill was written:

**Tier 1 — cheap closed model, full grid.** One dialect per invocation; repeat once
per dialect id (list them with
`python3 -c "import json; print(sorted(json.load(open('suites/v1.json'))['dialects']))"`).
Visual dialects run on this tier's cheap multimodal model first, before any flagship
image spend — that ordering is a rule, not a suggestion.

```bash
uv run inspect eval src/lofbench/tasks/single.py \
  --model anthropic/claude-haiku-4-5-<date> \
  -T n=400 -T seed=20260704 -T renderer=parens.canonical \
  --batch \
  --log-dir logs/release-<date>/tier1/
```

**Visual gate — cheap multimodal model, image dialects.** Same shape, an image
dialect (`enclosure.canonical-v1`, `rooms.canonical-v1`, etc. — nine spatial dialects
in suite v1, all `format: image`):

```bash
uv run inspect eval src/lofbench/tasks/single.py \
  --model google/gemini-3-flash-preview \
  -T n=400 -T seed=20260704 -T renderer=enclosure.canonical-v1 \
  --batch \
  --log-dir logs/release-<date>/visual/
```

**Tier 2 — flagship core.** Suite v1 does not itself flag which dialects form "the
frozen core" (measurement doc section 7 sets selection rules but leaves the final list
to the run). Pick a fixed subset — always including `parens.canonical` as the
reference arm — and record exactly which ids you picked in the writeup, so the next
release run can reuse the same list:

```bash
uv run inspect eval src/lofbench/tasks/single.py \
  --model anthropic/claude-sonnet-4-5-20250929 \
  -T n=120 -T seed=20260704 -T renderer=parens.canonical \
  --batch --reasoning-tokens 10000 \
  --log-dir logs/release-<date>/tier2/
```

**Tier 3 — open-weight via OpenRouter.** No batch tier from OpenRouter (confirmed:
`--batch` is a no-op there per the design doc); price at standard plus the 5.5 percent
credit fee:

```bash
uv run inspect eval src/lofbench/tasks/single.py \
  --model openrouter/qwen/qwen3-235b-a22b \
  -T n=120 -T seed=20260704 -T renderer=parens.canonical \
  --reasoning-effort high \
  --log-dir logs/release-<date>/tier3/
```

**Tier 4 — reasoning-toggle pair.** Same model, same dialect list, run twice — once
with reasoning on, once off. The V1.1 pilot found Gemini 3 Flash swung from 22 percent
to 95 percent between these two settings on one noisy dialect, which is the whole
reason this tier exists:

```bash
# reasoning on
uv run inspect eval src/lofbench/tasks/single.py \
  --model google/gemini-3-flash-preview \
  -T n=120 -T seed=20260704 -T renderer=parens.canonical \
  --batch --reasoning-effort high \
  --log-dir logs/release-<date>/tier4-on/

# reasoning off
uv run inspect eval src/lofbench/tasks/single.py \
  --model google/gemini-3-flash-preview \
  -T n=120 -T seed=20260704 -T renderer=parens.canonical \
  --batch --reasoning-effort none \
  --log-dir logs/release-<date>/tier4-off/
```

Done when: every planned tier has a log directory under `logs/release-<date>/`, and
none of it ran before step 2's approval closed.

## 4. Run the data pipeline

```bash
uv run python -m lofbench.pipeline logs/release-<date>/ data/release-<date>/ --suite-version v1
```

Confirmed by running this against a real log: it writes
`items.parquet`, `calls.parquet`, `sensitivity.parquet` and `transcripts.jsonl` under
`data/release-<date>/v1/`, and prints a summary (logs seen/processed/skipped, calls
and items written, sensitivity rows, transcripts written). The `headline.parquet`
naming mismatch this skill's plan warned about is gone — the pipeline and the site
now agree on `items`/`calls`/`sensitivity`/`transcripts`.

The `--suite-version v1` flag here is bookkeeping for where the artifacts land and how
they get labelled; it does not mean the underlying eval logs are verified-v1 data, per
the step-0 warning. Label it `v1` if that is genuinely what this run is meant to
represent (per your own judgement call in step 3's tier-2 note), otherwise use a
distinct version string so this run's artifacts never get silently mixed with a real
future v1 run's.

## 5. Rebuild the site

Charts bake fresh from the parquet/JSONL artifacts on every page request — confirmed
by booting the site against a freshly-piped output directory and requesting `/charts`.
There is no separate build step and no restart needed to see new data; only point the
environment variables at the new output and the next request re-bakes:

```bash
LOFSITE_DATA_DIR=data/release-<date> LOFSITE_SUITE_VERSION=v1 \
  uv run --extra site python -m lofsite.app
```

Confirm, in order:

- `/` (sandbox) and `/axioms` return 200 and render.
- `/charts`, `/matrix`, `/walkthroughs` return 200. With real paired data present they
  render actual charts (confirmed: `lofsite/pages/charts.py` calls
  `sensitivity_svg`/`accuracy_matrix_svg` against real rows, not a stub). If a page
  still shows the "awaiting suite v1 data" placeholder, that means the pipeline found
  no matching `schema_version`-stamped artifact at that path — check the env vars and
  the pipeline's output directory again before assuming a code bug.
- Restarting the site process is a manual step; `deploy/lofsite.service` is an example
  unit only, not wired to any deploy automation. Deploying to Valerie's server is her
  manual step, out of scope here.

## 6. Sanity-check against prior suite versions

Compare this run's per-model, per-dialect numbers in the new `sensitivity.parquet`
against the previous suite-version run's `sensitivity.parquet`, as a smell test only —
does a new model's ranking and rough sensitivity level look plausible next to what
came before, not a rigorous statistical claim.

Restate measurement-doc section 12 explicitly: v0 pilot numbers (`pilot-v0` and
earlier) are never compared to v1 numbers in anything published. This comparison step
is for the operator's own sanity, not a chart or claim for the writeup.

## 7. Draft the external writeup

This skill drafts, from the run's own artifacts:

- The headline sensitivity number(s) per model, and the worst-case dialect per model
  (`sensitivity.parquet`).
- One or two notable surprises pulled from `transcripts.jsonl` (a model that reasoned
  correctly about containment but still answered wrong, an unexpectedly large
  reasoning-toggle swing, and so on).
- One or two verbatim transcript exhibits.

This skill does not draft framing, narrative or the decision to publish. Those stay
Valerie's. Do not ship copy under her name — hand over the data and exhibits and stop.

## Known gaps, current as of 2026-07-05

State these to the operator up front; do not let a run surprise them mid-way.

1. **No eval-time suite-runner.** `suites/v1.json` exists and passes its own rerun
   gate, but nothing feeds its frozen form table or suite-version-threaded dialect
   specs into `single_lof_task`. Every real run today is dialect-name-matched to v1,
   not identity-verified against it.
2. **`PRICING_TABLE` is missing Haiku and Qwen3-235B/OpenRouter entries**, both pinned
   models in the measurement doc's tiers 1 and 3. Add them before costing or running
   those tiers.
3. **Suite v1 does not designate a "flagship core" dialect subset.** The operator
   picks it per run, per measurement-doc section 7's rules, and should record the
   choice.

Fixed since this skill's plan was written: the pipeline/site artifact-name mismatch
(`headline.parquet` never existed; both sides now agree on
`items`/`calls`/`sensitivity`/`transcripts`), and real chart baking on the site (no
longer a phase-1 placeholder when data is present).
