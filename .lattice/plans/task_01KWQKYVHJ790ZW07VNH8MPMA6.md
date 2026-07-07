# DB-6: site with dialect sandbox

Binding context: `.lattice/notes/design-decisions-2026-07-04.md` and `.lattice/notes/rendering-architecture-2026-07-04.md`. Both are load-bearing for this plan and are not repeated in full here — read them before implementing.

## Stack decision

Use **FastHTML with HTMX** (not FastAPI+datastar).

Reason: the whole site is one round trip — a user submits a form, the server re-renders it in every registered dialect, and the page updates in place. FastHTML gives Python-native HTML components and ships HTMX wiring by default, so that round trip is a route function returning a fragment, with no template engine, no separate JS framework asset, and no datastar CDN or vendoring decision to make. FastAPI+datastar would need Jinja2 templates, manual route wiring and a datastar script to do the same job with more moving parts. Given the mandate to keep ops light and the audience is eval researchers (rigour over polish), fewer moving parts wins.

Add `python-fasthtml` as a dependency (a new `site` optional-dependency group in `pyproject.toml`, alongside the existing `eval`, `dev`, `analysis` groups). Reuse `matplotlib` (already a dependency) to bake chart images server-side rather than adding a client-side charting library.

## Page structure

1. **Sandbox** (landing page, centrepiece). A text input, "render" button. On submit, the server renders the input form through every renderer in `lofbench.renderers.list_renderers()` and returns a fragment with one panel per dialect: text dialects show the rendered string immediately; image dialects (renderers whose `RenderedForm.metadata.get("format") == "image"`) show the data-URI image as soon as it's built. No client-side rendering, no pre-baked form set — every request is server-rendered from live input, per the binding note ("the sandbox is the centrepiece... server-rendered").
2. **Headline chart**. Per-model sensitivity score (paired accuracy drop vs canonical dialect), baked from DB-5's parquet artifact at deploy time. Shown on the landing page above or beside the sandbox, per the cbrower.dev/vpct tone reference (one hero chart, not a dashboard).
3. **Model-by-dialect matrix and paired-delta charts**. Second-level detail page, linked from the headline chart. Baked the same way.
4. **Axioms explainer**. I1 (calling) and I2 (crossing), using the CMY colour-cue pedagogy from `demo/13--visual-transforms--colour.png`: cyan/magenta/yellow washes mark the same subform (`a`, `b`, and their pairing) across different surface renderings, to make the point that containment is the invariant and everything else is a distractor. Colour cues are pedagogy for this page only — never shown to models, consistent with the rendering-architecture note.
5. **Transcript walkthroughs**. Correct and failed real transcripts, pulled from DB-5's per-sample transcript extracts.
6. **Links out**. A short list of external writeups (Valerie's blog or similar), one per release. Footer or a dedicated page — the writeup itself lives outside this site.

Navigation: sandbox is `/` and stays the centrepiece; other pages hang off a simple top nav (Charts, Matrix, Axioms, Walkthroughs, Writeups).

## Sandbox: validation and rendering

`lofbench.core.string_to_form` is not a safe validator: it silently skips any non-paren character and does not raise on unbalanced parens (it just stops parsing early). It cannot be trusted to reject junk from a public form field. The sandbox route must do its own validation before calling any renderer:

1. Character whitelist: only `(`, `)`, and whitespace. Reject anything else with a plain error message.
2. Balance check: parens must nest to zero net depth and never go negative.
3. Size cap: reject input over roughly 200 characters.
4. Depth cap: reject nesting over roughly 20, since spatial renderers (circle packing) and eventually image rasterisation cost scale with depth and every request re-renders through every registered dialect.
5. On success, round-trip the input through `string_to_form` then `form_to_string` before handing it to any renderer, so every renderer receives a canonical, whitespace-free form string rather than the raw user text.

The sandbox iterates `list_renderers()` / `get_renderer()` rather than hard-coding a dialect list, so DB-2/DB-3/DB-4 landing new archetypes make them appear in the sandbox with no site code change.

## DB-5 artifact fields this site requires

DB-5 owns the schema; these are requirements on it, not a spec:

Settled contract, confirmed with DB-5 (no longer just a requirement stated one-sidedly): the shared field name is `reasoning_setting` (not `reasoning_effort`) across `items` and `calls`; DB-5's `items` schema carries `modality` and `format` (`"text"`/`"image"`) columns and a `schema_version` stamp on every written artifact file; and tokens and cost are read by joining the separate `calls` table on `call_id`, never by reading a per-item cost column (none exists, by design, to avoid double-counting composite bundles).

- `form_id` — stable pairing key across dialects, needed to join chart rows and to look up the original form string for matrix and walkthrough display.
- `dialect_id`, `family`, `modality`, `format` (`"image"`/`"text"`) — chart axis identity and rendering hints; `format` lets a walkthrough page know whether to show a string or an image.
- `suite_version` — every chart and score must be labelled with the frozen suite version it came from; scores across suite versions are never compared.
- `model`, `reasoning_setting` — matrix rows/columns and the reasoning-toggle comparison.
- `difficulty`, `depth`, `steps`, `correct` — needed for the matrix breakdown and for filtering walkthrough examples (e.g. "show a failed deep-nesting case"). Composite pilot logs (DB-5's pre-DB-4 pilot-v0 data, the overwhelming majority of today's 48 real logs) carry null `depth`/`steps` for these rows — DB-5's own schema marks them `int | None`, populated for single-task samples only today. The walkthrough's deep-nesting filter must degrade gracefully (skip or clearly label unavailable) over pilot data rather than erroring or silently treating null as zero depth.
- `tokens`, `cost_usd` — read via a join on `call_id` against DB-5's `calls` table (not present per-item on `items`), shown or at least summed for context; not the headline metric.
- The headline sensitivity aggregate itself: per-model paired accuracy drop vs canonical, with McNemar test result and bootstrap confidence interval, per suite version. This is a separate small aggregate artifact from the per-sample table, and the headline chart bakes directly from it rather than recomputing statistics at page-render time.
- Per-injector `applied`/`resample_count` coverage (from provenance), if the matrix or a chart footnote reports coverage-adjusted drops, per the suite-construction rules in the rendering-architecture note. If DB-5 doesn't carry this through, the matrix omits the coverage caveat rather than fabricating one.
- Distinct-form count alongside raw `n`, per the de-duplication rule, so confidence-interval width is reported against the correct denominator.
- Per-sample transcript extracts (prompt, model completion, expected vs actual, correct/incorrect), keyed by `form_id` + `dialect_id` + `model`, for the walkthrough pages.
- A stable, documented artifact path and a schema/version tag on the file itself (e.g. a `schema_version` field or a versioned filename), so a DB-5 schema change fails the site build loudly instead of silently rendering stale or mismatched columns.

## What works before DB-4/DB-5 land vs after

**Works today, no dependency on DB-4/DB-5:**
- Sandbox, against the 5 renderers already registered (`canonical`, `noisy_parens`, `sexpr`, `circle`, `nested_list`). It imports `lofbench.renderers` directly, so it needs nothing from the frozen suite or the pipeline.
- Axioms explainer (static content plus the reference image's colour pedagogy).
- Links-out page.

**Needs DB-5's parquet artifact:**
- Headline chart, model-by-dialect matrix, paired-delta charts, transcript walkthroughs.

Consequence for build order: these DB-5-dependent pages must degrade gracefully — a documented "not yet built" placeholder — when the expected parquet file is absent, rather than the app failing to boot. This lets the site ship (sandbox + axioms first) before DB-5 lands, and lets DB-5 land without a synchronised DB-6 deploy.

## Ops

One process, one systemd unit on Valerie's server (default recommendation over a container: fewer moving parts for a single-operator server already using `uv`; revisit if Valerie wants portability or isolation — flagged as an open question rather than assumed). The unit runs the FastHTML app under `uv run` from the checked-out repo.

Deploy step for the release flow (consumed by DB-8's release skill): one documented command that (1) re-reads whatever DB-5 parquet artifacts exist, (2) re-bakes the chart images/data into the site's data directory, and (3) restarts the systemd unit. This should be idempotent and safe to run with no new DB-5 artifact (no-op on the baked charts, site keeps serving).

Booting from a clean checkout is one documented command (e.g. `uv sync --group site && uv run python -m sitepkg.app`), so the acceptance criterion "boots from a clean checkout with one documented command" is literal, not aspirational.

## Acceptance criteria

- The sandbox renders a single typed form in every renderer returned by `list_renderers()` in one HTTP round trip, with text dialects showing text and image dialects showing their image, no client-side rendering.
- Charts (headline, matrix, deltas) regenerate correctly from a fresh DB-5 parquet artifact with no site code change, given the artifact matches the documented schema.
- Pages that depend on a DB-5 artifact degrade to a clear placeholder when that artifact is absent, rather than failing the app boot.
- The app boots from a clean checkout with one documented command.
- Malformed sandbox input (bad characters, unbalanced parens, oversized, over-deep) is rejected with a plain error message before any renderer runs.

## Risks / open questions

- FastHTML is a comparatively young framework; if Valerie has a strong existing preference for FastAPI (e.g. reuse across Elendil's other services), that overrides this recommendation — flagged for human confirmation, not a blocker to planning.
- Container vs systemd unit for deployment is a genuine open question dependent on Valerie's server setup, which this plan does not have visibility into.
- Some image renderers (`circle`) may get slower as DB-2/DB-3 add more spatial archetypes; the sandbox's per-request "render every dialect" design should be revisited if the dialect count grows large enough to make a single round trip slow — not a concern at today's 5 renderers.
- The exact DB-5 artifact path and schema-version convention is not yet fixed; this plan states requirements on it but the concrete contract should be agreed directly between the DB-5 and DB-6 implementers before either starts writing the file-reading code.

marker: plan-wave-2-20260704

## Reset 2026-07-05 by agent:claire-orchestrator

## Reset 2026-07-05 by agent:claire-orchestrator
