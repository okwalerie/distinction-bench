"""``preset``: select an s-expression preset's glyphs (symbol/open/close/
separator). Ports ``SExprRenderer``'s seven presets onto the composed
pipeline as an injector parameter (architecture doc worked example 5).
"""

from __future__ import annotations

import random

from ..archetypes.pattern import render_pattern
from ..pipeline.archetype import BaseRender
from ..pipeline.nodes import FormNode
from ..pipeline.registry import INJECTOR_REGISTRY
from ..sexpr import PRESETS


class PresetInjector:
    name = "preset"
    version = "1"
    modalities = frozenset({"text"})
    applicability = frozenset({"pattern"})  # archetype-specific per the ECS amendment

    def apply(
        self, base: BaseRender, root: FormNode, rng: random.Random, **params: object
    ) -> BaseRender:
        preset_name = str(params.get("name", "default"))
        preset = PRESETS.get(preset_name, PRESETS["default"])
        symbol = params.get("symbol") or preset["symbol"]
        open_ = params.get("open") if params.get("open") is not None else preset["open"]
        close_ = params.get("close") if params.get("close") is not None else preset["close"]
        separator = (
            params.get("separator") if params.get("separator") is not None else preset["separator"]
        )
        payload = render_pattern(root, str(symbol), str(open_), str(close_), str(separator))
        return BaseRender(modality="text", payload=payload, node_map=base.node_map)


INJECTOR_REGISTRY["preset"] = PresetInjector()
