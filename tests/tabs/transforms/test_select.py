"""Behavioral contracts for ``tabs.transforms.select``.

The selection layer turns the period/team combos and the category tab_bar
into the answers the apply/undo/reset callbacks need: the active op key, the
broadcast-or-single scope, and the expanded (period, team) leaf lists. DPG is
the seam (the combos and the tab_bar live in the DPG tree); it is stubbed via
``make_dpg_stub`` so the tests assert this module's own resolution decisions,
never DPG's behavior. The app and the broadcast/period helpers it calls are
trusted; only their inputs from this module are asserted.

Behavioral contracts guarded here
---------------------------------
_get_active_op_key
  C1  Returns the active category's current op when the tab_bar exists and its
      active tag resolves to a real category.

_resolve_scope
  C4  A specific period and team is single scope; period "All" and team "All"
      each broadcast and expand to the full division / team lists.
  C7  "All" detection reads the RAW combo value before bridging, so a broadcast
      is never collapsed into an empty single leaf.

_get_target
  C8  Bridges the period combo to its internal key and returns it with the raw
      team value; "All" bridges to None.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.transforms.results as results
import floodlight_gui.tabs.transforms.select as select
import floodlight_gui.tabs.transforms.state as state
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install a fake DPG into ``select`` (and ``results``) and return it.

    ``refresh_transforms_display`` calls into ``results._refresh_stack_display``,
    so the results module must share the same recorder to stay DPG-free.

    Returns
    -------
    SimpleNamespace
        The recorder produced by ``make_dpg_stub``; seed ``.values`` and
        ``.existing_items`` to model combo and tab_bar reads.
    """
    stub = make_dpg_stub()
    monkeypatch.setattr(select, "dpg", stub)
    monkeypatch.setattr(results, "dpg", stub)
    return stub


@pytest.fixture
def bound_app(monkeypatch, app_double):
    """Bind the shared app double onto the transforms tab state.

    Returns
    -------
    _AppDouble
        The app double now visible as ``state.app_instance`` to ``select``.
    """
    monkeypatch.setattr(state, "app_instance", app_double)
    return app_double


# --------------------------------------------------------------------------- #
# _get_active_op_key                                                            #
# --------------------------------------------------------------------------- #


def test_active_op_key_reads_active_category(dpg_stub):
    """C1: the op key comes from the active category resolved off the tab_bar."""
    dpg_stub.existing_items.add("transforms_category_tab_bar")
    dpg_stub.values["transforms_category_tab_bar"] = "transforms_category_spatial_tab"
    assert select._get_active_op_key() == "subtract_centroid"


# --------------------------------------------------------------------------- #
# _resolve_scope                                                                #
# --------------------------------------------------------------------------- #


def _seed_combos(stub, period, team):
    """Seed both selector combos with the given raw values."""
    stub.existing_items.update({"transforms_period_combo", "transforms_team_combo"})
    stub.values["transforms_period_combo"] = period
    stub.values["transforms_team_combo"] = team


@pytest.mark.parametrize(
    "period, team, exp_broadcast, exp_period_internal, exp_team, exp_periods_key, exp_teams_key",
    [
        # Specific period + team: single scope, one bridged leaf each side.
        ("First Half", "Home", False, "firstHalf", "Home", "single_period", "single_team"),
        # Period "All": broadcast, periods expand to every division, period_internal None.
        ("All", "Home", True, None, "Home", "all_periods", "single_team"),
        # Team "All": broadcast, teams expand to every team name (Ball included), team None.
        ("First Half", "All", True, "firstHalf", None, "single_period", "all_teams"),
    ],
    ids=["specific", "period-All", "team-All"],
)
def test_resolve_scope(
    dpg_stub,
    bound_app,
    period,
    team,
    exp_broadcast,
    exp_period_internal,
    exp_team,
    exp_periods_key,
    exp_teams_key,
):
    """C4: scope resolution across specific / period-All / team-All picks.

    Single scope reports ``is_broadcast=False`` with a one-leaf expansion; an
    "All" pick on either axis flips ``is_broadcast`` and expands that axis to
    the app's full division / team list while collapsing its single value to
    None.
    """
    _seed_combos(dpg_stub, period, team)
    expansions = {
        "single_period": ["firstHalf"],
        "all_periods": bound_app.get_temporal_divisions(),
        "single_team": ["Home"],
        "all_teams": bound_app.get_team_names(),
    }
    is_broadcast, period_internal, team_out, periods, teams = select._resolve_scope()
    assert is_broadcast is exp_broadcast
    assert period_internal == exp_period_internal
    assert team_out == exp_team
    assert periods == expansions[exp_periods_key]
    assert teams == expansions[exp_teams_key]


def test_resolve_scope_detects_all_before_bridging(dpg_stub, bound_app):
    """C7: "All" is detected on the raw value, not collapsed into an empty leaf.

    Bridging "All" first would yield ``period_internal=None`` and a single-leaf
    teams list, silently no-opping the broadcast. The scope must instead report
    broadcast with the full team expansion.
    """
    _seed_combos(dpg_stub, "First Half", "All")
    is_broadcast, _period, _team, _periods, teams = select._resolve_scope()
    assert is_broadcast is True
    assert len(teams) == len(bound_app.get_team_names())


# --------------------------------------------------------------------------- #
# _get_target                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "period_value, expected_internal",
    [
        ("First Half", "firstHalf"),
        ("All", None),
    ],
)
def test_get_target_bridges_period(dpg_stub, period_value, expected_internal):
    """C8: the period combo is bridged to its internal key; "All" bridges to None."""
    _seed_combos(dpg_stub, period_value, "Away")
    period_internal, team = select._get_target()
    assert period_internal == expected_internal
    assert team == "Away"
