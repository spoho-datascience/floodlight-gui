"""Shared pytest fixtures for the unit suite.

Every fixture here is DPG-free; tests run in pure Python. Synthetic floodlight
objects come from the ``make_*`` factories; the singleton EventBus is isolated
per test by the autouse ``_restore_bus_subscribers`` seam below.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from floodlight_gui.core.data_store import DataStore
from floodlight_gui.core.event_bus import Events
from floodlight_gui.core.event_bus import bus as event_bus_singleton


@pytest.fixture(autouse=True)
def _restore_bus_subscribers():
    """Isolate the singleton EventBus subscriber map per test.

    Establishes the bus-isolation seam every suite relies on: snapshots
    ``bus._subscribers`` before the test, clears it for the test body, and
    restores the snapshot on teardown. Without this, tab modules that
    subscribe at import time would persist for the whole session, fire on
    DPG-free emits with no live context, and capture per-test app instances
    across tests. The inner callback lists are copied so subscribe/unsubscribe
    during the test cannot mutate the snapshot.
    """
    snapshot = {ev: list(callbacks) for ev, callbacks in event_bus_singleton._subscribers.items()}
    event_bus_singleton._subscribers = {}
    yield
    event_bus_singleton._subscribers = {ev: list(cbs) for ev, cbs in snapshot.items()}


@pytest.fixture
def make_xy():
    """Factory returning a real floodlight.XY of shape (T, 2N).

    Per-column values are a deterministic linear ramp plus a high-frequency
    sinusoid, so lowpass filters have signal to smooth and a filtered result is
    distinguishable from the input. ``direction`` must be 'lr' or 'rl' per the
    XY contract; defaults to 'lr'.
    """
    from floodlight.core.xy import XY

    def _build(T: int = 100, N: int = 2, framerate: float = 25.0, direction: str = "lr") -> XY:
        rng = np.arange(T, dtype=float)
        # Ramp + sinusoid: a pure line is a no-op under polyorder-3 Savitzky-Golay.
        high_freq = np.sin(2.0 * np.pi * rng / 3.0)
        cols = [rng + i * 10.0 + high_freq for i in range(2 * N)]
        arr = np.stack(cols, axis=1)
        return XY(xy=arr, framerate=framerate, direction=direction)

    return _build


@pytest.fixture
def make_teamsheet():
    """Factory returning a teamsheet dict ``{team: obj-with-.teamsheet-DataFrame}``.

    The frame carries every canonical player-ID column (xID, pID, jID, player,
    position) so consumers exercise the full identity surface.
    """

    def _build(team: str = "Home", n_players: int = 2) -> dict:
        df = pd.DataFrame(
            {
                "xID": list(range(n_players)),
                "pID": [f"P{i:03d}" for i in range(n_players)],
                "jID": [str(10 + i) for i in range(n_players)],
                "player": [f"Player{i}" for i in range(n_players)],
                "position": ["MID"] * n_players,
            }
        )

        class _TS:
            def __init__(self, frame):
                self.teamsheet = frame

        return {team: _TS(df)}

    return _build


@pytest.fixture
def data_store(make_xy):
    """Fresh DataStore preloaded with one period (firstHalf) and Home/Away XY.

    Real DataStore and real XY; ``loaded_data`` is set directly to bypass the
    DPG-driven load path. The 4-tuple is
    ``(pitch, event_data, position_data, teamsheet)`` with
    ``position_data = (xy_dict, possession, ballstatus)``. Calls ``store.close()``
    on teardown to drop bus subscriptions bound to this instance.
    """
    store = DataStore()
    xy = make_xy(100, 2)
    xy_dict = {"firstHalf": {"Home": xy, "Away": make_xy(100, 2)}}
    position_data = (xy_dict, None, None)
    store.loaded_data = (None, None, position_data, None)
    store.data_metadata = {
        "format_type": "test",
        "temporal_divisions": ["firstHalf"],
        "teams": ["Home", "Away"],
    }
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def multi_period_data_store(make_xy):
    """DataStore with 2 periods x 3 teams (Home/Away/Ball) of real XY.

    Same shape as ``data_store`` but populates both ``firstHalf`` and
    ``secondHalf`` with all three team entities including Ball, giving a full
    (period, team) cross-product for broadcast-loop tests. Closes on teardown.
    """
    store = DataStore()
    xy_dict = {
        "firstHalf": {"Home": make_xy(50, 2), "Away": make_xy(50, 2), "Ball": make_xy(50, 1)},
        "secondHalf": {"Home": make_xy(50, 2), "Away": make_xy(50, 2), "Ball": make_xy(50, 1)},
    }
    position_data = (xy_dict, None, None)
    store.loaded_data = (None, None, position_data, None)
    store.data_metadata = {
        "format_type": "test",
        "temporal_divisions": ["firstHalf", "secondHalf"],
        "teams": ["Home", "Away", "Ball"],
    }
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def make_failing_apply():
    """Factory wrapping ``DataStore.apply_xy_op`` to raise on one (period, team).

    Returns ``failing_apply = make_failing_apply(store, fail_on=(period, team))``,
    intended for ``monkeypatch.setattr(store, "apply_xy_op", failing_apply)``.
    The wrapper raises ``ValueError`` when called with the matching target and
    otherwise delegates to the real ``apply_xy_op``, isolating the
    broadcast rollback path while leaving every other combo mutating normally.
    """

    def _build(store, fail_on):
        original = store.apply_xy_op

        def _wrapped(period, team, op_key, params):
            if (period, team) == fail_on:
                raise ValueError(f"simulated failure on {fail_on}")
            return original(period, team, op_key, params)

        return _wrapped

    return _build


@pytest.fixture
def bus_events():
    """Capture every event emitted on the singleton bus during the test.

    Yields a growing list of ``(Events, kwargs)`` tuples by subscribing one
    recorder per event at a low priority. Real bus, real emits; only the
    capture side is synthetic. Unsubscribes every recorder on teardown.
    """
    captured: list[tuple[Events, dict]] = []

    recorders: list[tuple[Events, callable]] = []
    for event in Events:

        def _make_recorder(ev):
            def _rec(**kwargs):
                captured.append((ev, kwargs))

            return _rec

        rec = _make_recorder(event)
        event_bus_singleton.subscribe(event, rec, priority=-100)
        recorders.append((event, rec))

    yield captured

    for ev, rec in recorders:
        event_bus_singleton.unsubscribe(ev, rec)
