"""Two-layer rendering pipeline: archetypes plus variation injectors.

Implements ``.lattice/notes/rendering-architecture-2026-07-04.md``. This
package is DB-4's milestones M1 (core abstractions) and M2 (registry
widening and spec format) only -- determinism/seed threading (M3),
per-stage verification and full provenance (M4), the five-renderer
migration (M5), applicability admission (M6), the frozen suite (M7), and
the task/analysis stamp (M8) land in later DB-4 work.
"""

from __future__ import annotations

from .archetype import Archetype, BaseRender, Primitive, induced_relation
from .composed import ComposedRenderer
from .emit import Emission, emit
from .injector import Injector
from .nodes import (
    FormNode,
    NodeId,
    containment_relation,
    form_to_nodes,
    iter_node_ids,
    nodes_to_form,
    relation_hash,
)
from .registry import ARCHETYPE_REGISTRY, INJECTOR_REGISTRY
from .spec import DIALECT_SPECS, DialectSpec, make_named_dialect_factory, register_named_dialect
from .verify import verify

__all__ = [
    "Archetype",
    "BaseRender",
    "Primitive",
    "induced_relation",
    "ComposedRenderer",
    "Emission",
    "emit",
    "Injector",
    "FormNode",
    "NodeId",
    "containment_relation",
    "form_to_nodes",
    "iter_node_ids",
    "nodes_to_form",
    "relation_hash",
    "ARCHETYPE_REGISTRY",
    "INJECTOR_REGISTRY",
    "DialectSpec",
    "DIALECT_SPECS",
    "make_named_dialect_factory",
    "register_named_dialect",
    "verify",
]
