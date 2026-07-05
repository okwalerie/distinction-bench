"""Form renderers for transforming canonical forms into alternative representations.

This module provides a flexible system for rendering Laws of Form expressions
in various notations while preserving their structural meaning.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import FormRenderer, RenderedForm
from .canonical import CanonicalConfig, CanonicalRenderer
from .nested_list import NestedListConfig, NestedListRenderer
from .noisy_parens import BRACKET_PAIRS, NoisyParensConfig, NoisyParensRenderer
from .pipeline.composed import ComposedRenderer
from .pipeline.spec import DialectSpec
from .sexpr import PRESETS as SEXPR_PRESETS
from .sexpr import SExprConfig, SExprRenderer
from .svg_circle_renderer import SVGCircleConfig, SVGCircleRenderer

__all__ = [
    "FormRenderer",
    "RenderedForm",
    "CanonicalRenderer",
    "CanonicalConfig",
    "SVGCircleRenderer",
    "SVGCircleConfig",
    "NoisyParensRenderer",
    "NoisyParensConfig",
    "BRACKET_PAIRS",
    "NestedListRenderer",
    "NestedListConfig",
    "SExprRenderer",
    "SExprConfig",
    "SEXPR_PRESETS",
    "ComposedRenderer",
    "DialectSpec",
    "get_renderer",
    "register_renderer",
    "list_renderers",
]


# Registry mapping renderer names to a callable that returns a FormRenderer.
# Widened from type[FormRenderer] to Callable[..., FormRenderer]: a named
# composed dialect registers a factory function, not a class (see
# lofbench.renderers.pipeline.spec.make_named_dialect_factory).
_RENDERER_REGISTRY: dict[str, Callable[..., FormRenderer]] = {
    "canonical": CanonicalRenderer,
    "noisy_parens": NoisyParensRenderer,
    "circle": SVGCircleRenderer,
    "nested_list": NestedListRenderer,
    "sexpr": SExprRenderer,
    "composed": ComposedRenderer,
}


def get_renderer(name: str, **kwargs: Any) -> FormRenderer:
    """Get a renderer instance by name.

    Args:
        name: The name of the renderer to instantiate
        **kwargs: Additional keyword arguments to pass to the renderer constructor

    Returns:
        An instance of the requested renderer

    Raises:
        ValueError: If the renderer name is not registered

    Examples:
        >>> renderer = get_renderer("canonical")
        >>> renderer = get_renderer("noisy_parens", config=NoisyParensConfig(mismatched=True))
    """
    if name not in _RENDERER_REGISTRY:
        available = ", ".join(sorted(_RENDERER_REGISTRY.keys()))
        raise ValueError(f"Unknown renderer: {name!r}. Available renderers: {available}")

    renderer_cls = _RENDERER_REGISTRY[name]
    return renderer_cls(**kwargs)


def register_renderer(name: str, renderer: Callable[..., FormRenderer]) -> None:
    """Register a new renderer factory.

    Accepts any callable that returns a FormRenderer: a FormRenderer
    subclass (the common case), or a plain factory function such as the
    named-dialect factories the composed pipeline registers (see
    ``lofbench.renderers.pipeline.spec.make_named_dialect_factory``). The
    guard is a callability check, not an issubclass check, so a named
    factory goes through this guarded path rather than a raw dict write.

    Args:
        name: The unique name to register the renderer under
        renderer: A FormRenderer subclass, or a callable returning one

    Raises:
        TypeError: If renderer is not callable
        ValueError: If the name is already registered

    Examples:
        >>> class MyRenderer(FormRenderer):
        ...     @property
        ...     def name(self):
        ...         return "my_renderer"
        ...     def render(self, form_string, rng=None):
        ...         return RenderedForm(form_string, form_string, self.name, {})
        >>> register_renderer("my_renderer", MyRenderer)
    """
    if not callable(renderer):
        raise TypeError(f"renderer must be callable, got {type(renderer)}")

    if name in _RENDERER_REGISTRY:
        raise ValueError(f"Renderer {name!r} is already registered")

    _RENDERER_REGISTRY[name] = renderer


def list_renderers() -> list[str]:
    """List all registered renderer names.

    Returns:
        Sorted list of registered renderer names

    Examples:
        >>> list_renderers()
        ['canonical', 'noisy_parens']
    """
    return sorted(_RENDERER_REGISTRY.keys())
