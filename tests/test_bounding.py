"""Unit tests for output bounding (clamp_lines)."""

import pytest

from infra_mcp.ssh import MAX_LINES, clamp_lines


@pytest.mark.parametrize("n", [1, 5, 50, 199, 200])
def test_values_at_or_below_max_pass_through(n):
    assert clamp_lines(n) == n


@pytest.mark.parametrize("n", [201, 500, 999, 100000])
def test_values_above_max_clamped(n):
    assert clamp_lines(n) == MAX_LINES


def test_custom_max():
    assert clamp_lines(50, max=10) == 10
    assert clamp_lines(5, max=10) == 5


def test_below_one_floored_to_one():
    assert clamp_lines(0) == 1
    assert clamp_lines(-3) == 1
