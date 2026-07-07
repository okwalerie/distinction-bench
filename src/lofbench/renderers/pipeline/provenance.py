"""``render_provenance`` stamping for the composed pipeline (M4).

Full schema per ``.lattice/notes/rendering-architecture-2026-07-04.md``,
"Provenance metadata schema" -- minus ``form_id``, which the doc's own
schema notes reaches ``RenderedForm.metadata`` from the task layer (M8,
``Task.metadata``/sample metadata stamping), not from ``ComposedRenderer``,
which only ever sees a bare form string, never an assigned id.

``payload_hash`` (M5-rest/M7 addition, not in the doc's original schema
listing but required by its "Frozen forms and payload hashing" section): a
blake2b of ``Emission.symbolic_source`` -- the emitted string for text, the
symbolic SVG scene for spatial, never rasterised pixels. Computed uniformly
at render time so the M7 suite freezer and its rerun gate can read it
straight from provenance rather than reconstructing it independently.
"""

from __future__ import annotations

from typing import Any


def stamp_provenance(
    *,
    suite_version: str,
    dialect_id: str,
    family: str,
    modality: str,
    fmt: str,
    archetype_name: str,
    archetype_version: str,
    injectors: list[dict[str, Any]],
    item_seed_value: int,
    input_relation_hash: str,
    render_relation_hash: str,
    structure_verified: bool,
    roundtrip_ok: bool | None,
    renderer_lib_version: str | None,
    payload_hash: str,
) -> dict[str, Any]:
    return {
        "suite_version": suite_version,
        "dialect_id": dialect_id,
        "family": family,
        "modality": modality,
        "format": fmt,
        "archetype": {"name": archetype_name, "version": archetype_version},
        "injectors": injectors,
        "item_seed": item_seed_value,
        "input_relation_hash": input_relation_hash,
        "render_relation_hash": render_relation_hash,
        "structure_verified": structure_verified,
        "roundtrip_ok": roundtrip_ok,
        "renderer_lib_version": renderer_lib_version,
        "payload_hash": payload_hash,
    }
