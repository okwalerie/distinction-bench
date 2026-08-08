---
name: bench-release
description: Plan, run, admit, validate, seal, and publish a distinction-bench release bundle. Triggers on "run a release", "new model dropped", "sample release", or "release run for distinction-bench".
---

# benchmark release

the release bundle is the publication boundary. the checked-in suite and
protocol registry are authorities; inspect logs and working state are inputs,
not published truth. never present a sample release as the final benchmark.

## hard preconditions

stop before any paid request unless all are true:

- `git status --short` is empty and the intended commit is checked out.
- `uv run ruff check .` and `uv run pytest -q` pass.
- the visual environment has cairo and
  `uv run python -m lofbench.suites --verify-only` passes for all frozen cells.
- the env file is mode `0600` or stricter. never print its values.
- the exact model and endpoint catalog row have known prompt and completion
  prices, structured-output support, and the input modality required by the
  selected dialect.
- routing pins exactly one endpoint with fallbacks disabled,
  `data_collection: deny`, and `zdr: true`.
- the operator has approved the written cost sheet and cap.

the runner is dry by default. paid execution requires both literal flags:
`--approve-paid-run --max-spend-usd 30`. omission or any other cap performs no
calls. unknown price, usage, endpoint, or resolved model stops the run.

## plan a release

run from a clean repository. put the mutable bundle and state outside git.
planning reads the public endpoint catalog but makes no model inference call.
it also materializes and hash-checks every frozen stimulus, so use the visual
environment.

```bash
uv run python -m lofbench.runner plan \
  --release /tmp/distinction-release \
  --state-root /tmp/distinction-state \
  --release-id <release-id> \
  --repository-url https://github.com/okwalerie/distinction-bench \
  --suite suites/v1.json \
  --model <exact-openrouter-model-id> \
  --form-set <frozen-form-set> \
  --dialect <exact-frozen-dialect-id> \
  --protocol reduce-infer-v1 \
  --protocol reduce-taught-v1 \
  --protocol transcribe-infer-v1 \
  --protocol transcribe-taught-v1
```

inspect `/tmp/distinction-state/cost-sheet.json`. check the exact catalog
timestamp, endpoint, provider, prices, run ids, trials, and conservative
reservations. ask for a typed approval if the operator has not already supplied
one for this exact scope. a prior generic budget is not approval for a changed
model, endpoint, suite, dialect, protocol set, or call count.

## sample.1 invariant

`v1.0.0-sample.1` is exactly four admitted direct-api runs over the same five
frozen `probe` forms and one nontrivial multimodal dialect/model configuration:

- `reduce-infer-v1`
- `reduce-taught-v1`
- `transcribe-infer-v1`
- `transcribe-taught-v1`

that is exactly twenty provider attempts. use `enclosure.plain-v1` and the
cheapest qualifying multimodal endpoint. if it cannot satisfy exact endpoint,
privacy, zdr, structured-output, and accounting gates, stop and re-plan with the
next-cheapest qualifying endpoint; record why. never enable a fallback.

for this sample use `--max-transport-attempts 1`. a failed attempt remains part
of the twenty-call budget; do not replace it with a twenty-first call. if any run
is incomplete, report the sample as blocked rather than changing the form set.

## run and resume

for each printed run id, execute its state directory:

```bash
uv run python -m lofbench.runner run \
  --release /tmp/distinction-release \
  --state-dir /tmp/distinction-state/<run-id> \
  --env-file /var/home/core/dbench.env \
  --approve-paid-run --max-spend-usd 30 \
  --max-transport-attempts 1
```

ordinary releases may use the default maximum of three transport attempts.
resume uses the same flags and schedules only missing trial ids:

```bash
uv run python -m lofbench.runner resume \
  --release /tmp/distinction-release \
  --state-dir /tmp/distinction-state/<run-id> \
  --env-file /var/home/core/dbench.env \
  --approve-paid-run --max-spend-usd 30
```

check a run without spending:

```bash
uv run python -m lofbench.runner status \
  --state-dir /tmp/distinction-state/<run-id>
```

## admit, derive, and inspect

admit only complete runs, one state directory at a time:

```bash
uv run python -m lofbench.runner admit \
  --release /tmp/distinction-release \
  --state-dir /tmp/distinction-state/<run-id>
```

admission checks the run's suite and protocol, expected trial ids, duplicate
ids, model identity, and completeness. after every expected run is admitted,
derive profiles/effects and build the bundle-only static gallery:

```bash
uv run python -m lofbench.runner prepare --release /tmp/distinction-release
```

inspect the generated `site/` and these invariants:

- competence and invariance are separate; consistent wrongness is not a win.
- text and spatial scores are distinct.
- direct-api and agent surfaces are separate tabs.
- every number comes from bundle artifacts.
- the human pilot has twelve local-only stimuli balanced across all four
  protocols, exports json locally, and posts nothing.
- the atlas exposes reading rules, provenance, limits, and every frozen cell.

## seal and export

sealing requires the same clean commit used at plan time, every expected run
admitted, a valid bundle schema, and a clean publication secret scan:

```bash
uv run python -m lofbench.runner seal --release /tmp/distinction-release
uv run python -m lofbench.runner export-inspect \
  --release /tmp/distinction-release \
  --out /tmp/distinction-inspect.zip
uv run python -m lofbench.runner archive \
  --release /tmp/distinction-release \
  --out /tmp/distinction-bench-<release-id>.tar.gz
```

re-open and validate the sealed directory before tagging or upload. never edit a
sealed directory; any checksum drift invalidates it. attach the tarball to the
matching github release. pages deployment downloads that exact asset, validates
it again, builds static output, and publishes the canonical cname.

## handoff

report exact run ids, model id, resolved model id, endpoint/provider, suite,
form/dialect sets, protocols, attempts, token use, observed cost, invalid-output
rate, competence, and bundle/archive checksums. include failures and exclusions.
for sample releases, say conspicuously that the result is a twenty-call protocol
smoke test, not the benchmark result or a model ranking.
