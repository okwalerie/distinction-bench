"""Terminal step: turn a base render into the payload the task layer understands."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .archetype import BaseRender

# Pinned rasteriser (M5): providers reject the `image/svg+xml` mime type, so
# spatial dialects rasterise to PNG. Version pinned in pyproject.toml
# (`cairosvg==2.9.0`); stamped into provenance as `renderer_lib_version` by
# ``ComposedRenderer.render`` (see ``pipeline.composed``).
try:
    CAIROSVG_VERSION: str | None = version("cairosvg")
except PackageNotFoundError:
    CAIROSVG_VERSION = None


@dataclass
class Emission:
    payload: str
    is_image: bool
    # The pre-rasterisation symbolic source: the emitted string itself for
    # text (``payload`` and ``symbolic_source`` are equal), the symbolic SVG
    # scene for spatial (never the rasterised PNG bytes). This is what a
    # payload hash must be computed over -- "hash of the symbolic SVG scene,
    # not pixels" -- so ``ComposedRenderer.render`` hashes this field
    # uniformly across both modalities rather than special-casing spatial.
    symbolic_source: str


def emit(
    base: BaseRender,
    style: dict[str, Any],
    *,
    to_svg: Callable[[BaseRender], str] | None = None,
) -> Emission:
    """Text: serialise the payload. Spatial: build symbolic SVG via
    ``to_svg``, then rasterise to a PNG data URI through the pinned
    ``cairosvg`` dependency.

    ``to_svg`` is the spatial archetype's own ``BaseRender -> str`` scene
    builder -- duck-typed and forwarded by the caller (mirrors the
    ``text_reader`` convention on ``Archetype``), since different spatial
    families draw different primitive kinds at different scales and there
    is no one generic way to turn an arbitrary ``Primitive`` tree into SVG.
    A spatial ``BaseRender`` reaching this function with no ``to_svg``
    supplied is a programming error, not a silent no-op, so this raises
    rather than emitting nothing.
    """
    if base.modality == "text":
        payload = str(base.payload)
        return Emission(payload=payload, is_image=False, symbolic_source=payload)

    if to_svg is None:
        raise NotImplementedError(
            f"spatial BaseRender (kind={type(base.payload).__name__}) has no to_svg "
            "hook to build a symbolic SVG scene from"
        )

    try:
        import cairosvg
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "spatial rendering requires the 'visual' extra and native cairo libraries"
        ) from exc

    svg = to_svg(base)
    png_bytes = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
    data_uri = f"data:image/png;base64,{base64.b64encode(png_bytes).decode('ascii')}"
    return Emission(payload=data_uri, is_image=True, symbolic_source=svg)
