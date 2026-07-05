"""Pure data-gathering for the rendering showcase gallery (DB-12).

No FastHTML imports here -- `pages/gallery.py` turns these dataclasses into
markup, so this module is unit-testable without spinning up the app. Every
structure below comes from iterating live registries
(`lofbench.renderers.list_renderers`, `lofbench.renderers.pipeline.spec.
DIALECT_SPECS`) and the checked-in `suites/v1.json` (via `lofbench.suites.
load_suite`) -- never a hand-written dialect list, per the gallery plan's
binding constraint that a new dialect registered anywhere in `lofbench`
appears on the next request with zero gallery edits.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from lofbench.core import DIFFICULTY_CONFIGS, generate_form_string, simplify_string, string_depth
from lofbench.renderers import get_renderer, list_renderers
from lofbench.renderers.pipeline.spec import DIALECT_SPECS
from lofbench.suites import load_suite

# Distinct from `lofbench.suites.GENERATION_SEED` so the gallery's own
# generator-section sample is visibly a separate, smaller draw and not a
# claim about suite v1's actual frozen forms (see the gallery plan, section
# "Section 2: the generator, sampled per tier").
GENERATOR_SAMPLE_SEED = 20260704

# Fixed seeds for the two legacy renderers that genuinely consume `rng`
# (`canonical` with spacing enabled, `noisy_parens` always -- see
# `_LEGACY_SEEDED_CTOR_KWARGS` below). `LEGACY_DEFAULT_SEED` seeds every
# legacy renderer's *primary* panel (a no-op for the three that never touch
# `rng`; without it, `noisy_parens`'s default `render(form, rng=None)` would
# fall back to an unseeded `random.Random()` and render differently on
# every call -- a real non-determinism bug this pins down, since
# `build_gallery_data`/`export_gallery.build_static_document` both claim to
# be pure, same-input-same-output). `LEGACY_PRIMARY_SEED`/`LEGACY_SECOND_SEED`
# are the distinct seed pair the legacy section's own seed-variation demo
# renders side by side -- deliberately different from the default seed so
# the "primary" panel and the "seed=1" demo panel are not accidentally
# identical.
LEGACY_DEFAULT_SEED = 0
LEGACY_PRIMARY_SEED = 1
LEGACY_SECOND_SEED = 2

# "circle" is a byte-identical legacy-key alias of "enclosure.canonical-v1"
# -- same archetype, same empty injector list. See `lofbench.suites`'s own
# module docstring for the full rationale (including both in a frozen suite
# would manufacture a guaranteed payload collision). The gallery does not
# render a second, visually-identical panel for it -- see `_group_dialects`.
CIRCLE_ALIAS_ID = "circle"
CIRCLE_ALIAS_OF = "enclosure.canonical-v1"
CIRCLE_ALIAS_NOTE = (
    f"'{CIRCLE_ALIAS_ID}' is a registered legacy-key alias of this exact archetype "
    f"({CIRCLE_ALIAS_OF!r}: same archetype, same empty injector list) -- "
    "see lofbench.suites's module docstring. Not rendered as a second panel."
)


@dataclass(frozen=True)
class RenderedPanel:
    """One dialect's rendering, ready for display."""

    dialect_id: str
    kind: str  # "text" or "image"
    content: str
    caption: str


@dataclass(frozen=True)
class ArchetypeGroup:
    """One archetype's canonical panel plus its variation arms (or, for an
    archetype with no injector-bearing sibling registered, the honest
    deeper-exemplar fallback -- see the gallery plan's "Grouping algorithm").
    """

    family: str
    archetype: str
    canonical: RenderedPanel
    variations: list[RenderedPanel]
    is_fallback: bool
    note: str = ""
    # Injector-bearing sibling ids that exist in the registry but were not
    # picked as one of the (capped at 3) variation panels -- e.g. `pattern`
    # has five presets besides `default` (java/lisp/python/rust/scheme);
    # only the first three by sorted id get a panel, per the plan's Tufte
    # small-multiples cap. Named here, not silently dropped, so the
    # registry-accounting acceptance criterion (every registered dialect
    # accounted for somewhere) holds even though not every sibling gets its
    # own panel.
    omitted_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LegacyEntry:
    """One bare legacy renderer (no `DialectSpec` / injector arm registered).

    `seed_pair` is `None` for a fully deterministic legacy renderer
    (`nested_list`, `sexpr` -- neither takes `rng`) and a real
    `(seed_a, seed_b)` pair for the two that do (`canonical`, `noisy_parens`)
    -- see the module docstring above `_LEGACY_SEEDED_CTOR_KWARGS`.
    """

    dialect_id: str
    panel: RenderedPanel
    seed_pair: tuple[RenderedPanel, RenderedPanel] | None = None


@dataclass(frozen=True)
class GeneratorSample:
    tier: str
    form_string: str
    depth: int
    steps: int


@dataclass(frozen=True)
class SuiteCellSample:
    form_id: str
    dialect_id: str
    family: str
    modality: str
    format: str
    structure_verified: bool
    payload_hash_prefix: str


@dataclass(frozen=True)
class SuiteSummary:
    suite_version: str
    n_forms: int
    n_dialects: int
    n_cells: int
    sample_cells: list[SuiteCellSample]


@dataclass(frozen=True)
class GalleryData:
    exemplar_shallow: str
    exemplar_deep: str
    families: list[ArchetypeGroup]
    legacy: list[LegacyEntry]
    circle_note: str
    generator_samples: list[GeneratorSample]
    suite_summary: SuiteSummary


# ---------------------------------------------------------------------------
# Exemplar forms
# ---------------------------------------------------------------------------


def _exemplar_forms() -> tuple[str, str]:
    """Shallow: the first form (by `form_id` order) with difficulty
    "1. easy" (deterministically `lof_001`). Deep: the first form with
    difficulty "5. extra" (deterministically `lof_097`). Loaded from
    `suites/v1.json` -- see the gallery plan's "Exemplar forms" section for
    why these are real frozen forms rather than a new ad-hoc generation.
    """
    forms = load_suite().forms
    shallow = next(f["form_string"] for f in forms if f["difficulty"] == "1. easy")
    deep = next(f["form_string"] for f in forms if f["difficulty"] == "5. extra")
    return shallow, deep


# ---------------------------------------------------------------------------
# Section 1: family/archetype panels
# ---------------------------------------------------------------------------


def _render_panel(
    dialect_id: str,
    form_string: str,
    caption: str,
    *,
    rng: random.Random | None = None,
    **ctor_kwargs: Any,
) -> RenderedPanel:
    renderer = get_renderer(dialect_id, **ctor_kwargs) if ctor_kwargs else get_renderer(dialect_id)
    result = renderer.render(form_string, rng) if rng is not None else renderer.render(form_string)
    kind = "image" if result.metadata.get("format") == "image" else "text"
    return RenderedPanel(dialect_id=dialect_id, kind=kind, content=result.rendered, caption=caption)


def _injector_caption(dialect_id: str) -> str:
    """The direct-label caption for an archetype-family panel: empty for a
    canonical (injector-free) dialect -- its id alone is the label -- or its
    injector name(s) and params for a variation, e.g.
    "whitespace_jitter(amp=1)". Never repeats `dialect_id` itself: the page
    shows that as the panel's own direct label (Tufte: no separate legend).
    """
    spec = DIALECT_SPECS[dialect_id]
    if not spec.injectors:
        return ""
    return ", ".join(
        f"{name}({', '.join(f'{k}={v}' for k, v in params.items())})"
        for name, params in spec.injectors
    )


def _group_dialects() -> tuple[dict[str, dict[str, list[str]]], list[str], bool]:
    """Registry-driven grouping: family -> archetype -> [dialect_ids],
    plus the bare legacy renderer ids and whether the "circle" alias is
    currently registered. Same exclusion of "composed" as the sandbox's own
    fan-out (`lofsite.rendering.render_all_dialects`) -- it needs a
    caller-supplied `DialectSpec`, so it is a parameterised mechanism, not a
    standalone dialect.
    """
    dialect_ids = [k for k in list_renderers() if k != "composed"]
    by_family: dict[str, dict[str, list[str]]] = {}
    legacy: list[str] = []
    circle_present = False
    for key in dialect_ids:
        if key == CIRCLE_ALIAS_ID:
            circle_present = True
            continue
        spec = DIALECT_SPECS.get(key)
        if spec is None:
            legacy.append(key)
            continue
        by_family.setdefault(spec.family, {}).setdefault(spec.archetype, []).append(key)
    return by_family, sorted(legacy), circle_present


def _build_family_groups(
    by_family: dict[str, dict[str, list[str]]],
    circle_present: bool,
    shallow: str,
    deep: str,
) -> list[ArchetypeGroup]:
    groups: list[ArchetypeGroup] = []
    for family in sorted(by_family):
        for archetype in sorted(by_family[family]):
            ids = sorted(by_family[family][archetype])
            canonical_candidates = sorted(i for i in ids if not DIALECT_SPECS[i].injectors)
            note = ""
            if not canonical_candidates:
                # Invariant break the plan flags but does not expect: no
                # injector-free id in this archetype's registered set. Pick
                # the alphabetically-first id and say so, rather than crash.
                canonical_candidates = ids
                note = (
                    f"no injector-free dialect registered for {archetype!r} -- "
                    f"picked {ids[0]!r} alphabetically as the canonical panel."
                )
            canonical_id = canonical_candidates[0]
            if len(canonical_candidates) > 1:
                note = (
                    f"tie among injector-free ids {canonical_candidates!r}: "
                    f"picked {canonical_id!r} alphabetically."
                )

            canonical_panel = _render_panel(canonical_id, shallow, _injector_caption(canonical_id))

            variation_candidates = [i for i in ids if i != canonical_id]
            omitted_ids: list[str] = []
            if variation_candidates:
                variation_ids = variation_candidates[:3]
                omitted_ids = variation_candidates[3:]
                variations = [
                    _render_panel(v, shallow, _injector_caption(v)) for v in variation_ids
                ]
                is_fallback = False
                if omitted_ids:
                    omitted_note = (
                        f"not shown as separate panels (2-3-panel cap): {', '.join(omitted_ids)} "
                        "-- same archetype and family, differing only in injector params."
                    )
                    note = f"{note} {omitted_note}".strip()
            else:
                fallback_caption = (
                    "deeper input -- no injector variation registered for this archetype -- "
                    "showing a deeper input instead"
                )
                variations = [_render_panel(canonical_id, deep, fallback_caption)]
                is_fallback = True
                fallback_note = (
                    "no injector variation registered for this archetype -- "
                    "showing a deeper input instead"
                )
                note = f"{note} {fallback_note}".strip()

            if family == "enclosure" and archetype == "enclosure@1" and circle_present:
                note = f"{note} {CIRCLE_ALIAS_NOTE}".strip()

            groups.append(
                ArchetypeGroup(
                    family=family,
                    archetype=archetype,
                    canonical=canonical_panel,
                    variations=variations,
                    is_fallback=is_fallback,
                    note=note,
                    omitted_ids=omitted_ids,
                )
            )
    return groups


# ---------------------------------------------------------------------------
# Legacy renderers
# ---------------------------------------------------------------------------

# `canonical` and `noisy_parens` are the only bare legacy renderers whose
# `.render()` genuinely consumes `rng` under default construction.
# `noisy_parens` always calls `rng.choice()` regardless of its `mismatched`
# flag. `canonical`'s default `CanonicalConfig(spacing=False)` returns the
# form unchanged and never touches `rng` at all -- `spacing=True` must be
# passed explicitly to observe real seed-driven variation. Showing two
# default-construction seeds side by side for `canonical` would render
# byte-identical content, which is exactly the "fake variation" the gallery
# plan forbids -- so this dialect's second-seed demo constructs with
# `spacing=True` explicitly and says so in its caption, rather than silently
# reusing the (non-varying) default construction the primary panel shows.
_LEGACY_SEEDED_CTOR_KWARGS: dict[str, dict[str, Any]] = {
    "canonical": {"spacing": True},
    "noisy_parens": {},
}


def _build_legacy_entries(legacy_ids: list[str], shallow: str) -> list[LegacyEntry]:
    entries = []
    for dialect_id in legacy_ids:
        primary = _render_panel(
            dialect_id,
            shallow,
            "legacy renderer, no DialectSpec / injector arm registered.",
            rng=random.Random(LEGACY_DEFAULT_SEED),
        )
        seed_pair = None
        if dialect_id in _LEGACY_SEEDED_CTOR_KWARGS:
            ctor_kwargs = _LEGACY_SEEDED_CTOR_KWARGS[dialect_id]
            seed_note = ""
            if dialect_id == "canonical":
                seed_note = (
                    " (spacing=True passed explicitly: the default construction above has "
                    "spacing=False and never consumes rng, so two default seeds would render "
                    "byte-identically -- this is where canonical's rng-driven variation is real)"
                )
            seed_a = _render_panel(
                dialect_id,
                shallow,
                f"seed={LEGACY_PRIMARY_SEED}{seed_note}",
                rng=random.Random(LEGACY_PRIMARY_SEED),
                **ctor_kwargs,
            )
            seed_b = _render_panel(
                dialect_id,
                shallow,
                f"seed={LEGACY_SECOND_SEED}{seed_note}",
                rng=random.Random(LEGACY_SECOND_SEED),
                **ctor_kwargs,
            )
            seed_pair = (seed_a, seed_b)
        entries.append(LegacyEntry(dialect_id=dialect_id, panel=primary, seed_pair=seed_pair))
    return entries


# ---------------------------------------------------------------------------
# Section 2: generator, sampled per tier
# ---------------------------------------------------------------------------


def _build_generator_samples() -> list[GeneratorSample]:
    samples = []
    for tier_name, min_d, max_d, max_w, max_m in DIFFICULTY_CONFIGS:
        rng = random.Random(GENERATOR_SAMPLE_SEED)
        form_string = (
            generate_form_string(
                min_depth=min_d, max_depth=max_d, max_width=max_w, max_marks=max_m, rng=rng
            )
            or "()"
        )
        _canonical, steps = simplify_string(form_string)
        samples.append(
            GeneratorSample(
                tier=tier_name,
                form_string=form_string,
                depth=string_depth(form_string),
                steps=len(steps),
            )
        )
    return samples


# ---------------------------------------------------------------------------
# Section 3: suite v1 headline + sample
# ---------------------------------------------------------------------------


def _build_suite_summary() -> SuiteSummary:
    suite = load_suite()
    cells = sorted(suite.cells, key=lambda c: (c["form_id"], c["dialect_id"]))
    n = len(cells)
    sample: list[SuiteCellSample] = []
    if n:
        stride = max(n // 12, 1)
        for i in range(0, n, stride):
            if len(sample) >= 12:
                break
            c = cells[i]
            sample.append(
                SuiteCellSample(
                    form_id=c["form_id"],
                    dialect_id=c["dialect_id"],
                    family=c["family"],
                    modality=c["modality"],
                    format=c["format"],
                    structure_verified=c["structure_verified"],
                    payload_hash_prefix=c["payload_hash"][:12],
                )
            )
    return SuiteSummary(
        suite_version=suite.suite_version,
        n_forms=len(suite.forms),
        n_dialects=len(suite.specs),
        n_cells=len(suite.cells),
        sample_cells=sample,
    )


# ---------------------------------------------------------------------------
# Top-level build
# ---------------------------------------------------------------------------


def build_gallery_data() -> GalleryData:
    """Gather everything the gallery page/export show, rendered fresh:
    iterate `list_renderers()`/`DIALECT_SPECS` for section 1, `generate_form_string`
    for section 2, `load_suite()` for section 3. No hand-written dialect list.
    """
    shallow, deep = _exemplar_forms()
    by_family, legacy_ids, circle_present = _group_dialects()
    families = _build_family_groups(by_family, circle_present, shallow, deep)
    legacy = _build_legacy_entries(legacy_ids, shallow)
    circle_note = CIRCLE_ALIAS_NOTE if circle_present else ""
    generator_samples = _build_generator_samples()
    suite_summary = _build_suite_summary()
    return GalleryData(
        exemplar_shallow=shallow,
        exemplar_deep=deep,
        families=families,
        legacy=legacy,
        circle_note=circle_note,
        generator_samples=generator_samples,
        suite_summary=suite_summary,
    )


def accounted_dialect_ids(data: GalleryData) -> set[str]:
    """Every dialect id the gallery's output accounts for: rendered as a
    panel (canonical, variation, or legacy), or -- for `circle` only --
    named in an explicit alias caption. Used by the registry-accounting
    test so it checks set equality against the live registry directly,
    rather than enumerating dialect ids by hand.
    """
    ids: set[str] = set()
    for group in data.families:
        ids.add(group.canonical.dialect_id)
        for v in group.variations:
            if not group.is_fallback:
                ids.add(v.dialect_id)
        ids.update(group.omitted_ids)
    for entry in data.legacy:
        ids.add(entry.dialect_id)
    if data.circle_note:
        ids.add(CIRCLE_ALIAS_ID)
    return ids
