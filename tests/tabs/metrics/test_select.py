"""Behavioral contracts for ``floodlight_gui.tabs.metrics.select``.

The select layer answers two questions for the rest of the metrics tab: which
metric descriptor is active (display-name <-> registry-key lookup) and what
(period, team) data slice the Step-1 combos currently select. The registry is
real (the three shipped metrics); the DPG toolkit is the seam and is replaced
with the shared fake-DPG stub.

Behavioral contracts guarded here
---------------------------------
key_for_display
  C1  Returns the registry key whose descriptor ``display_name`` matches the
      given display string.
  C2  Returns None when no descriptor carries that display name.

_step1_scope (the Step-1 period/team scope resolver)
  C3  Translates the two combo values into a
      (filter_period, period_internal, filter_team, raw_team) tuple: each
      filter flag is True only when its axis names a concrete value, and False
      for the "All" sentinel or a cold-start missing combo.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.metrics.select as select
from floodlight_gui.registry.metrics import METRICS_REGISTRY
from floodlight_gui.tabs._shared.broadcast import ALL_SENTINEL
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def dpg_combos(monkeypatch):
    """Install the fake DPG stub on the select module and seed combo values.

    Returns a callable ``(period_value, team_value)`` that registers the two
    Step-1 combo tags with the given values, or omits a tag entirely when its
    value is ``None`` (simulating a cold-start missing combo).

    Returns
    -------
    callable
        ``install(period_value, team_value) -> stub``.
    """

    def _install(period_value, team_value):
        values: dict = {}
        existing: set = set()
        if period_value is not None:
            values["metrics_period_combo"] = period_value
            existing.add("metrics_period_combo")
        if team_value is not None:
            values["metrics_team_combo"] = team_value
            existing.add("metrics_team_combo")
        stub = make_dpg_stub(values=values, existing_items=existing)
        monkeypatch.setattr(select, "dpg", stub)
        return stub

    return _install


# --------------------------------------------------------------------------- #
# key_for_display
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key",
    list(METRICS_REGISTRY.keys()),
)
def test_key_for_display_round_trips_every_registered_metric(key):
    """C1: each metric's display name resolves back to its own registry key."""
    display = METRICS_REGISTRY[key]["display_name"]
    assert select.key_for_display(display) == key


def test_key_for_display_unknown_returns_none():
    """C2: a display name no descriptor carries resolves to None."""
    assert select.key_for_display("No Such Metric") is None


# --------------------------------------------------------------------------- #
# _step1_scope
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "period_value, team_value, expected",
    [
        # Both axes "All": no filtering, internal period is None.
        (ALL_SENTINEL, ALL_SENTINEL, (False, None, False, ALL_SENTINEL)),
        # Concrete period + concrete team: both axes filter.
        ("First Half", "Home", (True, "firstHalf", True, "Home")),
        # Period "All", team concrete: only the team axis filters.
        (ALL_SENTINEL, "Away", (False, None, True, "Away")),
        # Cold start (both combos absent): defaults to "All" / no filter.
        (None, None, (False, None, False, ALL_SENTINEL)),
        # Empty-string team is treated as no filter.
        ("First Half", "", (True, "firstHalf", False, "")),
    ],
)
def test_step1_scope_resolves_filter_flags_and_internal_period(
    dpg_combos, period_value, team_value, expected
):
    """C3: combo values map to (filter_period, period_internal, filter_team, raw_team).

    A filter flag is True only when its axis names a concrete value; the "All"
    sentinel, an empty string, or a missing combo all disable the filter and the
    period axis yields a None internal key.
    """
    dpg_combos(period_value, team_value)
    assert select._step1_scope() == expected
