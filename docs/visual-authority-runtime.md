# Visual authority runtime

The frozen v1 PNG hashes are binary outputs of a complete renderer userspace,
not only of `cairosvg`. In particular, RNA-arc uses an SVG text glyph whose
raster bytes change across Cairo/Pango/font stacks. The authority must therefore
fail on environment drift rather than bless new hashes.

`.github/visual-runtime/Containerfile` reconstructs the authority on amd64 from:

- Debian 13.5 slim, amd64 manifest
  `sha256:a617c1cdde36a7e0194b2f07dff669e1753c03c3205356b94f9f350b0f9a57d1`;
- CPython 3.11.15 copied from the official amd64 manifest
  `sha256:78b39ef14d8e2b4d71f8dc304f1328c37df95fe0ef99477c2ae6bd3d03784553`;
- `uv` 0.11.26 copied from its official amd64 manifest
  `sha256:663211e7509e89ff2172cc3ca098afb4ac63028dd065d2b047e4251127a7d47a`;
- Debian package indexes frozen at `20260706T000000Z`, with the exact Cairo,
  Pango, Fontconfig, DejaVu, GDK Pixbuf, and libffi versions asserted by a
  checked-in preflight;
- the checked-in `uv.lock`, including `cairosvg==2.9.0`.

The local image that generated the sample authority,
`localhost/dbench-sample-runtime:2026-08-08`, was inspected directly. Both its
system interpreter and `/app/.venv` are Python 3.11.15. An earlier plan note said
3.13.5; that was host-version contamination and is not authoritative.

CI builds this container, runs `preflight.py` before the 11,600-cell rerun, and
then verifies the frozen registry without modifying it. The ordinary core job has
no visual extra and skips raster tests through the shared
`requires_visual_runtime` capability gate.
