"""Behavioral tests for the FloodlightApp shell (``app.py``).

FloodlightApp owns the DataStore and is the sole EventBus emitter.

C1 ``commit_loaded`` writes the canonical 4-tuple + metadata, then emits
   DATA_CLEARED before DATA_LOADED with a payload derived from metadata.
C2 ``replace_pitch`` swaps only the pitch and re-fires DATA_LOADED.
C3 ``load_provider_data`` round-trips: False on no/empty loader, else persists
   metadata and emits DATA_LOADED.
C4 XY-op wrappers (apply/undo/reset) delegate to the store and emit
   XY_STACK_CHANGED.
C5 ``__getattr__`` delegates reads to the store; writes land on the shell only.

DPG calls made by event subscribers are stubbed (no live context in a headless
test). Floodlight objects are trusted upstream: only their identity, not their
analytics, is asserted.
"""

from __future__ import annotations

import unittest.mock as mock

import numpy as np
import pytest
from floodlight.core.xy import XY

from floodlight_gui.app import FloodlightApp
from floodlight_gui.core.event_bus import Events


def _patch_dpg(monkeypatch):
    """Stub every DPG call the DATA_LOADED / DATA_CLEARED fan-out may make.

    Subscribers registered by tab modules call into ``dpg.set_value`` etc.;
    without a live DPG context those C bindings crash the worker, so each is
    replaced with a safe no-op or typed return.
    """
    import dearpygui.dearpygui as dpg

    noop = lambda *a, **kw: None  # noqa: E731
    for name in (
        "set_value",
        "configure_item",
        "delete_item",
        "add_text",
        "add_checkbox",
        "add_combo",
        "add_table_column",
        "add_table",
        "add_table_row",
        "add_input_int",
        "add_input_text",
        "add_input_float",
        "add_button",
        "add_group",
        "add_spacer",
        "add_separator",
    ):
        monkeypatch.setattr(dpg, name, noop, raising=False)
    monkeypatch.setattr(dpg, "does_item_exist", lambda *a, **kw: False, raising=False)
    monkeypatch.setattr(dpg, "get_value", lambda *a, **kw: "", raising=False)
    monkeypatch.setattr(dpg, "get_item_children", lambda *a, **kw: [], raising=False)
    monkeypatch.setattr(dpg, "get_item_alias", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(dpg, "get_item_label", lambda *a, **kw: "", raising=False)
    monkeypatch.setattr(dpg, "get_item_type", lambda *a, **kw: "", raising=False)


def _meta():
    """Return a representative provider metadata dict."""
    return {
        "format_type": "dfl",
        "temporal_divisions": ["firstHalf"],
        "teams": ["Home", "Away", "Ball"],
    }


def _loaded_tuple():
    """Return a ``(pitch, event_data, position_data, teamsheet)`` 4-tuple."""
    xy = XY(np.zeros((4, 22)), framerate=25.0, direction="lr")
    ball = XY(np.zeros((4, 2)), framerate=25.0, direction="lr")
    position_data = ({"firstHalf": {"Home": xy, "Away": xy, "Ball": ball}}, None, None)
    return (object(), None, position_data, None)


@pytest.fixture
def app(monkeypatch):
    """Yield a FloodlightApp with the DPG fan-out stubbed; closes the store on teardown."""
    _patch_dpg(monkeypatch)
    instance = FloodlightApp()
    try:
        yield instance
    finally:
        instance.store.close()


# --- commit_loaded: the single mutate-then-emit producer --------------------


def test_commit_loaded_writes_store_then_emits_cleared_before_loaded(app, bus_events):
    """``commit_loaded`` sets the canonical 4-tuple, persists metadata, and emits
    DATA_CLEARED before DATA_LOADED (DATA_CLEARED fires even on the first load).
    """
    loaded = _loaded_tuple()
    app.commit_loaded(loaded, metadata=_meta(), provider="dfl")

    assert app.store.loaded_data is loaded
    assert app.get_team_names() == ["Home", "Away", "Ball"]
    assert app.get_data_format() == "dfl"

    seq = [e for (e, _kw) in bus_events if e in (Events.DATA_CLEARED, Events.DATA_LOADED)]
    assert seq == [Events.DATA_CLEARED, Events.DATA_LOADED]


def test_commit_loaded_data_loaded_payload_shape(app, bus_events):
    """DATA_LOADED carries ``{app, provider, format, teams}`` derived from metadata."""
    app.commit_loaded(_loaded_tuple(), metadata=_meta(), provider="dfl")
    kw = next(kw for (e, kw) in bus_events if e == Events.DATA_LOADED)
    assert kw["app"] is app
    assert kw["provider"] == "dfl"
    assert kw["format"] == "dfl"
    assert kw["teams"] == ["Home", "Away", "Ball"]


def test_commit_loaded_defaults_format_and_teams_when_metadata_omits_them(app, bus_events):
    """Missing ``format_type`` / ``teams`` fall back to ``"unknown"`` and ``[]``."""
    app.commit_loaded(_loaded_tuple(), metadata={}, provider="custom")
    kw = next(kw for (e, kw) in bus_events if e == Events.DATA_LOADED)
    assert kw["provider"] == "custom"
    assert kw["format"] == "unknown"
    assert kw["teams"] == []


def test_replace_pitch_swaps_only_pitch_and_refires(app, bus_events):
    """``replace_pitch`` keeps the other three components and re-fires DATA_LOADED."""
    loaded = _loaded_tuple()
    app.commit_loaded(loaded, metadata=_meta(), provider="dfl")
    bus_events.clear()

    new_pitch = object()
    app.replace_pitch(new_pitch)

    t = app.store.loaded_data
    assert t[0] is new_pitch
    assert (t[1], t[2], t[3]) == (loaded[1], loaded[2], loaded[3])

    kw = next(kw for (e, kw) in bus_events if e == Events.DATA_LOADED)
    assert kw["provider"] == "dfl"
    assert kw["format"] == "dfl"
    assert kw["teams"] == ["Home", "Away", "Ball"]


# --- load_provider_data round-trip ------------------------------------------


def test_load_provider_data_returns_false_without_a_callback(app):
    """With no loader registered, the round-trip is a no-op returning False."""
    assert app.load_provider_data("dfl", ["x.xml"]) is False


def test_load_provider_data_returns_false_when_loader_yields_nothing(app):
    """A loader returning ``None`` reports failure and emits no DATA_LOADED."""
    app.set_load_callback(lambda *a, **kw: None)
    assert app.load_provider_data("dfl", ["x.xml"]) is False


def test_load_provider_data_round_trip_propagates_metadata(app):
    """The real ``load_provider_data`` round-trip persists provider metadata to the store.

    Only the per-file IO call is stubbed; ``engine.load_data.extract_metadata``
    runs for real so the actual metadata-shape contract is exercised.
    """

    def _xy(n):
        return XY(xy=np.zeros((10, 2 * n)), framerate=25.0, direction="lr")

    xy_dict = {
        "firstHalf": {"Home": _xy(11), "Away": _xy(11), "Ball": _xy(1)},
        "secondHalf": {"Home": _xy(11), "Away": _xy(11), "Ball": _xy(1)},
    }
    fake_loaded = (object(), None, (xy_dict, None, None), None)

    from floodlight_gui import _create_data_loader
    from floodlight_gui.engine import load_data

    # Patch the engine call so no real file IO happens; reuse the production
    # loader so the pitch-None / position-None failure guards run for real.
    with mock.patch.object(load_data, "load_provider_data", lambda *a, **kw: fake_loaded):
        app.set_load_callback(_create_data_loader(app))
        ok = app.load_provider_data("dfl", ["dummy.xml"])

    assert ok is True
    assert set(app.get_team_names()) >= {"Home", "Away", "Ball"}
    assert app.get_temporal_divisions() == ["firstHalf", "secondHalf"]
    assert app.get_data_format() == "dfl"


def test_load_provider_data_emits_data_loaded_with_provider_and_teams(app, bus_events):
    """A successful round-trip emits DATA_LOADED with the provider format and real teams."""
    xy_dict = {
        "firstHalf": {
            "Home": XY(np.zeros((5, 22)), framerate=25.0, direction="lr"),
            "Away": XY(np.zeros((5, 22)), framerate=25.0, direction="lr"),
            "Ball": XY(np.zeros((5, 2)), framerate=25.0, direction="lr"),
        }
    }
    fake_loaded = (object(), None, (xy_dict, None, None), None)

    from floodlight_gui import _create_data_loader
    from floodlight_gui.engine import load_data

    with mock.patch.object(load_data, "load_provider_data", lambda *a, **kw: fake_loaded):
        app.set_load_callback(_create_data_loader(app))
        app.load_provider_data("dfl", ["x"])

    kw = next(kw for (e, kw) in bus_events if e == Events.DATA_LOADED)
    assert kw["format"] == "dfl"
    assert "Ball" in kw["teams"]


# --- XY-op stack wrappers: delegate to store, emit XY_STACK_CHANGED ----------


def test_apply_xy_op_delegates_and_emits(app, monkeypatch, bus_events):
    """``apply_xy_op`` returns the store's derived XY and emits XY_STACK_CHANGED with ``app``."""
    sentinel = object()
    monkeypatch.setattr(app.store, "apply_xy_op", lambda p, t, k, params: sentinel)

    result = app.apply_xy_op("firstHalf", "Home", "butterworth_lowpass", {"order": 3})

    assert result is sentinel
    kw = next(kw for (e, kw) in bus_events if e == Events.XY_STACK_CHANGED)
    assert kw["app"] is app


def test_undo_xy_op_delegates_and_emits(app, monkeypatch, bus_events):
    """``undo_xy_op`` returns the store's derived XY and emits XY_STACK_CHANGED."""
    sentinel = object()
    monkeypatch.setattr(app.store, "undo_xy_op", lambda p, t: sentinel)

    result = app.undo_xy_op("firstHalf", "Home")

    assert result is sentinel
    assert any(e == Events.XY_STACK_CHANGED for (e, _kw) in bus_events)


def test_reset_xy_ops_delegates_with_targets_and_emits(app, monkeypatch, bus_events):
    """``reset_xy_ops`` forwards the period/team targets to the store and emits the event."""
    calls = []
    monkeypatch.setattr(
        app.store, "reset_xy_ops", lambda period=None, team=None: calls.append((period, team))
    )

    app.reset_xy_ops(period="firstHalf", team="Home")

    assert calls == [("firstHalf", "Home")]
    assert any(e == Events.XY_STACK_CHANGED for (e, _kw) in bus_events)


# --- accessor delegation via __getattr__ ------------------------------------


def test_getattr_delegates_reads_to_store(app):
    """Attribute reads not on the shell fall through to the DataStore."""
    app.store.data_metadata = {
        "format_type": "tracab",
        "teams": ["A", "B"],
        "temporal_divisions": [],
    }
    assert app.get_data_format() == "tracab"
    assert app.get_team_names() == ["A", "B"]


def test_getattr_raises_for_unknown_attribute(app):
    """An attribute absent from both shell and store raises AttributeError."""
    with pytest.raises(AttributeError):
        _ = app.definitely_not_a_real_attribute


def test_writes_land_on_shell_not_store(app):
    """There is no write delegation: ``app.x = v`` sets the shell, leaving the store clean.

    This footgun is why all store writes route through explicit methods.
    """
    app.some_new_field = 7
    assert app.__dict__["some_new_field"] == 7
    assert "some_new_field" not in vars(app.store)
