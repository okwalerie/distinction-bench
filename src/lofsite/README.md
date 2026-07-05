# lofsite

The distinction-bench public site (Lattice task DB-6, phase 1). A small
FastHTML/HTMX app that imports `lofbench` directly -- the renderers are one
source of truth between the eval harness and this site.

## Phase 1 scope

- **Sandbox** (`/`): type a form, see it rendered server-side through every
  renderer in `lofbench.renderers.list_renderers()` in one HTTP round trip.
  Text dialects render inline; the `circle` dialect's SVG is embedded as a
  data URI.
- **Axioms** (`/axioms`): I1 (calling) / I2 (crossing) explainer, using the
  CMY colour-cue pedagogy from `demo/13--visual-transforms--colour.png`
  (copied to `static/axioms-cmy.png`).
- **Charts / Matrix / Walkthroughs** (`/charts`, `/matrix`,
  `/walkthroughs`): placeholders. These depend on DB-5's parquet artifact,
  which does not exist yet -- each page checks for it and shows "Awaiting
  suite v1 data" instead of crashing. See `lofsite/data.py` for the expected
  artifact paths (`data/suite_v1/items.parquet`,
  `data/suite_v1/headline.parquet` by default, overridable via the
  `LOFSITE_DATA_DIR` env var). This path/schema convention is provisional
  pending the DB-5/DB-6 contract, not a claim about DB-5's final layout.

Out of phase-1 scope: actually baking charts from a DB-5 artifact once one
exists, and the links-out/writeups page.

## Boot from a clean checkout (one command)

```bash
uv sync --extra site && uv run python -m lofsite.app
```

This starts the app on `http://0.0.0.0:5001` (override with the `PORT` env
var, and `HOST` to bind elsewhere). There is no dev auto-reload in phase 1 --
restart the process to pick up code changes.

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
