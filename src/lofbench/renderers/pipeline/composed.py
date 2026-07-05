"""ComposedRenderer: the glue between a DialectSpec-driven pipeline and the
existing FormRenderer contract, so the task layer, scorers, and the ``-T``
contract do not change.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, "Core
abstractions" and "Pipeline spec format".
"""

from __future__ import annotations

import random
from typing import Any

from ..base import FormRenderer, RenderedForm
from .emit import emit
from .injector import Injector
from .nodes import containment_relation, form_to_nodes, relation_hash
from .provenance import stamp_provenance
from .registry import ARCHETYPE_REGISTRY, INJECTOR_REGISTRY
from .spec import DialectSpec
from .verify import verify


class ComposedRenderer(FormRenderer):
    """Parse, build, verify, fold injectors, emit, stamp provenance.

    M1/M2 skeleton:

    - ``verify`` runs once, immediately after the archetype builds.
      ``render_relation_hash`` therefore reflects the base render *before*
      any injector runs. Per-injector re-verification (so the hash tracks
      the final, post-injector state) with resample-or-drop on violation is
      M4 scope -- injectors below apply unconditionally and are not yet
      re-checked.
    - The task-layer ``rng`` is threaded straight through to the archetype
      and every injector, unmodified. Content-addressed ``item_seed``
      (spec digest plus form string) and decorrelated per-injector
      substreams are M3 scope.
    """

    def __init__(self, spec: DialectSpec | None = None, **kwargs: Any) -> None:
        # get_renderer splats renderer_config as kwargs, so accept both paths:
        # a named factory passes spec directly; the ad-hoc -T path passes a
        # flat spec dict rebuilt here via DialectSpec.from_dict.
        self.spec = spec if spec is not None else DialectSpec.from_dict(kwargs)

    @property
    def name(self) -> str:
        return self.spec.dialect_id

    def render(self, form_string: str, rng: random.Random | None = None) -> RenderedForm:
        root = form_to_nodes(form_string)
        archetype = ARCHETYPE_REGISTRY[self.spec.archetype]

        build_rng = rng if rng is not None else random.Random()

        base = archetype.build(root, build_rng)
        structure_verified, render_relation_hash = verify(base, root, archetype.predicate)
        input_relation_hash = relation_hash(containment_relation(root))

        for injector_name, params in self.spec.injectors:
            injector: Injector = INJECTOR_REGISTRY[injector_name]
            base = injector.apply(base, root, build_rng, **params)

        emission = emit(base, self.spec.style)
        metadata = stamp_provenance(
            dialect_id=self.spec.dialect_id,
            family=self.spec.family,
            modality=base.modality,
            fmt="image" if emission.is_image else "text",
            input_relation_hash=input_relation_hash,
            render_relation_hash=render_relation_hash,
            structure_verified=structure_verified,
        )
        return RenderedForm(
            original=form_string,
            rendered=emission.payload,
            renderer_name=self.name,
            metadata=metadata,
        )
