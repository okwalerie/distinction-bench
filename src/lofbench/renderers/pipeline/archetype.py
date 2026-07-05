"""Layer one: turning parsed structure into a base render.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, "Core
abstractions".
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from .nodes import FormNode, NodeId, containment_relation, form_to_nodes

Modality = Literal["text", "spatial"]


@dataclass(frozen=True)
class Primitive:
    """One rendered shape or token, structurally labelled or a decoration.

    ``node_id`` is ``None`` for a decorative distractor that carries no
    structure -- it can never affect an induced containment relation by
    construction. ``geom`` holds structural numerics only; ``style`` is
    cosmetic and must never be read by a predicate.

    ``tags`` is the ECS-flavoured affordance from the 4 July
    design-decisions amendment: advisory in v1 and unused by any admission
    check here, kept so a later release can key injector applicability
    structurally (by component) instead of nominally (by archetype name)
    without a data-model rewrite.
    """

    node_id: NodeId | None
    kind: str
    geom: dict[str, Any]
    children: tuple[Primitive, ...] = ()
    style: dict[str, Any] = field(default_factory=dict)
    tags: frozenset[str] = frozenset()


@dataclass
class BaseRender:
    """The archetype's output: a payload plus the node-to-primitive map.

    ``node_map`` must contain every real node id from the parsed tree
    exactly once -- verification cannot be trusted against a lazily or
    wrongly built map. Decorative primitives (``node_id is None``) are not
    required to be reachable through ``node_map``.
    """

    modality: Modality
    payload: Any
    node_map: dict[NodeId, Primitive]


@runtime_checkable
class Archetype(Protocol):
    name: str
    version: str
    family: str
    modality: Modality

    def build(self, root: FormNode, rng: random.Random) -> BaseRender: ...

    def predicate(self, parent: Primitive, child: Primitive) -> bool: ...


def induced_relation(
    base: BaseRender,
    pred: Any,
    *,
    text_reader: Any = None,
) -> frozenset[tuple[NodeId, NodeId]]:
    """Observe the containment a render actually encodes.

    Spatial: fold ``pred`` over every labelled primitive pair. A predicate
    such as the enclosure family's circle-in-circle check is only ever
    asserted on direct pairs in the design doc's prose, but folding it over
    *all* pairs recovers the full transitive closure directly with no
    separate transitive step, as long as the predicate itself is
    transitive-safe -- true of a geometric "wholly inside" test.

    Text: parse the emitted payload back into a node tree and read off its
    ``containment_relation`` -- the "parse-back fold" from the doc. ``pred``
    is unused for text. ``text_reader`` defaults to the canonical parens
    reader (``nodes.form_to_nodes``), which is correct for the ``parens``
    archetype family. A notation-specific reader (sexpr and any other text
    archetype) is expected to pass its own reader here; wiring that in per
    archetype, plus recording ``roundtrip_ok``, is M4/M5 scope. M1 ships the
    mechanism and the canonical default.
    """
    if base.modality == "spatial":
        labelled = [(nid, prim) for nid, prim in base.node_map.items() if nid is not None]
        pairs: set[tuple[NodeId, NodeId]] = set()
        for parent_id, parent_prim in labelled:
            for child_id, child_prim in labelled:
                if parent_id == child_id:
                    continue
                if pred(parent_prim, child_prim):
                    pairs.add((parent_id, child_id))
        return frozenset(pairs)

    reader = text_reader or form_to_nodes
    reparsed_root = reader(str(base.payload))
    return containment_relation(reparsed_root)
