"""Minimal provenance stamping for the composed pipeline (M1 skeleton).

The full ``render_provenance`` schema -- ``suite_version``, ``form_id``,
the ordered injector chain with per-stage ``resample_count``, ``item_seed``,
``roundtrip_ok``, ``renderer_lib_version`` -- lands in M4. M1 stamps only
the fields the skeleton verify step actually produces: the two relation
hashes and the verified flag. That is still the load-bearing isomorphism
record the repository lacked entirely before this package existed.
"""

from __future__ import annotations

from typing import Any


def stamp_provenance(
    *,
    dialect_id: str,
    family: str,
    modality: str,
    fmt: str,
    input_relation_hash: str,
    render_relation_hash: str,
    structure_verified: bool,
) -> dict[str, Any]:
    return {
        "dialect_id": dialect_id,
        "family": family,
        "modality": modality,
        "format": fmt,
        "input_relation_hash": input_relation_hash,
        "render_relation_hash": render_relation_hash,
        "structure_verified": structure_verified,
    }
