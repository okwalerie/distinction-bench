# DB-15: add presentation charts and price the full v1 run

## outcome

finish two bounded deliverables without making another model request:

1. preserve and reprice the already-settled four-tier full-run design, including an
   explicit translation from its illustrative 10,080-call grid to the actual frozen-v1
   19,760-trial grid. publish a checked-in, dated cost sheet that distinguishes a complete
   full release from the affordable first tier and records current availability gaps
   instead of silently changing the experiment.
2. add a presentation page and a small set of static, accessible charts for the existing
   four-protocol/five-observation sample. every plotted value comes only from the validated
   aggregate `PublicationView.profiles` rows. rebuild through the typed no-network reissue
   boundary, retain the full evidence locally, and update only the reviewed site subtree on
   `gh-pages`.

the result should make the sample easy to present while keeping its epistemic status
obvious: this is one model, one spatial dialect, four protocols, and `n=5` per protocol.
it is a smoke test, not a model comparison or a statistically powered dialect result.

## settled full-run definition

do not rename the broad tier as the whole full run. section 8/9 of
`docs/measurement-design.md` defines one broad cheap-model line followed by four core
lines: flagship, open-weight, reasoning on, and reasoning off. the illustrative arithmetic
used 18 broad dialect slots and six core dialect slots:

| settled line | literal calls |
|---|---:|
| cheap text | `12 * 400 = 4,800` |
| visual gate | `6 * 400 = 2,400` |
| flagship core | `6 * 120 = 720` |
| open-weight core | `6 * 120 = 720` |
| reasoning-on core | `6 * 120 = 720` |
| reasoning-off core | `6 * 120 = 720` |
| **literal total** | **10,080** |

the frozen suite resolved to 20 text dialects, nine spatial dialects, and a 17-dialect
core containing eight text and nine spatial dialects. applying the settled tiers to the
actual registry therefore yields:

| translated line | frozen-v1 trials |
|---|---:|
| broad text | `20 * 400 = 8,000` |
| broad spatial | `9 * 400 = 3,600` |
| flagship core | `(8 text + 9 spatial) * 120 = 2,040` |
| open-weight core | `(8 text + 9 spatial) * 120 = 2,040` |
| reasoning-on core | `(8 text + 9 spatial) * 120 = 2,040` |
| reasoning-off core | `(8 text + 9 spatial) * 120 = 2,040` |
| **frozen-v1 total** | **19,760** |

this is a translation, not a new definition. the increase is caused by the frozen
dialect sets, not epochs or retries: v1 still has one trial per model/form/dialect cell.

## checked-in cost sheet

add `docs/cost-sheets/openrouter-full-run-2026-08-11.md` and link it from the current-cost
note in `docs/measurement-design.md`. leave the historical worked sheet intact. the new
sheet must include:

- the authenticated catalog retrieval timestamp in utc. if the exact time was not retained,
  record `2026-08-11` with day precision rather than inventing a clock time;
- key accounting without any key/id/value: `$30.00` limit, `$0.069506875` used by the
  sample, and `$29.930493125` remaining;
- the exact endpoint eligibility predicate: declared model, structured output, image input
  where required, priced image input, zdr, data-collection denial, and no fallback;
- current qualifying multimodal rows and standard OpenRouter prices per million tokens:
  `google/gemini-3.1-flash-lite` at `$0.125 / $0.75 / $0.125`
  prompt/completion/image, `google/gemini-3.6-flash` at
  `$0.75 / $3.75 / $0.75`, and `google/gemini-3.1-pro-preview` at
  `$1.00 / $6.00 / $1.00`; the selected exact endpoint is
  `google-vertex/global/flex` and fallback remains disabled;
- the declared text candidates that pass the text-only predicate, clearly labelled as
  insufficient for a 17-dialect core when they fail the exact multimodal intersection;
- formulas, unrounded intermediate values, final rounded dollar values, provenance for
  observed sample usage and local png byte counts, and a separate uncertainty section;
- an explicit statement that this sheet made authenticated `get` requests only and made
  zero inference calls.

### literal 10,080-call reprice

retain the old measurement-design token assumptions when repricing this historical shape:
260 text input tokens, 300 cheap output tokens, 4,040 flagship output tokens, 5,200 other
reasoning output tokens, and 1,600 image plus 200 prompt tokens. OpenRouter standard pricing
does not inherit the old provider-batch assumption. show the current substitution and its
compatibility status on every row:

| literal line | current arithmetic | projected cost | status |
|---|---|---:|---|
| cheap text, gemini lite substitute | `4,800 * (260*.125 + 300*.75) / 1e6` | `$1.2360` | priceable substitution, not the historical haiku condition |
| visual, gemini lite substitute | `2,400 * (1,800*.125 + 300*.75) / 1e6` | `$1.0800` | priceable substitution |
| flagship, gemini pro substitute | `720 * (260*1 + 4,040*6) / 1e6` | `$17.6400` | priceable substitution, not the historical sonnet condition |
| open-weight core | no qualifying declared multimodal open-weight endpoint | `unknown` | unavailable |
| reasoning on, gemini 3.6 proxy | `720 * (260*.75 + 5,200*3.75) / 1e6` | `$14.1804` | only a token-model proxy; reasoning is mandatory and unprobed |
| reasoning off, gemini 3.6 counterfactual | `720 * (260*.75 + 300*3.75) / 1e6` | `$0.9504` | unavailable: this endpoint exposes no off state |

the known-row subtotal is `$35.0868`, already above the remaining key balance while omitting
the unpriced open-weight line. consequently the literal grid has no honest current executable
total. retain the historical `$45.71` baseline as a dated design estimate, not a current quote.

### frozen-v1 19,760-trial reprice

record the evidence-bounded projection separately from provider pricing:

- all 3,600 spatial pngs total `68,660,548` bytes;
- the 1,080 core spatial pngs total `20,586,289` bytes;
- the five observed sample image calls fit
  `input_tokens = 0.9689227 * png_bytes - 953.48`;
- the maximum observed ratio is `0.94472575 input_tokens/png_byte`;
- the primary `reduce-infer-v1` projection uses 1,024 input tokens per text cell and 12
  completion tokens per trial, taken from the sample. it is not a provider quote and does
  not price reasoning-on behavior.

show at least these calculated rows:

| frozen-v1 line | central regression | max-ratio | max-ratio + 25% | execution status |
|---|---:|---:|---:|---|
| 11,600-cell broad, gemini lite | `$9.01517944` | `$9.23657346` | `$11.54571683` | executable after new approval |
| 2,040-cell flagship core, gemini pro | `$20.04668432` | `$20.57831732` | `$25.72289664` | endpoint qualifies, but broad + buffered core exceeds the key |
| 2,040-cell gemini 3.6 no-reasoning-shaped proxy | `$15.01665324` | `$15.41537799` | `$19.26922248` | not a runnable off estimate; reasoning is mandatory and unprobed |
| 2,040-cell open-weight core | `unknown` | `unknown` | `unknown` | no declared open-weight candidate passes the exact multimodal predicate |
| 2,040-cell reasoning-off core | `unknown` | `unknown` | `unknown` | gemini 3.6 has no off state |

the sheet must say that the complete 19,760-trial total is presently unavailable regardless
of nominal spend: the tier-3 model class cannot execute all core modalities, the declared
reasoning pair does not expose an off state, and reasoning-on completion usage has not been
probed. do not fill those cells with zero, a text-only partial, or a silent model replacement.

### budget recommendation

recommend one first tranche only: the complete 11,600-trial broad
`reduce-infer-v1` line on `google/gemini-3.1-flash-lite`, with a `$11.55` operator cap.
this fits both the existing `$18.00` broad-cohort allocation and the exact
`$29.930493125` key remainder; it leaves `$6.45` in the broad allocation and about `$18.38`
on the key before actual reconciliation. call it the **full broad-suite tier**, never the
entire four-tier full release. it adds full dialect/form coverage on the proven cheap model;
the next-model/core tranche needs a new probe, compatibility decision, cost sheet, and
approval. db-15 performs no paid request and does not treat the old sample approval as approval
for this tranche.

## presentation charts

add a dedicated `presentation.html` page and link it from the shared navigation. place a
compact outcome figure on the homepage, and place the complete chart set on both the
presentation page and the existing results page through shared render functions. do not
create chart-specific json, javascript, canvas, raster files, or external assets. generate
inline svg and css deterministically during the static build.

all chart data must come from the tuple of validated aggregate rows at
`PublicationView.profiles`. do not read `publication.trials`, `runs`, `calls`, `effects`,
transcripts, live registries, or loose parquet files in the chart projection. the current
sample has four rows, each with five observed/expected trials:

- `reduce-infer-v1`: competence `0.4`, invalid rate `0`, `n=5`;
- `reduce-taught-v1`: competence `0.4`, invalid rate `0`, `n=5`;
- `transcribe-infer-v1`: competence `0`, invalid rate `0`, `n=5`;
- `transcribe-taught-v1`: competence `0`, invalid rate `0`, `n=5`.

render three presentation-useful figures:

1. **outcome by protocol** — horizontal competence bars ordered reduce-infer,
   reduce-taught, transcribe-infer, transcribe-taught, with direct percentage labels and
   `n=5` on every row. the surrounding copy may say the current one-dialect sample makes
   competence equal observed accuracy, but the reusable chart remains named competence.
2. **valid output versus correct result** — paired high-contrast bars or dots for
   `1 - invalid_output_rate` and competence, again with `n`. this makes the important result
   visible: all four protocols produced schema-valid output, while the two transcription
   profiles still scored zero. label validity and correctness directly; never rely on color
   alone.
3. **resource footprint** — aligned small multiples by protocol for aggregate cost in cents,
   mean latency in seconds, and output tokens per observation. also show the exact profile-row
   totals in text: 20 observations, `$0.069506875`, 546,491 input tokens, and 1,594 output
   tokens. keep the axes independent and labelled; do not put dollars, milliseconds, and
   tokens on one deceptive common scale.

the copy must avoid causal claims from five observations. in particular, say “observed the
same result” rather than “teaching had no effect,” and state that the sample cannot estimate
dialect sensitivity, invariance, or controlled effects because it has only one dialect.

### svg and empty-state contract

- centralize a typed/validated chart projection in `src/lofsite/build.py`; sort by execution
  surface, resolved model, protocol, and reasoning before rendering and use fixed numeric
  formatting so identical profiles yield byte-identical html;
- reject booleans, non-finite numbers, rates outside `[0,1]`, negative counts/resources,
  or observed counts above expected counts instead of drawing nonsense;
- each `<figure>` has a visible heading/caption and an inline `<svg role="img">` with unique
  `aria-labelledby` title/description ids. include direct text labels and a compact accessible
  exact-value table or list adjacent to the graphic;
- use a color-blind-safe, high-contrast palette, sufficient text contrast, patterns or direct
  labels where series share a plot, scalable `viewBox` geometry, and css that remains legible
  on a narrow viewport and in print;
- if profiles are empty, still build `presentation.html`, but render one clear “no admitted
  aggregate profiles; charts are not available” notice. emit no empty axes, `nan`, division by
  zero, or fabricated zero bars. homepage and results use the same empty-state helper;
- keep the smoke-test notice visually adjacent to every chart group and repeat `n=5` in the
  figure, not only in body prose.

## public boundary and exact site shape

extend the sole public allowlist with `presentation.html`. the sanitized tree then has 3,642
site files: seven root html/cname files, 29 dialect pages, 3,600 spatial pngs, and five safe
downloads. the chart implementation must not change the downloaded suite, protocols, human
schema, aggregate parquet schemas/rows, or any spatial bytes.

retain all existing verifier properties:

- exact path/directory closure and local-link resolution;
- restrictive csp and zero external browser references or external assets;
- exact fixed-schema aggregate downloads and the two explicitly permitted inert json-schema
  identifiers only;
- rejection of private run/trial/call ids, raw responses, prompts, exact endpoint/routing/
  catalog evidence, provider request data, ledgers, manifests, archives, inspect exports,
  credentials, unsupported url-bearing attributes, symlinks, and unexpected files;
- all 29 homepage and atlas exemplars, with 20 text and nine spatial representations of the
  same medium probe form.

the chart renderer may display the already-public profile fields such as resolved model and
protocol labels, but it must never interpolate a private execution value. leave the legacy
source-site authentication contract in `src/dbench/release_policy.py` unchanged; it describes
the immutable historical source. current reissued output remains governed by
`lofsite.build.verify_public_site`.

## files and tests

implementation scope:

- `src/lofsite/build.py`: aggregate chart projection, inline accessible svg renderers,
  presentation page, homepage/results integration, navigation, css/print rules, empty state,
  and exact root allowlist;
- `tests/test_static_site.py`: exact 3,642-file surface; populated and empty profile behavior;
  chart order, labels, `n`, titles/descriptions, accessible exact-value fallback, no external
  dependencies, csp, private-id rejection, and deterministic rebuild;
- `tests/test_release_bundle.py`: populated sample sealing/reissue assertions for the new page
  and unchanged public evidence boundary;
- `docs/static-publication.md`: document the presentation page, aggregate-only chart source,
  inline/no-network format, and new exact site inventory;
- `docs/cost-sheets/openrouter-full-run-2026-08-11.md`: current reprice and recommendation;
- `docs/measurement-design.md`: add only a pointer distinguishing the current cost sheet from
  the preserved historical worked budget.

do not edit suite/protocol registries, profile/effect schemas, sample rows, renderers, runner,
provider adapters, or sealed artifacts in place. no dependency is needed for inline svg.

focused gates before handoff:

```text
.venv/bin/python -m pytest -q tests/test_static_site.py
.venv/bin/python -m pytest -q tests/test_release_bundle.py -k 'sample or reissue or site'
.venv/bin/ruff check src/lofsite/build.py tests/test_static_site.py tests/test_release_bundle.py
.venv/bin/ruff format --check src/lofsite/build.py tests/test_static_site.py tests/test_release_bundle.py
```

also parse every generated html page, run `verify_public_site`, compare two independent builds
byte-for-byte, and confirm the safe downloads and all 3,600 pngs are byte-identical to the
current sealed site-only projection. a full nonvisual suite is appropriate after the focused
gates; the separate db-14 authority job owns the 11,600-cell raster replay.

## reissue, review, and deployment

1. implement and commit only source/tests/docs/lattice changes on
   `feat/public-v1-release`; make no network or inference request.
2. move to review and give a fresh cold reviewer the plan, committed diff, focused/full test
   results, and a generated preview. review both standards and the exact public-boundary spec.
3. after source approval, use the normal typed, no-network `reissue-sealed-release` flow from
   the authenticated immutable original
   `/var/home/core/dev/distinction-bench-runs/v1.0.0-sample.1` plus its matching archive and
   complete state directories into one absent sibling such as
   `/var/home/core/dev/distinction-bench-runs/v1.0.0-sample.1-presentation`. prepare, validate,
   seal, and retain that entire sibling locally. do not edit either existing sealed release.
4. have a second cold artifact review prove release validation, exact 3,642-file public
   closure, all links, accessible chart markers and values, aggregate-download equality,
   forbidden-content/secret scan, and byte-identical rebuild at the reviewed commit.
5. update the isolated orphan `gh-pages` snapshot with only the reviewed `site/`, regenerated
   byte-sorted `SHA256SUMS`, and minimal updated provenance. the expected tracked closure is
   the 3,642 site files plus the workflow, inventory, and provenance; the workflow must still
   verify the inventory and upload only `site/`.
6. cold-review the staged deployment diff, push the source branch and `gh-pages` commit, and
   monitor both ci and pages to green. no github release asset is created or uploaded.
7. verify the github-pages url and, if dns is ready, the custom domain. probe at least the
   homepage chart, `presentation.html`, results chart set, atlas, one spatial asset, and the
   five safe downloads. report custom-domain dns/certificate state separately if it remains
   external.

## acceptance

- the checked-in cost sheet preserves both the 10,080-call definition and 19,760-trial
  frozen translation, reproduces the numbers above, exposes every current availability and
  uncertainty gap, recommends only the `$11.55` broad tier, and records zero paid calls;
- homepage, presentation, and results pages clearly show the sample outcome, valid-vs-correct
  distinction, and resource footprint from only the four public profile rows, with `n=5` and
  the smoke-test caveat attached;
- empty profiles produce an honest accessible notice; populated profiles produce deterministic,
  labelled, keyboard/screen-reader-compatible static markup with no external runtime;
- the public tree has exactly 3,642 approved files, retains all dialect exemplars and safe
  downloads, and contains no raw/private evidence or new external references;
- a new typed sealed sibling and complete audit evidence remain local; only its independently
  reviewed site subtree is deployed; and all relevant source ci/pages checks plus representative
  live urls pass before db-15 is marked done.

## review cycle 1 findings

implementation-level rework is required before source approval:

1. the generic site builder must not make sample-specific claims for empty, non-sample, or
   fuller profile sets. derive chart scope from the authenticated sample contract and validated
   aggregate projection, gate sample prose on that scope, and add populated and empty non-sample
   regressions;
2. the accessible resource fallback must preserve exact fixed decimal profile values instead of
   rounding mean latency and output tokens per observation to three decimals;
3. the real typed reissue acceptance regression must assert `presentation.html`, the exact
   3,642-file closure, and unchanged evidence, aggregate-download, and spatial-asset boundaries.

## Reset 2026-08-11 by agent:codex-db15-rework
