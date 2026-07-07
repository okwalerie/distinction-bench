"""Tests for the shared platform-stable geometry helpers.

See ``.lattice/plans/task_01KWQKYTW4ZVFG87K6K5Z9BTWJ.md``, "Shared
infrastructure this task adds", and its risk note: ``stable_trig`` needs
its own correctness tests against ``math``, on top of the
platform-stability property, which is about bit-identical repeatability,
not accuracy against ``math``.
"""

from __future__ import annotations

import math
from decimal import Decimal
from fractions import Fraction

import pytest

from lofbench.renderers.archetypes._geometry import (
    interval_subset,
    interval_subset_3d,
    polar_to_cartesian,
    rect_subset,
    stable_cos,
    stable_sin,
    stable_sqrt,
    turn_to_radians,
)


class TestStableTrigAccuracy:
    """Correctness against ``math``, a wide tolerance -- accuracy is a
    separate property from bit-identical repeatability."""

    @pytest.mark.parametrize("degrees", [0, 30, 45, 60, 90, 137, 180, 225, 270, 300, 359])
    def test_sin_matches_math_within_tolerance(self, degrees):
        # Same underlying angle value fed to both -- this isolates the
        # Taylor-series-vs-libm algorithm difference from any error in
        # deriving the angle itself.
        theta_float = math.radians(degrees)
        got = float(stable_sin(Decimal(theta_float)))
        expected = math.sin(theta_float)
        assert got == pytest.approx(expected, abs=1e-12)

    @pytest.mark.parametrize("degrees", [0, 30, 45, 60, 90, 137, 180, 225, 270, 300, 359])
    def test_cos_matches_math_within_tolerance(self, degrees):
        theta_float = math.radians(degrees)
        got = float(stable_cos(Decimal(theta_float)))
        expected = math.cos(theta_float)
        assert got == pytest.approx(expected, abs=1e-12)

    def test_sqrt_matches_math(self):
        for value in [2, 3, 10, 0.5, 144]:
            assert float(stable_sqrt(Decimal(str(value)))) == pytest.approx(
                math.sqrt(value), abs=1e-12
            )


class TestStableTrigDeterminism:
    """Bit-identical repeatability -- the actual platform-stability
    guarantee, distinct from accuracy against ``math``."""

    def test_sin_is_bit_identical_across_calls(self):
        theta = Decimal("1.23456789")
        assert stable_sin(theta) == stable_sin(theta)

    def test_cos_is_bit_identical_across_calls(self):
        theta = Decimal("2.71828182")
        assert stable_cos(theta) == stable_cos(theta)

    def test_polar_to_cartesian_is_bit_identical_across_calls(self):
        a = polar_to_cartesian(Decimal("100"), Fraction(1, 3))
        b = polar_to_cartesian(Decimal("100"), Fraction(1, 3))
        assert a == b


class TestAngleReduction:
    def test_turn_to_radians_full_turn_is_two_pi(self):
        from lofbench.renderers.archetypes._geometry import TWO_PI

        assert turn_to_radians(Fraction(1, 1)) == TWO_PI

    def test_sin_handles_angles_outside_reduced_range(self):
        # An angle several turns outside (-pi, pi] must reduce to the same
        # value (within the Decimal context's own precision budget -- a
        # larger-magnitude input before reduction retains fewer significant
        # fractional digits, so this compares within a tolerance far
        # tighter than any float/libm drift, not bit-for-bit) as its
        # already-small equivalent.
        from lofbench.renderers.archetypes._geometry import TWO_PI

        base = Decimal("0.7")
        big = base + TWO_PI * 5
        assert abs(stable_sin(big) - stable_sin(base)) < Decimal("1e-20")
        assert abs(stable_cos(big) - stable_cos(base)) < Decimal("1e-20")


class TestRectSubset:
    def test_true_when_strictly_inside(self):
        parent = (Fraction(0), Fraction(0), Fraction(10), Fraction(10))
        child = (Fraction(2), Fraction(2), Fraction(8), Fraction(8))
        assert rect_subset(child, parent) is True

    def test_false_when_exceeds_parent(self):
        parent = (Fraction(0), Fraction(0), Fraction(10), Fraction(10))
        child = (Fraction(2), Fraction(2), Fraction(11), Fraction(8))
        assert rect_subset(child, parent) is False

    def test_touching_boundary_passes_with_zero_margin(self):
        parent = (Fraction(0), Fraction(0), Fraction(10), Fraction(10))
        child = (Fraction(0), Fraction(0), Fraction(10), Fraction(10))
        assert rect_subset(child, parent, margin=Fraction(0)) is True

    def test_tight_case_does_not_silently_pass_a_near_miss(self):
        """A child that exceeds the parent by an arbitrarily small exact
        Fraction still fails -- no epsilon is smuggled in."""
        parent = (Fraction(0), Fraction(0), Fraction(10), Fraction(10))
        child = (Fraction(0), Fraction(0), Fraction(10, 1) + Fraction(1, 10**9), Fraction(10))
        assert rect_subset(child, parent) is False

    def test_margin_shrinks_parent(self):
        parent = (Fraction(0), Fraction(0), Fraction(10), Fraction(10))
        child = (Fraction(1), Fraction(1), Fraction(9), Fraction(9))
        assert rect_subset(child, parent, margin=Fraction(2)) is False
        assert rect_subset(child, parent, margin=Fraction(1)) is True


class TestIntervalSubset:
    def test_true_when_strictly_inside(self):
        assert interval_subset((Fraction(2), Fraction(8)), (Fraction(0), Fraction(10))) is True

    def test_false_when_exceeds_parent(self):
        assert interval_subset((Fraction(-1), Fraction(8)), (Fraction(0), Fraction(10))) is False

    def test_tight_case_grandparent_to_grandchild_margin(self):
        parent = (Fraction(0), Fraction(10))
        just_inside = (Fraction(0), Fraction(10))
        just_outside = (Fraction(0), Fraction(10) + Fraction(1, 1000))
        assert interval_subset(just_inside, parent) is True
        assert interval_subset(just_outside, parent) is False


class TestIntervalSubset3D:
    def _box(self, x, y, z):
        return (
            (Fraction(x[0]), Fraction(x[1])),
            (Fraction(y[0]), Fraction(y[1])),
            (Fraction(z[0]), Fraction(z[1])),
        )

    def test_all_three_axes_must_hold(self):
        parent = self._box((0, 10), (0, 10), (0, 10))
        good = self._box((1, 9), (1, 9), (1, 9))
        bad_z = self._box((1, 9), (1, 9), (1, 11))
        assert interval_subset_3d(good, parent) is True
        assert interval_subset_3d(bad_z, parent) is False
