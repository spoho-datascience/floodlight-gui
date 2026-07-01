"""Behavioral contracts for ``floodlight_gui.core.periods``.

This module owns the bidirectional period vocabulary between UI display
names ("First Half") and floodlight internal keys ("firstHalf"). Both
converters translate a recognised value and pass anything unrecognised
through unchanged so callers degrade gracefully instead of raising. The
functions are pure with no collaborators, so the tests call them directly.

Behavioral contracts guarded here
---------------------------------
period_display_to_internal
  C1  A recognised display name maps to its internal key. The lowercase
      ``str.lower()`` variants of the internal keys also resolve, since
      they arise from DPG tag construction.
  C2  An internal key passes through unchanged (idempotent on internal
      keys).
  C3  An unrecognised value is returned unchanged rather than raising.

period_internal_to_display
  C4  A recognised internal key maps to its display name.
  C5  A display name passes through unchanged (idempotent on display
      names).
  C6  An unrecognised value is returned unchanged rather than raising.
"""

from __future__ import annotations

import pytest

from floodlight_gui.core.periods import (
    period_display_to_internal,
    period_internal_to_display,
)

# --------------------------------------------------------------------------- #
# period_display_to_internal                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "display, internal",
    [
        ("First Half", "firstHalf"),
        ("Second Half", "secondHalf"),
        ("Full Match", "fullMatch"),
        # Lowercase variants produced by str.lower() on internal keys.
        ("firsthalf", "firstHalf"),
        ("secondhalf", "secondHalf"),
        ("fullmatch", "fullMatch"),
    ],
)
def test_display_to_internal_maps_known_names(display, internal):
    """C1: a recognised display name maps to its internal key."""
    assert period_display_to_internal(display) == internal


@pytest.mark.parametrize("internal", ["firstHalf", "secondHalf", "fullMatch"])
def test_display_to_internal_passes_internal_keys_through(internal):
    """C2: an internal key passes through unchanged."""
    assert period_display_to_internal(internal) == internal


@pytest.mark.parametrize("value", ["", "Overtime", "HT3", "random"])
def test_display_to_internal_passes_unknown_through(value):
    """C3: an unrecognised value is returned unchanged, not raised on."""
    assert period_display_to_internal(value) == value


# --------------------------------------------------------------------------- #
# period_internal_to_display                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "internal, display",
    [
        ("firstHalf", "First Half"),
        ("secondHalf", "Second Half"),
        ("fullMatch", "Full Match"),
    ],
)
def test_internal_to_display_maps_known_keys(internal, display):
    """C4: a recognised internal key maps to its display name."""
    assert period_internal_to_display(internal) == display


@pytest.mark.parametrize("display", ["First Half", "Second Half", "Full Match"])
def test_internal_to_display_passes_display_names_through(display):
    """C5: a display name passes through unchanged."""
    assert period_internal_to_display(display) == display


@pytest.mark.parametrize("value", ["", "Overtime", "HT3", "random"])
def test_internal_to_display_passes_unknown_through(value):
    """C6: an unrecognised value is returned unchanged, not raised on."""
    assert period_internal_to_display(value) == value
