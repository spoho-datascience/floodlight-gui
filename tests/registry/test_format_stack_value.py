"""Behavioral contracts for ``registry.transforms.format_stack_param_value``.

This is the pure formatter that renders one applied-op param value for the
transforms results (op-stack) panel. Its job is narrow: round top-level floats
to three decimals to suppress floating-point noise that in-place XY math leaves
behind (e.g. ``XY.translate`` storing ``1.0000000000000119``), and fall through
to ``str()`` for everything else. The function is pure, so the tests call it
directly with the value shapes real op params actually produce.

Behavioral contracts guarded here
---------------------------------
format_stack_param_value
  C1  A top-level float is rounded to three decimal places and rendered via
      f-string, so floating-point noise collapses (``1.0000000000000119`` ->
      ``"1.0"``) and a genuine fraction keeps three digits (``1/3`` ->
      ``"0.333"``).
  C2  Every non-float value falls through to ``str()`` verbatim: ints, None,
      bools, strings, and the sequence shapes (``shift`` tuples, ``xIDs``
      lists) that op params carry. Floats nested inside a sequence are NOT
      rounded, because only the top-level value is type-checked.
"""

from __future__ import annotations

import pytest

from floodlight_gui.registry.transforms import format_stack_param_value

# --------------------------------------------------------------------------- #
# C1 -- top-level floats are rounded to three decimals                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value, expected",
    [
        (1.0, "1.0"),  # already clean
        (1.0000000000000119, "1.0"),  # in-place-math noise collapses
        (1 / 3, "0.333"),  # genuine fraction keeps three digits
        (0.5, "0.5"),  # exact half is unchanged
        (0.123456, "0.123"),  # rounds to three places
    ],
)
def test_top_level_float_rounded_to_three_places(value, expected):
    """C1: a top-level float is rounded to three decimals before rendering."""
    assert format_stack_param_value(value) == expected


# --------------------------------------------------------------------------- #
# C2 -- non-floats fall through to str() verbatim                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value, expected",
    [
        (2, "2"),  # int (e.g. order, n_iter)
        (None, "None"),  # blank-leaning param (e.g. max_gap, noise)
        (True, "True"),  # bool (e.g. remove_short_seqs)
        (False, "False"),
        ("central", "central"),  # enum (e.g. difference, axis)
        ((1.0, 2.0), "(1.0, 2.0)"),  # shift tuple: inner floats NOT rounded
        ([1, 2], "[1, 2]"),  # xIDs list
        ((0.1234567, 9.0), "(0.1234567, 9.0)"),  # nested float left intact
    ],
)
def test_non_float_falls_through_to_str(value, expected):
    """C2: non-float values render via str(); nested floats are not rounded."""
    assert format_stack_param_value(value) == expected
