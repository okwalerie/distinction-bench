# DB-4: two-layer rendering architecture

## Scope

Build the two-layer rendering pipeline the design doc settles: family archetypes plus variation injectors, glued behind the existing `FormRenderer` contract. This is the load-bearing task. DB-2 (text dialect families plus novel self-similar dialects) and DB-3 (the nine-family visual taxonomy) add archetypes on top of the abstractions landed here and touch nothing else. DB-4 owns `enclosure@1` (the svg_circle migration in M5) outright; DB-3 consumes the landed archetype as its worked exemplar and builds the other visual families.

This plan transcribes `.lattice/notes/rendering-architecture-2026-07-04.md` into concrete work items. That doc is binding and adversarially reviewed. Do not redesign what it settles. It is the primary source; read it in full before implementing. Design decisions in `.lattice/notes/design-decisions-2026-07-04.md` bind too.

The one open design point, the ECS amendment, is decided below. Everything else is transcription.

## Decision on the ECS amendment

Valerie proposes an entity-component-system idiom: scene primitives are entities, their attributes are components, injectors are systems that run over entities holding the components they need, so injector applicability becomes structural (which components exist) rather than nominal (which archetype name).

Verdict: refine, do not adopt wholesale. Keep the doc's `Primitive` plus `BaseRender` plus nominal applicability as the v1 spine. Add one cheap ECS-flavoured affordance that preserves the option without paying for it now: give `Primitive` a `tags: frozenset[str]` field, and let `Injector` applicability be checked against a set that in v1 holds archetype names and modality names (nominal, per the amendment). Ship nominal applicability for v1. Defer structural keying on component tags until DB-2 has stabilised the spatial component vocabulary.

Reasons for this refinement:
- The useful kernel of the ECS idea is decoupling an injector from a specific archetype name. A `tags` field captures that kernel for near-zero cost and lets a later release move applicability from nominal to structural without a data-model rewrite. Nominal is then just the special case where the tag is the archetype name.
- Full ECS needs a stable component ontology (has-geometry, has-glyph, has-boundary, containment-link) to exist before injectors can key on it. That ontology is exactly what DB-2 and DB-3 have not built yet. Committing to it on the load-bearing task, before the spatial families exist, invites churn on the one file everything depends on.
- The constraint "no scheduler, spec order is execution order" strips ECS of its actual machinery. Systems normally get ordered by a scheduler resolving data dependencies. Forbidden here. So the ECS proposal reduces to "filter injectors by a structural predicate", not real ECS. Adopting the vocabulary without the machinery is confusing without being useful.
- The determinism and verification schemas key on `node_map` by `NodeId` and assert every node id appears exactly once. Nominal applicability keeps that invariant obviously intact. An open component bag must not be allowed to break it, which is easier to guarantee if the bag stays advisory (`tags`) rather than load-bearing in v1.
- The doc is adversarially reviewed and DB-2 and DB-3 build on its exact abstractions. A data-model pivot now is the riskiest possible moment for one.

Net: the containment link stays the single verified invariant (a relation over node ids, unchanged), applicability ships nominal with the amendment's admission-time rejection, and the `tags` field keeps the structural path open for a later release. This is one added field and one convention, not an architecture change.

## Key files

Existing, to read first:
- `src/lofbench/renderers/base.py` — `FormRenderer`, `RenderedForm`.
- `src/lofbench/renderers/__init__.py` — `_RENDERER_REGISTRY`, `get_renderer`, `register_renderer`, `list_renderers`.
- `src/lofbench/renderers/noisy_parens.py`, `sexpr.py`, `canonical.py`, `nested_list.py`, `svg_circle_renderer.py` — the five existing renderers.
- `src/lofbench/datasets/factory.py` — `create_single_dataset`, `create_composite_dataset`. The shared-generator determinism bug lives here.
- `src/lofbench/tasks/single.py`, `composite.py` — the `-T` entry points and `Task.metadata`.
- `src/lofbench/analysis.py` — `get_log_metadata`.
- `src/lofbench/core.py` — `string_to_form`, `form_to_string`, `generate_test_cases` (assigns `lof_{i+1:03d}` by shuffled position).
- `tests/test_renderers.py`, `tests/test_core.py`.

New, to create (module names are guidance, not a contract):
- `src/lofbench/renderers/pipeline/` (new package): `nodes.py`, `archetype.py`, `injector.py`, `emit.py`, `composed.py`, `spec.py`, `seeding.py`, `verify.py`, `registry.py`, `provenance.py`.
- `src/lofbench/renderers/archetypes/` (new package): `parens.py`, `pattern.py`, `enclosure.py`, and the shared registry wiring.
- `src/lofbench/renderers/injectors/` (new package): `whitespace_jitter.py`, `bracket_swap.py`, `boundary_jitter.py`, `distractor_marks.py`, `preset.py`.
- `suites/v1.json` (checked-in frozen suite) and `src/lofbench/suites.py` (loader).
- `tests/test_pipeline.py`, `tests/test_archetypes.py`, `tests/test_injectors.py`, `tests/test_determinism.py`, `tests/test_suite_freeze.py`.

## Milestones, in landing order

Each milestone is independently testable and leaves the tree green. The five existing renderers stay registered and passing throughout. No forced migration.

### M1 — core abstractions (unblocks DB-2 and DB-3 earliest)

Land the pipeline spine from the doc's "Core abstractions" section. This is the highest-priority milestone: DB-2 and DB-3 cannot start until these types and the archetype and injector protocols exist and are stable.

- `NodeId`, `FormNode`, `form_to_nodes` (wraps `core.string_to_form`), `nodes_to_form` (round-trip tested left inverse).
- `containment_relation(root) -> frozenset[tuple[NodeId, NodeId]]` (transitive ancestor closure) and `relation_hash` (blake2b over the sorted relation).
- `Primitive` dataclass with the doc's fields plus the added `tags: frozenset[str] = frozenset()` field from the ECS decision. `BaseRender` with `modality`, `payload`, `node_map`.
- `Archetype` protocol (`name`, `version`, `family`, `modality`, `build`, `predicate`).
- `Injector` protocol (`name`, `version`, `modalities`, plus an `applicability` set of archetype names or modality names per the amendment, and `apply`).
- `induced_relation(base, pred)` for spatial; parse-back fold for text.
- `verify(base, root, pred) -> (bool, hash)`.
- `emit(base, style) -> Emission`.
- `ComposedRenderer(FormRenderer)` with the `spec: DialectSpec | None = None, **kwargs` constructor, `name` property returning `spec.dialect_id`, and `render` doing parse, build, verify, fold injectors with per-stage verify, emit, and provenance stamping.
- Three registries: `ARCHETYPE_REGISTRY: dict[str, Archetype]`, `INJECTOR_REGISTRY: dict[str, Injector]`, and the widened `_RENDERER_REGISTRY: dict[str, Callable[..., FormRenderer]]`.

M1 ships a skeleton `verify`: one hash-equality check after the archetype builds, plus minimal provenance (the two relation hashes). It does not yet implement per-stage verification after every injector, resample-or-drop on violation, or the full `render_provenance` schema — those complete in M4.

Acceptance for M1: node-map exactly-once test passes; registry-key-equals-name test passes; `verify` returns equal hashes for a hand-built identity render.

### M2 — registry widening and spec format

- Change `_RENDERER_REGISTRY` value type from `type[FormRenderer]` to `Callable[..., FormRenderer]` in `src/lofbench/renderers/__init__.py`.
- Relax `register_renderer`'s `issubclass` guard to accept any callable that returns a `FormRenderer`, so named factories go through the guarded path rather than a raw dict write.
- Keep `get_renderer(name, **kwargs)` doing `renderer_cls(**kwargs)`; confirm the `spec=None, **kwargs` `ComposedRenderer` constructor makes the ad-hoc `-T renderer_config='{...spec...}'` path construct rather than raise `TypeError`.
- `DialectSpec` frozen dataclass with `dialect_id`, `family`, `archetype`, `injectors: list[tuple[str, dict]]`, `style`, and `suite_version: str = "adhoc"`. Add `from_dict` (round-trip tested, rebuilds nested injectors and style) and `seed_digest` (canonical JSON digest of the whole spec).
- A checked-in `DIALECT_SPECS` dict and named factories that build `ComposedRenderer(DIALECT_SPECS[id])` and forward kwargs into `style`.

Acceptance for M2: `-T renderer=<named-dialect>` resolves; ad-hoc `-T renderer=composed -T renderer_config='{...}'` constructs without `TypeError`; existing five names still resolve unchanged.

### M3 — determinism and seed threading

Fix the real order-dependence in `factory.py`. Both `create_single_dataset` and `create_composite_dataset` build one `random.Random(render_seed)` and pass it to every `render` call, so subsetting or reordering the case list shifts every draw.

- Add content-addressed `item_seed(spec, form_string) -> SeedSequence` (blake2b over `spec.seed_digest()` and the form string). Derive per-injector substreams keyed by injector name plus occurrence index among instances of the same name.
- Move seeding inside `ComposedRenderer.render`. Fold `render_seed`, when set, into `item_seed` as an extra field so the task-level knob still perturbs draws for composed dialects; frozen runs leave it unset. Document the divergence.
- Stop the factory sharing one generator across the loop. Route composite per-expression provenance through the same emit path so `render_metadata` is no longer dropped in the composite branch.
- Injectors take a generator handle and must not touch module-level `random` or `numpy.random`. Add a lint rule or review check.

Acceptance for M3: same spec plus same form gives byte-identical output regardless of position in the case list; reordering two distinct injectors leaves every draw unchanged; two instances of the same injector decorrelate.

### M4 — provenance and structure verification

M4 completes what M1 ships only a skeleton of: per-stage verification after every injector (not just after the archetype build), resample-or-drop on violation, and the full `render_provenance` schema below.

- Write the `render_provenance` schema from the doc into `RenderedForm.metadata` uniformly for single and composite: `suite_version`, `form_id`, `dialect_id`, `family`, `modality`, `format`, `archetype`, `injectors` (with `applied`, `resample_count`, `seed_key`), `item_seed`, `input_relation_hash`, `render_relation_hash`, `structure_verified`, `roundtrip_ok`, `renderer_lib_version`.
- Verify after the archetype builds and after every injector. On violation, resample injector params up to a cap and record `resample_count`; on exhaustion mark `applied: False` and record coverage loss. Never leave a stage silently non-isomorphic.
- Text dialects also record `roundtrip_ok` via a notation-appropriate parse-back reader.
- Spatial dialects verify on the symbolic scene only, never on pixels.

Acceptance for M4: provenance carries the full ordered chain with per-stage config; `structure_verified` is true for every admitted stimulus; a deliberately structure-breaking injector is caught and recorded, not emitted silently.

### M5 — migrate the five existing renderers (follows the doc's ordered steps)

No renderer is deleted until parity is tested. Order:

1. Canonical, as `parens@1` with no injectors, optional `whitespace_jitter`. Keep the old class as an alias until parity holds. New baseline id is `parens.canonical`.
2. Sexpr, as `pattern@1`. Its seven presets (`default`, `lisp`, `scheme`, `python`, `rust`, `java`, `haskell` in `src/lofbench/renderers/sexpr.py`) become the `preset` injector parameter. Smallest lift.
3. Noisy parens, as `parens@1` plus a `bracket_swap` injector, where `mismatched` is an ordinary parameter, not a special case. Delete the character-scan depth counter. The swap walks the node map. Parse-back uses a bracket-agnostic reader keyed on nesting, not glyph identity.
4. Nested list, left as-is. It is a debug and ground-truth view, not a suite family. Register as a utility archetype only if convenient.
5. Svg circle, reimplemented as the `enclosure@1` archetype. DB-4 owns this migration outright; DB-3 consumes the landed archetype as its worked exemplar and does not reimplement it. Keep the radial packer geometry but compute it in platform-stable arithmetic (integer, rational, or fixed-point), not bare `math.sin`, `math.cos`, `math.sqrt`. Delete the false circlify docstring (the code claims circlify but does not use it). Add the circle-in-circle predicate `dist + r_child <= r_parent - eps` with an epsilon far larger than float drift, and ship its epsilon unit tests against hand-labelled scenes. Force fill style to none for model-facing stimuli. Emit a PNG data URI via a pinned rasteriser (cairosvg or resvg), not an SVG mime type. Stamp `renderer_lib_version`. This is the only substantial code write.

Then alias the old registry keys (`canonical`, `noisy_parens`, `circle`, `sexpr`, `nested_list`) to the composed specs for one release, and enforce key-equals-name throughout. Replace the five copy-pasted kwargs loops with one shared config-merge helper on the base.

Acceptance for M5: the five existing renderer names resolve and produce structurally equivalent output to the pre-migration renderers; the enclosure predicate unit tests pass; `image/svg+xml` is no longer emitted.

### M6 — injector applicability admission (the amendment)

- Each injector declares its applicability set: named archetypes or a whole modality. Generic text injectors (whitespace jitter, bracket swap) stay single implementations. Archetype-specific injectors (door gaps, boundary jitter) live with their archetype.
- The spec validator rejects unsupported archetype-injector pairings at admission, before any model spend.

Acceptance for M6: a spec pairing a rooms-only injector with the parens archetype is rejected at admission with a clear error, before any render runs.

### M7 — frozen suite and payload hashing

- `suites/v1.json`: suite version, an explicit enumerated `form_id -> form_string` table, the list of structured specs, the factor grid, and per stimulus the resolved render inputs and a payload hash. Not just generator parameters.
- Loader in `src/lofbench/suites.py`. A run cites `--suite v1`. The flagship core subset is a fixed list of form ids from the table, never `generate_test_cases` re-run with a smaller `n` (positional `lof_{i+1:03d}` ids would otherwise denote different forms).
- Payload hash per stimulus: blake2b of the emitted string for text, hash of the symbolic SVG scene for spatial, never pixels. A rerun asserts the recomputed payload hash matches the frozen one and fails loudly on mismatch.
- Freeze-time gates: node-map exactly-once, full-set verification before spend, payload-collision check (no two distinct `dialect_id`s share a payload hash for one `form_id`), and de-duplication by distinct form string. The suite must be an ablation lattice (canonical, archetype plus injector A only, archetype plus injector B only, archetype plus both) so per-stage effects are attributable.

Acceptance for M7: a rerun of a frozen suite reproduces every payload hash; an un-versioned renderer edit that changes a stimulus fails the payload-hash gate; the admission gate rejects a stack that fails verification anywhere in the generation set; the collision check rejects two dialects emitting identical payloads for one form.

### M8 — task-layer stamp and analysis migration (breaking)

DB-4 owns these edits outright. The architecture doc's narrative use of "DB-1" for task-layer changes does not match this repo's actual Lattice numbering: DB-1 here is a docs-only task (refresh CLAUDE.md/README), unrelated to rendering. DB-4's provenance is inert without the task-layer stamp and the analysis rewrite, so this milestone lands under DB-4, not DB-1.

- `src/lofbench/tasks/single.py`: stamp `suite_version`, `dialect_id`, `family`, and `form_id` into `Task.metadata` and the sample metadata, alongside the existing `renderer`, `renderer_config`, `render_seed`, and difficulty fields.
- `src/lofbench/analysis.py` `get_log_metadata`: rewrite, not patch. Read `dialect_id`, `family`, and per-injector params from sample provenance (`sample.metadata.render_metadata`), not `task_args`. Delete the top-level `config.get('mismatched')` lookup (under the composed scheme `mismatched` lives inside a `bracket_swap` injector entry, so the old lookup always reads false and loses the balanced-versus-mismatched distinction). Delete the `renderer == 'parens'` and `renderer == 'noisy_parens'` string branches and the `'parens'` default; the new baseline id is `parens.canonical`. This is a breaking migration.
- Add cost extraction: read `sample.output.usage`, join a pricing table to produce a `cost_usd` column, sum per suite against the 100 to 150 US dollar budget gate.

Acceptance for M8: analysis joins read `suite_version`, `form_id`, `dialect_id`, `family` from sample provenance; the balanced-versus-mismatched distinction survives; a mixed run of old-string and new-id logs does not silently mislabel dialects.

## Test plan

- Round trips: `nodes_to_form(form_to_nodes(s)) == s` across the generator's forms; `DialectSpec.from_dict(spec.to_dict()) == spec`.
- Structure preservation: for every archetype and injector, `verify` returns equal input and render relation hashes across the whole generation set. A deliberately structure-breaking injector is caught.
- Node map: exactly-once test, every node id present exactly once.
- Registry: key-equals-name across the whole registry; the five legacy names resolve; ad-hoc `-T renderer_config` constructs without `TypeError`.
- Determinism: same spec plus same form gives identical output independent of case-list position; reordering distinct injectors is a no-op on draws; two same-name injector instances decorrelate; content-addressed `item_seed` is stable across runs.
- Predicate unit tests: each spatial archetype ships hand-labelled scenes with explicit epsilons, including tangent and touching primitives that must read as not-contained.
- Applicability admission: bad archetype-injector pairings are rejected before any render.
- Payload hash: rerun reproduces hashes; an edited renderer fails the gate; the collision check rejects two dialects that emit identical payloads for one form.
- Existing suites: `uv run pytest` stays green throughout; `tests/test_renderers.py` and `tests/test_core.py` unchanged and passing.
- Lint and format: `uv run ruff check .` and `uv run ruff format .` clean.

## What unblocks DB-2 and DB-3 earliest

M1 is the unblock. Once `FormNode`, `containment_relation`, `relation_hash`, `Primitive`, `BaseRender`, the `Archetype` and `Injector` protocols, `verify`, `emit`, `ComposedRenderer`, and the three registries exist and are stable, DB-2 and DB-3 can write archetypes and register specs against a frozen contract. M2 (registry widening and `DialectSpec`) is the second unblock, because a new dialect needs the factory and spec format to register. M3 through M8 harden the pipeline but do not change the archetype-authoring surface, so DB-2 and DB-3 can start after M2 lands and proceed in parallel with M3 onward.

For DB-3 specifically, this unblock covers symbolic-scene authoring and predicate verification only. Model-facing PNG emission for spatial archetypes waits on M5's pinned rasteriser (cairosvg or resvg); DB-3 can build and verify scenes symbolically after M1/M2 lands, but cannot emit real image payloads until M5.

## Acceptance criteria (failable)

Pulled from the architecture doc. Each is a pass or fail check, not a judgement call.

1. Same seed plus same spec gives identical output. A test renders the same `DialectSpec` and form at two different positions in the case list and asserts byte-identical payloads.
2. Provenance records the full chain. `RenderedForm.metadata` for a composed dialect contains the ordered archetype and injector list with per-stage params, both relation hashes, `structure_verified`, and (for text) `roundtrip_ok`.
3. Existing renderer names resolve to structurally equivalent output. `get_renderer('canonical')`, `get_renderer('noisy_parens')`, `get_renderer('circle')`, `get_renderer('sexpr')`, `get_renderer('nested_list')` all return a renderer whose output is structurally equivalent to the pre-migration renderer for the same input (per M5's parity check), not merely a renderer that runs without error.
4. Existing tests pass. `uv run pytest` is green with no changes to `tests/test_renderers.py` or `tests/test_core.py` beyond additions.
5. Composed pipeline is runnable via `-T`. `-T renderer=parens.mismatched-jitter-v1` (named) and `-T renderer=composed -T renderer_config='{...}'` (ad-hoc) both run without error.
6. Payload-hash gate works. Rerunning a frozen suite reproduces every payload hash; a deliberate un-versioned renderer edit makes the gate fail loudly.
7. Admission gate rejects bad archetype-injector pairings. A spec pairing an archetype-specific injector with an unsupported archetype is rejected before any render or model spend.
8. Containment is asserted between stages. Every stage of every admitted stimulus has equal input and render relation hashes; a structure-breaking transform is caught, not emitted.
9. Composite samples carry full provenance. A composite-task `Sample`'s metadata contains the same full `render_provenance` chain (ordered archetype and injector list, both relation hashes, `structure_verified`) as a single-task sample for the same render — the composite branch no longer drops `render_metadata`.

## Risks

- The architecture doc's prose scopes the task-layer and analysis edits (M8) to a narrative "DB-1", but this repo's actual DB-1 Lattice task is docs-only (CLAUDE.md/README refresh) and unrelated to rendering. M8 lands under DB-4 outright; there is no DB-1 merge boundary to manage.
- Platform-stable spatial geometry is the hardest write. If the enclosure packer keeps transcendentals, the predicate can flip across machines at the tightest grandparent-to-grandchild margins. Mitigation: integer or fixed-point coordinates, an epsilon far larger than drift, a pinned toolchain recorded in provenance, and a checked-in verification transcript as a gate.
- The ECS `tags` field must stay advisory in v1. If an implementer starts keying applicability on structural tags before DB-2 stabilises the component vocabulary, the load-bearing task churns. Mitigation: ship nominal applicability only; `tags` is unused by the validator in v1.
- Rasteriser dependency (cairosvg or resvg) breaks the stdlib-preference constraint. Justified because providers reject SVG mimes and there is no stdlib rasteriser. Mitigation: pin the version and stamp it in `renderer_lib_version`.
- The frozen suite must be an ablation lattice for per-stage attribution and must de-duplicate by distinct form string, or the headline sensitivity score is not a valid causal measurement. This binds the freeze step (M7), not the renderer code, but a suite built without the single-stage arms cannot be decomposed after the fact. Mitigation: M7 acceptance includes the lattice and de-duplication checks.

marker: plan-wave-2-20260704

## Reset 2026-07-05 by agent:claire-orchestrator

## Reset 2026-07-05 by agent:claire-orchestrator
