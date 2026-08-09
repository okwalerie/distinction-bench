# DB-14: ship the public v1 benchmark, gallery, and model sweep

## outcome

ship release `v1.0.0` as a public, reproducible diagnostic of how models handle the
same laws-of-form ground form across different representational dialects. a sealed
release bundle is the sole interface used by evaluation, analysis, the static site,
downloads, inspect exports, and citations. no public number is read from the live
renderer registry or inferred from a loose directory of logs.

the critical path is:

1. make the repository testable and correct the form generator;
2. freeze a valid, balanced 400-form suite and its named subsets;
3. add the release-bundle seam and suite-driven inspect task;
4. add the capped, resumable openrouter runner and revised metrics;
5. build the public static gallery from a sealed bundle;
6. admit affordable model runs, seal the release, and deploy.

db-14 subsumes db-10. once the suite-driven task and its acceptance tests pass,
record that fact on db-10 and close it through its own review gate rather than
maintaining two implementations of the same runner.

## scope and boundaries

### required for `v1.0.0`

- ground arithmetic only: every abstract form has the normal value `marked` or
  `unmarked`; variables and primary algebra remain out of scope.
- 400 distinct, depth-valid, target-balanced abstract forms, with a fixed 120-form
  core and smaller probe/agent subsets stored as explicit id lists.
- the 29 current composed dialects, renamed with `reference` and `plain` terminology
  and documented in the suite. bare legacy renderers are development tools, not
  release dialects.
- `reduce-infer-v1` as the scored public model protocol: a notation-neutral statement
  of the laws, no dialect legend, no requested chain of thought, and strict
  `{"value":"marked|unmarked"}` output.
- a direct-api cohort run through inspect/openrouter and a separately labelled
  codex/claude agent-surface cohort. never pool the cohorts.
- competence and invariance profiles, controlled within-archetype effects, exact run
  provenance, costs, coverage, and failures.
- a no-runtime-api static site and downloadable sealed bundle.

### implemented as interfaces, not release blockers

- define `reduce-taught-v1`, `transcribe-infer-v1`, and `transcribe-taught-v1` in the
  protocol registry, but do not require a full model grid for them in `v1.0.0`.
  they may run on the 40-form diagnostic subset only if the direct-api reserve remains.
- define the anonymous human-trial schema and publish a 12-stimulus conference
  instrument. collection and analysis can follow after the model release; no hosted
  collection service is part of db-14.
- produce a redaction-capable inspect bundle interface. publishing inspect logs is
  optional until the secret/path scan passes; the static site and tidy public trials
  remain authoritative.
- github pages output is required. cloudflare dns and the redirect from
  `distinction.waler.ie` may pause at `needs_human` if credentials are unavailable.
  use `distinction.valeriekim.ca` as the canonical hostname.

### explicitly out of scope

- opencode zen, primary algebra, arbitrary-form evaluation on the public site,
  bundling several forms in one model call, epochs, and claims that pilot-v0 results
  are comparable with v1.
- provider-native integrations when openrouter already exposes the requested model.
  the provider-neutral core must not contain model api calls.

## domain language and identity

add a root `CONTEXT.md` and use these terms in code, manifests, prose, and charts:

- `abstract_form`: the containment tree being tested.
- `reference_transcription`: its neutral parentheses serialization; it is not a
  privileged model condition.
- `normal_value`: `marked` or `unmarked`.
- `dialect`: a deterministic rendering convention.
- `stimulus`: one abstract form rendered in one dialect.
- `protocol`: instructions, response schema, and scorer rules.
- `trial`: one model response to one stimulus.
- `run`: a declared set of trials under one model configuration and execution
  surface.
- `release`: an admitted, checksummed set of suite, protocols, runs, and analyses.

rename public dialect ids before freezing:

- `parens.canonical` becomes `parens.reference-v1`.
- each injector-free family/archetype arm uses `.plain-v1`, including the present
  `*.canonical-v1` ids and `pattern.default`.
- treatment ids retain their descriptive suffixes and gain `-v1` consistently.
- `canonical_string`/`canonical` remain only when reading historical pilot data.
  new core names are `normal_form_string` and `normal_value`; do not rewrite old log
  provenance.

add `docs/adr/0001-sealed-release-bundle.md`. it records the hard-to-reverse decision
that the sealed release bundle, rather than registries, logs, or site code, is the
publication authority. there is one deep `ReleaseBundle` module with a small
interface: `create_working`, `open`, `admit_run`, `validate`, and `seal`.

## gate zero: repair the baseline

these are preconditions, not cleanup to postpone:

- remove eager native-cairo loading. `import lofbench`, core generation, suite-json
  inspection, and text-only tests must work without cairo. use `importlib.metadata`
  for the cairosvg version and import/rasterise only on the spatial emit path.
- move cairosvg to a `visual` extra. keep a core ci job without native cairo and a
  visual ci job that installs cairo plus `--extra visual` and exercises spatial
  renderers and the full suite gate.
- fix all 13 current ruff findings so `uv run ruff check .` is a hard gate.
- add the actual mit licence file for code and a cc by 4.0 data/content licence;
  state which files each covers and add repository/homepage metadata.

## suite v1 and protocol definition

### generator and form table

- fix `generate_form_string` so `remaining_max <= 0` returns the empty interior and
  never adds a further mark. enforce both minimum and maximum depth in tests; the
  current suite is one level too deep in 109 of 120 forms.
- freeze 80 distinct forms per difficulty tier, exactly 40 marked and 40 unmarked.
  reject a candidate that violates the tier's depth or mark bounds, duplicates an
  existing reference transcription, or misses its target quota.
- assign content ids as `lof_` plus a 20-hex-character blake2b digest of the utf-8
  reference transcription. fail on any digest collision. sort by difficulty then id;
  ids never depend on generation order or requested sample count.
- store `depth`, `mark_count`, `normal_value`, difficulty, containment-tree data, and
  reference transcription for every form.
- store these explicit form sets in the suite; evaluation never regenerates them:

  - `full`: all 400 ids.
  - `core`: 24 per tier, selected as the first 12 ids of each normal value after
    sorting; 120 ids total.
  - `agent`: 8 per tier, four of each normal value; 40 ids total.
  - `probe`: one fixed form per tier, with the resulting 3/2 normal-value split stated
    in the manifest; five ids total.

### dialect table and stimuli

- extend `DialectSpec` with `label`, `reading_rule`, `description`, `modality`,
  `model_format`, provenance/citation, and limitations. the suite serializes these;
  the public site never reaches into `DIALECT_SPECS`.
- retain all 29 composed dialects and all 13 families. materialize 11,600 cells.
  each cell records `abstract_form_id`, `dialect_id`, family, archetype, injectors,
  symbolic payload hash, exact model-payload sha256, asset path, structure check, and
  round-trip result where applicable.
- make payload collision, incomplete node maps, nondeterminism, structural mismatch,
  unknown injectors, and missing assets hard freeze failures. the current
  warning-only collision behaviour is not acceptable for a public suite.
- define dialect sets in the suite:

  - `all`: all 29.
  - `text` and `spatial`: derived and then stored explicitly.
  - `core`: the injector-free `plain` arm for each of the 16 archetypes plus
    `parens.noisy-mismatched-v1`, 17 dialects total.
  - `agent`: one declared plain arm for each of the 13 families, 13 dialects total.
  - `probe_text`: `parens.reference-v1` and `prose.containment-plain-v1`.
  - `probe_multimodal`: `parens.reference-v1` and `enclosure.plain-v1`.

the exact renamed ids are emitted once by the freezer and treated as data thereafter;
tests assert the counts and membership rather than duplicating a second hand-written
site list.

### protocols

introduce a versioned `ProtocolSpec` with `protocol_id`, system text, user template,
response json schema, scorer id/version, maximum output tokens, whether a dialect
legend is included, and whether the requested answer is a normal value or a
structural transcription.

- `reduce-infer-v1` is primary. use one stimulus per call, temperature zero where the
  provider supports it, no chain-of-thought solver, and at most 512 completion tokens.
  exact-schema `marked`/`unmarked` parses score normally; malformed output, refusal,
  or extra values complete the trial as `invalid` and score incorrect.
- the three diagnostic protocols use the same trial schema and fixed 40-form subset.
  `taught` protocols include only the suite's declared `reading_rule`; transcription
  protocols compare parsed containment trees, not strings.
- transport/provider failures are not scored. retry them at most three times with
  the attempt history retained. never retry a valid but wrong or invalid response.

## release-bundle seam and migration

a working release directory becomes immutable when sealed:

```text
release-v1.0.0/
  release.json
  suite.json
  protocols.json
  runs.jsonl
  trials.parquet
  calls.parquet
  profiles.parquet
  effects.parquet
  transcripts.jsonl
  stimuli/text/<dialect>.json
  stimuli/image/<dialect>/<form>.png
  inspect/
  site/
```

`release.json` records bundle schema, release id/status, repository url and commit,
suite/protocol ids, creation/seal timestamps, licences, citations, expected and
admitted run ids, and sha256 plus byte size for every file. sealing fails if the tree
is dirty, any artifact is unlisted, or any checksum changes after validation.

`RunManifest` records a deterministic `run_id` over suite, form/dialect set,
protocol, requested and provider-resolved model ids, execution surface, provider and
endpoint, routing/privacy policy, sdk/cli version, reasoning settings, generation
settings, billing channel, expected trial ids, attempts, token usage, latency, cost,
status, and rejection reason. allowed lifecycle is `planned -> probed -> complete ->
admitted|rejected`; only admitted runs enter a sealed bundle.

`TrialRecord` extends the present pipeline row with `trial_id`, `run_id`, `protocol_id`,
`execution_surface`, `requested_model_id`, `resolved_model_id`, provider endpoint,
prompt hash, stimulus hashes, parse status, attempt count, latency, token categories,
and observed cost. `trial_id` is deterministic over run, form, and dialect, so resume
cannot duplicate a paid call.

migration rules:

- replace the invalid checked-in `suites/v1.json` before any v1 spend. record its git
  sha and the depth defect in `docs/pilot-v0.md`; git history is the archive.
- all existing logs, notebooks, and generated artifacts remain `pilot-v0`, keep their
  historical dialect strings, and are never admitted to `v1.0.0`.
- retain the current pilot pipeline only as an explicit `pilot-v0` importer. the new
  release analysis reads `TrialRecord`s from a bundle and must not infer suite identity
  from a command-line `--suite-version` label.
- update `.claude/skills/bench-release/SKILL.md` after the suite runner lands: remove
  its obsolete “no eval-time suite runner” instructions and make bundle validation,
  cost probing, typed approval, admission, and sealing the sequence.

## suite-driven evaluation and spend controls

### inspect task

make `single_lof_task` suite-driven with the interface `suite`, `form_set`,
`dialect`, and `protocol`; default public values are `v1`, `core`,
`parens.reference-v1`, and `reduce-infer-v1`. it loads frozen forms and the frozen
`DialectSpec`, checks every selected cell's hashes before model initialisation, and
stamps exact suite/protocol/run provenance into task and sample metadata.

move generator-driven research to an explicitly named `adhoc_single_lof_task`; the
release runner refuses it. do not support mixed `n`/`seed` and suite arguments. route
both tasks through one case-to-`Sample` implementation so text/image payload and
metadata behaviour cannot drift.

### runner interface

add one runner command with `plan`, `probe`, `run`, `resume`, `status`, and `admit`
subcommands. it accepts the release directory and an explicit env-file path; it reads
`~/dbench.env` without echoing values and requires mode `0600` or stricter. no secret
value, request header, or env-file content may enter logs or manifests.

use an internal `TrialExecutor` seam with these adapters:

- `InspectExecutor`: direct api runs through inspect/openrouter.
- `CodexCliExecutor` and `ClaudeCliExecutor`: isolated one-shot cli runs using saved
  subscription auth. they write the same records but set
  `execution_surface=agent_cli`, record cli version/system-context limitations, and
  are never shown in the direct-api cohort.
- an in-memory executor for tests. this second adapter makes the seam real and keeps
  budget/resume/admission logic testable without network calls.

for cli trials, use a fresh temporary non-repository directory, pass the prompt as a
subprocess argument list rather than a shell string, ignore project/user rules where
supported, disable tools where supported, request the same json schema, and record
any tool invocation as a protocol violation. never pass `~/dbench.env` to cli-agent
subprocesses.

### openrouter routing and model programme

at probe time fetch the public openrouter model and endpoint catalog. require the
exact requested id, record the returned catalogue row and retrieval time, select one
endpoint satisfying no training/data collection and zdr when available, and pass
`provider={order:[selected_slug], allow_fallbacks:false, data_collection:"deny",
zdr:true}` through inspect's openrouter model arguments. if no qualifying endpoint
exists, mark the configuration unavailable; never loosen privacy or permit fallback
silently.

use the model ids below as the initial programme; availability and resolved ids are
frozen in each manifest, not silently substituted:

- full-suite candidates: `openai/gpt-5.6-luna`, `google/gemini-3.6-flash`,
  `qwen/qwen3.7-flash`, `qwen/qwen3.7-plus`,
  `deepseek/deepseek-v4-flash-0731`, `moonshotai/kimi-k2.6`, `z-ai/glm-5.2`, and
  `minimax/minimax-m2.7`.
- core candidates: `openai/gpt-5.6-terra`, `openai/gpt-5.6-sol`,
  `google/gemini-3.1-pro-preview`, `qwen/qwen3.8-max`,
  `deepseek/deepseek-v4-pro`, and `moonshotai/kimi-k3`.
- agent cohort: codex `gpt-5.6-luna`, `gpt-5.6-terra`, `gpt-5.6-sol`; claude code
  the currently installed haiku, sonnet, and opus aliases, with their resolved ids
  recorded. run the 40-form by 13-family agent set only.

every model/reasoning configuration first runs the five probe forms through the two
appropriate probe dialects: text-only models use `probe_text`; multimodal models use
`probe_multimodal`. probe output estimates observed input, output, reasoning, image,
latency, failure, and cost per trial. it is labelled calibration and excluded from
benchmark profiles.

probe the provider default and highest supported reasoning setting. promote the
default; promote the high setting only when its projected declared run costs at most
`$1.00` and the cohort allocation remains. run intermediate levels only when the
projected complete contrast costs less than `$1.00` in total.

### hard spend safeguards

the openrouter ceiling is `$30.00`, matching the limited key in `~/dbench.env`:

- `$18.00` broad/default full-suite runs.
- `$7.50` frontier-core and reasoning contrasts.
- `$4.50` reserve for transport retries and one small diagnostic protocol run.

before any paid request, write a cost sheet from current catalog prices and observed
probe usage. the operator's existing “lets fucking go” approval is recorded with the
`$30.00` cap in the working manifest; scope increases still require a new approval.
the runner also requires `--approve-paid-run` and `--max-spend-usd 30`, so an
accidental plain `run` is a dry run.

keep an fsynced append-only spend ledger. before each request reserve the conservative
projected cost; after the response replace it with observed cost. abort before the
next request when observed plus reserved cost would exceed the global or cohort cap.
unknown pricing, missing usage, missing cost, provider fallback, or a resolved-model
mismatch stops the run. the openrouter key limit is defence in depth, not the runner's
budget mechanism. resume schedules only missing transport-failed trial ids.

capture complete provenance-ready cli candidates before the august 9 subscription
credit expiry only after suite verification, mock dry runs, and a clean committed
tree. admission may happen later after the normal gates.

## metrics and public claims

replace `compute_sensitivity` and `sensitivity.parquet` for v1 with a profile. do not
publish one scalar leaderboard.

- competence is dialect accuracy averaged within family and then equally across the
  13 families. also report text and spatial family-macro competence separately.
- within-family invariance is `1 -` mean pairwise prediction disagreement for the
  same form among dialects of an archetype/family with at least two observed arms.
- cross-family invariance is the same pairwise disagreement over declared plain arms,
  reported separately for text and spatial stimuli.
- controlled effects exist only for a plain/treatment pair under the same archetype.
  report signed paired difference, exact mcnemar result, coverage, and a seeded
  paired bootstrap interval over abstract forms. do not call a spatial-vs-text
  comparison a “drop.”
- every profile also reports raw per-dialect accuracy, invalid-output rate, coverage,
  latency, tokens, and cost. missing cells are `not run`, never zero.
- a run must be complete for its declared form/dialect set before aggregate admission.
  a valid malformed answer is a completed incorrect trial; transport failures remain
  missing until resumed.

tests must include a consistently wrong synthetic model with competence zero and
invariance one, a perfect model, a representation-sensitive model, missing dialects,
invalid responses, modality separation, unequal dialect counts per family, and a
controlled treatment that helps rather than hurts.

## public static gallery

replace the one-file gallery export with `python -m lofsite.build --release <dir>
--out dist/site`. it opens a sealed bundle and builds only static html, css, json, and
assets. keep the fasthtml server and arbitrary-form sandbox as local development
tools; github pages never depends on python or an api.

the site has two explanatory layers: plain lof-native explanation first, methods and
statistics one click deeper. generate these pages from the bundle:

- “what is tested”: one complete worked path from abstract containment tree through
  reference transcription, rendered stimulus, exact prompt, response, scorer, and
  profile contribution.
- “forms and protocols”: all suite counts/distributions, explicit set membership,
  exact prompts, response schemas, scorers, and known confounds.
- “dialect atlas”: every dialect's reading rule, family, archetype, injectors,
  provenance, limitations, and curated shallow/deep examples.
- “stimulus explorer”: all 11,600 cells, loaded in per-dialect chunks so the landing
  page stays small; spatial assets load only when selected. show exact model payload
  hash and symbolic hash.
- “models and runs”: separate direct-api and agent-cli tabs, competence/invariance
  profiles, dialect/family matrices, reasoning contrasts, coverage, costs, latency,
  exact model/provider provenance, and explicit `not run` cells.
- “downloads and citation”: sealed bundle, suite/protocol files, licences, checksums,
  citation metadata, caveats, and the pilot-v0 clean-break statement.

export a small set of redacted admitted `.eval` logs with `inspect view bundle
--log-dir <redacted> --output-dir <site>/inspect`. scanning must reject api-key-like
strings, the actual key value, home-directory paths, request headers, and unapproved
metadata. if redaction cannot be proven, omit the inspect directory and say why; do
not block the authoritative tidy artifacts or invent a second viewer.

add github actions for core ci, visual/full-suite ci, static build/link/accessibility
checks, and github pages deployment from a sealed bundle. write `CNAME` for
`distinction.valeriekim.ca`; document the cloudflare cname and redirect needed for
`distinction.waler.ie` without committing credentials.

## human-pilot interface

publish a 12-stimulus static instrument balanced over the four protocols and a
`HumanTrialRecord` schema with anonymous participant code, familiarity band, form,
dialect, protocol, answer/transcription, confidence, and elapsed time. collect no
name, email, ip address, or free text. the page exports local json; it does not post
responses. any later publication includes the instrument and deidentified rows and
calls the result a pilot, not a human benchmark.

## test and acceptance gates

no paid or public stage advances until the preceding gate passes.

1. `uv sync --group dev`, `uv run pytest` for core, and `uv run ruff check .` pass on
   a machine without native cairo. a separate visual job passes with cairo installed.
2. generator property tests over many seeds enforce syntax, exact depth bounds, mark
   bounds, determinism, uniqueness, and requested normal-value quotas.
3. the committed suite has exactly 400 forms, 29 dialects, 11,600 cells, 13 families,
   correct explicit subset counts, no collision, and deterministic content/model
   payload hashes. full re-verification passes on the pinned visual toolchain.
4. the suite task under inspect's mock model consumes only frozen ids, reproduces
   every selected payload hash, stamps exact provenance, scores strict json, and does
   not construct a model when the suite gate fails.
5. runner tests with the in-memory executor cover dry-run default, typed approval,
   endpoint pinning, no fallback, secret non-disclosure, conservative reservation,
   global/cohort exhaustion, unknown prices/usage, three transport retries, invalid
   answer handling, crash-safe resume, duplicate prevention, and reconciliation.
6. bundle tests reject pilot rows, dirty/uncommitted code, incomplete declared runs,
   duplicate trial ids, resolved-model drift, unlisted files, checksum drift, secrets,
   and schema mismatches. rebuilding from identical admitted inputs is byte-stable
   apart from declared timestamps.
7. metric fixtures prove the competence/invariance distinctions and family/modality
   balancing. every displayed number recomputes from bundle trials.
8. the static build has no runtime request dependency, every internal link works,
   every dialect and cell is reachable, image assets lazy-load, mobile and keyboard
   navigation work, and no pilot-v0 number appears in a v1 chart.
9. after probes, reconcile provider usage/cost against the local ledger before any
   promotion. after full runs, expected, completed, failed, and admitted trial counts
   sum exactly and the observed openrouter spend is at most `$30.00`.
10. deployment is complete only when the github pages artifact checksum matches the
    sealed bundle and `https://distinction.valeriekim.ca` serves it. the waler.ie
    redirect is a separate human-access gate if cloudflare is unavailable.

## staged commits

keep generated results out of implementation commits and stop after any failed gate:

1. `fix(core): make imports cairo-optional and enforce generator bounds`
2. `feat(suite): define domain language and freeze balanced suite v1`
3. `feat(release): add protocol specs and sealed release-bundle interface`
4. `feat(eval): make inspect suite-driven and add guarded run orchestration`
5. `feat(metrics): replace sensitivity scalar with competence/invariance profiles`
6. `feat(site): build the static dialect atlas and stimulus explorer`
7. `chore(release): add licences, ci, pages workflow, and updated release skill`
8. `data(v1): admit capped model runs and seal release v1.0.0`
9. `deploy(v1): publish the sealed static site and record verification`

implementation commits 1-7 must be independently reviewable and green without paid
calls. commit 8 contains only admitted, provenance-complete data and manifests; raw
working logs stay outside git and the sealed downloadable bundle is attached as a
release artifact if it is too large for the repository.

## first reporting milestone: sample release

after commits 1-7 and every prerequisite gate pass, build and seal
`v1.0.0-sample.1` before beginning the broad sweep. the sample contains the full
frozen suite and protocol registry but exactly four admitted runs and 20 paid calls:
one five-form probe run for each of `reduce-infer-v1`, `reduce-taught-v1`,
`transcribe-infer-v1`, and `transcribe-taught-v1`, all using the same five frozen
probe forms and one nontrivial dialect/model configuration. prefer the cheapest
qualifying multimodal openrouter endpoint with `enclosure.plain-v1`; if no exact
endpoint satisfies the declared privacy and routing gates, select the cheapest
qualifying candidate and record the reason. label and tag this artifact only as the
sample release, never as the completed public benchmark. retain the typed approval,
endpoint pinning, provenance, ledger, retry, completeness, admission, secret scan,
and `$30.00` global safeguards above. after reporting the sealed sample, stop rather
than beginning the broad sweep.

## Reset 2026-08-08 by agent:codex-root

## canonical migration residual rereview — ev_01KZJ28A489CWBTWVRMXYXSS4M

the second canonical-migration review approved the identity axis but found three
residual durability/serialization gaps. route every migration rename through one
durable helper that fsyncs every distinct source and destination parent directory;
this includes predecessor state moving into its nested recovery directory, candidate
state replacing the root, and rollback restoring the predecessor. tests spy the exact
parents and exercise recovery after the rename boundary.

make `plan` a peer lifecycle writer. it must acquire the same stable root lock before
checking whether release/state paths exist and retain it through catalog selection,
release construction, every planned run manifest, and the cost sheet. while migration
is paused under this lock, both a stale runner and a concurrent plan must receive the
same already-running result without touching their output paths.

committed recovery cannot trust only journal state/release digests. before deleting a
committed journal or returning success, reverify the separately persisted migration
audit: its schema and digest, exact live-event authority, predecessor/candidate
authorities and hashes, all four run mappings, all twenty trial mappings, retained
request/evidence/call hashes, scored first trial, exact settlement, one retained
attempt, and nineteen remaining. corruption fails closed while retaining journal and
backup. keep the live release untouched through independent rereview.

## canonical migration semantic rereview — ev_01KZJ3N1AZD10JW6435DKG5KFT

the third review found that committed recovery still treated the twenty trial ids as
unordered sets and checked only selected retained-attempt fields. reconstruct each
old/new run pair by protocol, independently derive its ordered expected trial ids from
the frozen sample task, zip those authoritative sequences, and require the persisted
run and trial maps to equal the resulting dictionaries exactly.

move provider-evidence closure into ordinary orchestration as one pure derivation of
the canonical call, scored trial, reserve/settle events, and operational run aggregate.
normal reconciliation, staged migration construction, and committed recovery must all
reuse it. recovery rekeys the unchanged predecessor evidence chain, projects both
revisions with the current projector, and compares the complete request, response,
score/correctness, hashes, token counts, latency, cost, error, ids, ledger pair, and
aggregate. tests recompute every outer digest around both an inactive positional-map
swap and a coordinated forged attempt graph; both must still fail closed.

## canonical migration authority rereview — ev_01KZJ5K7GT5S9JWY6NFPZERW9Y

the fourth review found that even exact positional maps are meaningless if recovery
lets candidate registry copies define their own order before proving their authority.
committed recovery must load both predecessor and candidate `AuthorityManifest`
objects, bind their canonical digests through both journal and audit, and call the
existing `verify_authority_copies` for each before parsing suite/protocol content. pass
the repository when available so both manifests are also proved against their exact
recorded git trees.

after that boundary, rederive every predecessor and candidate `RunAuthority` from the
verified suite cells and form order, verified protocol bytes, catalog row/retrieval,
execution spec, and source provenance. only those rederived authorities may supply run
and trial identities for the exact map check. an adversarial committed-recovery test
rewrites suite order, protocol content, candidate runs, maps, manifest/audit authority
digests, and journal outer digests coherently; git-backed authority-copy verification
must reject it before registry loading or run parsing.

## canonical migration review repair — ev_01KZHZR7TDT9ZE1NHVPWZMRXC2

the first canonical-migration review correctly blocked live mutation on three
authority gaps. replace the private migration lock with one stable state-root
lifecycle flock shared by `run`, `resume`, and `probe`; application commands hold it
from preflight (including salvage catalog selection) through reconciliation/provider
execution and final manifests. retain run and ledger locks inside the fixed
lifecycle → run → ledger order.

make the state replacement a predecessor-first journaled transaction. before any
rename, fsync the exact predecessor release bytes and completed migration audit into
the recovery directory, then fsync each initializing, prepared, predecessor-moved,
candidate-installed, release-installed, and committed phase. next invocation must
idempotently roll back every noncommitted phase to the verified predecessor, finalize
only a verified committed phase, or retain the journal and report an explicit
unrecoverable phase. simulate process death with `baseexception` after every durable
write/rename boundary, not merely caught exceptions.

bind the migration to the one tracked live mismatch event
`ev_01KZHVJWTQAXJEACS9BTVN34Z9`: verify its unique id, task, actor, type, exact body,
and body sha256 from the current git authority. arbitrary ids and tampered tracked
events fail before staging. concurrency tests pause migration under the lifecycle
lock, prove a stale-id runner receives already-running, then prove the next dry runner
observes only migrated ids. keep the real release untouched until independent review.

## live identity repair — ev_01KZHVJWTQAXJEACS9BTVN34Z9

the first paid sample call proved that openrouter exposes two intentional model
identities: the public requested alias in the chat response and the immutable
provider permaslug in generation accounting. planning must authenticate
`/api/v1/models/user`, join exactly one row whose `id` equals the requested alias,
require its nonempty `canonical_slug`, retain that complete row plus exact retrieval
evidence and digest in the catalog authority, and use the slug as
`resolved_model_id`. endpoint and zdr joins remain exact on the requested alias,
endpoint tag, and provider.

the openrouter evidence projector will validate the chat response model against
`requested_model_id`, the generation model against `resolved_model_id`, and exact
provider/endpoint identity independently. it will not require alias and permaslug to
equal one another. absent or contradictory catalog/user/generation evidence remains
fail-closed.

add an explicit, one-use `salvage-working-model-identity` application command. it is
not part of normal run validation. under a migration lock it must prove the release
is the exact unsealed `v1.0.0-sample.1` predecessor shape, all four old planned runs
share the same alias/catalog/endpoint and are unadmitted, exactly one run contains
one request, two linked evidence revisions, two call revisions for one effective call,
and one unsettled reservation, and the other runs contain no execution records. it must also
prove the retained raw chat alias, generation canonical permaslug, provider,
endpoint, tokens, completion, and exact cost without changing any provider source.

the migration constructs fresh current-authority runs from the frozen release
suite/protocol copies and an authenticated endpoint selection, rekeys every derived
run/trial/call/request/ledger identity, re-captures the unchanged provider envelopes
into a fresh digest chain, reprojects the final evidence, settles exactly the
observed cost, scores the first trial, and updates all four expected/approved run ids.
it stages a complete replacement state and release metadata set, validates it before
publication, records old/new ids and digests in a durable migration audit record,
and commits by recoverable directory swaps without deleting the predecessor backup.
the migrated first run has one complete trial and one attempt; the other three remain
planned, leaving nineteen provider calls in the frozen twenty-attempt contract.

tests cover alias/permaslug selection and projection, missing/duplicate/mismatched
authenticated rows, exact four-run migration, raw-evidence preservation, atomic
failure rollback, exact ledger settlement, nineteen remaining calls, normal current
validation, and rejection of forged or non-predecessor state. update the adr,
domain/release skill vocabulary, run focused/full/ruff/clean gates with no external
model calls, commit, and hand off for independent review.

## Review Cycle 7 Findings — idempotent evidence reconciliation

the seventh review found two remaining trust gaps and one publication leak. provider
errors must still close billed generation accounting: derive request identity, exact
token usage, cost, and latency from generation evidence before returning
`provider_error`; without that proof return `accounting_unknown`, never an invented
zero. make every envelope projection total over malformed timing and numeric values.

replace forward-only orchestration with one reconcile-before-act state machine. on
every start, replay evidence revisions, effective calls, ledger events, trials, and
run state; derive and fsync any missing call, settlement, or trial from already
persisted evidence before scheduling inference. deterministic call ids are the
exactly-once inference keys. fault-inject after reserve/evidence/call/settlement/trial
and run-state writes and require resume to converge without executing the same call
twice.

retain only an explicit audit-safe response-header allowlist and reject sensitive or
unknown header names/values during publication scanning. update adr 0001, preserve
the neutral dependency direction and prior forgery gates, run focused/full/frozen
wheel gates without inference, commit, and return db-14 to review tied to
`ev_01KZHKZ0S8T9XFBA4DYN8NK178`.

## Review Cycle 8 Findings — durable request intent and quarantine

the eighth review narrows the remaining crash ambiguity. add a typed, fsynced
`RequestStartedRecord` immediately before the executor boundary. its deterministic
request hash binds the call/run/trial/attempt and frozen prompt/payload identities.
reconciliation may execute a reserved call only when no request marker exists. a
request marker without attempt evidence is permanently ambiguous: preserve the
reservation, mark the run ambiguous, and refuse every inference resend.

fault-inject both sides of the remote boundary: after the request marker but before
the executor, and after the executor returns but before evidence persistence. resume
must make zero executor calls for the ambiguous call, while every later durable
append-boundary convergence test stays green. admit and seal must require exact
request-marker/call closure, so ambiguous state cannot enter a release and the exact
sample cannot exceed twenty posts.

replace name-only response-header filtering with one strict parser: retain only
required audit names, require bounded visible-ascii token values for request ids and
a small known content-type grammar, and reject controls, whitespace, separators, or
credential/cookie/key material case-insensitively. use the same validator in capture,
evidence replay, and publication scanning. update adr 0001, run focused/full/clean
wheel gates without inference, commit, and return db-14 to review tied to
`ev_01KZHQ09SFRXEYKPVMFE8AHE8N`.

## Review Cycle 9 Findings — durable single-writer execution

the ninth review closes the remaining local crash and concurrency mechanics. jsonl
append must retry interrupted and short writes, fsync the complete file, and fsync
its parent after first creation. atomic manifests must fsync their payload and parent
after rename. lock-file creation follows the same durable ordering.

one nonblocking advisory linux lock per run spans all reconciliation, request marking,
provider execution, evidence, accounting, scoring, and the final run manifest. a
second executor receives an explicit `already_running` result and cannot post or read
partial append state. different runs retain independent execution locks but cross the
same release-wide exclusive ledger lock for every reserve and settle.

prove the interface with fsync spies, real subprocess creation/append, a blocking fake
executor under two concurrent callers, exact one-marker/evidence/call/trial closure,
and a different-run global-cap race. preserve every prior crash/quarantine/forgery
gate, update adr 0001, run focused/full/clean gates without inference, commit, and
return db-14 to review tied to `ev_01KZHRWA2VW17EC9A5Y56FA3C4`.

## Review Cycle 5 Findings — canonical provider-evidence projection

the fifth review proved that `AttemptEvidence` still permits sibling typed assertions
to contradict its opaque raw transcript. replace that split trust path with one typed,
immutable provider-evidence envelope owned by the application adapter and one pure
projection used both to construct and to validate all attempt/call/trial fields.
retain the exact chat response and any generation-accounting lookup response with
deterministic source labels and timing; fail closed when required lookup evidence is
absent. derive completion, request/model/provider/endpoint identity, usage, cost,
latency, and error semantics solely from the projection, including trial errors.

also package the exact frozen suite registry beside the protocol registry, rename the
singular public `dialect_set` field to `dialect_id` before paid execution, and deepen
the `ReleaseBundle` publication interface so cli/site consumers do not interpret its
mutable internals or provider sample policy independently. update adr 0001 to describe
the implemented boundary. add adversarial contradiction, missing-generation,
installed-wheel, and dialect-identity regressions while preserving the prior forgery
matrix. run focused, full, clean-room, and wheel-install gates without model calls,
commit the result, and return db-14 to independent review tied to
`ev_01KZHD2KCS0N6MHCW1JHJJ90HA`.

## Review Cycle 6 Findings — total attempt persistence and accounting recovery

the sixth review found that provider evidence is canonical only on the happy path:
projection exceptions can escape before the attempt is persisted, and an unknown
generation lookup currently settles a possibly billed chat at zero. deepen the
provider-evidence seam so the provider-neutral envelope, response source, projection
status contract, and recovery revision live in `lofbench`, while openrouter parsing
and routing validation remain in `dbench`. inject the application projector into run
orchestration and release admission; generic core must not import or interpret
openrouter or the sample protocol catalog.

make openrouter execution retain every chat and generation http exchange, including
timestamps, status, headers, raw bytes/text, and json parse outcome for success,
non-2xx, malformed, timeout, error, and cancellation paths. its pure projector must
be total over retained envelopes and close provider/model/request identity,
finish/error semantics, usage, cost, provider/measured latency, and cross-source
equalities. only `complete` can score; `provider_error`, `transport_error`, and
`accounting_unknown` always persist evidence and a call first.

unknown accounting retains the conservative reservation instead of settling zero.
resume may perform bounded generation-get recovery using the retained request id but
must never resend inference; recovered raw exchanges revise the attempt evidence,
settle exact cost, and continue only after closure. unrecoverable accounting blocks
the run with the reservation outstanding. test this through the orchestration/release
interfaces with reviewer-specified malformed, non-2xx, error, cancelled,
finish-reason, token/cost/identity, latency, and recovery cases. then update public
docs/vocabulary, run focused/full/wheel/clean gates without inference, commit, and
return db-14 to review tied to `ev_01KZHGKGHRFYNJ97QT9NJ3ABME`.

## Review Cycle 4 Findings — sealed authority and evidence closure

the fourth adversarial review proved that the bundle still had two parallel trust
paths: mutable copied registries were treated as authorities, and optional transcript
rows were not members of the call/trial accounting graph. repair the boundary as one
deterministic projection:

1. add a checked-in protocol registry beside the checked-in suite registry and an
   `AuthorityManifest` which records their source commit, git blob ids, byte sha256
   digests, and canonical paths. create bundles from the recorded git tree, treat the
   bundle copies only as evidence, and verify both copies against that manifest (and
   against `git show <commit>:<path>` whenever the source repository is supplied).
2. derive a `RunAuthority` with canonical sha256 digests for the registry bytes, exact
   selected form records, exact selected cells, selected protocol, endpoint catalog
   evidence, and provider-neutral execution spec. include it in the sole run identity
   projection; reconstruct it during planning, admission, and sealed validation.
3. replace optional transcript dictionaries with one typed `AttemptEvidence` per
   `CallRecord`. its canonical digest covers response/completion, request id, model,
   provider, endpoint, usage, cost, latency, status, and a deterministic redacted raw
   projection. each call references this digest and each trial references its final
   completion evidence. require exact set equality and reject contradictions.
4. validate every numeric resource as finite and nonnegative, then recompute attempts,
   usage, cost, and latency from calls. retain release-wide ledger, metric, site,
   secret-scan, sample-contract, endpoint, scorer, and human-schema gates.
5. keep adversarial regressions for identity collision, registry mutation, missing or
   contradictory evidence, numeric forgeries, and the complete prior review matrix.

## Reset 2026-08-08 by agent:codex-root

## Reset 2026-08-08 by agent:codex-root

## Reset 2026-08-08 by agent:codex-root

## Reset 2026-08-08 by agent:codex-seal-boundary

## Reset 2026-08-08 by agent:codex-root

## Reset 2026-08-08 by agent:codex-root

## Reset 2026-08-08 by agent:codex-root

## Reset 2026-08-08 by agent:codex-root

## Reset 2026-08-08 by agent:codex-root

## Reset 2026-08-08 by agent:codex-root

## Reset 2026-08-09 by agent:codex-root

## canonical migration protocol-authority rereview — ev_01KZJ7FF2HAYDXY6AB2DT9MDN4

the fifth review found that proving bundled `protocols.json` was insufficient while
frozen-task construction and reconciliation could still consult the running source
tree's module-global protocol registry. parse `ProtocolSpec` values directly from the
exact bytes that passed predecessor/candidate authority-copy verification and thread
those values through every migration task, staged reconciliation, orchestrator, and
committed-recovery derivation. protocol id agreement is an explicit reconciliation
precondition; the runtime registry has no authority on this path.

an adversarial committed-recovery test replaces the runtime registry with divergent
prompts, answer kinds, targets, and scoring semantics while retaining the verified
bundle. recovery must still derive the valid retained trial solely from the bundle;
the existing coherent bundled-registry forgery remains rejected at the authority-copy
boundary. keep the live release and state untouched pending another review.

## Reset 2026-08-09 by agent:codex-root

## Reset 2026-08-09 by agent:codex-root

## Reset 2026-08-09 by agent:codex-root

## Reset 2026-08-09 by agent:codex-root
