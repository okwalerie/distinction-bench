# Design decisions from the 4 July 2026 planning interview

Valerie set these decisions in a structured interview. They bind all DB tasks. Planning agents must read this file before writing a plan.

## What the benchmark measures

The benchmark measures representation sensitivity. The only relation that matters in any stimulus is containment (parenthood, enclosure, being above or below). Every dialect re-presents the same containment structure. Everything else in a stimulus is a distractor.

This means adversarial features and isomorphism are separate axes. A mismatched-bracket dialect is still isomorphic in containment. It tests whether a model can ignore the bracket-matching prior and read only containment. Do not treat adversarial dialects as a separate, "dishonest" family.

## Dialect families

The reference image at demo/13--visual-transforms--colour.png shows 9 visual families, each rendering the same form (XOR, ((a b)((a)(b)))) with CMY colours marking isomorphic subforms:

paths (re-entrant wiring), blocks (isometric stacking), parens (text), enclosure (nested ovals), trees, graph (nodes on a ring), map (centred), map, rooms (floor plan with door gaps).

Rules:
- each family gets a most-abstracted archetype generator, and variations are injected on top of the archetype (two-layer architecture: archetype, then variation injectors)
- sexpr and similar notations form their own "pattern" family, separate from parens
- nearly any self-similar structure can host containment; Valerie wants a handful of novel dialects drawn from mathematics or nature, beyond the 9 families
- colour cues that mark isomorphisms are pedagogy for the site only; models never see them

## Task and prompt design

- version 1 uses ground forms only (primary arithmetic). Every stimulus reduces to marked or unmarked. Variables (primary algebra) are out of scope for v1.
- dialect instruction is minimal hint: the prompt says the stimulus encodes a Laws of Form containment structure and asks the model to reduce it. The prompt does not teach the dialect's reading convention. The writeup must say that sensitivity therefore mixes convention inference with execution.
- the system prompt keeps the current parens-flavoured axiom examples. Open question, recorded for the measurement doc: parens examples anchor models toward the parens dialect; mixed brackets like [()] perform better than (()); a fully notation-neutral prompt makes models default to parens anyway. A prompt-anchoring ablation is a candidate for a later release.
- retire the old design of k=3 samples with 4 expressions per sample and all-pass scoring. Version 1 uses one sample per (form, dialect, model) pair.

## Measurement design

- headline number: a per-model sensitivity score, the paired accuracy drop against the canonical dialect
- paired design: the same forms run through every dialect; compare within items; use McNemar tests and bootstrap confidence intervals over items
- frozen suite versions: suite v1 fixes the dialects, forms and seeds. Scores are always reported per suite version. New dialects create suite v2.
- model grid, 4 tiers: cheap closed models (full suite); 2 to 3 flagships (frozen core only); open-weight models through OpenRouter; reasoning-toggle pairs (same model, reasoning on and off) where the API allows
- budget: 100 US dollars per release run, stretch to 150. Batch APIs at half price wherever possible. Visual dialects run on cheap multimodal models first.

## Site and publishing

- the site is a small Python server (fasthtml or fastapi with datastar/htmx) on Valerie's own server. It is not a static site. It imports lofbench directly.
- the sandbox is the centrepiece: type a form, see it rendered in every dialect at once, server-rendered. One source of truth: the Python renderers.
- charts are baked from the data pipeline's parquet artifacts at deploy time
- audience is eval researchers; rigour comes before polish
- the per-release writeup lives on an external platform (Valerie's blog or similar); the site hosts charts and the sandbox and links out
- the release skill targets Claude Code only, as a SKILL.md in this repo

## Amendments from Valerie, 4 July 2026 (post-design review)

- injectors are coupled to what they render. Every injector declares an applicability set: either named archetypes (a door-gap injector only applies to rooms) or a whole modality (whitespace jitter applies to any text archetype). The spec validator rejects unsupported archetype-injector pairings before any model spend. Generic injectors stay single implementations; archetype-specific injectors live with their archetype.
- the old composite design (4 expressions per call, all-pass scoring) had reasons: a single marked/unmarked answer has a 50% guessing floor; the rewrite system always simplifies, so large forms reduce quickly; and one short expression wastes a strong model's context and per-call overhead. Whether bundling returns as a cost mode is an open DB-7 question. If it returns, scoring is per expression (never all-pass) and statistics must treat the call as a cluster.
- suite v1 is a clean break from all previous findings. Prior runs become pilot data (v0), cited in the writeup as motivation, not compared against. The frozen suite versioning exists exactly for this.
- budget may grow: Valerie is considering soliciting API credits from providers or organisations. Design for the 100 to 150 US dollar envelope; do not depend on more.
- settled: the frozen suite runs one expression per call. Bundling may return later as a cost mode for cheap models only if a pilot shows real savings, scored per expression with the call as a statistical cluster.
- settled: forms are frozen as an explicit id-to-form table in the suite file; the flagship core set is a fixed subset of ids, never a smaller regeneration.
- to evaluate in DB-4 planning: Valerie proposes an entity component system idiom for the rendering engine. The mapping: scene primitives are entities; their attributes (geometry, glyphs, colour, containment link) are components; injectors are systems that run over entities holding the components they need. Injector applicability then becomes structural (which components exist) rather than nominal (which archetype name). Constraints if adopted: no ECS framework dependency, no scheduler (spec order stays the execution order), the containment component is the verified invariant, scenes are small so this is a data-model choice, not a performance one.

## Process

- releases are manual and event-driven (a new model ships); a person approves each run because runs cost money
- all tickets and plans are written in GOV.UK plain style
- subagents run on right-sized lower-power models
