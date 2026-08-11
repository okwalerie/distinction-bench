"""Canonical (identity) renderer with optional spacing."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .base import FormRenderer, RenderedForm, merge_config_kwargs


@dataclass
class CanonicalConfig:
    """Configuration for canonical renderer.

    Attributes:
        spacing: If True, add random benign whitespace between tokens.
    """

    spacing: bool = False


class CanonicalRenderer(FormRenderer):
    """Identity renderer that returns forms unchanged (with optional spacing).

    This renderer provides a baseline for comparing other rendering
    strategies. By default it returns the input form without any transformation.
    With spacing=True, it adds random whitespace to test model robustness.
    """

    def __init__(self, config: CanonicalConfig | None = None, **kwargs):
        """Initialize the canonical renderer.

        Args:
            config: Configuration for rendering. If None, uses defaults.
            **kwargs: Override config values (e.g., spacing=True).
        """
        self.config = config or CanonicalConfig()
        merge_config_kwargs(self.config, kwargs)

    @property
    def name(self) -> str:
        return "canonical"

    def render(self, form_string: str, rng: random.Random | None = None) -> RenderedForm:
        """Return the form, optionally with added spacing.

        Args:
            form_string: The input form string
            rng: Random generator for spacing (uses default if None and spacing enabled)

        Returns:
            RenderedForm with original and rendered forms
        """
        if not self.config.spacing or not form_string:
            return RenderedForm(
                original=form_string,
                rendered=form_string,
                renderer_name=self.name,
                metadata={"spacing": False},
            )

        # Add random spacing between characters
        if rng is None:
            rng = random.Random()

        result = []
        for i, char in enumerate(form_string):
            result.append(char)
            # Randomly add space after ) or before ( (between tokens)
            if i < len(form_string) - 1:
                next_char = form_string[i + 1]
                # Add space between adjacent marks or around nesting
                if (char == ")" and next_char == "(") or \
                   (char == ")" and next_char == ")") or \
                   (char == "(" and next_char == "("):
                    if rng.random() < 0.5:
                        result.append(" ")

        rendered = "".join(result)
        return RenderedForm(
            original=form_string,
            rendered=rendered,
            renderer_name=self.name,
            metadata={"spacing": True},
        )
