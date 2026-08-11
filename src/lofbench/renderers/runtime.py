"""Capability checks for the optional deterministic raster runtime."""

from __future__ import annotations

import ctypes.util
import importlib
from collections.abc import Callable


def visual_runtime_available(
    *,
    find_library: Callable[[str], str | None] = ctypes.util.find_library,
    import_module: Callable[[str], object] = importlib.import_module,
) -> bool:
    """Return whether both the Python and native raster dependencies load."""

    if find_library("cairo") is None:
        return False
    try:
        import_module("cairosvg")
    except (ImportError, OSError):
        return False
    return True
