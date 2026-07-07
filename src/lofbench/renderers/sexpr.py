"""S-expression renderer - serialize forms as Lisp-like expressions."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

from lofbench.core import string_to_form

from .base import FormRenderer, RenderedForm, merge_config_kwargs


# Preset configurations for different language styles
PRESETS: dict[str, dict[str, str]] = {
    "default": {
        "symbol": "x",
        "open": "(",
        "close": ")",
        "separator": " ",
    },
    "lisp": {
        "symbol": "quote",
        "open": "(",
        "close": ")",
        "separator": " ",
    },
    "scheme": {
        "symbol": "cons",
        "open": "(",
        "close": ")",
        "separator": " ",
    },
    "python": {
        "symbol": "fn",
        "open": "(",
        "close": ")",
        "separator": ", ",
    },
    "rust": {
        "symbol": "Box::new",
        "open": "(",
        "close": ")",
        "separator": ", ",
    },
    "java": {
        "symbol": "new Node",
        "open": "(",
        "close": ")",
        "separator": ", ",
    },
    "haskell": {
        "symbol": "Node",
        "open": " ",
        "close": "",
        "separator": " ",
    },
}

PresetName = Literal["default", "lisp", "scheme", "python", "rust", "java", "haskell"]


@dataclass
class SExprConfig:
    """Configuration for S-expression renderer.

    Attributes:
        preset: Name of preset to use (default, lisp, scheme, python, rust, java, haskell)
        symbol: Override the symbol used (e.g., "mark", "call", "x")
        open: Opening delimiter (default "(")
        close: Closing delimiter (default ")")
        separator: Separator between children (default " ")
    """

    preset: PresetName = "default"
    symbol: str | None = None
    open: str | None = None
    close: str | None = None
    separator: str | None = None


class SExprRenderer(FormRenderer):
    """Render forms as S-expressions with configurable syntax.

    Converts parenthesis notation to S-expression style where each mark
    becomes a node with a symbol.

    Examples (default preset):
        "()" -> "(x)"
        "()()" -> "(x) (x)"
        "(())" -> "(x (x))"
        "(()())" -> "(x (x) (x))"

    Examples (lisp preset):
        "()" -> "(quote)"
        "(())" -> "(quote (quote))"

    Examples (rust preset):
        "()" -> "Box::new()"
        "(())" -> "Box::new(Box::new())"
        "(()())" -> "Box::new(Box::new(), Box::new())"
    """

    def __init__(self, config: SExprConfig | None = None, **kwargs):
        """Initialize the S-expression renderer.

        Args:
            config: Configuration for rendering. If None, uses defaults.
            **kwargs: Override config values (e.g., preset="lisp", symbol="mark").
        """
        self.config = config or SExprConfig()
        merge_config_kwargs(self.config, kwargs)

        # Resolve preset and overrides
        preset_config = PRESETS.get(self.config.preset, PRESETS["default"])
        self._symbol = self.config.symbol or preset_config["symbol"]
        self._open = self.config.open if self.config.open is not None else preset_config["open"]
        self._close = self.config.close if self.config.close is not None else preset_config["close"]
        self._separator = self.config.separator if self.config.separator is not None else preset_config["separator"]

    @property
    def name(self) -> str:
        return "sexpr"

    def _render_form(self, form: list) -> str:
        """Recursively render a form (nested list) as S-expression."""
        if not form:
            # Empty form (void) - shouldn't typically appear as a mark
            return ""

        # A mark is represented as (symbol children...)
        children = [self._render_mark(child) for child in form]
        return self._separator.join(children)

    def _render_mark(self, mark: list) -> str:
        """Render a single mark with its children."""
        if not mark:
            # Leaf mark with no children
            return f"{self._open}{self._symbol}{self._close}"

        # Mark with children
        children_str = self._render_form(mark)
        return f"{self._open}{self._symbol} {children_str}{self._close}"

    def render(self, form_string: str, rng: random.Random | None = None) -> RenderedForm:
        """Render a form as an S-expression.

        Args:
            form_string: The input form string (e.g., "(()())")
            rng: Not used for this renderer (deterministic)

        Returns:
            RenderedForm with S-expression representation
        """
        if not form_string:
            # Empty/void form
            rendered = ""
        else:
            form = string_to_form(form_string)
            rendered = self._render_form(form)

        return RenderedForm(
            original=form_string,
            rendered=rendered,
            renderer_name=self.name,
            metadata={
                "preset": self.config.preset,
                "symbol": self._symbol,
                "open": self._open,
                "close": self._close,
                "separator": self._separator,
            },
        )
