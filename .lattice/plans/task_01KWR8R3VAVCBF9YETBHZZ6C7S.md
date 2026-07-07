# DB-12: Rendering showcase gallery: every dialect, variation and generator, live

## Goal

Add a `/gallery` page to `lofsite` that shows the whole rendering system working, built only from real code output: every registered renderer, its variations, a sample of what the generator produces at each difficulty tier, and the headline numbers from the frozen suite v1. Add a static export so the same content can be saved as one offline-readable HTML file.

Everything the page shows must come from iterating live registries and the checked-in `suites/v1.json` — no hand-written dialect list, no hardcoded sample forms. A new dialect registered anywhere in `lofbench` must appear on the next request with zero gallery edits.

## What already exists and gets reused

- `lofsite.rendering.render_all_dialects` (the sandbox's fan-out) already shows the pattern: iterate `lofbench.renderers.list_renderers()`, call `get_renderer(key)`, skip (honestly, in a `.skipped` list) anything that needs constructor args (`"composed"`) or raises `NotImplementedError` on render. The gallery reuses this exact skip discipline rather than inventing a second one.
- `lofbench.renderers.pipeline.spec.DIALECT_SPECS` gives every named dialect's `family`, `archetype`, and `injectors` list — this is the structured data the family/variation grouping is built from, not string-parsing of dialect ids.
- `lofbench.suites.load_suite()` reads the checked-in `suites/v1.json` directly (no DB-5 pipeline dependency, always present in the repo) — `LoadedSuite.forms`, `.specs`, `.cells` give the frozen form table and per-cell data the gallery needs for section 2 and section 3.
- `lofbench.core.DIFFICULTY_CONFIGS`, `generate_form_string`, `string_depth`, `simplify_string` give tier configs, a generator, depth, and step count respectively — everything the "generator" section needs.
- `lofsite.layout.page` / `layout.nav` / `layout._STYLE` give the shared chrome and CSS variables (`--surface-1`, `--diverging-pos`, etc. — the dataviz palette already wired for `.viz-root`) that the live route should reuse for nav/footer consistency. The static export does **not** reuse `layout.page` (see "Static export" below) but does inline the same CSS text.
- FastHTML convention: pages are plain functions returning `fasthtml.common` FT nodes (`Div`, `H3`, `Pre`, `Img`, `Table`, ...), composed in `layout.page(title, active, *content)` for a live route. `str(page_fn())` renders to a string in tests (see `test_lofsite.py`'s existing `str(charts_page())` pattern) and `fasthtml.common.to_xml(node)` renders any node tree — this is what the static exporter uses directly, without going through the live app or an HTTP request.

## New modules

Mirror the existing `charts_data.py` (pure data) / `pages/charts.py` (FastHTML composition) split:

- **`src/lofsite/gallery_data.py`** — pure data-gathering, no FastHTML imports. All of the registry-iteration and suite-file logic lives here so it can be unit-tested without spinning up the app.
- **`src/lofsite/pages/gallery.py`** — FastHTML composition only: turns `gallery_data` structures into `Div`/`Table` nodes. Exposes `gallery_page()` (wrapped in `layout.page`, for the live route) and `gallery_content()` (the bare content node list, reused by both the live route and the static exporter).
- **`src/lofsite/export_gallery.py`** — the static export CLI, mirroring `lofbench.suites`'s own `_main`/argparse-in-the-owning-module convention. No new script directory.
- **`src/lofsite/app.py`** — add the route: `@app.get("/gallery")` → `gallery_page()`, plus a `("/gallery", "Gallery")` entry in `layout._NAV_ITEMS`.

## Section 1: family panels (archetype + variations)

### Grouping algorithm (registry-driven, no hardcoded dialect list)

```
dialect_ids = [k for k in list_renderers() if k != "composed"]   # same exclusion as the sandbox fan-out

by_family: dict[str, dict[str, list[str]]] = {}   # family -> archetype_key -> [dialect_ids]
legacy: list[str] = []                             # canonical, nested_list, noisy_parens, sexpr

for key in dialect_ids:
    spec = DIALECT_SPECS.get(key)
    if spec is None:
        legacy.append(key)                          # no DialectSpec: a bare legacy renderer
        continue
    by_family.setdefault(spec.family, {}).setdefault(spec.archetype, []).append(spec.dialect_id)
```

`"circle"` is a byte-identical legacy-key alias of `"enclosure.canonical-v1"` (same archetype, same empty injector list — see `lofbench.suites`'s own module docstring, which excludes it from the frozen suite for exactly this reason). The gallery does **not** render a second, visually-identical panel for it: within the `enclosure` family block, render `enclosure.canonical-v1` once and add one line of caption text noting `"circle"` is a registered alias of this exact archetype (cite the suites.py rationale). This still satisfies "every registered dialect appears" — `"circle"` is named and accounted for, not silently dropped — while not wasting a Tufte panel on a duplicate image. A test (below) checks this accounting explicitly rather than trusting eyeballing.

Each family section iterates its `archetype_key -> [dialect_ids]` map (most families have exactly one archetype; `parens` and `trees` and `biopolymer` have two — e.g. `parens@1` vs `word_brackets@1` inside the `parens` family, or the spatial `trees@1` vs the text `tree_indent@1` inside `trees`). Each archetype gets its own row of panels within the family section:

- **Canonical panel**: the dialect id in this archetype's list with `spec.injectors == []` (there is currently exactly one per archetype in every registered case — verified by the accounting test below; if a future archetype breaks that invariant, pick the alphabetically-first injector-free id and note the tie in a caption rather than crash).
- **Variation panels** (2–3, per the task description):
  - If the archetype has other, injector-bearing dialect ids in the same family (e.g. `pattern.java`/`pattern.lisp`/`pattern.python` alongside `pattern.default`; `parens.jitter-v1`/`parens.noisy-v1`/`parens.noisy-mismatched-v1` alongside `parens.canonical`), pick the first 3 by sorted dialect id, render each on the **same exemplar form** as the canonical panel, label each panel with its dialect id and its injector list (e.g. `whitespace_jitter(amp=1)`) — this is the "injector settings" case.
  - If the archetype has **no** other dialect id in the registry (true today for all 8 DB-3 spatial families — trees, blocks, graph, map, map-centred, rooms, rna-arc, paths — and for `enclosure`, since no spatial injector is implemented yet; see `lofbench.suites`'s own documented gap), there is no injector-setting variation to show. Fall back honestly: render the same archetype on the **deep** exemplar form instead of a second copy of the shallow one, labelled "no injector variation registered for this archetype — showing a deeper input instead" rather than faking a variation. Do not attempt to fake variation via `render_seed`: every DB-3 spatial archetype's `build()` takes an `rng` argument but never calls it (checked directly against the archetype source), so re-rendering the same form under a different seed for these families would silently produce a byte-identical image — a fake variation is worse than an honest gap.
- **Legacy renderers** (`canonical`, `nested_list`, `noisy_parens`, `sexpr`) get their own section, one panel each on the shallow exemplar, captioned "legacy renderer, no `DialectSpec` / injector arm registered." `canonical` and `noisy_parens` *do* take an `rng` and vary internally (checked: both call `rng.random()`/`rng.choice()`), so their panel additionally shows one more render under a second fixed seed side by side, captioned with the seed value — this is the one place true seed-based variation is real and worth showing.

### Panel content

Reuse the sandbox's own image/text split: `kind = "image" if result.metadata.get("format") == "image" else "text"`; image panels are `Img(src=result.rendered)` (already a `data:image/png;base64,...` URI, per `pipeline.emit`), text panels are `Pre(result.rendered)` styled monospace. Every panel is directly labelled (dialect id, not a legend) per the Tufte constraint below.

## Section 2: the generator, sampled per tier

For each of the 5 `DIFFICULTY_CONFIGS` tiers, generate one form with a fixed seed (a module constant in `gallery_data.py`, e.g. `GENERATOR_SAMPLE_SEED = 20260704`, distinct from `lofbench.suites.GENERATION_SEED` so the gallery's own sample is visibly a separate, smaller draw and not a claim about suite v1's actual frozen forms), using that tier's own `(min_depth, max_depth, max_width, max_marks)`. For each sample, show: the tier name, the form string (monospace), `string_depth(form)`, and `len(simplify_string(form)[1])` (the step count). One row per tier, five rows total, in tier order — a small table or five small-multiple cards, not prose.

## Section 3: suite v1 headline + sampled cells

Load `lofbench.suites.load_suite()` once per request (cheap: parses the checked-in JSON, no re-rendering). Show the header numbers already computed at freeze time — `len(suite.forms)`, `len(suite.specs)`, `len(suite.cells)` (expected 120 / 29 / 3,480; computed live from the loaded object, never hardcoded, so the numbers self-correct if the suite is ever refrozen) — plus a deterministic sample of 12 cells (e.g. every `len(cells)//12`th cell by sorted `(form_id, dialect_id)` order, so the sample is stable across requests and reruns) shown as a table: `form_id`, `dialect_id`, `family`, `modality`, `format`, `structure_verified`, and a truncated `payload_hash` (first 12 hex chars, labelled as truncated).

## Exemplar forms: a fixed shallow/deep pair

Use two real forms already frozen in `suites/v1.json`, rather than inventing new literals or a new seed to justify:

- **Shallow**: the first form (by `form_id` order) with `difficulty == "1. easy"` — `generate_form_table`'s own docstring confirms ids are assigned once, in tier order, so this is deterministically `lof_001`.
- **Deep**: the first form with `difficulty == "5. extra"` — the tier with the deepest configured range (`max_depth=9`), deterministically `lof_097` (`4 * 24 + 1`).

Justification: reusing suite v1's own frozen, already-real forms ties the gallery's exemplars directly to the actual eval artifact instead of a second ad-hoc generation the reader has to trust separately, and needs no new constant to explain. Load via `load_suite().forms`, filter by `difficulty`, take the first — do not hardcode the resulting strings as literals (keeps the gallery correct if suite v1 is ever refrozen with a different seed).

## Rendering cost and caching decision

Benchmarked directly (`get_renderer` + `.render()` over all 34 non-`composed` registry keys, one exemplar form): **one full pass over every registered dialect takes ~0.25s, ~10 of those are `cairosvg` PNG rasterisations.** The gallery renders roughly 1.5–2x that many panels total (canonical + up to 3 variations per archetype, across two exemplar forms for the injector-free spatial fallback case), so a full page render is estimated at well under 1 second server-side.

**Decision: render at request time, no caching**, matching this codebase's existing, stated convention (`lofsite/README.md`: "Charts bake fresh from the parquet/jsonl artifacts on every page request [...] no separate build step, no process-start cache"). This keeps "a new dialect appears with no gallery edit" trivially true (nothing to invalidate) and keeps the module dependency-free of any cache layer. Acceptance criterion below pins a concrete budget so this decision is checked, not just assumed; if a future dialect count makes it measurably slow, the fix is a `functools.lru_cache` (or a module-level dict keyed on nothing, since the registry is fixed within one process) wrapped around the pure `gallery_data` builder functions — deliberately not built now, since there is nothing to cache against yet.

## Static export

`src/lofsite/export_gallery.py`:

```python
def build_static_document() -> str:
    """Self-contained HTML: gallery_content() serialised, styles inlined,
    no external script/link tags, no relative asset paths -- every image
    is already a base64 data URI from pipeline.emit, so no extra work is
    needed there."""
    ...

def _main(argv: list[str] | None = None) -> int:
    # argparse: --out (default dist/gallery.html)
    ...

if __name__ == "__main__":
    raise SystemExit(_main())
```

Run via `uv run python -m lofsite.export_gallery --out dist/gallery.html`.

Key design point: this does **not** go through `layout.page`/`Titled` or the live FastHTML app/request cycle at all — it calls `gallery_content()` directly (the bare node list shared with the live route) and wraps it in a hand-built minimal document shell:

```
<!doctype html><html><head><meta charset="utf-8">
<title>distinction-bench rendering gallery</title>
<style>{layout's CSS text, inlined verbatim}{gallery's own extra CSS, if any}</style>
</head><body>{to_xml(Div(*gallery_content()))}</body></html>
```

Reasons for not reusing `layout.page`: (1) `Titled` emits a top-level `<title>` sibling rather than nesting it inside `<head>` when serialised outside FastHTML's own response machinery (checked directly: `to_xml(Titled(...))` puts `<title>` before `<main>`, relying on the live app's response wrapping to hoist it) — building the shell by hand sidesteps that entirely; (2) site nav (`layout.nav`) is meaningless in an offline single file (the other routes don't exist to link to); (3) this guarantees zero dependency on whatever headers/CDN references the live `FastHTML()` app injects by default, independent of the site's own routing.

`layout._STYLE`'s CSS text needs a public accessor (rename to `layout.STYLE` or add a one-line `layout.style_css() -> str` returning the inline CSS text) so `export_gallery.py` can reuse it without reaching into a private name — a small, in-scope refactor, not a new stylesheet.

The footer's external link (`A("distinction-bench", href="https://github.com/...")`) is fine to keep in the static export: an `<a href>` to an external URL does not require network access to *render* the page, only to *follow* the link — this is different from a `<script src>`/`<link rel=stylesheet>`/`<img src="http...">`, none of which the export may contain.

## Design constraints (binding)

- Small multiples: every family/archetype's canonical + variation panels sit in one visual row/grid, same size, same layout, differing only in what the task calls attention to.
- Direct labels on every panel (dialect id, injector params, or "no injector variation" note) — no separate legend to cross-reference.
- No decorative ink: no drop shadows, no gradient chrome, no boxes-in-boxes beyond the existing flat `.panel` border already used site-wide (reuse `layout`'s existing `.panel`/`.panel-grid` classes rather than inventing new container chrome).
- Monospace for every form string and every text-dialect rendering (matching `.panel pre` already in `layout._STYLE`, which is already `font-family: ui-monospace, monospace`).
- One accent colour per section, used deliberately (reuse the existing `--diverging-pos`/`--seq-dark` viz-root variables already defined in `layout._STYLE` rather than picking new hex values) — e.g. the suite-v1 section's "structure_verified" column uses the existing correct/incorrect status-badge colours already defined (`.status-badge.correct`/`.incorrect`), not a new palette.
- Sentence-case headings throughout (`"Per-family renderings"`, not `"Per-Family Renderings"`).
- GOV.UK prose in every caption and intro paragraph: plain words, active voice, no filler.

## Files touched

- `src/lofsite/gallery_data.py` (new)
- `src/lofsite/pages/gallery.py` (new)
- `src/lofsite/export_gallery.py` (new)
- `src/lofsite/app.py` (add `/gallery` route)
- `src/lofsite/layout.py` (add `("/gallery", "Gallery")` nav entry; expose `STYLE`/`style_css()` publicly)
- `tests/test_lofsite_gallery.py` (new)

## Acceptance criteria

1. Every entry in `lofbench.renderers.list_renderers()` except `"composed"` is accounted for somewhere in the gallery's output (as a rendered panel, or — for `"circle"` only — as an explicit caption naming it as a registered alias). A test asserts this set equality directly against the live registry; it does not enumerate dialect ids by hand.
2. For every archetype that has at least one injector-bearing sibling dialect (the "seeded"/variation case), the variation panels' rendered content differs from the canonical panel's and from each other. A test renders both and asserts inequality — not just that both render without raising.
3. `export_gallery.build_static_document()` returns a string that: starts with `<!doctype html>`, contains at least one `data:image/png;base64,` payload, contains no `<script src="http`/`<link ... href="http`/`<img src="http` (no external resource loads), and — when written to a temp file and read back — is byte-identical (proving the function is pure and the write is a plain string dump, not something that depends on request context).
4. `gallery_page()` renders via `str(gallery_page())` without raising (smoke test, matching the existing `str(charts_page())` pattern in `test_lofsite.py`), and the live `/gallery` route returns 200 via `TestClient` (matching the existing route tests in `test_lofsite.py`).
5. Generator-tier section produces exactly 5 samples, one per `DIFFICULTY_CONFIGS` entry, each with a non-negative `string_depth` and step count.
6. Suite-v1 section's headline numbers are read from the loaded suite object, not hardcoded, and match `len(load_suite().forms)` / `.specs` / `.cells` exactly (a test loads the suite directly and compares).
7. A full `gallery_content()` build completes in under 3 seconds on a normal dev machine (loose budget, checked once in a test with a generous margin — this is a regression guard against, e.g., an accidental N² render loop, not a strict perf target).

## Risks / open questions for the implementer

- **`layout._STYLE` privacy**: renaming/exposing it is a one-line change but touches a file every other page imports — grep all call sites before renaming to confirm nothing else already reaches into `_STYLE` by name.
- **Family/archetype double-nesting readability**: `parens` and `trees` families contain two unrelated archetypes each (bracket-parens vs word-brackets; spatial tree-diagram vs text tree-indent). Confirm visually that nesting archetype-blocks inside a family section reads clearly rather than confusingly conflating two different renderings under one heading — if it reads badly, the fallback is to give each archetype its own top-level section and drop the `family` grouping to a subtitle instead; this is a presentation call worth a quick look at the real rendered page before finalising, not a blocking decision now.
- **cairosvg availability in CI/test env**: the benchmark above ran successfully in this environment, so no action needed, but the implementer should confirm `cairosvg` is on the `site` extra's dependency list already used by the sandbox (it is — `pipeline.emit` already depends on it) so no new dependency is introduced.
- **Suite v1 refreeze drift**: if `suites/v1.json` is ever regenerated with a different seed/`per_tier`, `lof_001`/`lof_097` remain correct by construction (looked up by difficulty tier, not hardcoded ids) — no action needed, noted for confidence only.

marker: plan-wave-4-20260704
