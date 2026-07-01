"""Behavioral contracts for ``floodlight_gui.core.player_mapping``.

This module maps a loaded teamsheet plus an xy_dict into one ordered
``PlayerSlot`` per XY player column. The XY objects are minimal stand-ins
exposing a ``.xy`` ndarray of shape ``(T, 2N)``; teamsheets are the small
``{team: Teamsheet-like}`` dict the source reads. Tests assert the exact slot
fields the mapper produces (col_index order, xid/pid/jersey/position/name, and
the ball identifiers), never any analytics.

Behavioral contracts guarded here
----------------------------------
_ball_slots
  C1  ``n == 1`` keeps the legacy ``pid="ball"`` / ``name="Ball"`` identifiers
      with all other fields ``None``.
  C2  ``n > 1`` (the realistic EIGD-H multi-ball case) suffixes ``pid`` and
      ``name`` per slot so each is unique, with col_index ordered ``0..n-1``.

build_player_slots
  C3  Every team in ``xy_dict`` gets a list of length ``XY.N`` ordered by
      col_index, and teamsheet row ``i`` populates ``col_index=i`` with that
      row's xid/pid/jersey/position/name.
  C4  A teamsheet shorter than ``XY.N`` leaves the trailing slots with all
      ``None`` identifier fields (Kinexon-style partial roster).
  C5  A teamsheet longer than ``XY.N`` discards the extra rows; the list length
      stays ``XY.N``.
  C6  A missing teamsheet (``None`` overall, or no entry for the team) yields
      all-``None`` identifier fields for every slot.
  C7  A team named "ball" (case-insensitive) always gets ball slots regardless
      of any teamsheet entry for that name.
  C8  A DFL-nested ``{period: {team: XY}}`` xy_dict is flattened via the first
      period.
  C9  A team whose XY carries no players (``XY.N == 0`` or no ``.xy``) yields an
      empty slot list.
  C10 Identifier fields resolve through their fallback-key chains
      (jID/jersey_number/number for jersey, player/name for name).
  C11 A non-dict xy_dict yields an empty mapping (defensive guard).

teamsheet_columns_for
  C12 Returns the column labels across the named teams' real teamsheets in
      first-seen order, de-duplicated across teams; a missing/``None``
      teamsheet (or a team with no teamsheet) contributes nothing.

teamsheet_row_for
  C13 Resolves one player's row by col_index into verbatim non-null
      ``(column, value)`` pairs in column order; an out-of-range col_index,
      an unknown team, or a missing teamsheet yields ``[]``.

teamsheet_column_values
  C14 Extracts one column's stringified values indexed by col_index; a
      missing column or a team with no teamsheet yields ``[]``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from floodlight_gui.core.player_mapping import (
    PlayerSlot,
    _ball_slots,
    build_player_slots,
    teamsheet_column_values,
    teamsheet_columns_for,
    teamsheet_row_for,
)

# --------------------------------------------------------------------------- #
# Test doubles                                                                  #
# --------------------------------------------------------------------------- #


class _XY:
    """Minimal XY stand-in exposing a ``.xy`` ndarray of shape ``(T, 2N)``.

    The mapper only reads ``xy.xy.shape[1] // 2`` to size each team's slot
    list, so the array content is irrelevant; only its column count matters.
    """

    def __init__(self, n: int, t: int = 4):
        self.xy = np.zeros((t, 2 * n), dtype=float)


class _NoArrayXY:
    """XY-like object with no ``.xy`` attribute (sizes to zero players)."""


class _Teamsheet:
    """Wrapper exposing a DataFrame via ``.teamsheet`` (floodlight shape)."""

    def __init__(self, frame: pd.DataFrame):
        self.teamsheet = frame


def _ts(frame: pd.DataFrame) -> _Teamsheet:
    """Wrap a DataFrame in the floodlight Teamsheet-like container."""
    return _Teamsheet(frame)


def _roster(n: int) -> pd.DataFrame:
    """Build an ``n``-row teamsheet with every canonical identifier column."""
    return pd.DataFrame(
        {
            "xID": list(range(n)),
            "pID": [f"P{i:03d}" for i in range(n)],
            "jID": [str(10 + i) for i in range(n)],
            "player": [f"Player{i}" for i in range(n)],
            "position": ["MID"] * n,
        }
    )


# --------------------------------------------------------------------------- #
# _ball_slots                                                                   #
# --------------------------------------------------------------------------- #


def test_ball_slots_single_column_uses_legacy_identifiers():
    """C1: a one-column ball keeps the plain ``ball`` / ``Ball`` identifiers."""
    slots = _ball_slots(1)
    assert slots == [
        PlayerSlot(
            col_index=0,
            team="Ball",
            xid=None,
            pid="ball",
            jersey=None,
            position=None,
            name="Ball",
        )
    ]


def test_ball_slots_multi_column_suffixes_unique_identifiers():
    """C2: a multi-column ball gets unique suffixed pid/name per ordered slot.

    EIGD-H handball produces a three-column ball; downstream widgets need
    unique pids and labels, so each slot is suffixed by its index.
    """
    slots = _ball_slots(3)
    assert [s.col_index for s in slots] == [0, 1, 2]
    assert all(s.team == "Ball" for s in slots)
    assert [s.pid for s in slots] == ["ball_0", "ball_1", "ball_2"]
    assert [s.name for s in slots] == ["Ball 1", "Ball 2", "Ball 3"]


# --------------------------------------------------------------------------- #
# build_player_slots                                                            #
# --------------------------------------------------------------------------- #


def test_build_maps_each_teamsheet_row_to_its_column():
    """C3: row ``i`` populates ``col_index=i``; the list is length ``XY.N``.

    A full two-player roster maps cleanly onto a two-player XY, with the
    integer xID coerced to ``int`` and the remaining identifiers stringified.
    """
    slots = build_player_slots({"Home": _ts(_roster(2))}, {"Home": _XY(2)})["Home"]
    assert [s.col_index for s in slots] == [0, 1]
    assert slots[0] == PlayerSlot(
        col_index=0,
        team="Home",
        xid=0,
        pid="P000",
        jersey="10",
        position="MID",
        name="Player0",
    )
    assert slots[1].xid == 1
    assert slots[1].name == "Player1"


def test_build_short_teamsheet_pads_trailing_slots_with_none():
    """C4: a roster shorter than ``XY.N`` leaves trailing identifiers ``None``.

    A three-player XY with a one-row teamsheet keeps slot 0 populated and
    fills slots 1 and 2 with all-``None`` identifier fields.
    """
    slots = build_player_slots({"Home": _ts(_roster(1))}, {"Home": _XY(3)})["Home"]
    assert len(slots) == 3
    assert slots[0].name == "Player0"
    for s in slots[1:]:
        assert (s.xid, s.pid, s.jersey, s.position, s.name) == (None, None, None, None, None)
        assert s.col_index in (1, 2)
        assert s.team == "Home"


def test_build_long_teamsheet_discards_extra_rows():
    """C5: a roster longer than ``XY.N`` is truncated to ``XY.N`` slots."""
    slots = build_player_slots({"Home": _ts(_roster(5))}, {"Home": _XY(2)})["Home"]
    assert len(slots) == 2
    assert [s.col_index for s in slots] == [0, 1]


@pytest.mark.parametrize(
    "teamsheet",
    [
        None,  # no teamsheet loaded at all (Kinexon)
        {},  # teamsheet dict present but no entry for this team
        {"Other": None},  # entry exists for a different team only
    ],
)
def test_build_missing_teamsheet_yields_all_none_identifiers(teamsheet):
    """C6: a missing teamsheet leaves every slot's identifier fields ``None``."""
    slots = build_player_slots(teamsheet, {"Home": _XY(2)})["Home"]
    assert len(slots) == 2
    for s in slots:
        assert (s.xid, s.pid, s.jersey, s.position, s.name) == (None, None, None, None, None)
        assert s.team == "Home"


@pytest.mark.parametrize("team_name", ["Ball", "ball", "BALL"])
def test_build_ball_team_always_gets_ball_slots(team_name):
    """C7: a "ball" team (any case) gets ball slots, ignoring any teamsheet.

    Even when a teamsheet entry exists under the ball's name, the ball branch
    overrides it and produces the ball identifiers.
    """
    slots = build_player_slots({team_name: _ts(_roster(1))}, {team_name: _XY(1)})[team_name]
    assert slots == _ball_slots(1)


def test_build_flattens_dfl_nested_xy_dict():
    """C8: a ``{period: {team: XY}}`` xy_dict is flattened via the first period."""
    nested = {"firstHalf": {"Home": _XY(2), "Away": _XY(2)}}
    slots = build_player_slots(None, nested)
    assert set(slots) == {"Home", "Away"}
    assert len(slots["Home"]) == 2


@pytest.mark.parametrize("xy_obj", [_XY(0), _NoArrayXY()])
def test_build_team_without_players_yields_empty_list(xy_obj):
    """C9: a team whose XY encodes zero players gets an empty slot list."""
    slots = build_player_slots(None, {"Home": xy_obj})
    assert slots["Home"] == []


@pytest.mark.parametrize(
    "jersey_col, name_col",
    [
        ("jersey_number", "name"),  # second-choice fallback keys
        ("number", "player"),  # third-choice jersey key
    ],
)
def test_build_resolves_identifier_fallback_keys(jersey_col, name_col):
    """C10: jersey and name resolve through their fallback-key chains.

    The mapper tries jID/jersey_number/number for jersey and player/name for
    name; a teamsheet supplying only an alternate key still populates the slot.
    """
    frame = pd.DataFrame({"xID": [0], jersey_col: ["77"], name_col: ["Alt"]})
    slots = build_player_slots({"Home": _ts(frame)}, {"Home": _XY(1)})["Home"]
    assert slots[0].jersey == "77"
    assert slots[0].name == "Alt"


def test_build_non_dict_xy_dict_yields_empty_mapping():
    """C11 (defensive guard): a non-dict xy_dict produces an empty mapping."""
    assert build_player_slots({"Home": _ts(_roster(2))}, None) == {}


# --------------------------------------------------------------------------- #
# teamsheet_columns_for                                                         #
# --------------------------------------------------------------------------- #


def test_columns_for_unions_team_columns_in_first_seen_order():
    """C12: column labels are unioned across teams in first-seen, de-duped order.

    Home contributes its columns first; Away's only novel column ("captain")
    is appended after the shared ones, and shared columns are not repeated.
    """
    home = pd.DataFrame({"xID": [0], "player": ["A"], "jID": ["10"]})
    away = pd.DataFrame({"xID": [0], "player": ["B"], "captain": ["C"]})
    teamsheet = {"Home": _ts(home), "Away": _ts(away)}

    cols = teamsheet_columns_for(teamsheet, ["Home", "Away"])
    assert cols == ["xID", "player", "jID", "captain"]


@pytest.mark.parametrize(
    "teamsheet, teams",
    [
        (None, ["Home"]),  # no teamsheet loaded at all
        ({}, ["Home"]),  # teamsheet present but no entry for the team
        ({"Home": _ts(_roster(2))}, []),  # no teams requested
    ],
)
def test_columns_for_missing_teamsheet_yields_empty(teamsheet, teams):
    """C12: a missing/None teamsheet or no requested teams yields no columns."""
    assert teamsheet_columns_for(teamsheet, teams) == []


# --------------------------------------------------------------------------- #
# teamsheet_row_for                                                            #
# --------------------------------------------------------------------------- #


def test_row_for_returns_non_null_pairs_in_column_order():
    """C13: a player's row becomes verbatim non-null (column, value) pairs.

    The pairs follow the teamsheet's own column order; a null cell in the
    middle of the row is dropped rather than emitted as a placeholder.
    """
    frame = pd.DataFrame(
        {
            "xID": [0, 1],
            "pID": ["P000", "P001"],
            "player": ["Player0", "Player1"],
            "position": [None, "MID"],
        }
    )
    pairs = teamsheet_row_for({"Home": _ts(frame)}, "Home", 0)
    assert pairs == [("xID", "0"), ("pID", "P000"), ("player", "Player0")]


@pytest.mark.parametrize(
    "teamsheet, team, col_index",
    [
        ({"Home": _ts(_roster(2))}, "Home", 5),  # col_index past the last row
        ({"Home": _ts(_roster(2))}, "Away", 0),  # unknown team
        (None, "Home", 0),  # no teamsheet loaded at all
    ],
)
def test_row_for_not_found_yields_empty(teamsheet, team, col_index):
    """C13: an out-of-range index, unknown team, or missing teamsheet yields []."""
    assert teamsheet_row_for(teamsheet, team, col_index) == []


# --------------------------------------------------------------------------- #
# teamsheet_column_values                                                      #
# --------------------------------------------------------------------------- #


def test_column_values_extracts_stringified_values_by_row():
    """C14: a column maps to one stringified value per row, indexed by col_index.

    Values are stringified through the same coercion as slots; a null cell
    becomes ``None`` while keeping its row position.
    """
    frame = pd.DataFrame({"xID": [0, 1, 2], "player": ["A", None, "C"]})
    assert teamsheet_column_values({"Home": _ts(frame)}, "Home", "player") == ["A", None, "C"]


@pytest.mark.parametrize(
    "teamsheet, team, column",
    [
        ({"Home": _ts(_roster(2))}, "Home", "nonexistent"),  # column not present
        ({"Home": _ts(_roster(2))}, "Away", "player"),  # unknown team
        (None, "Home", "player"),  # no teamsheet loaded at all
    ],
)
def test_column_values_missing_column_or_team_yields_empty(teamsheet, team, column):
    """C14: a missing column or a team with no teamsheet yields []."""
    assert teamsheet_column_values(teamsheet, team, column) == []
