"""Noisy parentheses renderer with configurable bracket substitution."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .base import FormRenderer, RenderedForm, merge_config_kwargs

# Various bracket pairs for substitution
BRACKET_PAIRS = [
    # ASCII brackets
    ("(", ")"),
    ("[", "]"),
    ("{", "}"),
    ("<", ">"),
    # Unicode angle brackets
    ("\u27e8", "\u27e9"),  # ⟨⟩ Mathematical angle brackets
    ("\u2329", "\u232a"),  # 〈〉 Left/right-pointing angle brackets
    ("\u3008", "\u3009"),  # 〈〉 Left/right angle brackets
    ("\u300a", "\u300b"),  # 《》 Left/right double angle brackets
    ("\u300c", "\u300d"),  # 「」 Left/right corner brackets
    ("\u300e", "\u300f"),  # 『』 Left/right white corner brackets
]


@dataclass
class NoisyParensConfig:
    """Configuration for noisy parentheses rendering.

    Attributes:
        bracket_pairs: List of (open, close) bracket pair tuples to use
        mismatched: If True, opening and closing brackets can be from
                   different pairs. If False, pairs are kept consistent
                   per depth level.
    """

    bracket_pairs: list[tuple[str, str]] = None
    mismatched: bool = False

    def __post_init__(self):
        if self.bracket_pairs is None:
            self.bracket_pairs = BRACKET_PAIRS


class NoisyParensRenderer(FormRenderer):
    """Renderer that substitutes parentheses with random bracket types.

    This renderer replaces the canonical '()' notation with various
    bracket types while preserving the structural meaning. It can
    operate in two modes:

    1. Matched mode (default): Each depth level is assigned a consistent
       bracket pair, making the structure more readable but still varied.

    2. Mismatched mode: Opening and closing brackets are chosen independently,
       creating maximally confusing but still structurally valid expressions.

    Examples:
        Matched mode:
            "(())" -> "[{()}]" or "⟨〈〉⟩"

        Mismatched mode:
            "(())" -> "[{⟩」" or "⟨}]〉"
    """

    def __init__(self, config: NoisyParensConfig | None = None, **kwargs):
        """Initialize the noisy parentheses renderer.

        Args:
            config: Configuration for bracket substitution. If None,
                   uses default config with all bracket pairs and
                   mismatched=False.
            **kwargs: Override config values (e.g., mismatched=True).
                     Useful for CLI usage with inspect eval -T mismatched=true
        """
        self.config = config or NoisyParensConfig()
        merge_config_kwargs(self.config, kwargs)

    @property
    def name(self) -> str:
        return "noisy_parens"

    def render(self, form_string: str, rng: random.Random | None = None) -> RenderedForm:
        """Render a form string with noisy bracket substitution.

        Args:
            form_string: The input form string (using standard parentheses)
            rng: Random instance for reproducibility. If None, uses default.

        Returns:
            RenderedForm with substituted brackets and metadata containing
            the substitution map (depth -> (open, close) pair).
        """
        if rng is None:
            rng = random.Random()

        if not form_string:
            return RenderedForm(
                original=form_string,
                rendered=form_string,
                renderer_name=self.name,
                metadata={"substitution_map": {}},
            )

        # Track substitution choices per depth level
        depth_to_brackets: dict[int, tuple[str, str]] = {}
        current_depth = 0
        result = []

        for char in form_string:
            if char == "(":
                if self.config.mismatched:
                    # Choose independent open bracket
                    open_bracket = rng.choice(self.config.bracket_pairs)[0]
                    result.append(open_bracket)
                else:
                    # Assign consistent pair for this depth if not yet assigned
                    if current_depth not in depth_to_brackets:
                        depth_to_brackets[current_depth] = rng.choice(self.config.bracket_pairs)
                    result.append(depth_to_brackets[current_depth][0])
                current_depth += 1

            elif char == ")":
                current_depth -= 1
                if self.config.mismatched:
                    # Choose independent close bracket
                    close_bracket = rng.choice(self.config.bracket_pairs)[1]
                    result.append(close_bracket)
                else:
                    # Use the matching close bracket for this depth
                    result.append(depth_to_brackets[current_depth][1])

            else:
                # Preserve any other characters (though standard forms shouldn't have any)
                result.append(char)

        # Build metadata
        metadata = {
            "substitution_map": {
                str(depth): {"open": pair[0], "close": pair[1]}
                for depth, pair in depth_to_brackets.items()
            },
            "mismatched": self.config.mismatched,
        }

        return RenderedForm(
            original=form_string,
            rendered="".join(result),
            renderer_name=self.name,
            metadata=metadata,
        )
