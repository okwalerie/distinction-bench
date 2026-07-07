"""Archetype and injector registries for the composed rendering pipeline.

Plain dicts, no metaclass, mirroring the existing renderer registry in
``lofbench.renderers``. Archetype and injector modules populate these
directly (see the architecture doc's worked example 4); DB-2 and DB-3 add
entries here without touching anything else in the pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .archetype import Archetype
    from .injector import Injector

ARCHETYPE_REGISTRY: dict[str, Archetype] = {}
INJECTOR_REGISTRY: dict[str, Injector] = {}
