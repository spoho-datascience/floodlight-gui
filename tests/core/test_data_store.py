"""Behavioral contracts for ``floodlight_gui.core.data_store``.

``DataStore`` owns loaded match data, provider metadata, and the per-(period,
team) XY-op stack. Its job is: unwrap the provider position payload into a
period/team XY dict, resolve the active XY (pristine until ops are pushed,
derived after), maintain the apply/undo op stack with rollback on failure,
persist load-time metadata behind typed accessors, and count events across
the provider event shapes.

Seams
-----
The floodlight XY objects come from the root-conftest ``make_xy`` fixture
(real ``floodlight.core.xy.XY``). The XY-op transform itself
(``apply_xy_op`` imported into this module) is stubbed in the stack tests so
the assertions cover the STACK behavior (depth, pristine-vs-active swap,
shape preserved) and never the transform math. ``compute_summary`` runs over
sentinel event objects exposing a ``.events`` container.

Behavioral contracts guarded here
---------------------------------
_xy_dict (payload unwrap)
  C1  Unwraps the real provider shapes: a position tuple yields its first
      slot, a ``{"position_data": ...}`` wrapper yields the inner value, a
      bare period/team dict yields itself, and any other value yields ``{}``.

active-XY resolution
  C2  get_active_xy returns the pristine loaded XY when no op is stacked, and
      the derived XY once an op has been applied.
  C3  _get_pristine_xy resolves both the nested ``{period:{team:XY}}`` layout
      and the flat single-period ``{team:XY}`` layout, and returns ``None``
      for an absent (period, team).

apply / undo / reset stack
  C4  apply_xy_op pushes one op, grows the stack depth, and swaps the active
      XY to a derived object distinct from pristine while preserving shape.
  C5  apply_xy_op rolls the just-pushed op back and re-raises when the
      transform fails, leaving stack depth and active XY unchanged.
  C6  undo_xy_op pops the last op and restores the prior active XY; undo on
      an empty stack returns ``None`` and does not raise.
  C7  reset_xy_ops clears one (period, team) key, or every key when called
      with no arguments.

store_loaded_data + accessors
  C8  store_loaded_data persists metadata behind the typed accessors (format,
      temporal divisions, team names, has-ball).
  C9  store_loaded_data overwrites the pitch field on every load, including
      back to ``None``.
  C10 store_loaded_data extracts possession/ball-status from the DFL 3-tuple
      and the Kinexon single-slot tuple, and clears stale values when the new
      payload omits them.
  C11 A DATA_LOADED emission wipes the XY-ops stack and derived cache.

fps extraction
  C12 extract_fps_from_position_data derives the framerate from the first XY
      carrying ``.framerate`` across the provider position shapes (DFL nested
      tuple, Kinexon flat tuple, bare nested dict), and falls back to
      ``original_fps`` when no XY exposes one. get_fps returns ``original_fps``.

compute_summary
  C13 compute_summary counts events across the provider event shapes: the
      DFL/IDSSE ``(events_dict, ...)`` tuple, the bare nested dict, and a
      legacy flat ``Events`` object, summing ``len(obj.events)`` over every
      period/team leaf.
  C14 compute_summary reports ``frames`` (the position-row count) for both the
      multi-period nested layout and the single ``fullMatch`` flat layout.

player slots
  C15 get_player_slots returns the stored slots for a known team and an empty
      list for an unknown one.
"""

from __future__ import annotations

import numpy as np
import pytest

import floodlight_gui.core.data_store as ds_module
from floodlight_gui.core.data_store import DataStore
from floodlight_gui.core.event_bus import Events
from floodlight_gui.core.event_bus import bus as event_bus_singleton


@pytest.fixture
def store():
    """Return a fresh, empty ``DataStore`` and unsubscribe it on teardown.

    Each ``DataStore`` subscribes a bound method to ``DATA_LOADED``; teardown
    via ``close`` keeps the singleton bus free of stale per-test subscribers.
    """
    s = DataStore()
    try:
        yield s
    finally:
        s.close()


def _nested_position(xy_dict):
    """Wrap a period/team XY dict as the canonical provider position tuple.

    Parameters
    ----------
    xy_dict : dict
        ``{period: {team: XY}}`` mapping.

    Returns
    -------
    tuple
        ``(xy_dict, None, None)`` (the DFL/IDSSE position-data shape).
    """
    return (xy_dict, None, None)


class _FakeEvents:
    """Sentinel event object exposing a sized ``.events`` container.

    Stands in for a floodlight ``Events`` leaf so ``compute_summary`` can sum
    ``len(obj.events)`` without constructing real provider event frames.
    """

    def __init__(self, n):
        self.events = list(range(n))


# --------------------------------------------------------------------------- #
# _xy_dict: payload unwrap                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload, expected_key",
    [
        # Position tuple (DFL/Kinexon): first slot is the xy dict.
        (({"firstHalf": {"Home": "XY"}}, None, None), "tuple-first-slot"),
        # Wrapper dict: inner "position_data" value is returned.
        ({"position_data": {"firstHalf": {"Home": "XY"}}}, "wrapper-inner"),
        # Bare nested dict: returned as-is.
        ({"firstHalf": {"Home": "XY"}}, "bare-dict"),
    ],
)
def test_xy_dict_unwraps_provider_shapes(payload, expected_key):
    """C1: _xy_dict unwraps the tuple, wrapper, and bare-dict payloads."""
    result = DataStore._xy_dict(payload)
    assert result == {"firstHalf": {"Home": "XY"}}, expected_key


@pytest.mark.parametrize("payload", [None, "string", 42, (), [1, 2]])
def test_xy_dict_returns_empty_for_unresolvable(payload):
    """C1: _xy_dict returns ``{}`` for values it cannot unwrap.

    An empty tuple and non-tuple/non-dict scalars carry no XY mapping.
    """
    assert DataStore._xy_dict(payload) == {}


# --------------------------------------------------------------------------- #
# active-XY resolution                                                         #
# --------------------------------------------------------------------------- #


def test_get_active_xy_returns_pristine_then_derived(store, make_xy, monkeypatch):
    """C2: active XY is pristine until an op is pushed, then the derived XY."""
    pristine = make_xy(10, 2)
    store.loaded_data = (None, None, _nested_position({"firstHalf": {"Home": pristine}}), None)

    assert store.get_active_xy("firstHalf", "Home") is pristine

    sentinel = make_xy(10, 2)
    monkeypatch.setattr(ds_module, "apply_xy_op", lambda xy, op_key, params: sentinel)
    store.apply_xy_op("firstHalf", "Home", "noop", {})

    assert store.get_active_xy("firstHalf", "Home") is sentinel


def test_get_pristine_resolves_nested_and_flat_layouts(store, make_xy):
    """C3: pristine resolution handles nested and flat layouts and misses.

    Nested ``{period:{team:XY}}`` and flat single-period ``{team:XY}`` both
    resolve; an absent (period, team) yields ``None``.
    """
    nested_xy = make_xy(5, 2)
    store.loaded_data = (None, None, _nested_position({"firstHalf": {"Home": nested_xy}}), None)
    assert store.get_active_xy("firstHalf", "Home") is nested_xy
    assert store.get_active_xy("firstHalf", "Nobody") is None

    flat_xy = make_xy(5, 2)
    store.loaded_data = (None, None, ({"Home": flat_xy}, None, None), None)
    assert store.get_active_xy("anyPeriod", "Home") is flat_xy
    assert store.get_active_xy("anyPeriod", "Away") is None


# --------------------------------------------------------------------------- #
# apply / undo / reset stack                                                   #
# --------------------------------------------------------------------------- #


def test_apply_pushes_op_and_swaps_active_preserving_shape(store, make_xy, monkeypatch):
    """C4: apply grows the stack and makes active a distinct, same-shape XY.

    The transform seam is stubbed to return a deep-copy with one column
    nudged, so the assertion targets the stack contract (depth, active swap,
    shape preserved) and not the transform's numbers.
    """
    pristine = make_xy(8, 2)

    def _stub(xy, op_key, params):
        out = xy.__class__(xy=xy.xy.copy() + 1.0, framerate=xy.framerate, direction=xy.direction)
        return out

    monkeypatch.setattr(ds_module, "apply_xy_op", _stub)
    store.loaded_data = (None, None, _nested_position({"firstHalf": {"Home": pristine}}), None)

    store.apply_xy_op("firstHalf", "Home", "butterworth", {"order": 2})

    assert store.get_xy_ops_stack("firstHalf", "Home") == [("butterworth", {"order": 2})]
    active = store.get_active_xy("firstHalf", "Home")
    assert active is not pristine
    assert active.xy.shape == pristine.xy.shape
    assert not np.array_equal(active.xy, pristine.xy)


def test_apply_rolls_back_and_reraises_on_transform_failure(store, make_xy, monkeypatch):
    """C5: a failing transform is rolled back and re-raised, leaving state intact."""
    pristine = make_xy(8, 2)
    store.loaded_data = (None, None, _nested_position({"firstHalf": {"Home": pristine}}), None)

    def _boom(xy, op_key, params):
        raise ValueError("transform exploded")

    monkeypatch.setattr(ds_module, "apply_xy_op", _boom)

    with pytest.raises(ValueError):
        store.apply_xy_op("firstHalf", "Home", "bad", {})

    assert store.get_xy_ops_stack("firstHalf", "Home") == []
    assert store.get_active_xy("firstHalf", "Home") is pristine


def test_undo_pops_and_restores_prior_active(store, make_xy, monkeypatch):
    """C6: undo pops the last op and restores the previous active XY."""
    pristine = make_xy(8, 2)
    store.loaded_data = (None, None, _nested_position({"firstHalf": {"Home": pristine}}), None)
    monkeypatch.setattr(
        ds_module,
        "apply_xy_op",
        lambda xy, op_key, params: xy.__class__(
            xy=xy.xy.copy() + 1.0, framerate=xy.framerate, direction=xy.direction
        ),
    )

    store.apply_xy_op("firstHalf", "Home", "op1", {})
    store.apply_xy_op("firstHalf", "Home", "op2", {})
    assert len(store.get_xy_ops_stack("firstHalf", "Home")) == 2

    store.undo_xy_op("firstHalf", "Home")
    assert store.get_xy_ops_stack("firstHalf", "Home") == [("op1", {})]

    store.undo_xy_op("firstHalf", "Home")
    assert store.get_xy_ops_stack("firstHalf", "Home") == []
    # With the stack empty, active falls back to pristine.
    assert store.get_active_xy("firstHalf", "Home") is pristine


def test_undo_on_empty_stack_returns_none(store, make_xy):
    """C6: undo with nothing stacked returns None and does not raise."""
    store.loaded_data = (None, None, _nested_position({"firstHalf": {"Home": make_xy(4, 2)}}), None)
    assert store.undo_xy_op("firstHalf", "Home") is None


def test_reset_clears_one_key_or_all(store, make_xy, monkeypatch):
    """C7: reset clears a single (period, team) key, or every key when bare."""
    monkeypatch.setattr(ds_module, "apply_xy_op", lambda xy, op_key, params: xy)
    xy_dict = {"firstHalf": {"Home": make_xy(4, 2), "Away": make_xy(4, 2)}}
    store.loaded_data = (None, None, _nested_position(xy_dict), None)

    store.apply_xy_op("firstHalf", "Home", "op", {})
    store.apply_xy_op("firstHalf", "Away", "op", {})

    store.reset_xy_ops("firstHalf", "Home")
    assert store.get_xy_ops_stack("firstHalf", "Home") == []
    assert store.get_xy_ops_stack("firstHalf", "Away") == [("op", {})]

    store.apply_xy_op("firstHalf", "Home", "op", {})
    store.reset_xy_ops()
    assert store.get_xy_ops_stack("firstHalf", "Home") == []
    assert store.get_xy_ops_stack("firstHalf", "Away") == []


# --------------------------------------------------------------------------- #
# store_loaded_data + accessors                                                #
# --------------------------------------------------------------------------- #


def test_store_loaded_data_persists_metadata_accessors(store):
    """C8: load-time metadata is exposed through the typed accessors."""
    metadata = {
        "format_type": "dfl",
        "temporal_divisions": ["firstHalf", "secondHalf"],
        "teams": ["Home", "Away", "Ball"],
        "has_ball": True,
    }
    store.store_loaded_data(None, ({}, None, None), None, pitch=None, metadata=metadata)

    assert store.get_data_format() == "dfl"
    assert store.get_temporal_divisions() == ["firstHalf", "secondHalf"]
    assert store.get_team_names() == ["Home", "Away", "Ball"]
    assert store.has_ball_data() is True


def test_store_loaded_data_overwrites_pitch_including_none(store):
    """C9: pitch is overwritten on every load, including back to None."""
    store.store_loaded_data(None, ({}, None, None), None, pitch="PITCH", metadata={})
    assert store.pitch == "PITCH"

    store.store_loaded_data(None, ({}, None, None), None, pitch=None, metadata={})
    assert store.pitch is None


@pytest.mark.parametrize(
    "position_data, expected_possession, expected_ball",
    [
        # DFL 3-tuple: (xy_dict, possession, ballstatus).
        (({}, "POSS", "BALL"), "POSS", "BALL"),
        # Kinexon single-slot tuple: only the xy dict, no possession/ballstatus.
        (({},), None, None),
    ],
)
def test_store_loaded_data_extracts_possession_and_ballstatus(
    store, position_data, expected_possession, expected_ball
):
    """C10: possession/ball-status come from the provider tuple slots."""
    store.store_loaded_data(None, position_data, None, metadata={})
    assert store.possession_data == expected_possession
    assert store.ball_status == expected_ball


def test_store_loaded_data_clears_stale_possession_on_reload(store):
    """C10: a payload omitting possession/ball-status clears prior values."""
    store.store_loaded_data(None, ({}, "POSS", "BALL"), None, metadata={})
    assert store.possession_data == "POSS"

    store.store_loaded_data(None, ({},), None, metadata={})
    assert store.possession_data is None
    assert store.ball_status is None


def test_data_loaded_event_wipes_xy_ops(store, make_xy, monkeypatch):
    """C11: a DATA_LOADED emission clears the op stack and derived cache."""
    monkeypatch.setattr(ds_module, "apply_xy_op", lambda xy, op_key, params: xy)
    store.loaded_data = (None, None, _nested_position({"firstHalf": {"Home": make_xy(4, 2)}}), None)
    store.apply_xy_op("firstHalf", "Home", "op", {})
    assert store.get_xy_ops_stack("firstHalf", "Home") == [("op", {})]

    event_bus_singleton.emit(Events.DATA_LOADED, app=None)

    assert store.get_xy_ops_stack("firstHalf", "Home") == []
    assert store.xy_derived == {}


# --------------------------------------------------------------------------- #
# fps extraction                                                              #
# --------------------------------------------------------------------------- #


def test_extract_fps_from_provider_shapes(store, make_xy):
    """C12: framerate comes from the first XY carrying ``.framerate``.

    The real provider shapes are a DFL nested ``{period:{team:XY}}`` tuple, a
    Kinexon flat ``{team:XY}`` tuple, and a bare nested dict. Each XY here
    carries framerate 50, so extraction must return that, not the default.
    """
    nested = {"firstHalf": {"Home": make_xy(4, 2, framerate=50.0)}}
    flat = {"Home": make_xy(4, 2, framerate=50.0)}

    assert store.extract_fps_from_position_data((nested, None, None)) == 50.0  # DFL tuple
    assert store.extract_fps_from_position_data((flat, None, None)) == 50.0  # Kinexon tuple
    assert store.extract_fps_from_position_data(nested) == 50.0  # bare nested dict


def test_extract_fps_falls_back_to_original_when_absent(store):
    """C12: with no XY exposing ``.framerate``, extraction returns original_fps.

    An empty position payload carries no XY; the default ``original_fps`` is
    returned unchanged, and ``get_fps`` reports that same value.
    """
    store.original_fps = 30
    assert store.extract_fps_from_position_data(({}, None, None)) == 30
    assert store.get_fps() == 30


# --------------------------------------------------------------------------- #
# compute_summary                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "make_event_data",
    [
        # DFL/IDSSE shape: events dict wrapped in (events_dict, teamsheets, pitch).
        lambda d: (d, "teamsheets", "pitch"),
        # Fallback shape: the bare nested dict.
        lambda d: d,
    ],
)
def test_compute_summary_counts_events_across_shapes(store, make_event_data):
    """C13: event counting sums len(obj.events) over the nested event shapes.

    The DFL/IDSSE shape wraps the events dict in a tuple
    ``(events_dict, teamsheets, pitch)``; the fallback shape is the bare
    nested dict. Both sum to the same total over the period/team leaves.
    """
    events_dict = {
        "firstHalf": {"Home": _FakeEvents(3), "Away": _FakeEvents(2)},
        "secondHalf": {"Home": _FakeEvents(5)},
    }
    store.event_data = make_event_data(events_dict)

    summary = store.compute_summary()
    assert summary["events"] == 10


def test_compute_summary_counts_legacy_flat_events_object(store):
    """C13: a legacy flat ``Events`` object is counted via its own ``.events``.

    Some older paths hand a single Events leaf (not nested by period); its
    ``len(.events)`` becomes the whole count.
    """
    store.event_data = _FakeEvents(7)
    assert store.compute_summary()["events"] == 7


def test_compute_summary_reports_format_and_teamsheet_keys(store):
    """C13: summary carries the format string and teamsheet team keys.

    With no events loaded the count is zero; format comes from metadata and
    ``teams`` reflects the teamsheet's keys.
    """
    store.data_metadata = {
        "format_type": "kinexon",
        "temporal_divisions": ["fullMatch"],
        "teams": [],
    }
    store.teamsheet = {"Home": object(), "Away": object()}

    summary = store.compute_summary()
    assert summary["format"] == "kinexon"
    assert summary["events"] == 0
    assert summary["teams"] == ["Home", "Away"]


def test_compute_summary_counts_frames_multi_period(store, make_xy):
    """C14: frames sum one team's row count per period in the nested layout.

    Each period contributes ``XY.shape[0]`` rows from the first matching team;
    a 40-row first half and a 30-row second half total 70 frames.
    """
    xy_dict = {
        "firstHalf": {"Home": make_xy(40, 2), "Away": make_xy(40, 2)},
        "secondHalf": {"Home": make_xy(30, 2)},
    }
    store.loaded_data = (None, None, _nested_position(xy_dict), None)
    store.position_data = _nested_position(xy_dict)
    store.data_metadata = {
        "format_type": "dfl",
        "temporal_divisions": ["firstHalf", "secondHalf"],
        "teams": ["Home", "Away"],
    }
    assert store.compute_summary()["frames"] == 70


def test_compute_summary_counts_frames_fullmatch_flat(store, make_xy):
    """C14: a single ``fullMatch`` flat layout reports that XY's row count.

    The flat ``{team: XY}`` single-period shape contributes the first matching
    team's ``XY.shape[0]`` rows.
    """
    flat = {"Home": make_xy(55, 2), "Away": make_xy(55, 2)}
    store.position_data = (flat, None, None)
    store.data_metadata = {
        "format_type": "kinexon",
        "temporal_divisions": ["fullMatch"],
        "teams": ["Home", "Away"],
    }
    assert store.compute_summary()["frames"] == 55


# --------------------------------------------------------------------------- #
# player slots                                                                 #
# --------------------------------------------------------------------------- #


def test_get_player_slots_known_and_unknown_team(store):
    """C15: known team returns its stored slots; unknown team returns ``[]``."""
    store.player_slots = {"Home": ["slot0", "slot1"]}
    assert store.get_player_slots("Home") == ["slot0", "slot1"]
    assert store.get_player_slots("Away") == []
