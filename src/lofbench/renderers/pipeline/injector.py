"""Layer two: named, parametrised transforms over a base render.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, "Core
abstractions", and the injector-applicability amendment in
``.lattice/notes/design-decisions-2026-07-04.md``.
"""

from __future__ import annotations

import random
from typing import Any, Protocol, runtime_checkable

from .archetype import BaseRender
from .nodes import FormNode


@runtime_checkable
class Injector(Protocol):
    """A transform that must preserve the induced containment relation.

    ``applicability`` is the 4 July amendment's admission set: archetype
    names or whole modality names (for example ``{"parens"}`` or
    ``{"text"}``) this injector is allowed to run against. ``modalities``
    stays as the doc's coarser text/spatial split. The spec validator that
    rejects an unsupported pairing at admission is M6 scope; M1 ships the
    data shape only, not the gate.
    """

    name: str
    version: str
    modalities: frozenset[str]
    applicability: frozenset[str]

    def apply(
        self, base: BaseRender, root: FormNode, rng: random.Random, **params: Any
    ) -> BaseRender: ...
