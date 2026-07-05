"""M6: injector applicability admission gate.

Valerie's 4 July amendment (see ``.lattice/notes/design-decisions-
2026-07-04.md``): each injector declares an ``applicability`` set of
archetype names or whole modality names it is allowed to run against. The
spec validator here rejects an unsupported archetype-injector pairing at
construction, before any render or model spend -- not a runtime surprise
deep inside ``ComposedRenderer.render``.

Two declaration conventions coexist in the registry today, both valid per
the amendment's own wording ("archetype names or a whole modality"):
DB-4's own injectors (``whitespace_jitter``, ``bracket_swap``, ``preset``)
declare the bare ``Archetype.name`` (e.g. ``"parens"``) or a modality
(``"text"``); DB-2's text-dialect injectors declare the full registry key
(``Archetype.name`` + ``"@"`` + ``Archetype.version``, e.g.
``"rna_dotbracket@1"``). :func:`_archetype_identifiers` matches an
injector's ``applicability`` against every one of an archetype's own
identifying strings -- its bare name, its registry key, its family, and
its modality -- so both conventions are honoured without editing either
side's already-declared ``applicability`` values.
"""

from __future__ import annotations

from .registry import ARCHETYPE_REGISTRY, INJECTOR_REGISTRY
from .spec import DialectSpec


def _archetype_identifiers(archetype: object) -> frozenset[str]:
    return frozenset(
        {
            archetype.name,  # type: ignore[attr-defined]
            f"{archetype.name}@{archetype.version}",  # type: ignore[attr-defined]
            archetype.family,  # type: ignore[attr-defined]
            archetype.modality,  # type: ignore[attr-defined]
        }
    )


def validate_applicability(spec: DialectSpec) -> None:
    """Reject a spec pairing an injector with an archetype it does not
    declare itself applicable to.

    Deliberately permissive about *unknown* registry keys: an archetype or
    injector name absent from the registries is a separate failure
    (``ComposedRenderer.render`` raises its own clear ``KeyError`` for
    that), not an applicability mismatch, and several existing test
    fixtures construct a ``DialectSpec`` with a placeholder archetype key
    and no injectors precisely to exercise construction in isolation from
    the registries -- this function must not require every such fixture to
    also register a real archetype.
    """
    if not spec.injectors:
        return

    archetype = ARCHETYPE_REGISTRY.get(spec.archetype)
    if archetype is None:
        return

    identifiers = _archetype_identifiers(archetype)
    for injector_name, _params in spec.injectors:
        injector = INJECTOR_REGISTRY.get(injector_name)
        if injector is None:
            continue

        applicability = getattr(injector, "applicability", None)
        if applicability is None:
            continue  # no declaration at all: nothing to admit against (should not happen)

        if not (applicability & identifiers):
            raise ValueError(
                f"injector {injector_name!r} (applicability={sorted(applicability)!r}) is not "
                f"applicable to archetype {spec.archetype!r} (name={archetype.name!r}, "
                f"family={archetype.family!r}, modality={archetype.modality!r}) "
                f"in dialect {spec.dialect_id!r} -- rejected at admission, before any render"
            )
