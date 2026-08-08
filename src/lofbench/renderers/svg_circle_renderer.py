"""SVG-based circle renderer for Laws of Form expressions with proper containment."""

from __future__ import annotations

import math
import urllib.parse
from dataclasses import dataclass
from typing import Literal

from .base import FormRenderer, RenderedForm, merge_config_kwargs


@dataclass
class SVGCircleConfig:
    """Configuration for SVG circle rendering."""

    canvas_size: int = 512
    background_color: str = "white"
    stroke_color: str = "black"
    stroke_width: float = 3.0  # Increased from 2 for better visibility
    padding: float = 0.05  # DEPRECATED: no longer used with radial layout
    fill_style: Literal["none", "alternating"] = "none"
    min_radius: float = 0.02  # Minimum readable radius (fraction of viewport)


class SVGCircleRenderer(FormRenderer):
    """Renders LoF expressions as SVG concentric circles with proper containment.

    Legacy renderer, superseded by ``lofbench.renderers.archetypes.enclosure``
    (DB-4 M5) as the "circle" registry key's active implementation. Kept
    unmodified and importable -- no renderer is deleted until parity is
    tested -- but no longer wired to any registry key.

    Uses a hand-rolled radial packer (no circlify or other packing
    dependency, despite what an earlier version of this docstring claimed).
    """

    def __init__(self, config: SVGCircleConfig | None = None, **kwargs):
        self.config = config or SVGCircleConfig()
        merge_config_kwargs(self.config, kwargs)

    @property
    def name(self) -> str:
        return "svg_circle"

    def render(self, form_string: str, rng=None) -> RenderedForm:
        """Render form as SVG concentric circles.

        Args:
            form_string: Canonical form string (e.g., "(())" or "()[()]")
            rng: Random (unused, for signature compatibility)

        Returns:
            RenderedForm with SVG data URL
        """
        # Parse structure
        structure = self._parse_structure(form_string)

        # Calculate layout with custom algorithm
        circles = self._calculate_layout(structure, cx=0.0, cy=0.0, r=0.9, depth=0)

        # Generate SVG
        svg = self._generate_svg(circles)

        # Convert to data URL (URL-encoded for safety)
        svg_encoded = urllib.parse.quote(svg)
        data_url = f"data:image/svg+xml;charset=utf-8,{svg_encoded}"

        return RenderedForm(
            original=form_string,
            rendered=data_url,
            renderer_name=self.name,
            metadata={
                "format": "image",
                "encoding": "svg",
                "fill_style": self.config.fill_style,
            },
        )

    def _parse_structure(self, form_string: str) -> list:
        """Parse form string into nested list structure.

        Args:
            form_string: String like "(())" or "()[()]"

        Returns:
            Nested list representing structure
        """
        stack = [[]]
        for char in form_string:
            if char == "(":
                new_level = []
                stack[-1].append(new_level)
                stack.append(new_level)
            elif char == ")":
                if len(stack) > 1:
                    stack.pop()
        return stack[0]

    def _calculate_scale(self, n_children: int, depth: int) -> float:
        """Calculate adaptive scale factor based on number of children and depth.

        Args:
            n_children: Number of sibling circles
            depth: Current depth in the structure

        Returns:
            Scale factor to apply (child_r = parent_r * scale)

        Notes:
            AGGRESSIVE MODE: Favors inner detail over outer prominence.
            - More children → smaller scale to fit siblings
            - Deeper nesting → slight boost to maintain readability
        """
        if n_children == 0:
            return 0.0
        elif n_children == 1:
            return 0.70  # Single child: tighter concentric nesting
        else:
            # Aggressive scaling: outer circles compressed more
            # Stronger sqrt coefficient + lower base
            base_scale = 0.75 / (1 + 0.4 * math.sqrt(n_children))

            # Depth boost: help maintain inner visibility
            depth_boost = min(0.15, depth * 0.03)

            return min(0.70, base_scale + depth_boost)

    def _calculate_child_position_radial(
        self,
        i: int,
        n_children: int,
        parent_cx: float,
        parent_cy: float,
        parent_r: float,
        child_r: float,
    ) -> tuple[float, float]:
        """Position child i of n_children radially around parent center.

        Args:
            i: Index of this child (0-indexed)
            n_children: Total number of siblings
            parent_cx, parent_cy: Parent circle center
            parent_r: Parent circle radius
            child_r: Child circle radius

        Returns:
            (cx, cy) position for this child

        Notes:
            Children arranged in a circle for efficient 2D space usage:
            - 1 child: centered
            - 2 children: opposite sides (left/right)
            - 3+ children: evenly distributed around circle
        """
        if n_children == 1:
            return (parent_cx, parent_cy)  # centered

        # Angle for this child (distribute evenly around circle)
        angle = 2 * math.pi * i / n_children

        # Distance from parent center (keep children inside parent)
        # Leave margin: use (parent_r - child_r) * 0.85 as radius
        ring_radius = (parent_r - child_r) * 0.85

        # Calculate position
        child_cx = parent_cx + ring_radius * math.cos(angle)
        child_cy = parent_cy + ring_radius * math.sin(angle)

        return (child_cx, child_cy)

    def _enforce_minimum_radius(self, circles: list[dict]) -> list[dict]:
        """Ensure all circles meet minimum readable size.

        Args:
            circles: List of circle dictionaries with 'r', 'cx', 'cy' keys

        Returns:
            Modified circles list with minimum size enforced

        Notes:
            If any circle is smaller than config.min_radius, scales up
            the entire layout proportionally to meet the constraint.
        """
        if not circles:
            return circles

        min_circle_r = min(c["r"] for c in circles)

        if min_circle_r < self.config.min_radius:
            scale_factor = self.config.min_radius / min_circle_r
            # Scale all circles and positions
            for circle in circles:
                circle["r"] *= scale_factor
                circle["cx"] *= scale_factor
                circle["cy"] *= scale_factor

        return circles

    def _calculate_layout(
        self, structure: list, cx: float, cy: float, r: float, depth: int
    ) -> list[dict]:
        """Calculate circle layout with proper containment and space efficiency.

        Uses adaptive scaling and radial distribution for optimal space usage.

        Args:
            structure: Nested list structure
            cx, cy: Center coordinates of parent
            r: Radius of parent
            depth: Current depth

        Returns:
            List of circle dictionaries with {cx, cy, r, depth}
        """
        circles = []

        n_children = len(structure)
        if n_children == 0:
            return circles

        # Adaptive scaling based on branching factor and depth
        scale = self._calculate_scale(n_children, depth)
        child_r = r * scale

        # For radial layout, ensure children don't overlap
        # Angular constraint: arc length between centers must be >= 2*child_r
        if n_children > 1:
            # Maximum radius that allows radial placement without overlap
            # ring_radius * (2π / n) >= 2 * child_r
            # ring_radius >= n * child_r / π
            # We want ring_radius = (r - child_r) * margin
            # So: (r - child_r) * margin >= n * child_r / π
            # Solve for child_r: child_r <= r * margin / (margin + n/π)
            margin = 0.85
            max_child_r_radial = r * margin / (margin + n_children / math.pi)

            # Take the minimum of scale-based and radial constraint
            child_r = min(child_r, max_child_r_radial)

        for i, item in enumerate(structure):
            if isinstance(item, list):
                # Calculate position using radial distribution
                child_cx, child_cy = self._calculate_child_position_radial(
                    i, n_children, cx, cy, r, child_r
                )

                # Add this circle
                circles.append({
                    "cx": child_cx,
                    "cy": child_cy,
                    "r": child_r,
                    "depth": depth + 1,
                })

                # Recurse for nested children
                if len(item) > 0:
                    circles.extend(
                        self._calculate_layout(item, child_cx, child_cy, child_r, depth + 1)
                    )

        # Enforce minimum size constraint (only at root level)
        if depth == 0:
            circles = self._enforce_minimum_radius(circles)

        return circles

    def _generate_svg(self, circles: list[dict]) -> str:
        """Generate SVG string from circle layout.

        Args:
            circles: List of circle dictionaries with {cx, cy, r, depth}

        Returns:
            SVG markup as string
        """
        size = self.config.canvas_size

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="-1.1 -1.1 2.2 2.2">',
            '<rect x="-1.1" y="-1.1" width="2.2" height="2.2" '
            f'fill="{self.config.background_color}"/>',
        ]

        # Sort circles by depth (draw deeper circles first so they appear behind)
        sorted_circles = sorted(circles, key=lambda c: c["depth"])

        for circle in sorted_circles:
            depth = circle["depth"]

            # Determine fill based on style
            if self.config.fill_style == "alternating":
                # Depth 1 = black (first mark), depth 2 = white, etc.
                fill_color = "black" if depth % 2 == 1 else "white"
            else:
                fill_color = "none"

            svg_parts.append(
                f'<circle cx="{circle["cx"]}" cy="{circle["cy"]}" r="{circle["r"]}" '
                f'fill="{fill_color}" '
                f'stroke="{self.config.stroke_color}" '
                f'stroke-width="{self.config.stroke_width / size}"/>'
            )

        svg_parts.append("</svg>")
        return "".join(svg_parts)
