# openrouter full-run cost sheet — 2026-08-11

this is the current, evidence-bounded reprice of the settled version-1 model grid. the
authenticated catalog and key-accounting retrieval was made on `2026-08-11` utc; the clock time
was not retained, so this document does not invent one. those requests were authenticated `get`
requests only. this cost-sheet work made **zero inference calls**.

the limited key has a `$30.00` limit. the admitted sample used `$0.069506875`, leaving exactly
`$29.930493125`. no key label, id, or value is recorded here.

## endpoint eligibility and current catalog

an executable row must match the declared model id and support structured output. image trials
additionally require image input and a priced image-input field on the same endpoint. selection
then requires `zdr`, denial of data collection, and an exact provider slug with fallback disabled.
a model-level image claim is not enough when no endpoint passes that intersection.

the current qualifying multimodal rows use standard openrouter prices in dollars per million
prompt / completion / image-input tokens:

| declared model | exact endpoint | prompt | completion | image | status |
|---|---|---:|---:|---:|---|
| `google/gemini-3.1-flash-lite` | `google-vertex/global/flex` | `$0.125` | `$0.75` | `$0.125` | qualifies |
| `google/gemini-3.6-flash` | `google-vertex/global/flex` | `$0.75` | `$3.75` | `$0.75` | qualifies; reasoning is mandatory and unprobed |
| `google/gemini-3.1-pro-preview` | `google-vertex/global/flex` | `$1.00` | `$6.00` | `$1.00` | qualifies |

these declared candidates pass the text-only predicate. “text only” in the last column means
only that the candidate cannot execute the 17-dialect core under the exact multimodal predicate;
it is not a claim that every model architecture is intrinsically text-only.

| declared model | exact text endpoint | prompt | completion | multimodal/core status |
|---|---|---:|---:|---|
| `google/gemini-3.1-flash-lite` | `google-vertex/global/flex` | `$0.125` | `$0.75` | exact multimodal intersection qualifies |
| `openai/gpt-5.6-luna` | `azure` | `$0.20` | `$1.20` | image-advertised, but no priced-image zdr intersection |
| `google/gemini-3.6-flash` | `google-vertex/global/flex` | `$0.75` | `$3.75` | exact multimodal intersection qualifies |
| `deepseek/deepseek-v4-flash-0731` | `deepinfra/fp4` | `$0.08` | `$0.18` | text-only endpoint |
| `moonshotai/kimi-k2.6` | `digitalocean` | `$0.76` | `$3.20` | image-advertised, but no priced-image zdr intersection |
| `z-ai/glm-5.2` | `novita/fp8` | `$0.4886` | `$1.5356` | text-only endpoint |
| `minimax/minimax-m2.7` | `mara` | `$0.24` | `$0.96` | text-only endpoint |
| `openai/gpt-5.6-terra` | `azure` | `$2.00` | `$12.00` | no qualifying image endpoint |
| `openai/gpt-5.6-sol` | `azure` | `$5.00` | `$30.00` | no qualifying image endpoint |
| `google/gemini-3.1-pro-preview` | `google-vertex/global/flex` | `$1.00` | `$6.00` | exact multimodal intersection qualifies |
| `deepseek/deepseek-v4-pro` | `novita/fp8` | `$0.63168` | `$1.26336` | text-only endpoint |
| `moonshotai/kimi-k3` | `morph` | `$2.80` | `$14.00` | image-advertised, but no priced-image zdr intersection |

`qwen/qwen3.7-flash`, `qwen/qwen3.7-plus`, and `qwen/qwen3.8-max` did not provide a
qualifying text endpoint. none of the declared open-weight candidates provides a qualifying
multimodal endpoint for the frozen core.

## settled grid and frozen-v1 translation

the historical worked design used 18 broad dialect slots and a six-dialect core. it is retained
verbatim in the measurement design: cheap text `12 × 400 = 4,800`, visual gate
`6 × 400 = 2,400`, then four core lines of `6 × 120 = 720`, for **10,080 calls**.

the frozen registry resolved to 20 text dialects, nine spatial dialects, and a 17-dialect core
containing eight text plus nine spatial dialects. applying the same tiers gives:

| translated line | frozen-v1 trials |
|---|---:|
| broad text | `20 × 400 = 8,000` |
| broad spatial | `9 × 400 = 3,600` |
| flagship core | `(8 + 9) × 120 = 2,040` |
| open-weight core | `(8 + 9) × 120 = 2,040` |
| reasoning-on core | `(8 + 9) × 120 = 2,040` |
| reasoning-off core | `(8 + 9) × 120 = 2,040` |
| **frozen-v1 total** | **19,760** |

this is a translation, not a new definition. version 1 still has one trial per
model/form/dialect cell; the count increased because the frozen dialect sets are larger, not
because epochs or retries were added.

## current reprice of the literal 10,080-call shape

this comparison retains the historical token assumptions: 260 text input tokens, 300 cheap
output tokens, 4,040 flagship output tokens, 5,200 other reasoning output tokens, and 1,600
image plus 200 prompt tokens. current openrouter standard pricing does not inherit the old
provider-batch assumption.

| literal line | current arithmetic | projected cost | compatibility status |
|---|---|---:|---|
| cheap text, gemini lite substitute | `4,800 × (260×.125 + 300×.75) / 1e6` | `$1.2360` | priceable substitution, not the historical haiku condition |
| visual, gemini lite substitute | `2,400 × (1,800×.125 + 300×.75) / 1e6` | `$1.0800` | priceable substitution |
| flagship, gemini pro substitute | `720 × (260×1 + 4,040×6) / 1e6` | `$17.6400` | priceable substitution, not the historical sonnet condition |
| open-weight core | no qualifying declared multimodal open-weight endpoint | `unknown` | unavailable |
| reasoning on, gemini 3.6 proxy | `720 × (260×.75 + 5,200×3.75) / 1e6` | `$14.1804` | token-model proxy only; reasoning is mandatory and unprobed |
| reasoning off, gemini 3.6 counterfactual | `720 × (260×.75 + 300×3.75) / 1e6` | `$0.9504` | unavailable: this endpoint exposes no off state |

the unrounded known-row sum is `1.236 + 1.08 + 17.64 + 14.1804 + 0.9504 = 35.0868`,
or **$35.0868**. that already exceeds the remaining key balance while omitting the unknown
open-weight line, so the literal grid has no honest current executable total. the historical
`$45.71` remains a dated design estimate, not a current quote.

## frozen-v1 19,760-trial projection

this projection is bounded by local evidence rather than a provider tokenizer quote:

- all 3,600 spatial pngs total `68,660,548` bytes;
- the 1,080 core spatial pngs total `20,586,289` bytes;
- the five observed sample image calls fit
  `input_tokens = 0.9689227 × png_bytes − 953.48`;
- the maximum observed ratio is `0.94472575 input tokens / png byte`;
- the primary `reduce-infer-v1` projection uses 1,024 input tokens per text cell and 12
  completion tokens per trial, taken from the admitted sample. it does not price
  reasoning-on behaviour.

for the broad line, the central image input is
`0.9689227×68,660,548 − 953.48×3,600 = 63,094,235.5516396` tokens; max-ratio image input
is `0.94472575×68,660,548 = 64,865,387.70471100`. text input is `8,000×1,024 =
8,192,000`, and completion is `11,600×12 = 139,200` tokens.

for each core line, central image input is
`0.9689227×20,586,289 − 953.48×1,080 = 18,916,764.3208603` tokens; max-ratio image input
is `0.94472575×20,586,289 = 19,448,397.31524175`. text input is `960×1,024 = 983,040`,
and completion is `2,040×12 = 24,480` tokens.

each unbuffered cell below is calculated as
`((text input + projected image input) × prompt price + completion tokens × completion price)
/ 1,000,000`. the buffered column is `max-ratio cost × 1.25`; it buffers the whole projected
line and is rounded for display only after the unrounded multiplication.

| frozen-v1 line | central regression | max-ratio | max-ratio + 25% | execution status |
|---|---:|---:|---:|---|
| 11,600-cell broad, gemini lite | `$9.01517944` | `$9.23657346` | `$11.54571683` | executable after new approval |
| 2,040-cell flagship core, gemini pro | `$20.04668432` | `$20.57831732` | `$25.72289664` | endpoint qualifies, but broad plus buffered core exceeds the key |
| 2,040-cell gemini 3.6 no-reasoning-shaped proxy | `$15.01665324` | `$15.41537799` | `$19.26922248` | not a runnable off estimate; reasoning is mandatory and unprobed |
| 2,040-cell open-weight core | `unknown` | `unknown` | `unknown` | no declared open-weight candidate passes the exact multimodal predicate |
| 2,040-cell reasoning-off core | `unknown` | `unknown` | `unknown` | gemini 3.6 has no off state |

before display rounding, the broad values are `9.01517944395495`, `9.236573463088875`,
and `11.54571682886109375`; the flagship values are `20.0466843208603`,
`20.57831731524175`, and `25.7228966440521875`; the gemini 3.6 proxy values are
`15.016653240645225`, `15.4153779864313125`, and `19.269222483039140625`.

the complete 19,760-trial total is presently unavailable regardless of nominal spend: the
tier-3 model class cannot execute every core modality, the declared reasoning pair exposes no
off state, and reasoning-on completion usage has not been probed. unknown cells are not zero,
a text-only partial, or permission for a silent model replacement.

## budget recommendation

approve at most one first tranche: the complete **full broad-suite tier** of 11,600
`reduce-infer-v1` trials on `google/gemini-3.1-flash-lite`, pinned to
`google-vertex/global/flex`, with fallback disabled and an operator cap rounded up to
**$11.55**. this is not the entire four-tier full release.

the cap fits the existing `$18.00` broad-cohort allocation and the exact `$29.930493125` key
remainder. it leaves `$6.45` in that cohort allocation and about `$18.38` on the key before
actual reconciliation. it adds complete dialect/form coverage on the already proven cheap
model. a next-model/core tranche requires a new probe, compatibility decision, cost sheet,
and approval. neither the old sample approval nor this document authorizes paid execution.

## provenance

observed sample token and cost totals come from the admitted aggregate profiles in the sealed
sample. png byte totals come from the frozen spatial assets in the local sealed site-only
projection. the regression and maximum ratio come from its five admitted image calls. catalog
eligibility, exact endpoints, and prices come from the authenticated 2026-08-11 retrieval.

## uncertainty

the five-point byte regression is a pragmatic extrapolation across fixed pngs, not a tokenizer
contract. provider accounting may not scale linearly with compressed byte size. completion
length, tokenizer behaviour, endpoint prices, routing availability, failures, and retries can
change. the 25% max-ratio buffer addresses only observed image-input variation; it does not
price unobserved reasoning output. every paid tranche therefore needs a fresh catalog check,
probe where required, reconciliation, cost sheet, and explicit approval.
