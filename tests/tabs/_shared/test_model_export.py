"""Behavioral contracts for ``tabs/_shared/model_export``.

``_property_to_dataframe`` dispatches a fitted-model method result across the
floodlight Property duck-types (PlayerProperty / TeamProperty / DyadicProperty /
XY / Tuple / fallback) into a labeled DataFrame, applying kinematic axis-suffix
columns. ``export_model_metric_directly`` resolves app context, projects, and
writes a CSV, soft-failing into ``(False, message)``. The projector is pure and
tested directly with real Property objects; the exporter's app and XY accessors
are the seams and are stubbed, with the CSV written to ``tmp_path``.

Behavioral contracts guarded here
---------------------------------
_property_to_dataframe
  C1  TeamProperty (T,) yields one ``{team}_{name}`` column.
  C2  PlayerProperty (T, N) yields one column per resolvable selected player,
      labeled by slot; an empty selection yields an empty DataFrame.
  C3  PlayerProperty kinematic axis suffix: velocity/acceleration map
      None->magnitude, "x"->x, "y"->y; non-kinematic names get no axis tag.
  C4  Tuple[PlayerProperty|TeamProperty, ...] labels both teams' columns from
      teams_in_data (falling back to Home/Away when fewer than two labels).
  C5  XY (T, 2) yields ``_x`` / ``_y`` centroid columns; wide XY yields per-slot
      ``_x`` / ``_y`` columns.
  C6  DyadicProperty raises NotImplementedError.
  C7  A fallback object exposing ``.property`` is projected by ndim; an object
      without ``.property`` raises ValueError.
  C8  A non-empty result prepends a ``frame`` column.

_slot_label / _build_resolve_slot
  C9  A slot labels by xid, falling back to ``col_{index}`` when xid is None.
  C10 resolve_slot matches by pid, ``col_<N>``, ``x<N>``, and bare-int xid, and
      returns None when nothing matches.

export_model_metric_directly
  C11 A PlayerProperty method result writes a CSV and returns (True, filepath).
  C12 A missing method name / no player slots / DyadicProperty soft-fail into
      (False, message) without raising.
"""

from __future__ import annotations

import numpy as np
import pytest
from floodlight.core.property import DyadicProperty, PlayerProperty, TeamProperty
from floodlight.core.xy import XY as FlXY

import floodlight_gui.tabs._shared.model_export as me
from floodlight_gui.core.player_mapping import PlayerSlot


def _slot(col_index, xid=None, pid=None):
    """Build a minimal PlayerSlot for projector / resolver tests."""
    return PlayerSlot(
        col_index=col_index,
        team="Home",
        xid=xid,
        pid=pid,
        jersey=None,
        position=None,
        name=None,
    )


def _project(method_result, **overrides):
    """Call ``_property_to_dataframe`` with sensible defaults for the projector."""
    slots = overrides.pop("slots", [_slot(0, xid=1), _slot(1, xid=2)])
    kwargs = {
        "name": "velocity",
        "team_name": "Home",
        "teams_in_data": ["Home", "Away"],
        "selected_players": [1, 2],
        "slots_resolver": lambda team: slots,
        "resolve_slot": me._build_resolve_slot(slots),
        "fit_params": None,
    }
    kwargs.update(overrides)
    return me._property_to_dataframe(method_result, slots, **kwargs)


# --------------------------------------------------------------------------- #
# C1: TeamProperty
# --------------------------------------------------------------------------- #


def test_team_property_single_column():
    """C1: a TeamProperty yields one team-labeled column."""
    tp = TeamProperty(property=np.arange(4.0), name="centroid")
    df = _project(tp, name="centroid")
    assert "Home_centroid" in df.columns
    assert list(df["Home_centroid"]) == [0.0, 1.0, 2.0, 3.0]


# --------------------------------------------------------------------------- #
# C2: PlayerProperty columns + empty selection
# --------------------------------------------------------------------------- #


def test_player_property_one_column_per_selected_player():
    """C2: a PlayerProperty yields one slot-labeled column per resolvable player."""
    pp = PlayerProperty(property=np.zeros((3, 2)), name="velocity")
    df = _project(pp, name="distance", selected_players=[1, 2])
    assert list(df.columns) == ["frame", "1_distance", "2_distance"]


def test_player_property_empty_selection_returns_empty():
    """C2: an empty selected_players list returns an empty DataFrame."""
    pp = PlayerProperty(property=np.zeros((3, 2)), name="velocity")
    df = _project(pp, selected_players=[])
    assert df.empty


# --------------------------------------------------------------------------- #
# C3: kinematic axis suffix
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name, axis, expected_suffix",
    [
        ("velocity", None, "velocity_magnitude"),
        ("velocity", "x", "velocity_x"),
        ("acceleration", "y", "acceleration_y"),
        ("distance", None, "distance"),  # non-kinematic: no axis tag
    ],
)
def test_player_property_axis_suffix(name, axis, expected_suffix):
    """C3: kinematic methods derive the axis suffix; others use the bare name."""
    pp = PlayerProperty(property=np.zeros((2, 1)), name=name)
    df = _project(
        pp,
        name=name,
        selected_players=[1],
        slots=[_slot(0, xid=1)],
        fit_params={"axis": axis},
    )
    assert f"1_{expected_suffix}" in df.columns


# --------------------------------------------------------------------------- #
# C4: Tuple two-team
# --------------------------------------------------------------------------- #


def test_tuple_labels_both_teams():
    """C4: a Tuple of PlayerProperties labels columns by both team names."""
    a = PlayerProperty(property=np.zeros((2, 1)), name="velocity")
    b = PlayerProperty(property=np.zeros((2, 1)), name="velocity")
    df = _project(
        (a, b),
        name="velocity",
        teams_in_data=["Home", "Away"],
        slots=[_slot(0, xid=1)],
        slots_resolver=lambda team: [_slot(0, xid=1)],
    )
    cols = list(df.columns)
    assert any(c.startswith("Home_") for c in cols)
    assert any(c.startswith("Away_") for c in cols)


def test_tuple_teamproperty_labels_both_teams():
    """C4: a Tuple of TeamProperties yields one labeled column per team."""
    a = TeamProperty(property=np.arange(2.0), name="metric")
    b = TeamProperty(property=np.arange(2.0), name="metric")
    df = _project((a, b), name="metric", teams_in_data=["Home", "Away"])
    assert "Home_metric" in df.columns
    assert "Away_metric" in df.columns


# --------------------------------------------------------------------------- #
# C5: XY centroid vs wide
# --------------------------------------------------------------------------- #


def test_xy_centroid_two_columns():
    """C5: an XY of width 2 yields ``_x`` / ``_y`` centroid columns."""
    xy = FlXY(xy=np.zeros((3, 2)), framerate=25)
    df = _project(xy, name="centroid")
    assert "Home_centroid_x" in df.columns
    assert "Home_centroid_y" in df.columns


def test_xy_wide_per_slot_columns():
    """C5: a wide XY yields per-slot ``_x`` / ``_y`` columns."""
    xy = FlXY(xy=np.zeros((3, 4)), framerate=25)
    slots = [_slot(0, xid=1), _slot(1, xid=2)]
    df = _project(xy, name="positions", slots=slots)
    assert "1_positions_x" in df.columns
    assert "2_positions_y" in df.columns


# --------------------------------------------------------------------------- #
# C6: DyadicProperty
# --------------------------------------------------------------------------- #


def test_dyadic_property_raises_not_implemented():
    """C6: a DyadicProperty result raises NotImplementedError."""
    dp = DyadicProperty(property=np.zeros((2, 2, 2)), name="dist")
    with pytest.raises(NotImplementedError):
        _project(dp, name="dist")


# --------------------------------------------------------------------------- #
# C7: fallback
# --------------------------------------------------------------------------- #


def test_fallback_property_attribute_projected():
    """C7: an unknown object exposing ``.property`` is projected by ndim."""

    class _Custom:
        property = np.arange(3.0)

    df = _project(_Custom(), name="custom")
    assert "Home_custom" in df.columns


def test_unknown_without_property_raises_value_error():
    """C7: an object with no ``.property`` attribute raises ValueError."""
    with pytest.raises(ValueError):
        _project(object(), name="mystery")


# --------------------------------------------------------------------------- #
# C8: frame column
# --------------------------------------------------------------------------- #


def test_nonempty_result_prepends_frame_column():
    """C8: a non-empty projection prepends a 0-based ``frame`` column."""
    tp = TeamProperty(property=np.arange(3.0), name="centroid")
    df = _project(tp, name="centroid")
    assert list(df.columns)[0] == "frame"
    assert list(df["frame"]) == [0, 1, 2]


# --------------------------------------------------------------------------- #
# C9 / C10: slot label + resolver
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "slot, expected",
    [
        (_slot(0, xid=7), "7"),
        (_slot(3, xid=None), "col_3"),
    ],
)
def test_slot_label(slot, expected):
    """C9: slot labels prefer xid, falling back to ``col_{index}``."""
    assert me._slot_label(slot) == expected


def test_resolve_slot_strategies():
    """C10: resolve_slot matches pid, ``col_<N>``, ``x<N>``, and bare-int xid."""
    slots = [_slot(0, xid=10, pid="PA"), _slot(1, xid=20, pid="PB")]
    resolve = me._build_resolve_slot(slots)
    assert resolve("PA").col_index == 0  # pid exact
    assert resolve("col_1").col_index == 1  # col_<N>
    assert resolve("x20").col_index == 1  # x<N> xid prefix
    assert resolve(10).col_index == 0  # bare int xid
    assert resolve("nope") is None  # no match


# --------------------------------------------------------------------------- #
# C11 / C12: export_model_metric_directly
# --------------------------------------------------------------------------- #


class _FakeApp:
    """App double exposing slots, XY accessor, and team metadata."""

    def __init__(self, slots, xy):
        self._slots = slots
        self._xy = xy
        self.data_metadata = {"teams": ["Home", "Away"]}

    def get_player_slots(self, team):  # noqa: ARG002
        return self._slots


class _FakeModel:
    """Fitted-model double whose method returns a fixed Property."""

    def __init__(self, result):
        self._result = result

    def velocity(self):
        return self._result


@pytest.fixture
def patch_xy(monkeypatch):
    """Stub the XY resolver so the exporter finds team data without a real load."""

    def _install(xy):
        monkeypatch.setattr(me, "get_xy_for_period_team", lambda app, half, team: xy)

    return _install


def test_export_writes_csv_and_returns_path(tmp_path, patch_xy):
    """C11: a PlayerProperty result writes a CSV and returns (True, filepath)."""
    slots = [_slot(0, xid=1), _slot(1, xid=2)]
    xy = FlXY(xy=np.zeros((3, 4)), framerate=25)
    patch_xy(xy)
    app = _FakeApp(slots, xy)
    model = _FakeModel(PlayerProperty(property=np.zeros((3, 2)), name="velocity"))
    success, path = me.export_model_metric_directly(
        app,
        model,
        [1, 2],
        "out",
        "firstHalf",
        "Home",
        "VelocityModel",
        "velocity",
        output_dir=str(tmp_path),
    )
    assert success is True
    assert path.endswith("out.csv")
    assert (tmp_path / "out.csv").exists()


@pytest.mark.parametrize(
    "slots, result, method_name",
    [
        ([], PlayerProperty(property=np.zeros((2, 1)), name="velocity"), "velocity"),  # no slots
        (
            [_slot(0, xid=1)],
            PlayerProperty(property=np.zeros((2, 1)), name="velocity"),
            "missing_method",
        ),  # method absent
        (
            [_slot(0, xid=1)],
            DyadicProperty(property=np.zeros((2, 2, 2)), name="d"),
            "velocity",
        ),  # dyadic
    ],
)
def test_export_soft_fails(tmp_path, patch_xy, slots, result, method_name):
    """C12: missing slots / method / DyadicProperty soft-fail into (False, message)."""
    xy = FlXY(xy=np.zeros((2, 2)), framerate=25)
    patch_xy(xy)
    app = _FakeApp(slots, xy)
    model = _FakeModel(result)
    success, message = me.export_model_metric_directly(
        app,
        model,
        [1],
        "out",
        "firstHalf",
        "Home",
        "Model",
        method_name,
        output_dir=str(tmp_path),
    )
    assert success is False
    assert isinstance(message, str) and message
