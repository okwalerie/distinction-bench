# DB-7 plan: measurement design doc

Status: planned. Actor: agent:claire-planner-db7.

This plan tells the implementation sub-agent what to write, where, and what number
comes from which source. The doc itself is the implementation deliverable. Write it
in GOV.UK plain style: plain words, active voice, sentence case, front-loaded
sections, no bold or italics for emphasis, every figure sourced.

## Binding decisions this doc must obey

These come from `.lattice/notes/design-decisions-2026-07-04.md`,
`.lattice/notes/rendering-architecture-2026-07-04.md`, and the DB-7 comment by
Valerie. They are settled. The doc records them, it does not reopen them.

- The benchmark measures representation sensitivity. Containment is the only relation
  that matters. Every dialect re-presents the same containment structure; everything
  else is a distractor.
- Headline number: a per-model sensitivity score, defined as the paired accuracy drop
  against the canonical dialect over the frozen suite.
- Paired design: same forms through every dialect, within-item comparison, McNemar
  tests, bootstrap confidence intervals over items.
- Frozen suites: suite v1 fixes dialects, forms and seeds. Scores always report per
  suite version. New dialects create suite v2.
- One expression per call is settled. The old design (k=3 samples, 4 expressions per
  call, all-pass scoring) is retired.
- Forms are frozen as an explicit id-to-form table in the suite file. The flagship
  core set is a fixed subset of ids, never a smaller regeneration.
- Suite v1 is a clean break. All prior runs (V0, V1, V1.1 in `data-history.org`)
  become pilot data, labelled v0, cited as motivation, never compared against v1
  scores.
- Model grid has 4 tiers. Budget is 100 US dollars per release run, stretch 150,
  batch APIs at half price, visual dialects gated to cheap multimodal models first.
- Bundling may return later as a cost mode for cheap models only, if a pilot shows
  real savings. If it returns: score per expression (never all-pass), treat the call
  as a statistical cluster, bundle identically across dialects.

## Doc location and role

- Path: `docs/measurement-design.md` (create the `docs/` directory).
- Role: the reference the release skill (DB-8) and every run cite. DB-8 depends on
  DB-7. Write it so DB-8 can link to named sections.
- Audience: eval researchers, the Elendil team, future agents, and provider
  research-credit reviewers. Rigour before polish.

## What the numbers are grounded in

The implementer must not invent cost or token figures. Two sources only:

1. Observed token usage from `logs/*.eval` (inspect_ai logs; token usage is in each
   log's `header.json` under `stats.model_usage`, and per sample under
   `model_usage`). The planner already extracted the load-bearing figures below.
2. Published provider prices, cited with a retrieval date, for the models in the
   grid. Anthropic batch API is 50 percent of standard price; OpenAI and Google batch
   tiers are also 50 percent. State the price table with a "prices as of <date>" line
   and a note that a run must re-check prices because they drift.

Token counts from logs are stable across model vintages (they measure prompt and
output size, not price), so they ground the per-call budget even though the log
models (Opus 4.5, Sonnet 4, Gemini 3 Pro, GPT-5.2) differ from the current grid.

### Observed per-call token figures (from logs/, use these)

- Single expression, canonical, no reasoning (Sonnet 4), n=100 samples:
  26,000 input / 28,366 output total. Per call about 260 input / 284 output
  (sample `lof_001`: 260 in / 228 out). This is the archetype for a cheap,
  reasoning-light, one-expression call: about 500 tokens per call.
  Source: `2025-12-15T18-06-27+00-00_single-lof-task_WJ8d7dCMEKohtz9s26pidR.eval`.
- Composite, 4 expressions per call, 64k thinking (Opus 4.5), about 2,055 groups:
  1,881,281 input / 78,576,629 output (6,664,419 reasoning), 80.4M total. About
  39,000 tokens per group. This is the "spent way too much money" run in
  `data-history.org` and the anti-pattern that motivates retiring heavy thinking on
  bundled calls. Source: `...MSnrYp76447Lbe8fD64VZs.eval`.
- Composite, 4 expressions, reasoning high (GPT-5.2), 30 samples:
  17,226 input / 519,310 output (514,657 reasoning). About 17,900 tokens per sample,
  about 4,500 per expression. Source: `...Y2VeJxvZZuER2C7u9LRSgx.eval`.
- Composite, 4 expressions, reasoning (Gemini 3 Pro), 90 samples: 1.87M total
  (1.72M reasoning). About 20,800 per sample, about 5,200 per expression.
  Source: `...Zj9CczHb4ruv6cpszVnLE2.eval`.
- Composite, 4 expressions, 10k reasoning (Sonnet 4.5), 30 samples: 128,162 total
  (24,374 reasoning). About 4,270 per sample. Source: `...cdqvjYE5bHa9MzbbSoLB2d.eval`.
- Input size: system prompt plus axioms is about 200 tokens. A single-expression call
  is about 260 input tokens; a 4-expression call about 619 (sample `comp_001`). So for
  the v1 one-expression design, budget about 260 input tokens per text call. The form
  string itself is a handful of tokens; the system prompt dominates input.

The reasoning span dominates output and therefore cost. A cheap non-reasoning call is
about 500 tokens; a heavy-reasoning call is 4k to 20k tokens per expression. The
budget sheet must state which reasoning setting each grid tier uses and cite the
matching figure above.

### Observed accuracy figures (for the v0-to-v1 break and difficulty section)

From `data-history.org` (label all as v0 pilot, never compared to v1):

- V0: 8 regular-parens forms, n=500. Per-item random baseline 50 percent, all-correct
  0.39 percent (8 coin flips). Opus 4.5 per-item 62.8 percent; Gemini 3 Pro 71.1
  percent.
- V1.1 dialect gap on Opus 4.5, same 5 forms, 10k thinking: canonical 60 percent,
  noisy-balanced 100 percent, noisy-mismatch 40 percent. This is direct evidence that
  representation changes accuracy on identical forms, the whole premise.
- Gemini 2.5 Flash canonical 7 to 12 percent, noisy dialects 1 to 7 percent (a floor
  model); Gemini 3 Flash high reasoning jumps to 95 percent on noisy-balanced. Use for
  the reasoning-toggle tier motivation.
- Accuracy falls with difficulty. Opus 4.5 by difficulty on the big run: easy 99.3,
  medium 97.3, hard 94.8, lunatic 92.2, extra 88.5 percent per item. Ties to
  `DIFFICULTY_CONFIGS`.

## DIFFICULTY_CONFIGS (from src/lofbench/core.py, cite exactly)

Five tiers, `(label, min_depth, max_depth, max_width, max_marks)`:

```
("1. easy",    2, 3, 2, 15)
("2. medium",  3, 4, 3, 20)
("3. hard",    4, 5, 6, 25)
("4. lunatic", 3, 8, 9, 30)
("5. extra",   4, 9, 3, 35)
```

Generation distributes forms uniformly across the five tiers. The doc's difficulty
stratification must reference these exact tuples and require n per dialect per tier.

## Renderers present in the codebase (ground the dialect list)

`src/lofbench/renderers/`: canonical, noisy_parens (balanced and mismatched via
`renderer_config`), sexpr, svg_circle (visual), nested_list. The rendering note names
nine visual families plus a pattern family (sexpr) plus novel dialects. The doc's
suite-v1 composition must reconcile "what exists in code today" with "what the frozen
core needs", and flag the gap as a dependency for the suite-build task, not something
DB-7 resolves.

## Doc outline

Write these sections in this order. Each bullet says what to include and the source.

1. Purpose and scope
   - One paragraph: this doc defines how a release run measures representation
     sensitivity, and it binds DB-8 and every run. State that v1 uses ground forms
     only (marked or unmarked outputs); variables are out of scope.
   - State the minimal-hint prompt policy: the prompt says the stimulus encodes a
     Laws of Form containment structure and asks the model to reduce it; it does not
     teach the dialect's reading convention. Therefore the sensitivity score mixes
     convention inference with execution. Say this plainly (binding note, task design).

2. The headline metric: per-model sensitivity score
   - Define it: for a model m and dialect d, sensitivity S(m,d) is the paired accuracy
     drop from the canonical dialect to d over the frozen suite:
     S(m,d) = acc(m, canonical) − acc(m, d), computed within items (same forms).
   - Define a per-model headline: either the mean drop across the frozen non-canonical
     dialects or the worst-case drop. Recommend the mean across dialects as the
     headline and worst-case as a companion; justify in one line.
   - Reconcile with `notebooks/measurement.org`. That note proposed an
     information-theoretic rating, Rating = 100 × (1 − H_binary(1 − p)), where p is
     per-item accuracy. The binding decision makes the paired drop the headline. Keep
     the information rating only as an optional secondary per-dialect capability
     readout, and say explicitly it is no longer the headline. Note that its 0 point
     sits at p = 0.5, which is the guessing floor discussed in section 4.

3. Paired design
   - Same forms run through every dialect. Each item is one form. The unit of analysis
     is the form, compared within itself across dialects.
   - McNemar test: for a canonical-vs-dialect pair, tabulate the 2x2 of correct or
     incorrect on the same form. The test statistic uses only the discordant cells
     (b, c). State the exact-binomial or chi-square-with-continuity form and when to
     use each (use exact binomial when b + c is small).
   - Bootstrap confidence intervals over items: resample forms with replacement,
     recompute S, take percentile interval. State the number of resamples (2,000 is
     enough for percentile intervals; say so).
   - Duplicate-form correction (from the rendering note): `generate_form_string`
     emits repeated trivial strings such as `()` and `(())` under distinct form_ids at
     easy difficulty. Because the item seed keys on the form string, duplicates get
     identical stimuli and correlated errors, which overstates the effective sample
     size. Rule: the frozen suite de-duplicates by distinct form string, or the
     analysis cluster-bootstraps by distinct form string. The report states the count
     of distinct forms alongside n and uses the distinct count for CI width.

4. The 50 percent guessing floor, and why paired deltas are robust to it
   - A single marked-or-unmarked answer has a 50 percent guessing floor. This is why
     the old design bundled 4 expressions and scored all-pass (all-correct random
     baseline 0.39 percent for 8, cite V0). State this history.
   - Show that paired deltas are floor-robust: a model that guesses in both dialects
     shows the same accuracy in both, so its drop S is zero, and that reading is
     correct. The floor inflates absolute accuracy but cancels in the within-item
     difference. Make this the core argument for why one expression per call is
     acceptable once the metric is the paired drop rather than absolute accuracy.
   - State the one caveat: a model that guesses only in the harder dialect will show a
     drop, which is the correct signal. The floor only masks sensitivity when a model
     guesses equally in both, and then there was no sensitivity to detect.

5. Sample size, confidence intervals and power
   - Unpaired baseline maths: for a binary proportion the 95 percent CI half-width is
     at most 1.96 × 0.5 / sqrt(n), about 1/sqrt(n). n = 100 gives about plus or minus
     10 percentage points, n = 400 about plus or minus 5. State this is the
     conservative worst case at p = 0.5. (This is the figure from the task
     description; keep it.)
   - Paired power argument: the SE of the drop S under McNemar is
     sqrt((b + c) − (b − c)^2 / n) / n, approximately sqrt((b + c)) / n for small
     effects. Because b + c (the discordant count) is usually far below n, the paired
     SE is much smaller than the unpaired sqrt(2 × 0.25 / n). Give a worked table: at
     n = 100 distinct forms with discordance b + c ≈ 30, SE(S) ≈ sqrt(30)/100 ≈ 0.055,
     so about plus or minus 11 points on the drop; at n = 300, about plus or minus 6;
     at n = 500, about plus or minus 5. Present this as a table of n vs SE(S) at a few
     discordance rates (0.1, 0.3, 0.5) so a reader can size the suite.
   - Concrete recommendation, stated as numbers:
     - Full suite (cheap tier): about 300 to 500 distinct forms, split across the 5
       difficulty tiers (60 to 100 per tier), through every dialect.
     - Flagship core: a fixed subset of about 100 to 150 distinct forms (20 to 30 per
       tier), through a reduced dialect set.
   - Give the concrete n per dialect per tier as a table. Derive the totals from the
     SE table and the difficulty tiers. The reviewer test is that a statistician can
     read off n for any (dialect, tier) cell and check the CI claim.
   - State the one-sample-per-cell rule: one sample per (form, dialect, model). No
     epochs. Justify: with the paired metric and the frozen form set, repeated epochs
     buy little; spend the budget on more distinct forms instead. Note temperature or
     reasoning nondeterminism as a residual, and say a later release may add a small
     repeat set to estimate within-cell noise (open question, not v1).

6. Retiring the old design (the sampling break)
   - Describe the old design: k = 3 samples, 4 expressions per call, all-pass scoring.
     Give its three original reasons (from the amendment note): the 50 percent
     guessing floor on single answers, forms always simplify so large forms reduce
     fast, and one short expression wastes a strong model's context and per-call
     overhead.
   - State why scores break across the change: all-pass over 4 expressions is a
     different estimand from per-item accuracy; the metric, the unit, and the baseline
     all change. Therefore v1 does not compare to v0. This is the mechanical reason for
     the clean break.
   - Point forward to the bundling-as-cost-mode pilot (section 10) as the controlled
     way bundling could return.

7. Frozen suite v1 composition
   - The suite file is checked in (`suites/v1.json`, per the rendering note). It holds
     the suite version, the explicit id-to-form table, the structured specs, the
     factor grid, and per stimulus the resolved render inputs and a payload hash. A run
     cites `--suite v1`.
   - Payload-hash gate: reproducibility comes from pinning both the seed and the
     emitted payload hash, not the seed alone. A freeze-time payload-collision check
     runs before any model spend. State this as a precondition of a valid suite.
   - Ablation lattice for per-stage attribution: to attribute a drop to a single
     rendering stage, the suite must contain the single-stage contrasts: canonical,
     archetype plus injector A only, archetype plus injector B only, and archetype plus
     both. A dialect that stacks two injectors under one dialect_id yields only a joint
     effect and cannot be decomposed after the fact. State the rule: the headline
     sensitivity for a multi-injector dialect attributes to the whole dialect, not to a
     stage, unless the lattice provides the single-stage arms.
   - Coverage-adjusted contrasts: resample-or-drop can set an injector to not-applied
     on deep single-branch or high-fan-out forms, exactly where it would bite hardest,
     so an unguarded average inflates that dialect's accuracy. Rule: the paired
     contrast for an injector excludes items where it was not applied, or reports a
     coverage-adjusted drop using the per-item applied vector in provenance. Report the
     coverage alongside the drop.
   - Which dialects enter the frozen core: reconcile the nine visual families plus
     sexpr plus novel dialects (rendering note) with the renderers that exist in code
     today (canonical, noisy_parens, sexpr, svg_circle). State that the frozen-core
     dialect list is chosen at suite-build time from validated renderers, that canonical
     is always the reference arm, and that the flagship core is a fixed subset of ids.
     Flag the renderer-coverage gap as a dependency on the suite-build task; DB-7 sets
     the rules, not the final list.
   - Distinct-form count and seed key: the suite version is part of the seed key, so a
     new suite re-randomises cleanly. Report distinct-form count with n.

8. Model grid: four tiers
   - Tier 1, cheap closed models: run the full dialect grid and the full form set.
     Purpose: broad coverage cheaply. Example role: a cheap multimodal model that can
     also take the visual dialects.
   - Tier 2, two to three flagships: run the frozen core only (reduced dialects,
     reduced forms). Purpose: headline numbers on the strongest models without paying
     full-grid cost.
   - Tier 3, open-weight via OpenRouter: run the core, priced at OpenRouter rates.
     Purpose: open-model coverage.
   - Tier 4, reasoning-toggle pairs: the same model with reasoning on and off, where
     the API allows. Purpose: isolate the reasoning contribution to sensitivity. Cite
     the Gemini 3 Flash low-vs-high result (noisy-balanced 22 percent low vs 95 percent
     high) as the pilot motivation. Dependency: slicing this tier requires DB-5's
     `reasoning_setting` column on `items`/`calls` (the settled field name, shared with
     DB-6). The doc must not define a reasoning-toggle metric the pipeline has no
     column to slice by; confirm DB-5 carries `reasoning_setting` before treating this
     tier as ready to run.
   - State that visual dialects run on cheap multimodal models first (cost gate) before
     any flagship spends on images.

9. Worked budget sheet
   - Before claiming the 100 dollar cap is satisfiable for any flagship tier, pin at
     least one concrete, fully worked flagship configuration and show its arithmetic
     closing under the cap. Anchor it on the observed Sonnet 4.5 figure (about 4,270
     tokens per sample, composite 4-expression call, 10k reasoning cap, from
     `...cdqvjYE5bHa9MzbbSoLB2d.eval`, roughly 4,300 tokens per sample): state the
     per-expression cost this implies at v1's one-expression-per-call design, the
     number of distinct forms it buys under 100 dollars at published Sonnet 4.5
     prices, and confirm that count meets or exceeds the flagship tier's target form
     count from section 5. Do not merely assert the cap is satisfiable — show the
     number.
   - Build it bottom-up from the observed per-call token figures in this plan and
     current published prices. Structure:
     - Per-call cost = (input tokens × input price + output tokens × output price) ×
       0.5 for batch. Use 260 input tokens per text call; use the output figure that
       matches the tier's reasoning setting (about 300 for cheap non-reasoning, 4k to
       20k per expression for reasoning models — cite the specific log figure each
       time).
     - Calls per tier = dialects × distinct forms (× 1 sample). Show the arithmetic.
     - Visual dialects: add image input tokens per call. State the image-token
       assumption and its source (provider vision pricing; note high-resolution vision
       can be 1.5k to 4.7k image tokens per image and cite the provider doc). Gate
       these to the cheap multimodal tier first.
   - Produce a line-item table that sums to a full release run at or under 100 US
     dollars, with a stretch column at 150. Show where batch pricing halves the bill
     and where the visual gate caps image spend.
   - Every figure in the table traces to either an observed token count (cite the .eval
     file) or a published price (cite source and date). Include a one-line sensitivity
     note: which single assumption (reasoning tokens on flagships, or image tokens)
     dominates the total, so a run knows what to watch.
   - Add a short paragraph: budget may grow if providers grant credits; design for the
     100 to 150 envelope and do not depend on more.

10. Bundling as a cost mode: pilot design
    - Frame it as an open, optional cost mode for cheap models only, not part of v1.
    - Constraints if it returns (binding): score per expression, never all-pass; treat
      the call as a statistical cluster (cluster-bootstrap or a mixed model with a
      per-call random effect, so correlated within-call errors do not shrink the CI);
      bundle identically across dialects so the paired comparison stays valid.
    - Pilot test: run a matched slice bundled and unbundled on one cheap model, compare
      per-expression accuracy and total cost. Adopt bundling only if it saves real
      money without shifting per-expression accuracy. State the pass criterion in
      numbers (for example, cost down by a stated fraction with accuracy within the
      unbundled CI).

11. Open questions
    - Prompt anchoring ablation. The current system prompt keeps parens-flavoured axiom
      examples (`()() = ()`, `(()) = nothing`; confirmed in
      `src/lofbench/tasks/prompts.py`). Recorded observations: parens examples anchor
      models toward the parens dialect; mixed brackets like `[()]` outperform `(())`;
      a notation-neutral prompt makes models default to parens anyway. Propose a
      later-release ablation that varies the system-prompt anchor and measures the
      shift. Not in v1.
    - Teach-vs-infer condition. v1 gives a minimal hint and does not teach the dialect.
      Propose a later condition that teaches the reading convention, to separate
      convention inference from execution. Not in v1.
    - Within-cell noise. One sample per cell leaves reasoning or temperature
      nondeterminism unmeasured. Propose a small repeat set in a later release.

12. The v0-to-v1 break statement
    - A short, explicit statement suitable to quote: suite v1 is a clean break. Prior
      runs are pilot data (v0), cited as motivation, never compared against v1 scores.
      The frozen-suite versioning exists precisely to make this break clean. Any chart
      that mixes v0 and v1 numbers is wrong.

13. Using this doc for provider research-credit applications
    - A short section shaping the doc as an attachment. State the ask (API credits to
      run the grid), the guardrails (frozen suite, paired design, published budget,
      one sample per cell, reproducible payload hashes), and the deliverable (a public
      per-release writeup and charts). Keep it factual: the rigour and the fixed budget
      are the selling points. One paragraph, no marketing tone.

## Data-source table the doc must include or satisfy

The implementer builds a short table (or inline citations) mapping every quantitative
claim to its source. Minimum rows:

- Per-call token counts -> named `logs/*.eval` file (list the five used).
- v0 accuracy and baselines -> `notebooks/data-history.org`.
- Difficulty tuples -> `src/lofbench/core.py` `DIFFICULTY_CONFIGS`.
- Prices -> provider price pages, with a retrieval date and a drift warning.
- Batch discount -> provider batch docs (Anthropic, OpenAI, Google all 50 percent).
- Suite-construction rules -> `.lattice/notes/rendering-architecture-2026-07-04.md`.
- Metric and design decisions -> `.lattice/notes/design-decisions-2026-07-04.md`.

## Acceptance criteria

- Every cost figure traces to an observed token count (named .eval file) or a
  published price (source plus date). No unsourced numbers.
- The worked budget run sums to at or under 100 US dollars, with a 150 stretch column,
  using batch pricing and the visual-dialect gate.
- The doc gives concrete n per dialect per tier as a table, derived from the SE-vs-n
  table and the five difficulty tiers.
- The paired design, McNemar, bootstrap, guessing-floor robustness, duplicate-form
  correction, ablation lattice, and coverage-adjusted contrast are all present and
  internally consistent.
- The v0-to-v1 break is stated explicitly and no v0 number is compared to a v1 number.
- The bundling pilot has a numeric pass criterion.
- A sceptical statistician can read the doc end to end and find no unsupported claim:
  each CI, power, and cost claim has a derivation or a citation.
- The doc lives at `docs/measurement-design.md` and is structured so DB-8 can cite
  named sections.
- GOV.UK plain style throughout.

## Risks and notes for the implementer

- The heaviest budget risk is flagship reasoning tokens (4k to 20k per expression) and
  image tokens on visual dialects. The budget sheet must make these two the explicit
  levers, not hide them in an average.
- The log models (Opus 4.5, Sonnet 4/4.5, GPT-5.2, Gemini 3 Pro/Flash, Gemini 2.5
  Flash) are older than the current grid. Use log token counts (vintage-stable) but
  current published prices. Do not quote the old models' prices as the grid's prices.
- The `output_tokens` field in the big Opus run (78.6M) is large relative to its
  reasoning field; report totals as observed and do not over-interpret the split.
- The frozen-core dialect list is not DB-7's to finalise. DB-7 sets the selection
  rules; the suite-build task picks the validated renderers. Flag this dependency
  rather than inventing a list.
- Keep the information-theoretic rating from `measurement.org` as a secondary readout
  only. Do not let it creep back to the headline; the binding decision is the paired
  drop.

marker: plan-wave-2-20260704
