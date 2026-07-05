"""Axioms explainer: I1 (calling) and I2 (crossing).

Uses the CMY colour-cue pedagogy from `demo/13--visual-transforms--colour.png`
(copied to this package's static assets as `axioms-cmy.png`): cyan/magenta/
yellow washes mark the same subform across different surface renderings, to
make the point that containment is the invariant and everything else is a
distractor. These colour cues are pedagogy for this page only -- models
never see them, per the rendering-architecture note.
"""

from __future__ import annotations

from fasthtml.common import H2, Div, Img, P, Pre

from lofsite import layout


def axioms_page():
    return layout.page(
        "Axioms",
        "/axioms",
        P(
            "Laws of Form (Spencer-Brown) defines a minimal calculus on a single "
            "distinction, the mark. Two axioms reduce every ground expression to "
            "either marked (",
            Pre("()", style="display:inline; padding:0 0.3em;"),
            ") or unmarked (void).",
        ),
        H2("I1 -- Calling"),
        P(
            Pre("()() -> ()", style="display:inline; padding:0 0.3em;"),
            ". Adjacent marks at the same level condense to one. Calling a "
            "distinction twice is the same as calling it once.",
        ),
        H2("I2 -- Crossing"),
        P(
            Pre("(()) -> void", style="display:inline; padding:0 0.3em;"),
            ". A mark nested directly inside another cancels to void. "
            "Crossing a boundary twice returns you to where you started.",
        ),
        H2("The same shape, many surfaces"),
        P(
            "Every dialect in the sandbox re-presents the same containment "
            "structure -- parenthood, enclosure, being above or below. The "
            "colours below are pedagogy, not part of any stimulus a model "
            "sees: cyan, magenta and yellow mark the same subform across "
            "nine visual families (paths, blocks, parens, enclosure, trees, "
            "graph, map, and rooms), so the eye can verify that containment "
            "is the invariant and everything else -- colour, style, notation "
            "-- is a distractor."
        ),
        Div(
            Img(src="/axioms-cmy.png", alt="CMY colour-cue pedagogy across nine visual families"),
            style="margin-top: 1rem;",
        ),
    )
