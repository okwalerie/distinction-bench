"""Content-addressed seeding for the composed pipeline.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, "Determinism
and seed threading" (DB-4 M3). The order-dependence bug this closes lives in
``lofbench.datasets.factory``, which shares one ``random.Random`` across a
whole batch; ``ComposedRenderer.render`` derives its own seed here and never
depends on draw order.
"""

from __future__ import annotations

from hashlib import blake2b

from .spec import DialectSpec

SEED_BYTES = 8


def _keyed_seed(seed_value: int, key: str, index: int) -> int:
    """hash(seed_value || key || index): the one keying primitive both
    per-injector substreams and resample retries derive from.
    """
    material = (
        seed_value.to_bytes(SEED_BYTES, "big")
        + b"\x00"
        + key.encode("utf-8")
        + b"\x00"
        + index.to_bytes(4, "big")
    )
    digest = blake2b(material, digest_size=SEED_BYTES).digest()
    return int.from_bytes(digest, "big")


def item_seed(spec: DialectSpec, form_string: str, render_seed_fold: int | None = None) -> int:
    """Canonical digest over the whole spec plus the form string.

    Content-addressed: the same spec and form give the same seed regardless
    of where the item sits in a case list, closing the real order-dependence
    bug in ``factory.py``. ``render_seed_fold``, when set, folds the
    task-level ``render_seed`` knob in as an extra field so it still
    perturbs draws for composed dialects; a frozen run leaves it unset, so
    freezing is unaffected. See the doc's worked derivation.
    """
    material = spec.seed_digest() + b"\x00" + form_string.encode("utf-8")
    if render_seed_fold is not None:
        material += b"\x00" + render_seed_fold.to_bytes(SEED_BYTES, "big", signed=False)
    digest = blake2b(material, digest_size=SEED_BYTES).digest()
    return int.from_bytes(digest, "big")


def injector_substream_seed(item_seed_value: int, injector_name: str, occurrence_index: int) -> int:
    """hash(item_seed || injector_name || occurrence_index_among_same_name).

    Reordering two distinct injectors leaves each one's key -- hence draws --
    unchanged. Two instances of the same injector get different occurrence
    indices, so they decorrelate instead of drawing identically; swapping
    the order of two same-name instances swaps their draws, which is
    harmless because the transform is identical.
    """
    return _keyed_seed(item_seed_value, injector_name, occurrence_index)


def resample_substream_seed(sub_seed: int, attempt: int) -> int:
    """Derive a fresh, deterministic substream for resample attempt ``N`` of
    an injector whose first try failed verification. ``attempt`` starts at 1
    -- attempt 0 is the injector's ordinary substream from
    ``injector_substream_seed``, not a resample.
    """
    return _keyed_seed(sub_seed, "resample", attempt)
