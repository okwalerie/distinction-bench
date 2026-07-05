# lofsite

The distinction-bench public site (Lattice task DB-6). A small FastHTML/HTMX
app that imports `lofbench` directly -- the renderers are one source of
truth between the eval harness and this site.

## Scope

- **Sandbox** (`/`): type a form, see it rendered server-side through every
  renderer in `lofbench.renderers.list_renderers()` in one HTTP round trip.
  Text dialects render inline; image dialects (`circle` today; any spatial
  archetype registered later) embed as a data URI. A dialect that cannot be
  zero-arg constructed, or whose `.render()` raises `NotImplementedError`
  (a spatial archetype registered ahead of its rasteriser landing -- see
  `lofsite/rendering.py`), is skipped and named honestly rather than
  crashing the page.
- **Axioms** (`/axioms`): I1 (calling) / I2 (crossing) explainer, using the
  CMY colour-cue pedagogy from `demo/13--visual-transforms--colour.png`
  (copied to `static/axioms-cmy.png`).
- **Charts / Matrix / Walkthroughs** (`/charts`, `/matrix`,
  `/walkthroughs`): baked fresh per request from `lofbench.pipeline`'s
  artifacts (`items.parquet`, `calls.parquet`, `sensitivity.parquet`,
  `transcripts.jsonl`) -- see `lofsite/data.py` for the path convention
  (`LOFSITE_DATA_DIR`/`LOFSITE_SUITE_VERSION` env vars) and schema_version
  check. When an artifact is absent, or present but stamped with a
  schema_version this site wasn't written against, the page degrades to a
  placeholder instead of crashing or rendering stale/wrong columns.

  **Every one of these pages is prominently labelled pilot data.** The only
  real artifact today is baked from 47 pre-DB-4 pilot-v0 logs, never the
  frozen suite v1 -- see the design-decisions note's "suite v1 is a clean
  break from all previous findings" rule. Do not compare these numbers
  against a future suite v1 run.

Out of scope: the links-out/writeups page, and deployment itself (see
"Deployment" below).

## Generating the data artifacts

Charts read from whatever `lofbench.pipeline` last wrote. To bake from the
real logs:

```bash
uv run python -m lofbench.pipeline logs/ data/site-artifacts --suite-version pilot-v0
```

This writes `data/site-artifacts/pilot-v0/{items,calls,sensitivity}.parquet`
and `transcripts.jsonl` -- `lofsite`'s default `LOFSITE_DATA_DIR`
(`data/site-artifacts`) and `LOFSITE_SUITE_VERSION` (`pilot-v0`) already
point here. `data/` is gitignored -- these are generated artifacts, not
checked-in data. Override either env var to point at a different re-bake
(e.g. a future frozen suite v1 run) without a site code change.

## Boot from a clean checkout (one command)

```bash
uv sync --extra site && uv run python -m lofsite.app
```

This starts the app on `http://0.0.0.0:5001` (override with the `PORT` env
var, and `HOST` to bind elsewhere). There is no dev auto-reload -- restart
the process to pick up code changes.

## Running tests / lint for this package

```bash
uv run pytest tests/test_lofsite.py -q
uv run --extra dev --extra site ruff check src/lofsite tests/test_lofsite.py
```

(`ruff` lives in the `dev` extra, not the default dependency group, so pass
`--extra dev` explicitly if you haven't already synced it.)

## Validation guard

`lofbench.core.string_to_form` silently drops non-paren characters and never
raises on unbalanced input -- it cannot be trusted with public form-field
text. `lofsite/validation.py` is the guard in front of it: character
whitelist, balance check, a 200-character size cap, a depth-20 nesting cap,
then a round-trip through `string_to_form`/`form_to_string` so every
renderer receives a canonical, whitespace-free form string. See
`tests/test_lofsite.py::TestValidateFormInput` for the cases it rejects.

## Deployment (out of scope for this task, example only)

`deploy/lofsite.service` is an example systemd unit for running this under
`uv run` on a single-operator server. It is illustrative, not a deployment
step -- adjust paths, user, and environment for the actual host before use.

Charts bake fresh from the parquet/jsonl artifacts on every page request
(no separate build step, no process-start cache) -- so a "re-bake" after a
new `lofbench.pipeline` run is just re-running the pipeline command into the
directory `LOFSITE_DATA_DIR`/`LOFSITE_SUITE_VERSION` already point at; no
site restart is required for the new data to show up on the next request.
Deployment itself (provisioning the host, running the systemd unit) is
Valerie's manual step, out of scope here.
