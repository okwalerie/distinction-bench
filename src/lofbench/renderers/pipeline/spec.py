"""DialectSpec: the structured identity of a dialect, plus named-dialect wiring.

See ``.lattice/notes/rendering-architecture-2026-07-04.md``, "Pipeline spec
format".
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from hashlib import blake2b
from typing import Any


@dataclass(frozen=True)
class DialectSpec:
    """A dialect is pure data. This is the identity of record.

    ``injectors`` is an ordered list of ``(name, params)`` pairs -- order is
    semantic, not incidental. ``suite_version`` is threaded from the frozen
    suite loader (M7); ad-hoc specs default to ``"adhoc"``. Frozen-run-only
    resolution of named specs is a governance rule enforced at freeze time
    (M7), not by anything on this class.
    """

    dialect_id: str
    family: str
    archetype: str
    injectors: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    style: dict[str, Any] = field(default_factory=dict)
    suite_version: str = "adhoc"
    label: str = ""
    reading_rule: str = ""
    description: str = ""
    modality: str = ""
    model_format: str = ""
    provenance: str = ""
    citation: str = ""
    limitations: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DialectSpec:
        """Rebuild a spec from a flat dict, including nested injectors and style.

        This is what makes the ad-hoc ``-T renderer_config='{...}'`` path
        construct: ``get_renderer`` splats the config dict as kwargs into
        ``ComposedRenderer(**kwargs)``, which forwards them here unchanged.
        """
        injectors = [(name, dict(params)) for name, params in d.get("injectors", [])]
        return cls(
            dialect_id=d["dialect_id"],
            family=d["family"],
            archetype=d["archetype"],
            injectors=injectors,
            style=dict(d.get("style", {})),
            suite_version=d.get("suite_version", "adhoc"),
            label=d.get("label", ""),
            reading_rule=d.get("reading_rule", ""),
            description=d.get("description", ""),
            modality=d.get("modality", ""),
            model_format=d.get("model_format", ""),
            provenance=d.get("provenance", ""),
            citation=d.get("citation", ""),
            limitations=tuple(d.get("limitations", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        """Inverse of ``from_dict``. ``from_dict(spec.to_dict()) == spec`` round-trips."""
        return {
            "dialect_id": self.dialect_id,
            "family": self.family,
            "archetype": self.archetype,
            "injectors": [[name, dict(params)] for name, params in self.injectors],
            "style": dict(self.style),
            "suite_version": self.suite_version,
            "label": self.label,
            "reading_rule": self.reading_rule,
            "description": self.description,
            "modality": self.modality,
            "model_format": self.model_format,
            "provenance": self.provenance,
            "citation": self.citation,
            "limitations": list(self.limitations),
        }

    def seed_digest(self) -> bytes:
        """Canonical digest of the whole spec: ``suite_version``, the
        archetype key with its version, the ordered injector list with
        params, and the style. Canonical JSON, not a delimiter join, so no
        field boundary can collide.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return blake2b(canonical.encode(), digest_size=16).digest()


DIALECT_SPECS: dict[str, DialectSpec] = {}


def make_named_dialect_factory(dialect_id: str) -> Callable[..., Any]:
    """Build a renderer-registry factory for a checked-in named dialect.

    Ad-hoc kwargs passed at resolution time (the ``-T`` research-path
    equivalent for a named dialect) merge into the spec's ``style`` rather
    than being silently dropped -- the gap a zero-argument ``lambda **kw``
    factory would leave (architecture doc, worked example 4). The checked-in
    spec in ``DIALECT_SPECS`` is never mutated; forwarding produces a new
    spec via ``dataclasses.replace``.
    """

    def factory(**kwargs: Any) -> Any:
        from .composed import ComposedRenderer  # deferred: composed imports this module

        spec = DIALECT_SPECS[dialect_id]
        if kwargs:
            spec = replace(spec, style={**spec.style, **kwargs})
        return ComposedRenderer(spec)

    return factory


def register_named_dialect(spec: DialectSpec) -> None:
    """Check a spec into ``DIALECT_SPECS`` and wire its renderer-registry entry.

    Uses the guarded ``register_renderer`` path, not a raw dict write, so a
    duplicate renderer key still raises rather than silently overwriting.
    """
    from .. import register_renderer  # deferred: renderers package imports this module

    DIALECT_SPECS[spec.dialect_id] = spec
    register_renderer(spec.dialect_id, make_named_dialect_factory(spec.dialect_id))
