# Deploying lofsite to waler (DB-13)

The distinction-bench site runs on Valerie's server `waler`
(`core@waler`, Fedora CoreOS / uCore, rootless podman, behind Tailscale) as a
podman **Quadlet** systemd *user* unit, with the pilot-v0 data artifacts baked
into the image. It is reachable on the tailnet at:

> **https://waler.fawn-augmented.ts.net:8094/**

TLS is terminated by `tailscale serve`; the container serves plain HTTP on
loopback only (`127.0.0.1:8094`), matching the existing `explorer-gallery` /
`walerie-gallery` pattern on the box.

## Files (this directory)

| File | Role |
|------|------|
| `Containerfile` | Builds the image: `python:3.11-slim` + cairosvg native libs + `uv sync --extra site` + packaged frozen registries. |
| `.containerignore` | Keeps `logs/`, `.git`, `.venv`, caches out of the build context. Passed explicitly (`--ignorefile`) because the context is the repo root. |
| `lofsite.container` | The Quadlet unit. Installs to `~/.config/containers/systemd/lofsite.container`. |
| `lofsite.service` | Legacy illustrative non-quadlet unit from DB-6 phase 1. Superseded by `lofsite.container`; kept for reference only. |

## Contract the image depends on

- App binds `0.0.0.0:5001` in-container (`HOST`/`PORT` envs). Quadlet maps
  `127.0.0.1:8094 -> 5001`.
- `LOFSITE_DATA_DIR=/data`, `LOFSITE_SUITE_VERSION=pilot-v0`. Artifacts live at
  `/data/pilot-v0/{items,calls,sensitivity}.parquet` + `transcripts.jsonl`.
- `lofsite.data` imports `lofbench.pipeline` (→ `inspect_ai`, pandas, pyarrow)
  and `lofsite.rendering` imports `lofbench.renderers` (→ cairosvg). So the full
  main deps are required, not just the `site` extra, and cairosvg's native libs
  (`libcairo2`, `libpango*`, `libgdk-pixbuf`, `libffi8`) must be in the image.
- `/gallery` reads the installed `lofbench/registries/suites-v1.json` package data.

## Redeploy (two commands)

Run from the repo root on a machine with `podman` and ssh access to `core@waler`:

```bash
# 1. Bake fresh artifacts + build + ship the image to the server.
uv run python -m lofbench.pipeline logs/ /tmp/deploy-artifacts --suite-version pilot-v0 && \
podman build -f src/lofsite/deploy/Containerfile \
  --ignorefile src/lofsite/deploy/.containerignore \
  --build-context artifacts=/tmp/deploy-artifacts \
  -t localhost/lofsite:latest . && \
podman save localhost/lofsite:latest | ssh core@waler /usr/bin/podman load

# 2. Install the unit (idempotent) and (re)start the service on the server.
scp src/lofsite/deploy/lofsite.container core@waler:~/.config/containers/systemd/lofsite.container && \
ssh core@waler 'systemctl --user daemon-reload && systemctl --user restart lofsite.service'
```

First-time-only ingress (already done on waler; additive, safe to re-run):

```bash
ssh core@waler 'tailscale serve --bg --https=8094 http://127.0.0.1:8094'
```

## Verify (from any tailnet device)

```bash
for r in / /gallery /charts /matrix /walkthroughs /axioms; do
  curl -s -o /dev/null -w "$r -> %{http_code}\n" "https://waler.fawn-augmented.ts.net:8094$r"
done
# expect all 200
```

## Notes / follow-ups

- Image is ~2 GB (full main deps incl. matplotlib + LLM SDKs, plus the ~348 MB
  `transcripts.jsonl`). Fine for a tailnet demo on a box with ~90 GB free.
- `/walkthroughs` loads the entire `transcripts.jsonl` into a DataFrame on every
  request (`lofsite.data.load_transcripts`), ~2-3 s per hit. Acceptable for a
  demo; a future task could sample/stream transcripts to slim both the image and
  the request path.
- Boot persistence: `core` has `loginctl` lingering enabled, and the unit has
  `[Install] WantedBy=default.target`, so it starts on reboot without a login.
