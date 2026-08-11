"""Base classes for form renderers."""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RenderedForm:
    """A form that has been rendered by a renderer."""

    original: str
    rendered: str
    renderer_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


def merge_config_kwargs(config: Any, kwargs: dict[str, Any]) -> None:
    """Apply CLI-style kwargs overrides onto a renderer's config object.

    Shared by the five legacy renderers (``canonical``, ``noisy_parens``,
    ``sexpr``, ``nested_list``, ``svg_circle``), replacing their five
    copy-pasted ``for key, value in kwargs.items(): if hasattr(...): ...``
    loops -- the plan's M5 "shared config-merge helper on the base" item.
    Silently ignores a kwarg that does not name an existing config
    attribute (matching the loops' prior behaviour exactly), so an
    ``inspect eval -T`` typo is not a new hard failure introduced by this
    refactor.
    """
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)


class FormRenderer(ABC):
    """Abstract base class for form renderers.

    Renderers transform form strings into alternative representations
    while preserving the underlying structure. Examples include:
    - Identity/canonical rendering (no transformation)
    - Noisy parentheses (substituting different bracket types)
    - Whitespace variations
    - Alternative notations
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the unique name of this renderer."""
        pass

    @abstractmethod
    def render(self, form_string: str, rng: random.Random | None = None) -> RenderedForm:
        """Render a single form string.

        Args:
            form_string: The input form string to render
            rng: Optional Random instance for reproducibility

        Returns:
            RenderedForm containing the original, rendered version, and metadata
        """
        pass

    def render_batch(self, forms: list[str], seed: int | None = None) -> list[RenderedForm]:
        """Render a batch of forms with optional seed for reproducibility.

        Args:
            forms: List of form strings to render
            seed: Optional seed for reproducible rendering

        Returns:
            List of RenderedForm objects
        """
        rng = random.Random(seed) if seed is not None else None
        return [self.render(form, rng) for form in forms]
