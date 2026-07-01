"""Behavioral contracts for ``floodlight_gui.tabs.inspect.collect``.

This module is the inspect tab's data-collection layer. Each collector walks
the live app's read accessors into the typed tree the SECTIONS engine consumes,
or returns ``None`` when the relevant data is absent. The controls readers
(``_selected_event_types`` / ``_position_source``) and ``state.app_instance``
are the seams; both are stubbed so the tests assert only this module's own
shape-walk and lookup decisions, never DPG or floodlight internals.

Behavioral contracts guarded here
---------------------------------
_events_for
  C1  Returns an empty DataFrame when event_data is not a non-empty tuple, when
      the (period, team) cell is absent, or when the team object lacks an
      ``.events`` attribute (one absent-source contract).
  C2  An empty selection returns an empty DataFrame (no selection means show
      nothing).
  C3  A non-empty selection keeps only rows whose eID is in the selection and
      returns them sorted by gameclock.

_collect_event
  C4  Returns None when event_data is absent / not a non-empty tuple.
  C5  Builds ``{period: {team: DataFrame}}`` over every division x team, passing
      the active event-type selection through to the per-cell filter.

_collect_position
  C6  Returns None when no position data is loaded.
  C7  Grouped ``{period: {entity: data}}`` structures produce per-entity leaf
      dicts carrying entity / data / period_internal / source, restricted to
      teams actually present under each period.
  C8  A single-period flat ``{entity: data}`` structure is resolved against the
      one division.

_collect_team
  C9  Returns None when no teamsheet is loaded.
  C10 Builds ``{team: DataFrame}`` only for teams present in the teamsheet.

_team_df
  C11 Extracts the frame via ``.teamsheet`` > ``.data`` > a bare DataFrame,
      and returns None for an unrecognised shape.

_collect_code
  C12 Returns None when the data is absent / not a non-empty dict.
  C13 Keys Code objects by temporal division: a division present in the data
      resolves, one absent is dropped.

_resolve_xy
  C14 Post-stack source with an active XY returns the active array, its shape,
      and its framerate.
  C15 Falls back to the raw ``data.xy`` when the source is not post-stack, when
      ``state.app_instance`` is unset, or when no active XY exists.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import floodlight_gui.tabs.inspect.collect as collect
from floodlight_gui.tabs.inspect.collect import (
    _collect_code,
    _collect_event,
    _collect_position,
    _collect_team,
    _events_for,
    _resolve_xy,
    _team_df,
)

from .conftest import make_events_obj


@pytest.fixture
def stub_controls(monkeypatch):
    """Stub the controls readers the collectors call.

    Returns a setter ``(selected, source)`` that pins ``_selected_event_types``
    and ``_position_source`` so collector tests are independent of DPG widget
    state.
    """

    def _install(selected=None, source="pristine"):
        monkeypatch.setattr(collect, "_selected_event_types", lambda: list(selected or []))
        monkeypatch.setattr(collect, "_position_source", lambda: source)

    return _install


def _event_data(period_team_dfs: dict) -> tuple:
    """Build a raw event_data tuple ``({period: {team: EventsObj}},)``."""
    tree: dict = {}
    for (period, team), df in period_team_dfs.items():
        tree.setdefault(period, {})[team] = make_events_obj(df)
    return (tree,)


# --------------------------------------------------------------------------- #
# _events_for                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "event_data, period, team",
    [
        (None, "First Half", "Home"),  # not a tuple
        ((), "First Half", "Home"),  # empty tuple
        (({"First Half": {}},), "First Half", "Home"),  # team absent
        (({"Other": {"Home": None}},), "First Half", "Home"),  # period absent
    ],
)
def test_events_for_absent_source_returns_empty(event_data, period, team):
    """C1: an absent or malformed source/cell yields an empty DataFrame."""
    result = _events_for(event_data, period, team, ["pass"])
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_events_for_no_events_attr_returns_empty():
    """C1: a team object without an ``.events`` attribute yields an empty df."""
    event_data = ({"First Half": {"Home": SimpleNamespace()}},)
    result = _events_for(event_data, "First Half", "Home", ["pass"])
    assert result.empty


def test_events_for_empty_selection_returns_empty():
    """C2: an empty selection returns an empty DataFrame regardless of rows."""
    df = pd.DataFrame({"eID": ["pass"], "gameclock": [1.0]})
    event_data = _event_data({("First Half", "Home"): df})
    result = _events_for(event_data, "First Half", "Home", [])
    assert result.empty


def test_events_for_filters_by_selection_and_sorts():
    """C3: selection keeps matching eIDs and orders rows by gameclock."""
    df = pd.DataFrame(
        {
            "eID": ["pass", "shot", "pass"],
            "gameclock": [30.0, 10.0, 20.0],
        }
    )
    event_data = _event_data({("First Half", "Home"): df})
    result = _events_for(event_data, "First Half", "Home", ["pass"])
    assert list(result["eID"]) == ["pass", "pass"]
    assert list(result["gameclock"]) == [20.0, 30.0]


# --------------------------------------------------------------------------- #
# _collect_event                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("event_data", [None, (), "notatuple"])
def test_collect_event_absent_returns_none(stub_controls, app_double, event_data):
    """C4: a missing or malformed event_data tuple collects to None."""
    stub_controls(selected=["pass"])
    app = app_double(event_data=event_data, divisions=["First Half"], teams=["Home"])
    assert _collect_event(app) is None


def test_collect_event_builds_period_team_tree(stub_controls, app_double):
    """C5: the collector nests every division x team and applies the filter."""
    df = pd.DataFrame({"eID": ["pass", "shot"], "gameclock": [1.0, 2.0]})
    event_data = _event_data(
        {
            ("First Half", "Home"): df,
            ("First Half", "Away"): df,
        }
    )
    stub_controls(selected=["pass"])
    app = app_double(event_data=event_data, divisions=["First Half"], teams=["Home", "Away"])
    tree = _collect_event(app)
    assert set(tree) == {"First Half"}
    assert set(tree["First Half"]) == {"Home", "Away"}
    # Filter applied: only the 'pass' row survives in each cell.
    assert list(tree["First Half"]["Home"]["eID"]) == ["pass"]


# --------------------------------------------------------------------------- #
# _collect_position                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("position_data", [None, [], {}, ()])
def test_collect_position_absent_returns_none(stub_controls, app_double, position_data):
    """C6: no position data collects to None."""
    stub_controls(source="pristine")
    app = app_double(position_data=position_data, divisions=["First Half"], teams=["Home"])
    assert _collect_position(app) is None


def test_collect_position_grouped_shape(stub_controls, app_double):
    """C7: grouped structure yields per-entity leaves with source/period tags."""
    structure = {
        "First Half": {"Home": "XY_HOME", "Away": "XY_AWAY"},
    }
    stub_controls(source="post_stack")
    app = app_double(
        position_data={"x": 1},
        position_structure=structure,
        divisions=["First Half"],
        teams=["Home", "Away", "Ball"],  # Ball absent from structure -> dropped
    )
    tree = _collect_position(app)
    assert set(tree["First Half"]) == {"Home", "Away"}
    home_leaf = tree["First Half"]["Home"]
    assert home_leaf == {
        "entity": "Home",
        "data": "XY_HOME",
        "period_internal": "First Half",
        "source": "post_stack",
    }


def test_collect_position_single_period_flat_shape(stub_controls, app_double):
    """C8: a single-period flat ``{entity: data}`` resolves against the division."""
    structure = {"Home": "XY_HOME", "Away": "XY_AWAY"}
    stub_controls(source="pristine")
    app = app_double(
        position_data={"x": 1},
        position_structure=structure,
        divisions=["First Half"],
        teams=["Home", "Away"],
    )
    tree = _collect_position(app)
    assert set(tree["First Half"]) == {"Home", "Away"}
    assert tree["First Half"]["Away"]["data"] == "XY_AWAY"


# --------------------------------------------------------------------------- #
# _collect_team                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("teamsheet", [None, {}, [], ()])
def test_collect_team_absent_returns_none(app_double, teamsheet):
    """C9: no teamsheet collects to None."""
    app = app_double(teamsheet=teamsheet, teams=["Home"])
    assert _collect_team(app) is None


def test_collect_team_builds_only_present_teams(app_double):
    """C10: only teams present in the teamsheet appear in the result."""
    home_df = pd.DataFrame({"player": ["A"]})
    teamsheet = {"Home": home_df}  # Away requested but absent
    app = app_double(teamsheet=teamsheet, teams=["Home", "Away"])
    out = _collect_team(app)
    assert set(out) == {"Home"}
    assert out["Home"] is home_df


# --------------------------------------------------------------------------- #
# _team_df                                                                      #
# --------------------------------------------------------------------------- #


def test_team_df_shape_dispatch():
    """C11: frame extraction prefers .teamsheet, then .data, then a bare df."""
    df = pd.DataFrame({"a": [1]})
    assert _team_df(SimpleNamespace(teamsheet=df)) is df
    assert _team_df(SimpleNamespace(data=df)) is df
    assert _team_df(df) is df
    assert _team_df(object()) is None


# --------------------------------------------------------------------------- #
# _collect_code                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("data", [None, {}, "notadict", []])
def test_collect_code_absent_returns_none(app_double, data):
    """C12: absent / non-dict / empty Code data collects to None."""
    collector = _collect_code(lambda app: data)
    app = app_double(divisions=["First Half"], teams=["Home"])
    assert collector(app) is None


def test_collect_code_keys_by_temporal_division(app_double):
    """C13: Code is keyed by the temporal division as stored.

    A division present in the data resolves to its Code object; a division
    absent from the data is dropped.
    """
    code_obj = SimpleNamespace(code=np.array([0, 1]))
    data = {"firstHalf": code_obj}
    collector = _collect_code(lambda app: data)
    app = app_double(divisions=["firstHalf", "secondHalf"], teams=["Home"])
    out = collector(app)
    assert set(out) == {"firstHalf"}
    assert out["firstHalf"] is code_obj


# --------------------------------------------------------------------------- #
# _resolve_xy                                                                   #
# --------------------------------------------------------------------------- #


def test_resolve_xy_post_stack_uses_active(monkeypatch):
    """C14: post-stack with an active XY returns its array/shape/framerate."""
    active = SimpleNamespace(xy=np.zeros((5, 4)), framerate=25.0)
    raw = SimpleNamespace(xy=np.ones((3, 4)), framerate=10.0)
    app = SimpleNamespace(get_active_xy=lambda period, entity: active)
    monkeypatch.setattr(collect.state, "app_instance", app)
    xy, shape, framerate = _resolve_xy("Home", raw, "post_stack", "firstHalf")
    assert xy is active.xy
    assert shape == (5, 4)
    assert framerate == 25.0


@pytest.mark.parametrize(
    "source, app_instance, active",
    [
        ("pristine", "SET", None),  # not post-stack
        ("post_stack", None, None),  # no app_instance
        ("post_stack", "SET", None),  # active XY missing
    ],
)
def test_resolve_xy_falls_back_to_raw(monkeypatch, source, app_instance, active):
    """C15: non-post-stack / no-app / no-active paths use the raw ``data.xy``."""
    raw = SimpleNamespace(xy=np.ones((3, 4)), framerate=10.0)
    if app_instance == "SET":
        app = SimpleNamespace(get_active_xy=lambda period, entity: active)
        monkeypatch.setattr(collect.state, "app_instance", app)
    else:
        monkeypatch.setattr(collect.state, "app_instance", None)
    xy, shape, framerate = _resolve_xy("Home", raw, source, "firstHalf")
    assert xy is raw.xy
    assert shape == (3, 4)
    assert framerate == 10.0


# --------------------------------------------------------------------------- #
# End-to-end collection flow: realistic store -> SECTIONS collect -> tree      #
# --------------------------------------------------------------------------- #
#
# Inspect has no executor; its "producer path" is data -> collected view. This
# drives the real SECTIONS descriptors' ``collect`` callables (the exact entry
# the engine invokes per section) against a multi-period, multi-team store and
# asserts the collected tree matches the input data: right periods, right teams,
# right rows under each cell, and the post-stack source tag threaded into the
# position leaves. A collection regression here means the user sees wrong data
# presented as correct -- the silent-and-corrupting failure this suite exists
# to catch.


def test_inspect_flow_collects_events_and_positions_matching_store(stub_controls, app_double):
    """Data-in -> correct-collected-out across the event and position sections.

    Builds a realistic two-period (First/Second Half), two-team (Home/Away)
    store with distinct event rows and XY objects per cell, then collects both
    sections through the live SECTIONS descriptors. Asserts: every (period,
    team) cell is present; each event cell carries exactly the selected rows for
    that cell (sorted by gameclock); each position leaf points at the right XY
    object and threads the active source.
    """
    from floodlight_gui.tabs.inspect.sections import _SECTION_BY_KEY

    # --- realistic event store: distinct rows per (period, team) cell -------- #
    fh_home = pd.DataFrame({"eID": ["pass", "shot"], "gameclock": [20.0, 10.0]})
    fh_away = pd.DataFrame({"eID": ["pass"], "gameclock": [5.0]})
    sh_home = pd.DataFrame({"eID": ["shot", "pass"], "gameclock": [3.0, 1.0]})
    sh_away = pd.DataFrame({"eID": ["tackle"], "gameclock": [2.0]})  # filtered out
    event_data = _event_data(
        {
            ("First Half", "Home"): fh_home,
            ("First Half", "Away"): fh_away,
            ("Second Half", "Home"): sh_home,
            ("Second Half", "Away"): sh_away,
        }
    )

    # --- realistic position store: one XY object per (period, entity) -------- #
    position_structure = {
        "First Half": {"Home": "XY_FH_HOME", "Away": "XY_FH_AWAY"},
        "Second Half": {"Home": "XY_SH_HOME", "Away": "XY_SH_AWAY"},
    }

    stub_controls(selected=["pass"], source="post_stack")
    app = app_double(
        event_data=event_data,
        position_data={"loaded": True},
        position_structure=position_structure,
        divisions=["First Half", "Second Half"],
        teams=["Home", "Away"],
    )

    # --- drive the real section collectors (the engine's per-section entry) -- #
    event_tree = _SECTION_BY_KEY["event"].collect(app)
    position_tree = _SECTION_BY_KEY["position"].collect(app)

    # Event section: right periods, right teams, right rows per cell.
    assert set(event_tree) == {"First Half", "Second Half"}
    assert set(event_tree["First Half"]) == {"Home", "Away"}
    assert set(event_tree["Second Half"]) == {"Home", "Away"}
    # First Half / Home kept both 'pass' rows, dropped the 'shot', sorted by clock.
    assert list(event_tree["First Half"]["Home"]["eID"]) == ["pass"]
    assert list(event_tree["First Half"]["Home"]["gameclock"]) == [20.0]
    assert list(event_tree["Second Half"]["Home"]["eID"]) == ["pass"]
    # The 'tackle'-only Away cell in the second half filters to empty.
    assert event_tree["Second Half"]["Away"].empty

    # Position section: right periods/entities, each leaf bound to its own XY,
    # carrying the post-stack source tag and its own internal period key.
    assert set(position_tree) == {"First Half", "Second Half"}
    sh_home_leaf = position_tree["Second Half"]["Home"]
    assert sh_home_leaf == {
        "entity": "Home",
        "data": "XY_SH_HOME",
        "period_internal": "Second Half",
        "source": "post_stack",
    }
    assert position_tree["First Half"]["Away"]["data"] == "XY_FH_AWAY"
