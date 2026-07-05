# DB-3: visual dialect families — archetype renderers for the taxonomy

This plan targets the two-layer rendering architecture in
`.lattice/notes/rendering-architecture-2026-07-04.md` (the "architecture doc")
and the decisions in `.lattice/notes/design-decisions-2026-07-04.md` (the
"decisions doc"). Both bind this task. Read them before touching code.

Naming note: the architecture doc's own prose swaps DB-2 and DB-3 when it
describes who builds what ("DB-2 delivers the spatial families", "DB-3
delivers the novel self-similar dialects"). The actual lattice tasks are the
other way round: DB-2 is titled "Text dialect families: archetypes plus novel
self-similar dialects" and owns text dialects plus novel maths/nature
dialects (for example the RNA arc example in the architecture doc). DB-3 (this
task) owns the nine-family visual taxonomy: enclosure, trees, blocks, graph,
map, map-centred, rooms, paths. Treat the task descriptions as ground truth
over the architecture doc's DB-2/DB-3 labels.

## Scope and boundary

In scope: six archetype generators (trees, blocks, graph, map, map-centred,
rooms) plus a written feasibility call on a seventh (paths), plus
`rna_arc@1` (the spatial RNA arc-diagram, architecture doc worked example 4,
family `biopolymer`). Enclosure is DB-4's M5 migration of
`svg_circle_renderer.py`; per the orchestrator's disposition, DB-3 does not
build `enclosure@1` and instead consumes the landed archetype as the worked
exemplar for the other families' archetype and predicate style. Each
archetype DB-3 does build is one file plus one registry line, per the
architecture doc's "one archetype file, one registry line" pattern.

Out of scope, per the architecture doc's own scoping of DB-2/DB-3: the
pipeline, `datasets/factory.py`, the scorers, and the analysis layer. This
task writes archetypes against the `Archetype` protocol and touches nothing
else. No injectors are implemented here beyond noting, per family, which
injectors from DB-4's generic set apply and which family-specific injectors
this task should register alongside its archetype (DB-4 owns the injector
protocol and the generic injectors; this task owns family-specific ones,
consistent with the decisions doc amendment that "archetype-specific
injectors live with their archetype").

No model API calls. No suite freezing. No eval runs. Budget is untouched by
this task.

## Blocking dependency — read this before starting implementation

The architecture doc is a design, not landed code. A repo grep on
2026-07-04 for `ARCHETYPE_REGISTRY`, `class Primitive`, `ComposedRenderer`,
`FormNode` returns nothing under `src/`. None of DB-4's core abstractions
exist yet: `FormNode`, `containment_relation`, `relation_hash`, `Primitive`,
`BaseRender`, the `Archetype` and `Injector` protocols, the three registries,
`ComposedRenderer`, `verify`, `emit`. There is also no rasteriser dependency
pinned yet (`cairosvg`/`resvg` are absent from `pyproject.toml` and the
venv) — `emit()` needs one to turn symbolic SVG into a PNG data URI, per the
architecture doc's rejection of SVG mime types for model stimuli.

This task cannot write real code against a stable interface until DB-4 lands
step 1 of its migration plan (core abstractions, registries, `verify`,
`emit`, the rasteriser pin). Do not start implementation before that. When
DB-4 lands, the implementer must:
1. The core abstractions live under DB-4's actual package layout,
   `src/lofbench/renderers/pipeline/` (`nodes.py`, `archetype.py`,
   `injector.py`, `emit.py`, `composed.py`, `spec.py`, `seeding.py`,
   `verify.py`, `registry.py`, `provenance.py`, per DB-4's plan) — not the
   `renderers/core.py` guess this plan previously made. Confirm exact
   submodule names against DB-4's landed code before importing, since
   module boundaries within `pipeline/` are "guidance, not a contract" per
   DB-4's own plan.
2. Re-check the exact `Archetype.build`/`predicate` signatures and the
   `Primitive`/`BaseRender` field names against the landed code, not against
   this plan's paraphrase of the architecture doc.
3. Confirm the rasteriser choice and its pin before relying on `emit()`.

If DB-4 has not landed when implementation is picked up, stop and move this
task to `blocked` rather than building against a guessed interface.

Staging within DB-4's milestones matters for planning this task's own work:
symbolic-scene authoring and predicate verification (building `Primitive`
trees, running `verify()` against `containment_relation`) are available to
DB-3 once DB-4 lands M1 (core abstractions) and M2 (registry widening,
`DialectSpec`) — no rasteriser is needed for that work. Model-facing PNG
emission for these six spatial archetypes waits on DB-4's M5, which pins
the rasteriser (cairosvg or resvg) and reimplements `emit()`'s spatial
path. Plan implementation in that order: build and verify scenes
symbolically first, wire real image emission last.

## Shared infrastructure this task adds

Three small helper modules, used by more than one family. Build them once,
under `src/lofbench/renderers/archetypes/_geometry.py` (exact path is the
implementer's call; keep it under one module so imports are simple):

- `stable_trig`: sin/cos/sqrt via `decimal.Decimal`, not `math`. `Decimal`
  arithmetic is specified by the General Decimal Arithmetic spec and
  correctly rounded — it does not depend on libm, so it is bit-identical
  across platforms, unlike `math.sin`/`math.cos` which the architecture doc
  names as the concrete platform-drift risk in `svg_circle_renderer.py`.
  Implementation: a fixed fifteen-term Taylor series for sin/cos over an
  argument range-reduced to `[-pi, pi]` using a hard-coded high-precision
  `Decimal` pi constant, plus `Decimal.sqrt()` (correctly rounded, part of
  the spec). Convert to `float` only at the final coordinate, after all
  comparisons that matter for the containment predicate have been done in
  `Decimal`. Context precision: 28 digits, matching the module default.
  Used by: enclosure, graph, map-centred (anything placing primitives by
  angle).
- `rect_subset`: exact `Fraction`-based rectangle-in-rectangle test with a
  margin, `child ⊆ parent.shrink(margin)`. All bounds are `Fraction`, so the
  test is exact — no epsilon needed until the final float conversion at
  emit time. Used by: map, rooms.
- `interval_subset`: exact `Fraction`-based 1-D and 3-D interval subset
  tests (the 3-D case is three independent 1-D tests, one per axis). Used
  by: blocks, trees (structural, see below), paths if the lite variant
  ships.

Rationale for `Fraction`/`Decimal` over "round to fixed precision": the
architecture doc explicitly rejects rounding as the guarantee ("a
transcendental that differs by one unit in the last place across libm can
straddle the rounding cut and still round two ways"). `Fraction` and
`Decimal` remove the transcendental-via-libm step entirely rather than
papering over it.

## Node ids and the containment predicate for trees and graph

For trees and graph, the rendered `Primitive` tree is built by walking
`FormNode` directly: one `Primitive` per node, parented to the same parent
as in `FormNode`. The induced relation is therefore the transitive closure
of the `Primitive` parent/child links, which mirrors `FormNode` by
construction. Structural adjacency alone ("child is a member of
parent.children") passes by construction and verifies nothing geometric —
a layout bug that overlaps two node discs, draws a child above its parent,
or draws an edge that does not actually touch the two node positions it
claims to connect would still pass a purely structural check. Per the
orchestrator's disposition, both families add a geometric sanity predicate
alongside the structural one, so a passing check is evidence about the
drawn picture, not just about which Python object references which:

- Trees: no two node discs overlap (a pairwise disc-disjointness check
  over the laid-out `(x, y, radius)` triples), and every child's y strictly
  exceeds (is laid out below) its parent's y.
- Graph: each drawn edge's two endpoints coincide, within the same epsilon
  discipline as the other spatial predicates, with the two ring positions
  of the parent and child primitives it is supposed to connect.

Both checks compose with the structural adjacency check — an archetype
passes only if both hold. This is on top of, not instead of, catching a
dropped primitive or a wrongly-wired decorative distractor
(`node_id=None`), which the structural check still catches. Say both
checks plainly in each archetype's docstring so a future reader does not
mistake the combined predicate for a purely structural one.

## Per-family plan, in cost order

### 1. Enclosure — owned by DB-4, consumed here as the worked exemplar

Enclosure is not this task's archetype to build. Per the orchestrator's
disposition, DB-4 owns `enclosure@1` outright as part of its M5 migration
of `svg_circle_renderer.py` (radial packer geometry through `stable_trig`,
the circle-in-circle predicate with margin, `boundary_jitter` and
`distractor_marks` injectors, PNG emission through the pinned rasteriser).
DB-3 consumes the landed `enclosure@1` archetype and its `stable_trig`
helper as the worked exemplar for the remaining six families' archetype and
predicate style, and reuses `stable_trig` directly (see graph and
map-centred below, which both convert polar coordinates to Cartesian
through the same helper). Do not re-implement enclosure under this task;
if DB-4's M5 has not landed `enclosure@1` when this task's implementation
starts, treat it the same as the rest of the blocking-dependency note
above.

### 2. Trees — cheap, pure integer arithmetic

Layout: standard recursive tree layout. Post-order DFS assigns each leaf a
sequential integer x-slot; each internal node's x is the mean of its
children's x (a `Fraction`, not a float, so it stays exact). Depth gives y
directly (`y = depth`, root at the top or bottom, either reads fine). Zero
transcendentals anywhere in this family — the cheapest and most robust of
the seven.

Containment predicate: structural adjacency plus the geometric sanity
check (no overlapping node discs; every child laid out below its parent),
see "Node ids and the containment predicate for trees and graph" above.

Injectors: layout jitter (rational perturbation of x within a slot and y
within a depth band — keep it a `Fraction` delta so it stays exact until
emit), distractor leaves (`node_id=None`, visually distinguished by style,
e.g. dashed stroke, cosmetic only, never reparented into the real subtree),
node-glyph substitution (shape swap at nodes, style only).

### 3. Blocks — cheap, isometric via dimetric projection

Layout: this is a 1-D interval subdivision problem, not a true isometric
projection problem. Each node gets a 3-D axis-aligned box: z-band by depth
(stacked), and at each depth, siblings divide their parent's footprint along
x by count (or by leaf-count weight, using common-denominator `Fraction`
arithmetic so the subdivision stays exact). Project to 2-D for drawing using
a fixed **dimetric** transform, not true isometric:
`screen_x = (x - y) * X_SCALE`, `screen_y = (x + y) * Y_SCALE / 2`. This is
the standard 2:1 tile-game projection — purely rational, no `sqrt(3)/2` or
any trig at all, and reads as "isometric-ish" without needing `stable_trig`.

Containment predicate: three-dimensional interval-subset, per the
architecture doc's explicit call-out ("Blocks use a three-dimensional
interval-subset predicate"). Because box bounds are kept as `Fraction`
through to the predicate check, this test is exact with no epsilon needed at
all — epsilon only enters, if ever, at final float emission, and even then
only as a rendering margin, not a verification fudge.

Injectors: isometric jitter (rational perturbation to box height/footprint,
still exact fractions), distractor floating blocks (`node_id=None`),
top-face label/glyph swap.

### 4. Graph — cheap, nodes on a ring

Layout: one node per `FormNode`, placed at N equally spaced angles around a
ring. Angles are exact `Fraction`s of a full turn (`k/N`); convert to
Cartesian only through `stable_trig`, reusing the same helper as enclosure —
call this reuse out explicitly in the code so a reviewer sees the shared
dependency. Edges are drawn as arcs/chords between parent and child ring
positions (a circular dendrogram / chord-diagram look, distinct from the
enclosure family and distinct from the "paths" family's literal wire
crossings).

Containment predicate: structural adjacency (edge exists between parent and
child) plus the geometric sanity check that each drawn edge's endpoints
coincide with the parent's and child's actual ring positions (see "Node ids
and the containment predicate for trees and graph" above). Position on the
ring is not itself the containment signal, the drawn edge is — but the edge
must actually terminate where the two node positions are, which is what the
geometric check catches. This matters for the injector design below:
ring-position jitter is safe precisely because the predicate does not
depend on a fixed angular position, only on the edge endpoints tracking
wherever the (possibly jittered) node positions actually are.

Injectors: ring-position jitter (perturb each node's angle by a bounded
`Fraction` delta — safe because the predicate is edge-based, not
position-based), arc-curvature style variation, distractor isolated nodes
(`node_id=None`, no edges).

### 5. Map — planar layout, treemap-with-gaps

Layout: a rectangular treemap. Recursively subdivide a parent rectangle into
child rectangles sized by subtree leaf-count weight (squarified slicing is
fine, or plain slice-and-dice if squarify is judged not worth the extra
code — either way, subdivision fractions are exact `Fraction`s of the
parent's width/height). Insert a fixed-width gap between sibling rectangles
and between a child and its parent's border, so the map reads as bordered
regions rather than a flush grid.

Containment predicate: `rect_subset`, child rectangle inside parent
rectangle shrunk by the gap margin. Exact `Fraction` comparison, no epsilon
needed pre-emission.

Injectors: gap-width jitter, region distractor (`node_id=None` area carved
from otherwise-unused parent space), region style/colour variance (site-only
per the decisions doc; model-facing stimuli force fill to none regardless).

### 6. Map-centred — planar layout, annular treemap (sunburst)

This family and "map" are listed as two separate entries in the decisions
doc ("map (centred), map"), and the architecture doc's DB-2/DB-3 handoff
section names both explicitly ("map-centred, map"), so this is deliberately
two archetypes, not a duplicate.

Layout: a radial/annular treemap (sunburst). The root sits at a fixed
central disk. Depth `d` occupies a fixed annulus band. Within each annulus,
a node's angular span is an exact `Fraction` of a full turn, proportional to
its leaf-count weight — the same weighting idea as "map", but in polar
coordinates instead of Cartesian. Convert (radius, angle) to Cartesian only
through `stable_trig`, again reusing the enclosure/graph helper.

Containment predicate: an interval-subset test in polar coordinates — child
radial interval subset of parent's, AND child angular interval subset of
parent's angular span. Pin the layout invariant that makes this
wraparound-safe as a plain `Fraction` subset test: children partition
their parent's angular span into a contiguous, non-overlapping sequence
covering the whole span exactly (weights summing to the parent's total
span), and no wedge is ever allowed to cross the zero-angle seam — the
layout algorithm splits any wedge that would straddle angle zero rather
than allowing an angular interval to wrap. Under that invariant every
angular span is representable as an ordinary `[lo, hi)` `Fraction` interval
with `0 <= lo < hi <= 1` (as a fraction of a full turn), so
`interval_subset` applies directly with no modular arithmetic. Do the
interval comparisons as exact `Fraction`s before any trig conversion; only
the final draw uses `stable_trig`.

Injectors: wedge-angle jitter, annulus-width jitter, distractor wedge
(`node_id=None`).

Judgement call, stated plainly: the reference image's bottom two panels look
like clustered circle blobs (closer to circle-packing / Voronoi treemaps
than to rectangles or annuli). True Voronoi treemaps need iterative
relaxation (Lloyd's algorithm or a power-diagram solve), which is not
cheaply exact-reproducible and does not have as clean a closed-form
containment predicate as rectangle or annulus subset tests. Given "maps...
need planar layout" is already the medium-cost tier, not the cheap one, this
plan recommends the two closed-form treemap variants (rectangular and
annular) for v1, and records true blob/Voronoi packing as a documented
stretch alternative for a later release if visual fidelity to the reference
art becomes a priority. This is a recommendation, not a foreclosure — an
implementer with time to spare could attempt Voronoi packing instead, but
should not treat it as free.

### 7. Rooms — planar layout, floor plan with door gaps

Layout: rectangular subdivision like "map", but simpler — plain slice-and-
dice (alternate horizontal/vertical cuts by depth parity) rather than
squarified treemap, because real floor plans read better as straight
corridor-and-room walls than as arbitrary squarified boxes, and slice-and-
dice is exactly representable with the same `Fraction` interval arithmetic,
even more simply than squarify. Reuse `rect_subset` from "map" — the same
helper, not a reimplementation; say so in the code.

The door gap is purely cosmetic. It does not change the rectangle geometry
or the containment predicate: a child rectangle's shared wall with its
parent's boundary gets a literal break in the drawn stroke (for example, a
gap centred at the midpoint of the shared edge, width a fixed fraction of
the edge length). The underlying `rect_subset` check is identical to "map".

Containment predicate: `rect_subset`, shared with "map".

Injectors: door-gap position jitter (move the door along the shared edge,
bounded so it stays within the edge, cosmetic only), door count/width
variance, corridor/furniture distractor rectangles (`node_id=None`).

### 8. Paths — hardest, feasibility call, recommend a lite variant plus explicit deferral

What the reference image shows (top-left panel): `a` and `b` as pin boxes,
wires routed through a circular boundary with genuine crossings and a
re-entrant point. This is hard for three concrete reasons, not just "it
looks hard":

1. There is no closed-form layout analogous to a treemap or circle-packer
   for "wire routing with over/under nesting." Real schematic auto-routers
   solve a channel-routing / crossing-minimisation problem, which is
   itself a hard combinatorial layout problem, not a simple recursive
   geometry function.
2. A geometric containment predicate for "this wire loop encloses that pin"
   needs a specific convention (for example, point-in-polygon /
   winding-number against the closed curve the wire traces) — implementable,
   but it presupposes the routing algorithm already exists to produce that
   closed curve.
3. Nothing already in the codebase gives a routing algorithm to lean on
   (unlike enclosure, which had `svg_circle_renderer.py` to migrate from).

Recommendation: defer the literal re-entrant crossing-wire family past v1,
stated as an honest scope cut, not a silent drop. Ship a much cheaper
"paths-lite" archetype instead, reusing the interval-nesting idea from the
architecture doc's own RNA-arc worked example (worked example 4): one arc
(or a "staple" shape — two verticals plus a horizontal, fully rational
coordinates, no need for true circular SVG arcs) per node over a shared
baseline, where a parent's span strictly contains its children's spans.
Containment predicate: `interval_subset` on the 1-D spans, identical
machinery to trees' structural test but expressed as literal geometric
nesting instead. Register this under `dialect_id = "paths.arc-nest-v1"`,
family `"paths"`, and document explicitly in its docstring and in
provenance-facing text that this is a simplified interval-nesting stand-in
for the taxonomy's literal wiring diagram, not a reproduction of it — so a
future reader (or a chart legend) does not conflate the two.

If the team decides "paths-lite" still doesn't earn its keep as a distinct
visual family (it is geometrically almost identical to trees, just drawn
flat), the alternative is to skip paths entirely for v1 and record the same
three-point feasibility note as the reason. Either choice is defensible;
this plan does not force one, but leans toward shipping the lite version
since it costs close to nothing given the RNA-arc code already exists as a
template.

### 9. RNA arc — spatial biopolymer family, build alongside paths-lite

Per the orchestrator's disposition, `rna_arc@1` (the architecture doc's
worked example 4: a nature dialect outside the nine visual families,
family `biopolymer`, modality `spatial`) is DB-3's, not DB-2's. DB-2's text
sibling `rna_dotbracket@1` shares the `biopolymer` family name but a
different modality; a one-line vocabulary sync between whoever implements
each is worth doing, per DB-2's own coordination note, but is not a
blocking dependency.

Layout: one non-crossing arc per node over a shared baseline sequence, a
parent's arc spanning its children's arcs by construction. This is
essentially the same interval-nesting idea as the "paths-lite" stand-in
above — one arc (or "staple" shape) per node, parent span strictly
containing child spans, fully rational coordinates. Because the two
families share this pattern almost exactly, build them together: write the
shared 1-D interval-nesting layout and containment logic once (reusing
`interval_subset` from the shared `_geometry.py` helpers), then specialise
the arc-drawing style (RNA sequence letters and arc glyphs for `rna_arc@1`,
the "staple" shape for `paths.arc-nest-v1`) on top of the common base. This
avoids two near-duplicate implementations of the same nesting logic.

Containment predicate: `interval_subset` on the 1-D spans, identical
machinery to trees' structural test and to paths-lite, but expressed as
literal geometric arc nesting.

Injectors: arc-style jitter (curvature, colour-neutral stroke variation),
distractor arc (`node_id=None`, a decorative arc that does not nest inside
any real span).

## Acceptance criteria

- Every archetype DB-3 builds (trees, blocks, graph, map, map-centred,
  rooms, rna_arc, and paths-lite if shipped) is deterministic under a
  fixed seed on the pinned toolchain: same `FormNode` input plus same
  archetype/injector params produces byte-identical emitted payload across
  repeated runs on the same machine.
- Every archetype's containment predicate is machine-verified: the induced
  relation from `verify()` equals the input `containment_relation` for a
  representative generation set covering at least depth 1 through 5 and
  branching factor 1 through 4. For trees and graph specifically, the
  predicate combines structural adjacency with the geometric sanity checks
  in "Node ids and the containment predicate for trees and graph" above
  (disc non-overlap and below-parent layout for trees; edge-endpoint
  coincidence for graph).
- Every archetype ships predicate unit tests against hand-labelled scenes
  with explicit epsilons (or exact `Fraction` equality where no epsilon is
  needed), per the architecture doc's "without those tests the mechanical
  assertion is theatre" rule. This includes at least one deliberately
  tight case per family (grandparent-to-grandchild margin, or equivalent)
  to prove the predicate does not silently pass a near-miss.
- Every archetype is reachable through the registry: `ARCHETYPE_REGISTRY`
  entry plus a `DIALECT_SPECS` entry plus a registered named factory, so
  `-T renderer=<dialect_id>` resolves, matching the existing `circle`
  precedent.
- `uv run pytest` and `uv run ruff check .` pass with these additions.
- No model API calls, no eval runs, no suite freezing in this task's diff.
- The `circle` registry key resolving as an alias to `enclosure@1` is
  DB-4's M5 responsibility, not re-asserted here; DB-3 depends on
  `enclosure@1` existing in the registry to build `rna_arc` and the other
  families' shared helpers against it.

## Budget note

Rendering costs nothing in this task — no model calls happen here. The
100 to 150 US dollar release budget is spent later, at eval time, and
visual dialects (all seven-plus-one families this task adds) run on cheap
multimodal models first, per the decisions doc. This task's only cost is
engineering time.

## Risks

- Blocking dependency risk (see above): DB-4's core abstractions and
  rasteriser pin do not exist yet. Do not start real implementation before
  DB-4 lands; the exact module path and protocol signatures assumed here
  are paraphrases of the architecture doc, not landed code.
- Map / map-centred fidelity risk: the closed-form treemap and annular-
  treemap choices are a deliberate cost/fidelity tradeoff against the
  reference image's blob-like look. Flagged above, not hidden.
- Paths feasibility risk: the literal re-entrant wiring family is
  deliberately deferred with a stated reason; "paths-lite" is a stand-in,
  not a reproduction, and should not be presented to readers as if it were
  the taxonomy's literal panel.
- Platform-stability risk: `stable_trig`'s Taylor-series sin/cos is a
  from-scratch numerical routine, not a library call. It needs its own
  correctness tests (compare against `math.sin`/`cos` within a wide
  tolerance, on top of the platform-stability property, which is about
  bit-identical repeatability, not accuracy against `math`).
- Squarified-vs-slice-and-dice choice for "map" is left to the implementer;
  either is exact-`Fraction`-representable, so this is a visual-quality
  tradeoff, not a correctness one.

marker: plan-wave-2-20260704
