# distinction-bench rendering architecture

Final design, 4 July 2026. Synthesis of three proposals and three judge reports. The compatibility-first skeleton wins. It carries the strongest structure-preservation provenance and the strongest analysis-layer schema grafted on from the runners-up. This document is binding for the rendering work in DB-1, DB-2 and DB-3.

## Summary

A dialect is a pure functor from a verified containment structure to a payload. Every stimulus in the benchmark re-presents one relation: containment. The architecture has two layers. An archetype turns the structure into a base render and records which primitive carries which node. Injectors then transform that base render without changing the containment it induces. A single glue class, `ComposedRenderer`, subclasses the existing `FormRenderer` so the task layer, the scorers and the `-T` contract do not change.

Structure preservation is machine-checked, not trusted. The archetype records a containment relation over stable node ids. After the archetype builds and after every injector runs, the induced relation is recomputed and hashed, and the hash is asserted equal to the input relation. That equality is the isomorphism proof the repository lacks today. Both hashes are written to provenance so the guarantee is diffable across frozen suite versions.

Determinism is content-addressed. The current code shares one random generator across a whole batch inside `factory.py`, so scores depend on batch order. The fix seeds each item from a canonical digest of the full dialect spec and the form string, computed inside `render`. The suite version reaches `render` through the dialect spec, which is a spec change we make on purpose, not a claim of no change. Seeding this way removes the order dependence at its real source.

Reproducibility needs both the seed and the emitted payload pinned, not the seed alone. The frozen suite therefore stores a content hash of every emitted payload and gates a rerun on it. Text payloads hash the emitted string. Spatial payloads hash the symbolic scene, never rasterised pixels. This decouples the score key from font, library and platform pixel drift, and it fails a run loudly if an un-versioned renderer edit changes a stimulus while the structured spec and the containment relation stay equal.

## Decisions this design binds to

This design implements the decisions in `.lattice/notes/design-decisions-2026-07-04.md`. The load-bearing ones:

- Containment is the only relation that matters. Everything else in a stimulus is a distractor. Adversarial features such as mismatched brackets stay isomorphic in containment and are not a separate family. This design encodes containment as the single verified invariant and treats bracket swaps as an ordinary injector, not a special case.
- Each family gets a most-abstracted archetype generator, and variations are injected on top. This is the two-layer architecture, made concrete as `Archetype` plus `Injector`.
- The nine visual families plus the pattern family plus a handful of novel self-similar dialects are all expressed as archetypes. Adding one is one file plus one registry line.
- Colour cues that mark isomorphisms are site pedagogy only. Model-facing stimuli force fill style to none.
- Version 1 uses ground forms only. Every form reduces to marked or unmarked. Variables are out of scope.
- Frozen suite versions fix dialects, forms and seeds. Scores are always reported per suite version. This design makes the suite a checked-in artifact and keys reproducibility on it.
- The site imports lofbench directly and renders every dialect from one source of truth. These renderers are that source of truth.

## Core abstractions

Parse the form to a tree once. No injector ever touches character positions. This removes the duplicated depth counter in `noisy_parens`.

```python
NodeId = str  # stable structural path, dialect-invariant, e.g. "0.1.0"

@dataclass(frozen=True)
class FormNode:
    id: NodeId
    children: tuple["FormNode", ...]

def form_to_nodes(form_string: str) -> FormNode: ...   # wraps core.string_to_form
def nodes_to_form(root: FormNode) -> str: ...          # left inverse, round-trip tested

def containment_relation(root: FormNode) -> frozenset[tuple[NodeId, NodeId]]:
    """Transitive ancestor closure. The invariant every dialect must preserve."""

def relation_hash(rel: frozenset[tuple[NodeId, NodeId]]) -> str:
    """blake2b over the sorted relation. Stable, diffable across suite versions."""
```

Layer one. An archetype is the functor from structure to a base render. It also supplies the containment predicate used to verify spatial dialects.

```python
@dataclass(frozen=True)
class Primitive:
    node_id: NodeId | None   # None means a decorative distractor carrying no structure
    kind: str                # "circle", "rect", "interval", "span", "token"
    geom: dict               # structural numerics only
    children: tuple["Primitive", ...]
    style: dict = field(default_factory=dict)   # cosmetic only, never structural

@dataclass
class BaseRender:
    modality: Literal["text", "spatial"]
    payload: Any                        # str for text, root Primitive for spatial
    node_map: dict[NodeId, Primitive]   # every node id appears exactly once, test-enforced

class Archetype(Protocol):
    name: str
    version: str
    family: str
    modality: Literal["text", "spatial"]
    def build(self, root: FormNode, rng: Generator) -> BaseRender: ...
    def predicate(self, parent: Primitive, child: Primitive) -> bool: ...

def induced_relation(base: BaseRender, pred) -> frozenset[tuple[NodeId, NodeId]]:
    """Fold the predicate over labelled primitive pairs to observe the containment
    the render actually encodes. For text, fold the parse-back tree instead."""
```

Layer two. An injector is a named, parametrised transform over a base render. It must preserve the induced relation. It declares which modalities it supports so an illegal combination is rejected when the suite is frozen, not at run time.

```python
class Injector(Protocol):
    name: str
    version: str
    modalities: frozenset[str]   # {"text"}, {"spatial"}, or both
    def apply(self, base: BaseRender, root: FormNode,
              rng: Generator, **params) -> BaseRender: ...
```

Terminal step. Emit turns a base render into the payload the task layer already understands.

```python
@dataclass
class Emission:
    payload: str
    is_image: bool

def emit(base: BaseRender, style: dict) -> Emission:
    """Text: serialise primitives to a string.
    Spatial: build symbolic SVG, then rasterise to a PNG data URI."""
```

The glue class. One subclass, registered once per named dialect for a clean `-T` key.

```python
class ComposedRenderer(FormRenderer):
    def __init__(self, spec: "DialectSpec" | None = None, **kwargs):
        # get_renderer splats renderer_config as kwargs, so accept both paths:
        # a named factory passes spec directly; the ad-hoc -T path passes a flat
        # spec dict that we rebuild with DialectSpec.from_dict, deserialising the
        # nested injector list and style. from_dict is round-trip tested.
        self.spec = spec if spec is not None else DialectSpec.from_dict(kwargs)
    @property
    def name(self) -> str: return self.spec.dialect_id
    def render(self, form_string, rng=None) -> RenderedForm: ...
        # parse -> archetype.build -> verify -> fold injectors (verify each)
        # -> emit -> RenderedForm(metadata={"format": ..., **provenance})
```

The existing `get_renderer(name, **kwargs)` does `renderer_cls(**kwargs)`, so the ad-hoc research path `-T renderer_config='{...spec...}'` arrives as keyword arguments, not one positional spec. The `spec=None, **kwargs` signature above is what makes that call construct rather than raise `TypeError`. The `renderer_config` payload is a flat spec dict, documented as such, not constructor kwargs.

Three plain dict registries mirror the existing one. No metaclass. The renderer registry value type widens from `type[FormRenderer]` to `Callable[..., FormRenderer]`, because a named dialect registers a factory, not a class. `register_renderer` relaxes its `issubclass` guard to accept any callable that returns a `FormRenderer`, so named factories go through the guarded path rather than a raw dict assignment. A named factory forwards any kwargs into the spec style or override, so `-T renderer_config` is not silently dropped the way a zero-argument `lambda **kw` would drop it.

```python
ARCHETYPE_REGISTRY: dict[str, Archetype]
INJECTOR_REGISTRY:  dict[str, Injector]
_RENDERER_REGISTRY: dict[str, Callable[..., FormRenderer]]
```

## Pipeline spec format

A dialect is pure data. The structured spec is the identity of record. A canonical string is derived from it for chart axes and pairing joins, but the frozen suite pins the structured spec, so a string canonicalisation bug can never silently split one dialect into two.

```python
@dataclass(frozen=True)
class DialectSpec:
    dialect_id: str                          # stable slug, e.g. "enclosure.jitter-v1"
    family: str                              # "enclosure"
    archetype: str                           # registry key, includes version
    injectors: list[tuple[str, dict]]        # ordered; order is semantic
    style: dict = field(default_factory=dict)  # emit-time cosmetics, never structural
    suite_version: str = "adhoc"             # threaded from the frozen suite loader

    @classmethod
    def from_dict(cls, d: dict) -> "DialectSpec": ...   # rebuilds nested injectors and style

    def seed_digest(self) -> bytes:
        """Canonical digest of the whole spec: suite_version, archetype key with its
        version, the ordered injector list with params, and the style. JSON canonical
        form, not a delimiter join, so no field boundary can collide."""
```

The spec carries `suite_version` so it reaches `render` without a signature change to `render` itself. This is a deliberate spec change; earlier drafts that claimed the seed key could include the suite version with no change at all were wrong, because `render(self, form_string, rng)` never sees a suite and the named factory takes no arguments.

The frozen suite is a checked-in file, `suites/v1.json`. It holds the suite version, an explicit enumerated form table, the list of structured specs, the factor grid and, per stimulus, the resolved render inputs and a payload hash. It does not hold only generation parameters. A run cites `--suite v1`. Nothing about the design is implicit. The form table and the resolved-input and payload-hash fields are described under "Frozen forms and payload hashing" below.

Two resolution paths run through `get_renderer(name, **kwargs)`:

- Named dialect, the frozen path. A curated `DIALECT_SPECS` dict is checked in. The renderer registry maps `enclosure.jitter-v1` to a factory that builds `ComposedRenderer(DIALECT_SPECS["enclosure.jitter-v1"])` and forwards any kwargs into the spec style. So `-T renderer=enclosure.jitter-v1` works with no task-layer change.
- Ad-hoc override, the research path. `-T renderer=composed -T renderer_config='{...spec...}'`. `get_renderer` splats the config as kwargs into `ComposedRenderer`, which rebuilds a `DialectSpec` with `from_dict`. This path is research only, is not comparable across releases, and is rejected from any frozen run: a frozen run resolves dialects only from the checked-in `DIALECT_SPECS`, so an ad-hoc spec whose `dialect_id` is a constant slug such as `composed` can never collide into a frozen seed key.

The registry key equals `spec.dialect_id` equals `renderer.name`. A continuous integration test asserts this across the whole registry. That permanently kills the `circle` against `svg_circle` naming asymmetry.

### Worked example 1: canonical text, identity

```json
{"dialect_id": "parens.canonical", "family": "parens",
 "archetype": "parens@1", "injectors": [], "style": {}}
```

Parse `(()())` to a tree. The parens archetype serialises the tree back to a string with default spacing. No injectors. Verify by parse-back. Emit text. This is the baseline dialect that every sensitivity score is paired against.

### Worked example 2: composed text pipeline

```json
{"dialect_id": "parens.mismatched-jitter-v1", "family": "parens",
 "archetype": "parens@1",
 "injectors": [["whitespace_jitter", {"amp": 2}],
               ["bracket_swap", {"mismatched": true}]],
 "style": {}}
```

Parse to a tree. Build the base text render. Apply `whitespace_jitter`, which inserts spaces around tokens and re-runs parse-back verification. Apply `bracket_swap`, which walks the node map and replaces bracket glyphs, some mismatched, so `(()` reads as containment despite the broken bracket-matching prior. Parse-back uses a bracket-agnostic reader that keys on nesting, not glyph identity, so it still recovers the tree. Each injector draws from its own seed substream, keyed by the injector name plus its occurrence index among instances of the same name, so reordering two distinct injectors leaves every draw unchanged, while two instances of the same injector are decorrelated rather than identical. Swapping the order of two same-name instances swaps their draws, which is harmless because they are the same transform. The seed key is defined in full under "Determinism and seed threading".

### Worked example 3: image pipeline

```json
{"dialect_id": "enclosure.jitter-v1", "family": "enclosure",
 "archetype": "enclosure@1",
 "injectors": [["boundary_jitter", {"harmonics": 3, "amp": 0.2}],
               ["distractor_marks", {"k": 2}]],
 "style": {"fill_style": "none", "px": 512}}
```

Parse to a tree. The enclosure archetype packs nested ovals, one per node, and records each oval in the node map. Its predicate is circle-in-circle with an explicit epsilon, `dist + r_child <= r_parent - eps`. The packer computes coordinates in a platform-stable way, not with bare `math.sin`, `math.cos` and `math.sqrt` whose last unit in the last place is libm and platform dependent. It works in integer or rational grid coordinates, or a fixed-point pipeline, and sets the epsilon far larger than any plausible float drift, so the predicate cannot flip across machines at the tight grandparent-to-grandchild margins where radial packing packs closest. Verify that the induced relation equals the input relation. Apply `boundary_jitter`, perturbing oval boundaries with a summed sine series. Re-verify. In a frozen suite the injector parameters and the resulting coordinates are resolved once at freeze time and stored in `suites/v1.json`, so no resample loop and no transcendental runs at eval time; the resample-and-cap logic below applies only during freezing. Apply `distractor_marks`, which adds two ovals with `node_id = None` that carry no structure and cannot affect the induced relation by construction. Force fill style to none. Emit symbolic SVG, then rasterise to a PNG data URI. Do not emit an SVG mime type. Most multimodal providers reject `image/svg+xml`.

### Worked example 4: novel self-similar dialect

An RNA arc diagram, a nature dialect outside the nine families. One archetype, roughly forty lines.

```python
class RnaArcArchetype:
    name, version, family, modality = "rna_arc", "1", "biopolymer", "spatial"
    def build(self, root, rng) -> BaseRender:
        # walk nodes -> one non-crossing arc per node over a base sequence
        # a parent arc spans its children's arcs by construction
        # node_map[node.id] = the arc
        ...
    def predicate(self, parent, child) -> bool:
        # nesting-interval subset: parent interval strictly contains child interval
        ...

ARCHETYPE_REGISTRY["rna_arc@1"] = RnaArcArchetype()
DIALECT_SPECS["rna-arc-v1"] = DialectSpec(
    "rna-arc-v1", "biopolymer", "rna_arc@1", [])

def _make_rna_arc(**kw):
    return ComposedRenderer(DIALECT_SPECS["rna-arc-v1"], **kw)  # kw forwards into style

register_renderer("rna-arc-v1", _make_rna_arc)   # guarded path, not a raw dict write
```

Containment verification, injectors, seed threading, provenance and emit are all inherited. A lower-power agent ships this in an afternoon.

### Worked example 5: pattern family, sexpr

```json
{"dialect_id": "pattern.lisp", "family": "pattern",
 "archetype": "pattern@1",
 "injectors": [["preset", {"name": "lisp"}]],
 "style": {}}
```

The pattern archetype renders a symbolic expression form. The seven existing sexpr presets become injector parameters that set the symbol, open, close and separator glyphs. This is the smallest migration lift because the old renderer is already archetype-shaped.

## Determinism and seed threading

The real order-dependence is in `factory.py`. Its `create_single_dataset` and `create_composite_dataset` loops build one `random.Random(render_seed)` and pass it to every `render` call, so subsetting or reordering the case list shifts every subsequent draw. Overriding `render_batch` does not fix this, because the factory never calls `render_batch`.

The fix is content-addressed seeding inside `render`. It needs no change to the `render` signature, but it does need `suite_version` to reach `render`, which is why the spec carries it. The factory is also cleaned up in migration.

```python
def item_seed(spec: DialectSpec, form_string: str) -> SeedSequence:
    # canonical digest over the WHOLE spec, not just dialect_id:
    # suite_version, archetype@version, ordered injectors+params, style, and the form.
    digest = blake2b(
        spec.seed_digest() + b"\x00" + form_string.encode(), digest_size=8
    ).digest()
    return SeedSequence(int.from_bytes(digest, "big"))
```

Seeding from the whole spec, not `dialect_id` alone, closes two holes. It stops two ad-hoc specs that share a constant `dialect_id` from colliding to one seed, and it keeps the form and the fields separated by a canonical encoding rather than a `|` join that an unescaped `|` inside a form or id could straddle. Ad-hoc specs are still barred from frozen runs, so this is defence in depth, not the primary guard.

`render` derives the item seed, then spawns one decorrelated child stream per injector instance. The substream key is `hash(item_seed || injector_name || occurrence_index_among_same_name)`. Reordering two distinct injectors leaves the key, hence the draws, unchanged. Two instances of the same injector get different occurrence indices, so they decorrelate instead of drawing identically; swapping the order of two same-name instances swaps their draws, which is harmless because the transform is identical. The earlier "keyed by name, not position" wording was self-contradictory: name alone makes repeated injectors draw identically, which is the opposite of independent. The occurrence index is the minimal fix and it does not reintroduce order dependence between distinct injectors.

Injectors receive a generator handle. They must not touch module-level `random` or `numpy.random`. A lint rule and review enforce this. Rounding float outputs to fixed precision reduces platform drift but does not remove it: a transcendental that differs by one unit in the last place across libm can straddle the rounding cut and still round two ways. So determinism does not rest on rounding. Spatial geometry is computed in a platform-stable way, integer or rational or fixed-point, and frozen suites store the resolved coordinates so no transcendental recomputes at eval time. Rounding is a tidy-up, not the guarantee.

`ComposedRenderer.render` derives its own generator from `item_seed` and ignores the `random.Random` that `factory.py` passes, so the task-level `render_seed` would otherwise be inert for composed dialects while the old renderers still honour it. To keep one meaning across renderers, `render_seed`, when set, folds into `item_seed` as an extra field, so the knob still perturbs draws. Frozen runs leave `render_seed` unset, so freezing is unaffected. The divergence is documented, not silent.

The suite version is part of the seed key, so a new suite re-randomises cleanly. A frozen suite is reproducible because the suite pins both the seed and the emitted payload hash, not because the seed alone makes text bit-stable or images structure-stable across arbitrary code. The payload-hash gate under "Frozen forms and payload hashing" is what turns reproducibility from a hope into a checked property.

## Provenance metadata schema

Record-then-replay, written to `RenderedForm.metadata` uniformly for single and composite. The current composite branch in `factory.py` drops `render_metadata` entirely. Migration routes composite per-expression metadata through the same emit path to close that gap. These fields flatten directly into parquet columns.

```python
render_provenance = {
    "suite_version": "v1",
    "form_id": "lof_042",                 # stable across dialects, the pairing key
    "dialect_id": "enclosure.jitter-v1",  # chart identity, from structured spec
    "family": "enclosure",                # grouping key
    "modality": "spatial",
    "format": "image",                    # the sole factory branch flag, "image" or "text"
    "archetype": {"name": "enclosure", "version": "1"},
    "injectors": [
        {"name": "boundary_jitter", "version": "1", "params": {"harmonics": 3, "amp": 0.2},
         "applied": True, "resample_count": 1, "seed_key": "boundary_jitter"},
        {"name": "distractor_marks", "version": "1", "params": {"k": 2},
         "applied": True, "resample_count": 0, "seed_key": "distractor_marks"},
    ],
    "item_seed": 1234567890123456789,
    "input_relation_hash": "…",           # containment of the parsed form
    "render_relation_hash": "…",          # containment induced by the final render
    "structure_verified": True,           # the two hashes are equal
    "roundtrip_ok": True,                 # text only: parse-back recovered the tree
    "renderer_lib_version": "…",          # stamps the rasteriser and its pins
}
```

The four join keys the analysis layer needs are `suite_version`, `form_id`, `dialect_id` and `family`. These live in `RenderedForm.metadata`, which reaches analysis through `sample.metadata.render_metadata`, so the analysis join reads sample provenance, not `task_args`. This is a scoped task-layer change, so the claim that the task layer never changes is retired. DB-1 edits `single.py` `Task.metadata` and the sample metadata to stamp `suite_version`, `dialect_id`, `family` and `form_id`, because today the task records only `renderer`, `renderer_config`, `render_seed` and difficulty. Calling the task layer untouched was wrong; the design itself relies on the task stamping the suite version and the structured spec.

The analysis layer gains cost extraction. It reads `sample.output.usage`, joins a pricing table to produce a `cost_usd` column per row, and sums per suite against the 100 to 150 US dollar budget gate. `get_log_metadata` is rewritten, not lightly patched. It reads `dialect_id`, `family` and per-injector params from sample provenance. The top-level `config.get('mismatched')` lookup goes away, because under the composed scheme `mismatched` lives inside an injector entry such as `["bracket_swap", {"mismatched": true}]`, so the old lookup would always read False and silently lose the balanced-against-mismatched distinction. The `renderer == 'parens'` and `renderer == 'noisy_parens'` string branches and the `'parens'` default also go away, because the new baseline id is `parens.canonical` and no code should key on the old literal strings. This is a breaking migration of `get_log_metadata`, not a drop-in.

## Structure-preservation verification

One predicate is used between every stage. Equality of the input relation hash and the render relation hash is the isomorphism assertion. It is recorded, so it is diffable across suite versions.

```python
def verify(base: BaseRender, root: FormNode, pred) -> tuple[bool, str]:
    observed = induced_relation(base, pred)
    return relation_hash(observed) == relation_hash(containment_relation(root)), \
        relation_hash(observed)
```

- After the archetype builds, verify.
- After each injector, verify. On violation, resample the injector parameters up to a capped number of tries and record `resample_count`. On exhaustion, mark the injector `applied: False` and record the coverage loss. Nothing is ever silently left non-isomorphic.
- Text dialects also record `roundtrip_ok`. Parse the emitted payload back with a notation-appropriate reader, rebuild the tree and compare. This is the parse-back the repository lacks entirely today.
- Spatial dialects verify on the symbolic scene, never on pixels or computer vision. The image is a cosmetic artifact of an already-verified structure.

The predicate is the trust boundary. A too-loose spatial predicate passes tangent or touching primitives as contained. To close this, every archetype ships predicate unit tests against hand-labelled scenes with explicit epsilons. Without those tests the mechanical assertion is theatre. This is a hard admission rule.

Admission to a frozen suite requires the whole archetype-and-injector stack to pass verification across the entire generation set before any model spend. The node map is checked to contain every node id exactly once, so verification cannot pass on a lazily or wrongly built map.

`induced_relation` folds the geometric predicate over primitive pairs, so whether it equals the transitive containment closure depends on the packer faithfully nesting every ancestor pair, including the off-centre grandparent-to-grandchild pairs that sit at the tightest margins in the radial packer. Because those margins are exactly where float choices bite, admission is only portable if the machine that freezes the suite and the machine that renders eval stimuli share a pinned toolchain. So admission verification runs on that same pinned toolchain, the toolchain and libm identity are recorded in provenance, and a checked-in verification transcript is a gate. A divergent eval machine then fails loudly against the transcript rather than silently under-applying an injector or admitting a stack that would fail elsewhere. Platform-stable geometry, above, is what keeps this from being fragile in the first place.

Reproducibility is keyed on the structured spec, the verified relation hash and the emitted payload hash, never on rasterised pixel bytes. Keying on spec and relation hash alone reproduces the score key but not the stimulus: an un-versioned edit to an archetype's packing geometry or an injector's spacing rule leaves the seed and both relation hashes unchanged and still passes verification, yet shows the model a different stimulus. The payload hash, described next, is what closes that gap. The cairosvg or resvg dependency is a justified exception to the stdlib-preference constraint, because providers reject SVG mimes and there is no stdlib rasteriser. The rasteriser is pinned and its version is stamped in `renderer_lib_version`.

### Frozen forms and payload hashing

Two artifacts turn the frozen suite from a reproducible key into a reproducible stimulus.

- Explicit form table. `suites/v1.json` stores an enumerated `form_id -> form_string` table. It does not store only the generator parameters. The core subset that flagship models run is a fixed list of form ids selected from that table, never the generator re-run with a smaller `n`. This matters because `generate_test_cases` assigns `lof_{i:03d}` by position in a shuffled difficulty list whose length is `n`, so re-running with a smaller `n` gives a different shuffle and `lof_042` denotes a different form. A paired McNemar or bootstrap join on `form_id` across a full-suite run and a core-only run would otherwise compare unrelated forms. A continuous integration assertion checks that `form_id -> form_string` is identical across every log joined in one analysis.
- Payload hash. Every stimulus records a content hash of its emitted payload: a blake2b of the emitted string for text, a hash of the symbolic SVG scene for spatial, never a hash of pixels. `suites/v1.json` stores these per-item hashes as an admission gate. A rerun asserts the recomputed payload hash matches the frozen one. A mismatch means an un-versioned renderer edit changed a stimulus, and the run fails loudly rather than scoring a changed image as if it were the frozen one. This also covers text, where `roundtrip_ok` only proves the tree parses back, not that the emitted string is byte-identical.

## Migration plan for the five existing renderers

No forced migration. All five stay registered and green while the composed path is built beside them. Ordered steps:

1. Land the core abstractions: `FormNode`, `containment_relation`, `relation_hash`, `Primitive`, `BaseRender`, the three registries, `ComposedRenderer`, `verify`, `emit`. Add the registry key equals name test. Add the node-map exactly-once test.
2. Fix `factory.py`. Move item seeding to content-addressed `item_seed` inside `render`. Stop sharing one generator across the loop. Route composite per-expression provenance through the same emit path so `render_metadata` is no longer dropped.
3. Migrate canonical. Express it as `parens@1` with no injectors. Optional `whitespace_jitter`. Keep the old class as an alias until parity is tested.
4. Migrate sexpr. Express it as `pattern@1`. Its seven presets become the `preset` injector parameter. Smallest lift.
5. Migrate noisy_parens. Express it as `parens@1` plus a `bracket_swap` injector, where `mismatched` is an ordinary parameter, not a special-cased boolean. Delete the character-scan depth counter. The swap walks the node map.
6. Leave nested_list as-is. It is a debug and ground-truth view, not a suite family. Register it as a utility archetype if convenient.
7. Reimplement svg_circle as the `enclosure@1` archetype. Keep the radial packer geometry. Delete the false circlify docstring. Add the circle-in-circle predicate and its epsilon unit tests. Force fill style to none for model-facing stimuli. Emit a PNG data URI. This is the only substantial code write.
8. Alias the old registry keys to the composed specs for one release, then deprecate. Enforce key equals name throughout.

A shared config-merge helper on the base replaces the five copy-pasted kwargs loops.

## How DB-2 and DB-3 build archetypes on this

DB-2 and DB-3 add the remaining visual families and the novel dialects. They write archetypes and register specs. They touch nothing in the pipeline, the factory, the scorers or the analysis layer. The task-layer and analysis edits above belong to DB-1, so they are already in place before DB-2 and DB-3 start; the "touch nothing" claim is scoped to DB-2 and DB-3, not to the project as a whole.

- DB-2 delivers the text dialect families plus the novel self-similar text dialects. Same recipe: one archetype file, one registry line, automatic provenance, verification, seed threading and pairing.
- DB-3 delivers the visual taxonomy families: paths, blocks, trees, graph, map-centred, map and rooms, plus the novel self-similar spatial dialects drawn from mathematics and nature, such as the RNA arc diagram in example 4. Each is one archetype with a `build` that places primitives and a `predicate` that expresses containment for that geometry. Blocks use a three-dimensional interval-subset predicate. Rooms use a floor-plan enclosure with door gaps and a rect-subset predicate. Each ships its predicate unit tests. Injectors compose orthogonally, so a family inherits jitter, distractors and other variations for free.

The composite scorer is a known v1 boundary. Its `normalize_to_parens` cannot invert a room, a tree or an arc diagram. So novel visual families route through `single_lof_task`, the dialect-blind scorer, in version 1. Generalising the composite echo scorer is deferred, not silently degraded.

## Suite construction for valid measurement

The architecture makes a stimulus reproducible and structure-preserving. It does not by itself make the headline sensitivity score a valid causal measurement. Four suite-construction rules close that gap, and they bind the freeze step, not the renderer code.

- Stage attribution needs an ablation lattice. The provenance records which injectors were applied, not a decomposed per-stage effect. To attribute a drop to a single stage, the frozen suite must contain the single-stage contrasts: canonical, archetype plus injector A only, archetype plus injector B only, and archetype plus both. A dialect that stacks two injectors under one `dialect_id`, as worked example 2 does, yields only a joint effect for that dialect and cannot be decomposed after the fact from metadata. The measurement doc states this: the headline sensitivity for a multi-injector dialect attributes to the whole dialect, not to a stage, unless the lattice provides the single-stage arms.
- Treatment is not uniform within a dialect, so report coverage. Resample-or-drop can set an injector to `applied: False` on deep single-branch or high-fan-out forms, which are exactly the forms where the injector would bite hardest, so an unguarded average inflates that dialect's accuracy where sensitivity should show. The paired contrast for an injector excludes the items where it was not applied, or reports a coverage-adjusted drop, using the per-item applied vector in provenance. This is the confound the open questions already flag, made into an analysis rule.
- Guard the collision direction, not only the split direction. The structured-spec identity stops one dialect splitting into two. It does not stop two distinct `dialect_id`s emitting the same payload for a given form. On a trivial form such as `()` or `(())`, `whitespace_jitter` has nothing to jitter and `bracket_swap` has no interior bracket, so a noisy dialect can emit a payload byte-identical to canonical yet be scored as a distinct condition, which deflates measured sensitivity. A freeze-time collision check asserts that no two distinct `dialect_id`s share a payload hash for any `form_id`, and flags near-identity for images. This reuses the payload hash defined above.
- Duplicate forms are pseudo-replication. `generate_form_string` emits repeated trivial strings such as `()` and `(())` under distinct `form_id`s at easy difficulty. Because `item_seed` keys on the form string, duplicates get identical stimuli per dialect and highly correlated model errors, so a bootstrap or McNemar over `form_id` overstates the effective sample size and narrows the confidence interval. The frozen suite de-duplicates by distinct form string, or the analysis cluster-bootstraps by distinct form string. The report states the count of distinct forms alongside `n` and uses the distinct count for confidence-interval width.

## Open questions

- Perceptual legibility is not verified. A dialect can pass isomorphism yet be unreadable at a high branching factor, where a model cannot resolve overlap within the predicate's epsilon. Verification checks geometry, not readability. Version 1 adds a freeze-time human or vision-model legibility spot-check, but it is not automated. A passing dialect is structurally fair, not proven perceptually fair.
- Injector coverage is form-dependent. An injector with an empty safe-parameter region on deep single-branch or high-fan-out forms silently under-applies through resample-or-drop. Provenance records `resample_count` and `applied`, but the sensitivity analysis must not assume uniform treatment across the suite. This is a confound to report, not hide.
- The named-spec path and the ad-hoc `renderer_config` path can diverge. A curated spec updated in code no longer reconstructs from an old cached config. Named specs are the only frozen-suite source. Ad-hoc is research only. Nothing in code enforces this, so it is a governance rule.
- Prompt anchoring. The system prompt keeps parens-flavoured axiom examples, which anchor models toward parens. A prompt-anchoring ablation is a candidate for a later release, per the decisions file.

## Rejected alternatives

- Category-theoretic IR, Scene and Serializer protocols with verification between every arrow (proposal 2 skeleton): correct and most general, but the ceremony and dual text-and-spatial code paths slow version 1 for no gain the grafted provenance does not already deliver.
- Stringly-typed canonical `dialect_id` as the identity of record (proposal 3): a canonicalisation bug silently splits one dialect into two on the pairing join. The structured spec is the identity instead; the string is derived and round-trip tested.
- Asserting byte-identical PNG in continuous integration (proposal 3): flakes on any font or library bump and, if trusted, invalidates frozen suites on infrastructure changes. Scores are keyed on structure, not pixels.
- Overriding `render_batch` to fix determinism (all three proposals): the factory never calls it. Content-addressed seeding inside `render` is the real fix.
- New per-renderer subclass per family: multiplies code and reintroduces copy-paste. A family is a config, not a class.
- Emitting an SVG data URI as svg_circle does: most multimodal providers reject the SVG mime type. Rasterise to PNG.
- Verifying spatial structure with computer vision on pixels: expensive and fragile. Verify the symbolic scene; the image is downstream of an already-verified structure.
- Keeping colour shading on model-facing stimuli: colour cues that mark isomorphisms are site pedagogy only. Fill style is forced to none.

## Adversarial review disposition

An adversarial pass on 4 July 2026 raised 16 findings against this design, checked against the current source. Every finding was valid and was fixed in the sections above. None was rejected as a misread. The fixes cluster as follows.

- Stimulus reproducibility. The design conflated a reproducible score key with a reproducible stimulus. Fixed by hashing the emitted payload into provenance and gating reruns on it, freezing resolved geometry at freeze time, computing spatial layout in platform-stable arithmetic, and correcting the claims that rounding removes float drift and that a frozen suite is inherently bit-stable or structure-stable across arbitrary code.
- Seed threading. The "keyed by name, not position" rule contradicted "two passes draw independently". Fixed by keying substreams on injector name plus occurrence index, and by seeding from a canonical digest of the whole spec plus a threaded suite version rather than `dialect_id` alone with a `|` join.
- Plumbing correctness. The ad-hoc `-T renderer_config` path would raise `TypeError` and the lambda factory could not register and would swallow config. Fixed by a `spec=None, **kwargs` constructor with `from_dict`, a `Callable` registry type, a relaxed `register_renderer`, and kwargs forwarding.
- Layer scope. The claim that the task, scorer and analysis layers do not change was false. Fixed by scoping the `single.py` `Task.metadata` and `get_log_metadata` changes into DB-1 and reading the analysis join from sample provenance.
- Measurement validity. Fixed by an explicit frozen `form_id` table, a stage-ablation lattice for causal attribution, coverage-adjusted contrasts for non-uniform injector application, a freeze-time payload-collision check, and de-duplication or cluster-bootstrapping to avoid pseudo-replication.

marker: ultracode-foundations-20260704
