# Measurement design

This document defines how a distinction-bench release run measures representation
sensitivity. It binds the release skill (DB-8) and every run. DB-8 and each run
cite named sections here. Where a section states a rule, that rule is settled;
this document records decisions, it does not reopen them.

Every quantitative claim traces to one of two sources: an observed token count in a
named `logs/*.eval` file, or a published provider price with a retrieval date. The
data-source table at the end lists them. Prices drift, so a run rechecks them before
it spends.

## 1. Purpose and scope

The benchmark measures how much a model's accuracy changes when the same Laws of
Form containment structure is re-presented in a different dialect. Containment is
the only relation that matters. Every dialect re-presents the same structure;
everything else in a stimulus is a distractor.

Version 1 uses ground forms only. Every stimulus reduces to marked (`()`) or
unmarked (void). Variables, which belong to the primary algebra, are out of scope
for version 1.

The prompt gives a minimal hint. It says the stimulus encodes a Laws of Form
containment structure and asks the model to reduce it. It does not teach the
dialect's reading convention. The sensitivity score therefore mixes two things: the
model inferring how to read an unfamiliar dialect, and the model executing the
reduction once it has read the structure. Any use of the score states this plainly.
Separating inference from execution is a later-release condition (section 11), not
version 1.

## 2. The headline metric: per-model sensitivity score

For a model `m` and a dialect `d`, the sensitivity `S(m, d)` is the paired accuracy
drop from the canonical dialect to `d`, computed within items over the frozen suite:

```
S(m, d) = acc(m, canonical) - acc(m, d)
```

Both accuracies are measured on the same forms, so the comparison is within-item.
Canonical is always the reference arm.

The per-model headline is the mean of `S(m, d)` across the frozen non-canonical
dialects. The worst-case drop, `max_d S(m, d)`, is reported alongside it as a
companion. The mean is the headline because it summarises a model's overall
robustness in one number, while the worst case flags the single dialect that breaks
the model hardest; a reader needs both.

An earlier note, `notebooks/measurement.org`, proposed an information-theoretic
rating, `Rating = 100 x (1 - H_binary(1 - p))`, where `p` is per-item accuracy and
`H_binary` is the binary entropy function. That rating is no longer the headline.
The binding decision makes the paired drop the headline. The information rating is
kept only as an optional secondary per-dialect capability readout. Note that its
zero point sits at `p = 0.5`, which is the guessing floor discussed in section 4, so
it reads a guessing model as a rating of zero.

## 3. Paired design

The same forms run through every dialect. Each item is one form. The unit of
analysis is the form, compared within itself across dialects. This pairing is what
makes the metric robust to the guessing floor (section 4) and gives it far tighter
confidence intervals than an unpaired design (section 5).

### McNemar test

For a canonical-versus-dialect pair, tabulate the 2x2 table of correct or incorrect
on the same form:

|                     | dialect correct | dialect incorrect |
|---------------------|-----------------|-------------------|
| canonical correct   | a               | b                 |
| canonical incorrect | c               | d                 |

Only the discordant cells `b` and `c` carry information about the drop. Use the
exact-binomial form of McNemar's test when the discordant count `b + c` is small
(a working threshold is `b + c < 25`): under the null, `b` follows a binomial with
`n = b + c` and probability 0.5. When `b + c` is larger, use the chi-square form with
continuity correction, `(|b - c| - 1)^2 / (b + c)`, referred to one degree of
freedom. The two agree as `b + c` grows; the exact form is used when it is small
because the chi-square approximation is unreliable there.

### Bootstrap confidence intervals

Resample forms with replacement, recompute `S`, and take the percentile interval.
Two thousand resamples give a stable percentile interval; more do not change the
reported bounds at the precision quoted here. Resample by distinct form string, not
by form id, for the reason in the next paragraph.

### Duplicate-form correction

`generate_form_string` emits repeated trivial strings, such as `()` and `(())`,
under distinct form ids at easy difficulty. The item seed keys on the form string,
so duplicates receive identical stimuli in every dialect and produce correlated
errors. Counting them as independent items overstates the effective sample size and
narrows the confidence interval falsely. The rule: the frozen suite de-duplicates by
distinct form string, or the analysis cluster-bootstraps by distinct form string.
The report states the count of distinct forms alongside the raw item count `n`, and
uses the distinct count for confidence-interval width.

## 4. The 50 percent guessing floor, and why paired deltas are robust to it

A single marked-or-unmarked answer has a 50 percent guessing floor. A model that
guesses is right half the time. This floor is the reason the retired design bundled
four expressions and scored all-pass: the all-correct random baseline for eight
independent answers is 0.39 percent, so bundling pushed the floor down far enough to
separate a real signal from chance (V0 pilot, `notebooks/data-history.org`).

Paired deltas are robust to the floor. A model that guesses in both the canonical
dialect and dialect `d` scores about 50 percent in both, so its drop `S` is about
zero. That reading is correct: the model has no dialect-specific sensitivity to
detect, and the metric reports none. The floor inflates absolute accuracy, but it
cancels in the within-item difference. This is the core reason one expression per
call is acceptable once the metric is the paired drop rather than absolute accuracy.

There is one caveat. A model that reads the canonical dialect but guesses only in the
harder dialect shows a real drop, which is the correct signal. The floor masks
sensitivity only when a model guesses equally in both arms, and in that case there
was no sensitivity to detect.

## 5. Sample size, confidence intervals and power

### Unpaired baseline

For a binary proportion, the 95 percent confidence interval half-width is at most
`1.96 x 0.5 / sqrt(n)`, which is about `1 / sqrt(n)`. This is the conservative worst
case, reached at `p = 0.5`. It gives:

- `n = 100`: about plus or minus 9.8 percentage points.
- `n = 400`: about plus or minus 4.9 percentage points.

An unpaired design would need these sample sizes per arm to reach these widths on
absolute accuracy alone.

### Paired power

The paired metric is far tighter. Under McNemar, the standard error of the drop `S`
is:

```
SE(S) = sqrt( (b + c) - (b - c)^2 / n ) / n
```

For small effects, where `b` is close to `c`, this is approximately
`sqrt(b + c) / n`. Writing the discordance rate as `r = (b + c) / n`, the standard
error is about `sqrt(r / n)`. Because the discordant count `b + c` is usually well
below `n`, the paired standard error is much smaller than the unpaired
`sqrt(2 x 0.25 / n)`.

The table gives `SE(S)` and the 95 percent half-width `1.96 x SE(S)`, in percentage
points, computed with the small-effect approximation:

| n (distinct forms) | r = 0.1 (SE / half-width) | r = 0.3 (SE / half-width) | r = 0.5 (SE / half-width) |
|--------------------|---------------------------|---------------------------|---------------------------|
| 100                | 3.2 / 6.2                 | 5.5 / 10.7                | 7.1 / 13.9                |
| 300                | 1.8 / 3.6                 | 3.2 / 6.2                 | 4.1 / 8.0                 |
| 500                | 1.4 / 2.8                 | 2.4 / 4.8                 | 3.2 / 6.2                 |

A reader sizes the suite by reading down the discordance column they expect. At a
moderate discordance of 30 percent, 100 distinct forms give about plus or minus 11
points on the drop, 300 give about plus or minus 6, and 500 give about plus or minus
5. Compare this with the unpaired plus or minus 9.8 points at `n = 100` on absolute
accuracy: the paired design at the same `n` measures the drop about as tightly as the
unpaired design measures a single accuracy, and it does so on the quantity that
matters.

### Concrete sample sizes

- Full suite, run on the cheap tier: about 300 to 500 distinct forms, split across
  the five difficulty tiers, through every dialect.
- Flagship core: a fixed subset of about 100 to 150 distinct forms, through a
  reduced dialect set.

The per-dialect, per-tier target, derived from the standard-error table and the five
difficulty tiers of section 6:

| Difficulty tier | Full suite (per dialect) | Flagship core (per dialect) |
|-----------------|--------------------------|-----------------------------|
| 1. easy         | 60 to 100                | 20 to 30                    |
| 2. medium       | 60 to 100                | 20 to 30                    |
| 3. hard         | 60 to 100                | 20 to 30                    |
| 4. lunatic      | 60 to 100                | 20 to 30                    |
| 5. extra        | 60 to 100                | 20 to 30                    |
| Total per dialect | 300 to 500             | 100 to 150                  |

At 80 distinct forms per tier, the full suite holds 400 distinct forms per dialect.
A statistician can read off the target for any cell and check it against the
standard-error table: 400 forms with a discordance near 30 percent gives a drop
half-width between 5 and 6 points, which is the intended precision.

### One sample per cell

Version 1 runs one sample per `(form, dialect, model)`. There are no epochs. With the
paired metric and a frozen form set, repeated epochs buy little; the budget is better
spent on more distinct forms. Temperature or reasoning nondeterminism remains an
unmeasured residual. A later release may add a small repeat set to estimate
within-cell noise (section 11); version 1 does not.

## 6. Difficulty stratification

Generation distributes forms uniformly across five difficulty tiers. The tiers are
defined in `src/lofbench/core.py` as `DIFFICULTY_CONFIGS`, each a tuple of
`(label, min_depth, max_depth, max_width, max_marks)`:

```
("1. easy",    2, 3, 2, 15)
("2. medium",  3, 4, 3, 20)
("3. hard",    4, 5, 6, 25)
("4. lunatic", 3, 8, 9, 30)
("5. extra",   4, 9, 3, 35)
```

The suite holds the sample sizes of section 5 in each tier, per dialect. Accuracy
falls with difficulty. On the V0 pilot big run, Opus 4.5 per-item accuracy by tier
was easy 99.3, medium 97.3, hard 94.8, lunatic 92.2, extra 88.5 percent
(`notebooks/data-history.org`). These are pilot figures, labelled v0, never compared
to version 1 scores (section 12); they show only that the tiers separate difficulty.

## 7. Frozen suite v1 composition

The suite is `src/lofbench/registries/suites-v1.json`. It holds the suite version, an
explicit id-to-form table, the list of structured dialect specs, the factor grid,
and, per stimulus, the resolved render inputs and a payload hash. A run cites
`--suite v1`. Scores are always reported per suite version. Adding a dialect creates
suite v2; version 1 scores are never mixed with version 2 scores.

The frozen core that flagship models run is a fixed list of form ids selected from
the id-to-form table. It is never the generator re-run with a smaller `n`. The
generator assigns `lof_{i:03d}` by position in a shuffled difficulty list whose
length is `n`, so re-running with a smaller `n` gives a different shuffle, and
`lof_042` then denotes a different form. A fixed id list keeps the pairing join valid
across a full-suite run and a core-only run.

### Payload-hash gate

Reproducibility comes from pinning both the seed and the emitted payload hash, not the
seed alone. Every stimulus records a content hash of its emitted payload: the emitted
string for text, the symbolic scene for spatial, never rasterised pixels. A rerun
asserts the recomputed hash matches the frozen one; a mismatch means an un-versioned
renderer edit changed a stimulus, and the run fails loudly. A freeze-time
payload-collision check runs before any model spend: it asserts that no two distinct
dialect ids emit the same payload for the same form. This matters on trivial forms
such as `()`, where a noisy dialect can have nothing to jitter and emit a payload
identical to canonical, which would deflate measured sensitivity. Passing this check
is a precondition of a valid suite.

### Ablation lattice for per-stage attribution

Provenance records which injectors were applied, not a decomposed per-stage effect.
To attribute a drop to a single rendering stage, the frozen suite must contain the
single-stage contrasts: canonical, archetype plus injector A only, archetype plus
injector B only, and archetype plus both. A dialect that stacks two injectors under
one dialect id yields only a joint effect and cannot be decomposed afterwards from
metadata. The rule: the headline sensitivity for a multi-injector dialect attributes
to the whole dialect, not to any one stage, unless the lattice provides the
single-stage arms.

### Coverage-adjusted contrasts

Treatment is not uniform within a dialect. When an injector cannot preserve
containment on a given form, the freeze step resamples its parameters a capped number
of times and, on exhaustion, marks it not applied and records the coverage loss. This
tends to happen on deep single-branch or high-fan-out forms, which are exactly where
the injector would bite hardest, so an unguarded average inflates that dialect's
accuracy. The rule: the paired contrast for an injector excludes items where it was
not applied, or reports a coverage-adjusted drop using the per-item applied vector in
provenance. The report states the coverage alongside the drop.

### Which dialects enter the frozen core

The renderers that exist in code today are canonical, noisy_parens (balanced and
mismatched via `renderer_config`), sexpr, svg_circle and nested_list
(`src/lofbench/renderers/`). The rendering architecture note names nine visual
families, a pattern family (sexpr), and a handful of novel self-similar dialects, most
of which are not yet built. The frozen-core dialect list is chosen at suite-build time
from validated renderers only. Canonical is always the reference arm. This document
sets the selection rules; it does not finalise the list. The gap between the dialects
named in the architecture and the renderers that exist today is a dependency on the
suite-build task, not something this document resolves.

## 8. Model grid: four tiers

The grid has four tiers. Batch APIs are used wherever a provider offers them, at half
the standard price. Visual dialects run on cheap multimodal models first, before any
flagship spends on images.

- Tier 1, cheap closed models. Run the full dialect grid and the full form set.
  Purpose: broad coverage cheaply. A cheap multimodal model here also carries the
  visual dialects.
- Tier 2, two to three flagships. Run the frozen core only: reduced dialects, reduced
  forms. Purpose: headline numbers on the strongest models without paying full-grid
  cost.
- Tier 3, open-weight models via OpenRouter. Run the core, priced at OpenRouter
  rates. Purpose: open-model coverage.
- Tier 4, reasoning-toggle pairs. Run the same model with reasoning on and off, where
  the API allows, over the core. Purpose: isolate the reasoning contribution to
  sensitivity. The V1.1 pilot motivates this: Gemini 3 Flash on the noisy-balanced
  dialect scored 22 percent at low reasoning and 95 percent at high reasoning
  (`notebooks/data-history.org`).

  This tier depends on the analysis pipeline carrying a `reasoning_setting` column on
  `items` and `calls`, the settled field name shared with DB-5 and DB-6. This document
  does not define a reasoning-toggle metric the pipeline has no column to slice by.
  Confirm DB-5 carries `reasoning_setting` before treating this tier as ready to run.

## 9. Worked budget sheet

the current openrouter availability check, literal-grid reprice, frozen-v1 translation, and
budget-bounded first-tranche recommendation are recorded in
[`docs/cost-sheets/openrouter-full-run-2026-08-11.md`](cost-sheets/openrouter-full-run-2026-08-11.md).
the worked sheet below is preserved as the historical design baseline, not a current quote.

The budget cap is 100 US dollars per release run, with a stretch to 150. This section
first pins one concrete flagship configuration and shows its arithmetic closing under
the cap, then builds a full-run line-item table.

All token figures are observed counts from `logs/*.eval`. All prices carry a source
and a date. Every number in the tables was computed by the Python snippet reproduced
below; the figures in the prose are that snippet's output, not hand arithmetic.

### Per-call token model

- Input per text call: 260 tokens. The system prompt plus axioms is about 200 tokens
  and dominates; the form string is a handful of tokens. Source: single-expression
  canonical run, sample `lof_001` at 260 input tokens
  (`2025-12-15T18-06-27+00-00_single-lof-task_WJ8d7dCMEKohtz9s26pidR.eval`).
- Output, cheap non-reasoning call: about 300 tokens. Same log: 28,366 output over
  100 samples is 284 per call.
- Output, flagship reasoning call: bounded conservatively at about 4,040 tokens, so
  the whole call is about 4,300 tokens. This uses the observed Sonnet 4.5 figure of
  4,272 tokens per sample at a 10,000-token reasoning cap
  (`2025-12-16T00-26-03+00-00_composite-lof-task_cdqvjYE5bHa9MzbbSoLB2d.eval`,
  128,162 total over 30 samples). That sample bundled four expressions, so charging a
  single version-1 expression the whole four-expression figure is a deliberate
  over-estimate; the true single-expression bill is lower.
- Output, other reasoning models: about 5,200 tokens per expression, from the Gemini
  3 Pro run at 5,196 tokens per expression
  (`2025-12-16T04-02-04+00-00_composite-lof-task_Zj9CczHb4ruv6cpszVnLE2.eval`,
  1,870,785 total over 90 four-expression samples). Used as a per-expression proxy for
  open-weight and reasoning-on flagship calls where no direct log exists.
- Image input: about 1,600 tokens per image at standard resolution, with a
  high-resolution sensitivity up to 4,784 tokens per image at 2,576 pixels on the long
  edge. Source: Anthropic vision pricing (prior cap about 1,568 image tokens;
  high-resolution up to 4,784), via the claude-api reference, cached 2026-06-24.

The heavy Opus 4.5 run at a 64,000-token reasoning cap
(`2025-12-16T01-43-50+00-00_composite-lof-task_MSnrYp76447Lbe8fD64VZs.eval`) reported
80.4 million total tokens (78.6 million output, of which 6.7 million reasoning). It is
the anti-pattern that motivates retiring heavy thinking on bundled calls, not a budget
input. Its totals are reported as observed and not decomposed further.

### Prices

Prices as of the dates given. A run rechecks them before spending, because provider
prices drift.

| Model / tier            | Standard input / output ($/MTok) | Batch input / output ($/MTok) | Source and date |
|-------------------------|----------------------------------|-------------------------------|-----------------|
| Sonnet 4.5 (flagship)   | 3.00 / 15.00                     | 1.50 / 7.50                   | Anthropic pricing, Sonnet tier, via claude-api reference cached 2026-06-24 |
| Haiku 4.5 (cheap text)  | 1.00 / 5.00                      | 0.50 / 2.50                   | Anthropic pricing, via claude-api reference cached 2026-06-24 |
| Opus tier (2nd flagship)| 5.00 / 25.00                     | 2.50 / 12.50                  | Anthropic pricing, via claude-api reference cached 2026-06-24 |
| Gemini 3 Flash          | 0.50 / 3.00                      | 0.25 / 1.50                   | Google / pricepertoken, retrieved 2026-07-04 |
| Qwen3-235B (OpenRouter) | 0.70 / 2.80                      | not offered                   | OpenRouter / betonai, retrieved 2026-07-04; add 5.5% credit fee |

Anthropic, OpenAI and Google all offer batch tiers at 50 percent of standard price.
OpenRouter passes provider token prices through and adds a flat 5.5 percent credit
fee; it does not offer a batch tier, so open-weight calls are priced at standard.

### Pinned flagship configuration

Model Sonnet 4.5, 10,000-token reasoning cap, batch pricing. Per-call cost using 260
input and 4,040 output tokens:

- Batch: 0.03069 US dollars per call.
- Standard: 0.06138 US dollars per call.

The flagship core is 6 dialects times 120 distinct forms, which is 720 calls. Its
cost is 22.10 US dollars at batch price, or 44.19 at standard. Both close under the
100-dollar cap on their own.

Turned around: at published Sonnet 4.5 batch price, 100 US dollars buys 543 distinct
forms across the 6-dialect core; at standard price it buys 271. Both exceed the
flagship-tier target of 100 to 150 distinct forms from section 5, with the batch
figure clearing it more than threefold. The cap is not just satisfiable in the
abstract; a concrete flagship run closes at 22 dollars and leaves headroom for a
second flagship.

### Full release run

Every line uses the per-call token model and the prices above. Calls per line are
dialects times distinct forms times one sample.

| Line | Model (pricing) | Reasoning | Calls | $/call | Line cost |
|------|-----------------|-----------|-------|--------|-----------|
| Tier 1 cheap text | Haiku 4.5 (batch) | none | 12 x 400 = 4,800 | 0.00088 | 4.22 |
| Visual gate | Gemini 3 Flash (batch), image std-res | none | 6 x 400 = 2,400 | 0.00090 | 2.16 |
| Tier 2 flagship core | Sonnet 4.5 (batch) | 10k cap | 6 x 120 = 720 | 0.03069 | 22.10 |
| Tier 3 open-weight core | Qwen3-235B (OpenRouter) | per-expr | 6 x 120 = 720 | 0.01555 | 11.20 |
| Tier 4 toggle, reasoning on | Gemini 3 Flash (batch) | per-expr | 6 x 120 = 720 | 0.00787 | 5.66 |
| Tier 4 toggle, reasoning off | Gemini 3 Flash (batch) | none | 6 x 120 = 720 | 0.00051 | 0.37 |
| **Baseline total** | | | | | **45.71** |

The baseline run closes at 45.71 US dollars, well under the 100-dollar cap.

The stretch column raises the form counts and adds a second, Opus-tier flagship:

| Line | Detail | Calls | Line cost |
|------|--------|-------|-----------|
| Tier 1 cheap text | Haiku 4.5, 500 forms | 6,000 | 5.28 |
| Visual gate | Gemini 3 Flash, 500 forms, high-res image | 3,000 | 5.09 |
| Tier 2 flagship core | Sonnet 4.5, 150 forms | 900 | 27.62 |
| Tier 2b second flagship | Opus tier, 100 forms, reasoning | 600 | 39.39 |
| Tier 3 open-weight | Qwen3-235B, 150 forms | 900 | 14.00 |
| Tier 4 toggle on | Gemini 3 Flash, 150 forms | 900 | 7.08 |
| Tier 4 toggle off | Gemini 3 Flash, 150 forms | 900 | 0.46 |
| **Stretch total** | | | **98.92** |

The stretch run closes at 98.92 US dollars, under the 150-dollar stretch cap. Batch
pricing halves the bill on every Anthropic and Google line; the visual gate keeps
image spend on the cheap multimodal tier and off the flagships.

### Sensitivity

Two assumptions dominate the total, and a run watches both:

- Flagship reasoning tokens. The flagship core is the largest baseline line at 22.10
  dollars, driven entirely by output tokens at 4,000 or more per call. A model that
  reasons at 5,000 or 20,000 tokens per expression, rather than the capped Sonnet 4.5
  figure, moves this line the most. The second-flagship Opus line in the stretch, at
  39.39 dollars, shows the lever directly: higher per-token price times heavier
  reasoning.
- Image tokens on visual dialects. Moving the visual gate from standard-resolution
  images (about 1,600 tokens) to high-resolution (up to 4,784 tokens) raises that
  line from 2.16 to 4.07 dollars, an increase of 1.91 dollars at 400 forms. This is
  small at the baseline form count but scales with it, and it is the reason visual
  dialects are gated to the cheap tier first.

The cheap text tier, at 4.22 dollars, is negligible by comparison. If a run overruns,
the flagship reasoning line and the image line are where to look.

### Research credits

The budget may grow if a provider grants API credits (section 13). The design targets
the 100-to-150-dollar envelope and does not depend on more. Credits widen the grid or
raise flagship form counts; they do not change the metric or the suite.

### Budget arithmetic (reproducible)

```python
M = 1_000_000
son_in, son_out = 3.00/M, 15.00/M      # Sonnet 4.5
hai_in, hai_out = 1.00/M,  5.00/M      # Haiku 4.5
opus_in, opus_out = 5.00/M, 25.00/M    # Opus tier
gem_in, gem_out = 0.50/M,  3.00/M      # Gemini 3 Flash
qwen_in, qwen_out = 0.70/M, 2.80/M     # Qwen3-235B (OpenRouter), +5.5% fee
batch = lambda x: x*0.5
def call(n_in, n_out, p_in, p_out): return n_in*p_in + n_out*p_out

IN_TEXT, OUT_CHEAP = 260, 300          # WJ8...eval
OUT_FLAG = 4300 - IN_TEXT              # cdqvj...eval, conservative whole-sample bound
OUT_REASON = 5200                      # Zj9C...eval, per-expression
IMG_STD, IMG_HIRES = 1600, 4784        # Anthropic vision

pc_batch = call(IN_TEXT, OUT_FLAG, batch(son_in), batch(son_out))   # 0.03069
core = 6*120                                                        # 720 calls
# baseline lines
t1  = 12*400 * call(IN_TEXT, OUT_CHEAP, batch(hai_in), batch(hai_out))   # 4.22
vis = 6*400  * call(IMG_STD+200, OUT_CHEAP, batch(gem_in), batch(gem_out))  # 2.16
t2  = core   * pc_batch                                                   # 22.10
t3  = core   * call(IN_TEXT, OUT_REASON, qwen_in, qwen_out) * 1.055       # 11.20
t4on  = core * call(IN_TEXT, OUT_REASON, batch(gem_in), batch(gem_out))   # 5.66
t4off = core * call(IN_TEXT, OUT_CHEAP,  batch(gem_in), batch(gem_out))   # 0.37
baseline = t1+vis+t2+t3+t4on+t4off     # 45.71
forms_per_100 = int(100/(6*pc_batch))  # 543
```

## 10. Bundling as a cost mode: pilot design

Bundling several expressions into one call is an open, optional cost mode for cheap
models only. It is not part of version 1. Version 1 runs one expression per call.

If bundling returns, three constraints bind, from the design decisions:

- Score per expression, never all-pass. All-pass over a bundle is a different
  estimand from per-item accuracy and would reintroduce the version-0 break of
  section 12.
- Treat the call as a statistical cluster. Errors within one call are correlated, so
  the analysis cluster-bootstraps by call, or fits a mixed model with a per-call
  random effect. Treating bundled expressions as independent would falsely shrink the
  confidence interval.
- Bundle identically across dialects, so the paired comparison stays valid.

Pilot test: run a matched slice of the suite bundled and unbundled on one cheap
model. Compare per-expression accuracy and total cost. Adopt bundling only if it
saves real money without shifting per-expression accuracy. The pass criterion, stated
in numbers: total cost falls by at least 40 percent and per-expression accuracy stays
within the unbundled 95 percent confidence interval. If accuracy moves outside that
interval, bundling has changed the measurement and is rejected regardless of savings.

## 11. Open questions

These are recorded for later releases. None is in version 1.

- Prompt anchoring. The system prompt keeps parens-flavoured axiom examples,
  `()() = ()` and `(()) = nothing`, confirmed in `src/lofbench/tasks/prompts.py`.
  Recorded observations: parens examples anchor models toward the parens dialect;
  mixed brackets such as `[()]` outperform `(())`; and a notation-neutral prompt makes
  models default to parens anyway. A later-release ablation varies the system-prompt
  anchor and measures the shift.
- Teach versus infer. Version 1 gives a minimal hint and does not teach the dialect,
  so the score mixes convention inference with execution (section 1). A later
  condition teaches the reading convention, to separate the two.
- Within-cell noise. One sample per cell leaves reasoning or temperature
  nondeterminism unmeasured. A later release adds a small repeat set to estimate it.

## 12. The v0-to-v1 break statement

Suite v1 is a clean break. All prior runs, labelled V0, V1 and V1.1 in
`notebooks/data-history.org`, are pilot data. They are cited as motivation and never
compared against version 1 scores. The frozen-suite versioning exists precisely to
make this break clean. Any chart that mixes a version-0 number with a version-1 number
is wrong.

The break is mechanical, not stylistic. The retired design used three samples, four
expressions per call, and all-pass scoring. All-pass over four expressions is a
different estimand from per-item accuracy; the metric, the unit of analysis, and the
random baseline all change. Version 1 therefore does not compare to version 0.

## 13. Using this document for provider research-credit applications

This document can serve as an attachment to a provider research-credit application.

The ask: API credits to run the model grid of section 8. The guardrails: a frozen,
versioned suite; a paired design with published statistics; a published budget that
closes under 100 US dollars per run; one sample per cell; and reproducible payload
hashes that make every stimulus re-derivable. The deliverable: a public per-release
writeup with charts, on an external platform, linked from the project site.

The selling points are the rigour and the fixed budget. The suite is frozen and
versioned, so results are reproducible and comparable across releases. The spend is
bounded and itemised. A grant widens coverage or raises form counts; it does not
change the metric or the suite.

## Data-source table

Every quantitative claim in this document maps to one of these sources.

| Claim | Source |
|-------|--------|
| Single-call input tokens, cheap output tokens | `logs/2025-12-15T18-06-27+00-00_single-lof-task_WJ8d7dCMEKohtz9s26pidR.eval` |
| Flagship reasoning tokens (Sonnet 4.5, 10k cap) | `logs/2025-12-16T00-26-03+00-00_composite-lof-task_cdqvjYE5bHa9MzbbSoLB2d.eval` |
| Per-expression reasoning tokens (Gemini 3 Pro) | `logs/2025-12-16T04-02-04+00-00_composite-lof-task_Zj9CczHb4ruv6cpszVnLE2.eval` |
| Heavy-reasoning anti-pattern totals (Opus 4.5, 64k cap) | `logs/2025-12-16T01-43-50+00-00_composite-lof-task_MSnrYp76447Lbe8fD64VZs.eval` |
| Per-expression reasoning tokens (GPT-5.2, cross-check) | `logs/2025-12-16T00-36-57+00-00_composite-lof-task_Y2VeJxvZZuER2C7u9LRSgx.eval` |
| V0 accuracy, baselines, difficulty breakdown, reasoning-toggle pilot | `notebooks/data-history.org` |
| Difficulty tuples (`DIFFICULTY_CONFIGS`) | `src/lofbench/core.py` |
| System-prompt axiom examples | `src/lofbench/tasks/prompts.py` |
| Information-rating formula (secondary readout) | `notebooks/measurement.org` |
| Anthropic prices, batch discount, vision image tokens | Anthropic pricing, via claude-api reference cached 2026-06-24 |
| Gemini 3 Flash price | Google / pricepertoken, retrieved 2026-07-04 |
| Qwen3-235B price, OpenRouter fee, batch tiers | OpenRouter / betonai, retrieved 2026-07-04 |
| Suite-construction rules | `.lattice/notes/rendering-architecture-2026-07-04.md` |
| Metric and design decisions | `.lattice/notes/design-decisions-2026-07-04.md` |
