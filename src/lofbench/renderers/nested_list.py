"""Nested list renderer - serialize forms as JSON arrays."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

from lofbench.core import string_to_form

from .base import FormRenderer, RenderedForm, merge_config_kwargs


@dataclass
class NestedListConfig:
    """Configuration for nested list renderer.

    Attributes:
        spacing: If True, add spaces after colons and commas for readability.
    """

    spacing: bool = True


class NestedListRenderer(FormRenderer):
    """Render forms as nested JSON arrays.

    Converts the parenthesis notation to its internal list representation,
    then serializes as JSON.

    Examples:
        "()" -> "[[]]"
        "()()" -> "[[], []]"
        "(())" -> "[[[]]]"
        "(()())" -> "[[[], []]]"
    """

    def __init__(self, config: NestedListConfig | None = None, **kwargs):
        """Initialize the nested list renderer.

        Args:
            config: Configuration for rendering. If None, uses defaults.
            **kwargs: Override config values (e.g., spacing=True).
        """
        self.config = config or NestedListConfig()
        merge_config_kwargs(self.config, kwargs)

    @property
    def name(self) -> str:
        return "nested_list"

    def render(self, form_string: str, rng: random.Random | None = None) -> RenderedForm:
        """Render a form as a nested JSON array.

        Args:
            form_string: The input form string (e.g., "(()())")
            rng: Not used for this renderer (deterministic)

        Returns:
            RenderedForm with JSON array representation
        """
        if not form_string:
            # Empty/void form
            rendered = "[]"
        else:
            form = string_to_form(form_string)
            if self.config.spacing:
                rendered = json.dumps(form, separators=(", ", ": "))
            else:
                rendered = json.dumps(form, separators=(",", ":"))

        return RenderedForm(
            original=form_string,
            rendered=rendered,
            renderer_name=self.name,
            metadata={"spacing": self.config.spacing},
        )
