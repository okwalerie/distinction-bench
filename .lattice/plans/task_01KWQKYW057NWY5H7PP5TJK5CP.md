# DB-8: Release-cadence skill: new-model-drop to refreshed charts

## What this task builds

A `SKILL.md` at `.claude/skills/bench-release/SKILL.md`. It is not automation
— it is a checklist-and-command-reference an operator (Valerie, or an agent
acting for her) follows by hand each time a new model ships. It walks the
seven steps in the task description, with the budget approval gate as a
hard stop.

Skill name: `bench-release`. Location: `.claude/skills/bench-release/`
(standard Claude Code project-skill discovery path). Format follows
`skills/lattice/SKILL.md`'s frontmatter convention: `name`, `description`,
one `#` heading, then plain-style prose and command blocks.

## Binding sources

- `.lattice/notes/design-decisions-2026-07-04.md` — process rules (releases
  are manual, budget 100-150 USD, GOV.UK style).
- `docs/measurement-design.md` — the doc the skill operationalises.
  Section 8 (model grid, four tiers), section 9 (worked budget sheet,
  including the reproducible Python snippet), section 7 (frozen suite v1
  rules, payload-hash gate), section 12 (v0/v1 break statement).
- `src/lofbench/pipeline.py` — merged DB-5 CLI:
  `uv run python -m lofbench.pipeline logs/ data/ --suite-version v1`.
- `src/lofsite/README.md` and `src/lofsite/data.py` — merged DB-6 phase 1.

## Verified facts that change the plan

I ran or read the actual merged code, not just the docs. Three things the
docs imply are ready are not, and the skill must say so instead of
pretending otherwise.

1. **No frozen suite file exists.** `suites/v1.json` (named in measurement
   doc section 7) does not exist in this repo. There is no suite-loading
   CLI path either — `single_lof_task` in `src/lofbench/tasks/single.py`
   takes `n`/`seed`/`renderer`/`renderer_config`, not `--suite`. "Frozen"
   today means "the operator pins the same n/seed/renderer values by hand
   across runs," not an actual checked-in, hash-verified suite file. This
   is the DB-4 M7 dependency the task told me to flag. **The skill's step 0
   must refuse to proceed past the budget gate if `suites/v1.json` is
   absent**, and must say plainly that until it lands, any run is
   pilot-grade (v0-style), not a v1 release.

2. **The pipeline and the site disagree on an artifact name.**
   `run_pipeline` in `pipeline.py` writes `items.parquet`, `calls.parquet`,
   `sensitivity.parquet` under `out_dir/<suite-version>/`. `lofsite/data.py`
   looks for `items.parquet` and **`headline.parquet`** — a file DB-5 never
   writes. This is a real, unresolved DB-5/DB-6 contract gap (lofsite's own
   docstring calls it "not yet fully fixed"). The skill's rebuild step must
   name this explicitly and tell the operator to treat a missing
   `headline.parquet` as expected today, not a bug to chase.

3. **The site does not bake charts yet, even with data present.**
   `src/lofsite/pages/charts.py` is phase-1: when `headline_status().available`
   is true it prints "data found, but phase-1 baking is not yet
   implemented" — it does not render a chart. `deploy/lofsite.service` is
   documented in the README as "illustrative, not a deployment step."
   **So step 5 (rebuild and redeploy the site) cannot yet produce refreshed
   charts on the live site — that is blocked on DB-6 phase-2 work, not on
   anything DB-8 can do.** The skill must say this and scope step 5 down to
   what is actually possible today: point `LOFSITE_DATA_DIR` at the new
   pipeline output, restart the site process, confirm the sandbox and
   axioms pages still boot, and confirm charts/matrix/walkthroughs show the
   "data found" placeholder (proof the pipeline output was found) rather
   than a live chart.

4. **Tier 4 (reasoning toggle) is actually ready.** `reasoning_setting_of`
   and the `reasoning_setting` column exist end-to-end in `pipeline.py`
   (items, calls, and sensitivity tables all carry it). Measurement-doc
   section 8's stated precondition is met — no gap to flag here, the skill
   can treat tier 4 as runnable once a frozen suite exists.

5. **Pricing-table drift.** `src/lofbench/pricing.py`'s `PRICING_TABLE` is
   keyed by literal inspect-ai model strings
   (`anthropic/claude-sonnet-4-5-20250929`, `google/gemini-3-flash-preview`,
   etc.) and has **no Haiku entry**, despite the measurement doc's tier-1
   pinned config using "Haiku 4.5." The skill's cost-sheet step must
   reconcile the informal model names in the measurement doc against real
   inspect-ai model strings and `PRICING_TABLE` keys, and must tell the
   operator to add a `ModelRates` row (with a dated source comment, per the
   file's own convention) before a run if the chosen model is missing —
   otherwise `compute_cost_usd` cannot price that model's calls at all
   during the pipeline step.

6. **`inspect eval` CLI confirmed** (ran `uv run inspect eval --help`):
   `--batch [TEXT]`, `--reasoning-tokens INTEGER` (Anthropic only),
   `--reasoning-effort [none|minimal|low|medium|high|xhigh]` all exist as
   real flags. The skill's example invocations use these, not invented
   syntax.

## Skill outline

Frontmatter:
```yaml
---
name: bench-release
description: Walk a distinction-bench operator through a model-drop release — cost sheet and approval gate, eval grid, data pipeline, site refresh, sanity check, writeup draft. Triggers on "run a release", "new model dropped", "release run for distinction-bench".
---
```

Body sections, each ending in a checkbox-style "done when" line:

**0. Preconditions (hard stop before anything else)**
- Confirm `suites/v1.json` exists. If absent: stop, tell the operator this
  is a v0-style pilot run at best, point at DB-4 M7, do not proceed to
  step 2 without explicit operator override.
- Confirm the model's id/provider/reasoning options exist in
  `PRICING_TABLE` (or note that they must be added) and in whichever
  inspect-ai provider the model uses.

**1. Confirm the model**
- Model id as the provider names it, provider, reasoning-toggle options
  (tokens vs effort vs none), which tier(s) it belongs in (measurement doc
  section 8).

**2. Produce the cost sheet and get approval (the hard gate)**
- Re-derive the worked budget sheet (measurement doc section 9) for this
  specific model/run: adapt the reproducible Python snippet in section 9,
  substituting this model's real price (re-checked against the provider's
  current published rate — prices drift, the doc says so explicitly) and
  the actual form/dialect counts intended for this run.
- Output a line-item table mirroring measurement doc section 9's format:
  tier, model, reasoning setting, calls, $/call, line cost, total.
- **Explicit stop: print the total, ask the operator to type an explicit
  go/no-go. Do not spend against any API before this confirmation.** This
  is Valerie's money; the gate cannot be skipped or defaulted-yes.
- If total exceeds 150 USD stretch cap, refuse and require the operator to
  cut scope first.

**3. Run the evaluation grid (batch APIs)**
- One `inspect eval` invocation per (tier, dialect) cell, using
  `single_lof_task` args (`n`, `seed`, `renderer`, `renderer_config`) plus
  `--model`, `--batch`, and the reasoning flag appropriate to that model
  (`--reasoning-tokens` for Anthropic, `--reasoning-effort` for others, per
  `--help` output). Give one fully worked example command per tier so the
  operator can copy-adjust, not four abstractly-described tiers.
- Visual dialects run on the cheap multimodal tier first per section 8,
  before any flagship spend on images — restate this ordering as a rule,
  not a suggestion.

**4. Run the data pipeline**
- `uv run python -m lofbench.pipeline logs/ data/ --suite-version v1`.
- Note the artifact set it actually writes (`items.parquet`,
  `calls.parquet`, `sensitivity.parquet`, `transcripts.jsonl`), and the
  known `headline.parquet` gap from finding 2 above.

**5. Rebuild the site (scoped to what's real today)**
- Point `LOFSITE_DATA_DIR=data/v1` (or wherever step 4 wrote), restart
  `uv run python -m lofsite.app`.
- Confirm sandbox and axioms pages boot.
- Confirm charts/matrix/walkthroughs show "data found" rather than
  "awaiting suite v1 data" — that is the extent of what's verifiable until
  DB-6 phase 2 ships real chart baking. State this limit explicitly so the
  operator doesn't go looking for a chart that isn't rendered yet.
- Restarting the process is a manual step today — `deploy/lofsite.service`
  is illustrative only, not wired to any deploy automation.

**6. Sanity-check against prior suite versions**
- Compare this run's per-model, per-dialect headline numbers against the
  previous suite-version's `sensitivity.parquet`, as a smell test only.
- Restate measurement doc section 12's rule explicitly: v0 pilot numbers
  are never compared to v1 numbers in any published claim. This
  comparison step is an internal sanity check for the operator, not a
  chart or claim that goes in the writeup.

**7. Draft the external writeup**
- The skill drafts: headline sensitivity number(s), the worst-case
  dialect per model, one or two notable surprises pulled from
  `transcripts.jsonl`, and one or two verbatim transcript exhibits.
- The skill does not draft: framing, narrative, or publication decisions —
  those stay Valerie's. State this division explicitly in the skill so a
  future agent doesn't over-reach and ship copy under her name.

## Acceptance criteria

- `SKILL.md` exists at `.claude/skills/bench-release/SKILL.md` with valid
  frontmatter (`name`, `description`) matching the repo's existing
  convention (`skills/lattice/SKILL.md`).
- `description` triggers on phrasings like "run a release," "new model
  dropped," "release run for distinction-bench" — a fresh Claude Code
  session must be able to find this skill from those phrasings alone.
- The step-2 budget/cost-sheet gate is written as a hard stop with no
  silent-default path forward — an implementer cannot satisfy this
  criterion by writing "ask for approval" as a soft suggestion; it must
  read as blocking.
- Step 0 makes the run refuse to proceed past the gate without
  `suites/v1.json`, and says explicitly that a run without it is v0-style
  pilot data, not a v1 release.
- Every command shown in the skill (the pipeline invocation, the site
  boot/env-var invocation, the `inspect eval` flag names) is copy-pasteable
  against the actually-merged code as of this plan — not invented syntax.
  An implementer should re-run `--help` and grep checks like the ones in
  this plan before finalising the skill text, not trust the measurement
  doc's prose alone.
- The three verified gaps above (no suite file, `headline.parquet`
  mismatch, no real chart-baking/deploy) are each named in the skill text
  itself, not just in this plan — an operator reading the skill cold
  should not be surprised by any of them mid-run.
- Step 7's writeup-drafting boundary (skill drafts data and exhibits;
  Valerie owns framing/narrative/publish) is stated explicitly.
- The skill file is written in GOV.UK plain style per the design-decisions
  note's process rule.

## Explicitly out of scope for this task

- Building `suites/v1.json` or a suite-loading CLI path (DB-4 M7).
- Fixing the `headline.parquet` naming mismatch (a DB-5/DB-6 cross-plan
  item).
- Implementing real chart baking or deploy automation for lofsite
  (DB-6 phase 2).
- Adding missing rows to `PRICING_TABLE` (the skill tells the operator to
  do this at run time; it does not pre-populate every possible model).

## Risks

- If DB-4 M7 lands with a suite-loading CLI different from what's guessed
  here (e.g. a `--suite` flag on `single_lof_task`), the skill's step-3
  example commands will need a follow-up edit. This plan does not attempt
  to predict that interface.
- The cost-sheet step asks an implementer/operator to adapt a Python
  snippet by hand each run; if this gets used often, a real
  `scripts/cost_sheet.py` would be worth a follow-up task, but that is not
  this task's scope (the skill is documentation, not new code).

marker: plan-wave-3-20260704
