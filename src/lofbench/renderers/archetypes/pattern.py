"""``pattern@1``: the s-expression family. M5 item 2 -- reimplements
``SExprRenderer`` as an archetype plus a ``preset`` injector that selects
glyphs (architecture doc worked example 5: the seven presets become
injector parameters).
"""

from __future__ import annotations

import random

from ..pipeline.archetype import BaseRender, Primitive
from ..pipeline.nodes import FormNode, NodeId, iter_node_ids
from ..pipeline.registry import ARCHETYPE_REGISTRY
from ..pipeline.spec import DialectSpec, register_named_dialect
from ..sexpr import PRESETS

# The six presets whose delimiters are literal "(" / ")" -- default, lisp,
# scheme, python, rust, java -- parse back cleanly with the canonical
# form_to_nodes/string_to_form reader, which already ignores any character
# that is not "(" or ")" (symbol text, separators). "haskell" (open=" ",
# close="") has no delimiter a parse-back reader can key on at all: a
# genuine pre-existing property of that notation, not a regression
# introduced here. It is intentionally left unregistered as a named dialect
# -- see the DB-4 M3-M5 lattice comment.
MIGRATED_PRESETS = ("default", "lisp", "scheme", "python", "rust", "java")


def render_pattern(root: FormNode, symbol: str, open_: str, close_: str, separator: str) -> str:
    """Serialise ``root`` as an s-expression with the given glyphs.

    Mirrors ``SExprRenderer._render_form``/``_render_mark`` node-for-node
    (same recursion shape, same draw/branch order) so the archetype's
    default build and the ``preset`` injector's rebuild are byte-identical
    to the pre-migration renderer for the same preset -- the M5
    structural-equivalence acceptance criterion.
    """

    def render_children(children: tuple[FormNode, ...]) -> str:
        return separator.join(render_mark(child) for child in children)

    def render_mark(node: FormNode) -> str:
        if not node.children:
            return f"{open_}{symbol}{close_}"
        inner = render_children(node.children)
        return f"{open_}{symbol} {inner}{close_}"

    return render_children(root.children)


def _node_map_from_root(root: FormNode) -> dict[NodeId, Primitive]:
    return {nid: Primitive(node_id=nid, kind="token", geom={}) for nid in iter_node_ids(root)}


class PatternArchetype:
    """The pattern (s-expression) family. Builds with the "default" preset;
    the ``preset`` injector rebuilds with any other preset's glyphs.
    """

    name = "pattern"
    version = "1"
    family = "pattern"
    modality = "text"
    # No custom text_reader: the default canonical reader already tolerates
    # any non-paren filler (see MIGRATED_PRESETS above), which is every
    # preset this migration wires up.

    def build(self, root: FormNode, rng: random.Random) -> BaseRender:
        preset = PRESETS["default"]
        payload = render_pattern(
            root, preset["symbol"], preset["open"], preset["close"], preset["separator"]
        )
        return BaseRender(modality="text", payload=payload, node_map=_node_map_from_root(root))

    def predicate(self, parent: Primitive, child: Primitive) -> bool:
        return False  # text modality: induced_relation ignores pred entirely


ARCHETYPE_REGISTRY["pattern@1"] = PatternArchetype()

for _preset_name in MIGRATED_PRESETS:
    _dialect_id = (
        "pattern.plain-v1"
        if _preset_name == "default"
        else f"pattern.{_preset_name}-v1"
    )
    register_named_dialect(
        DialectSpec(
            dialect_id=_dialect_id,
            family="pattern",
            archetype="pattern@1",
            injectors=([] if _preset_name == "default" else [("preset", {"name": _preset_name})]),
        )
    )
