"""ComposedRenderer: the glue between a DialectSpec-driven pipeline and the
existing FormRenderer contract, so the task layer, scorers, and the ``-T``
contract do not change.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, "Core
abstractions", "Determinism and seed threading" (M3), and
"Structure-preservation verification" (M4).
"""

from __future__ import annotations

import random
from hashlib import blake2b
from typing import Any

from ..base import FormRenderer, RenderedForm
from .emit import CAIROSVG_VERSION, emit
from .injector import Injector
from .nodes import containment_relation, form_to_nodes, relation_hash
from .provenance import stamp_provenance
from .registry import ARCHETYPE_REGISTRY, INJECTOR_REGISTRY
from .seeding import injector_substream_seed, item_seed, resample_substream_seed
from .spec import DialectSpec
from .verify import verify

# Capped retries per injector on a verification violation (M4). Once
# exhausted, the injector is dropped (``applied: False``) and the base
# render reverts to its last-verified state -- a stage is never left
# silently non-isomorphic.
RESAMPLE_CAP = 5


class ComposedRenderer(FormRenderer):
    """Parse, build, verify, fold injectors (verify each), emit, stamp
    provenance.

    Seeding is content-addressed (M3): the item seed is a digest of the
    whole spec plus the form string, derived inside ``render`` itself, so
    whether ``factory.py`` shares a generator across a batch cannot affect
    it, and reordering or subsetting a case list changes nothing. The
    task-layer ``rng``, when given, contributes exactly one deterministic
    draw folded into the digest as an extra field, so the ``render_seed``
    knob still perturbs composed dialects; a frozen run passes ``rng=None``
    and the item seed depends on the spec and form alone.

    Verification (M4) runs after the archetype builds and after every
    injector. A violating injector is resampled (fresh, deterministic
    substream) up to ``RESAMPLE_CAP`` times; on exhaustion it is dropped and
    the render reverts to the last verified state, so ``structure_verified``
    in the stamped provenance is always true for a returned render.
    """

    def __init__(self, spec: DialectSpec | None = None, **kwargs: Any) -> None:
        # get_renderer splats renderer_config as kwargs, so accept both paths:
        # a named factory passes spec directly; the ad-hoc -T path passes a
        # flat spec dict rebuilt here via DialectSpec.from_dict.
        if spec is None and not kwargs:
            # Zero-arg construction (e.g. a generic registry fan-out that
            # instantiates every entry with no arguments) has no spec to
            # build from. Fail with a clear message here rather than
            # falling through to DialectSpec.from_dict({})'s internal
            # KeyError on "dialect_id".
            raise ValueError("composed requires a DialectSpec or spec kwargs")
        self.spec = spec if spec is not None else DialectSpec.from_dict(kwargs)

    @property
    def name(self) -> str:
        return self.spec.dialect_id

    def render(self, form_string: str, rng: random.Random | None = None) -> RenderedForm:
        root = form_to_nodes(form_string)
        archetype = ARCHETYPE_REGISTRY[self.spec.archetype]
        text_reader = getattr(archetype, "text_reader", None)

        # M3: content-addressed item seed. `rng`, if given, contributes one
        # deterministic draw as the render_seed fold-in field -- not a
        # sequence of draws consumed in call order, so this stays
        # order-independent even if a caller reuses one `rng` across a batch.
        render_seed_fold = rng.getrandbits(64) if rng is not None else None
        seed_value = item_seed(self.spec, form_string, render_seed_fold)
        archetype_rng = random.Random(seed_value)

        # F2 handoff: lofbench.renderers.pipeline.archetype.assert_node_map_complete
        # is available for archetype authors and archetype-level tests to
        # assert this directly. It is deliberately not force-called here:
        # the pre-existing M1/M2 fixture archetype in tests/test_spec.py
        # ships an intentionally empty node_map (its text payload verifies
        # by parse-back alone, never reading node_map), and enforcing
        # completeness on every render would break that existing, unmodified
        # test. Every concrete archetype this pass adds
        # (lofbench.renderers.archetypes.*) does populate a complete map and
        # is covered by tests/test_archetypes.py.
        base = archetype.build(root, archetype_rng)

        input_relation_hash = relation_hash(containment_relation(root))
        structure_verified, render_relation_hash = verify(
            base, root, archetype.predicate, text_reader=text_reader
        )
        if not structure_verified:
            raise RuntimeError(
                f"archetype {archetype.name!r} failed containment verification "
                f"for form {form_string!r} before any injector ran"
            )

        occurrence_counts: dict[str, int] = {}
        injector_records: list[dict[str, Any]] = []

        for injector_name, params in self.spec.injectors:
            occurrence_index = occurrence_counts.get(injector_name, 0)
            occurrence_counts[injector_name] = occurrence_index + 1
            injector: Injector = INJECTOR_REGISTRY[injector_name]

            sub_seed = injector_substream_seed(seed_value, injector_name, occurrence_index)
            pre_injector_base = base
            applied = False
            resample_count = 0

            for attempt in range(RESAMPLE_CAP + 1):
                attempt_seed = (
                    sub_seed if attempt == 0 else resample_substream_seed(sub_seed, attempt)
                )
                injector_rng = random.Random(attempt_seed)
                candidate = injector.apply(pre_injector_base, root, injector_rng, **params)
                ok, candidate_hash = verify(
                    candidate, root, archetype.predicate, text_reader=text_reader
                )
                if ok:
                    base = candidate
                    render_relation_hash = candidate_hash
                    applied = True
                    resample_count = attempt
                    break
            else:
                # Exhausted every attempt including the first try: drop the
                # injector and keep the last verified state. Never emit a
                # silently non-isomorphic stage.
                resample_count = RESAMPLE_CAP

            injector_records.append(
                {
                    "name": injector_name,
                    "version": getattr(injector, "version", "1"),
                    "params": dict(params),
                    "applied": applied,
                    "resample_count": resample_count,
                    "seed_key": injector_name,
                }
            )

        # M5-rest: a spatial archetype supplies its own BaseRender -> str
        # scene builder (duck-typed, same convention as `text_reader`) since
        # there is no one generic way to draw an arbitrary Primitive tree.
        to_svg = getattr(archetype, "to_svg", None)
        emission = emit(base, self.spec.style, to_svg=to_svg)
        # `structure_verified` from the archetype-level check stays accurate
        # for the final `base` here: every injector either passed its own
        # verify (so `base` moved forward to an equally-verified state) or
        # failed and was reverted (so `base` stayed at the prior verified
        # state). Either way the emitted render is verified whenever this
        # line is reached -- the alternative (archetype-level failure) raises
        # above rather than falling through.
        roundtrip_ok = structure_verified if base.modality == "text" else None
        # Payload hash: blake2b of the symbolic source (the emitted string
        # for text, the pre-rasterisation SVG scene for spatial), never
        # rasterised pixels -- see provenance.py's module docstring.
        payload_hash = blake2b(emission.symbolic_source.encode("utf-8"), digest_size=16).hexdigest()
        metadata = stamp_provenance(
            suite_version=self.spec.suite_version,
            dialect_id=self.spec.dialect_id,
            family=self.spec.family,
            modality=base.modality,
            fmt="image" if emission.is_image else "text",
            archetype_name=archetype.name,
            archetype_version=archetype.version,
            injectors=injector_records,
            item_seed_value=seed_value,
            input_relation_hash=input_relation_hash,
            render_relation_hash=render_relation_hash,
            structure_verified=structure_verified,
            roundtrip_ok=roundtrip_ok,
            renderer_lib_version=f"cairosvg=={CAIROSVG_VERSION}" if emission.is_image else None,
            payload_hash=payload_hash,
        )
        return RenderedForm(
            original=form_string,
            rendered=emission.payload,
            renderer_name=self.name,
            metadata=metadata,
        )
