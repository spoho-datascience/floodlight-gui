"""Behavioral tests for the global status bar (``status_bar.create_status_bar``).

The status bar is GUI-shell chrome: a four-cell footer wired to five EventBus
events. The cell *text* is visible on sight, so only the silent contracts are
pinned here: that each status event has exactly one subscriber (a double or
missing subscriber silently de-syncs the bar), that subscribers settle after
the tab tier, and that DATA_CLEARED resets every cell. ``create_status_bar`` is
driven with the DPG stub; the events themselves are produced elsewhere.
"""

from __future__ import annotations

import pytest

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.status_bar import create_status_bar
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def status_bar_stub(monkeypatch):
    """Patch ``dpg`` in status_bar and build the bar, yielding the recording stub."""
    stub = make_dpg_stub()
    monkeypatch.setattr("floodlight_gui.status_bar.dpg", stub)
    create_status_bar()
    return stub


def test_data_cleared_resets_every_cell(status_bar_stub):
    """DATA_CLEARED restores all four cells to their default text.

    Reset is the one silent cell-write path: stale data/selection/frame/action
    text surviving a clear would silently misreport the session state.
    """
    bus.emit(Events.DATA_LOADED, format="DFL", teams=["A"])
    bus.emit(Events.SELECTION_CHANGED, summary="Team A")
    bus.emit(Events.FRAME_CHANGED, frame=42)
    bus.emit(Events.EXPORT_REQUESTED, kind="metric", target="/x")
    bus.emit(Events.DATA_CLEARED)
    assert status_bar_stub.values.get("statusbar_cell_data") == "No data"
    assert status_bar_stub.values.get("statusbar_cell_selection") == "No selection"
    assert status_bar_stub.values.get("statusbar_cell_frame") == "-"
    assert status_bar_stub.values.get("statusbar_cell_action") == "Ready"


def test_each_status_event_is_subscribed_once(status_bar_stub):
    """The bar registers exactly one subscriber per status event after one build.

    The five status events each drive a single cell; a second subscriber would
    double-handle an emit. ``create_status_bar`` already ran via the fixture.
    """
    for event in (
        Events.DATA_LOADED,
        Events.DATA_CLEARED,
        Events.SELECTION_CHANGED,
        Events.FRAME_CHANGED,
        Events.EXPORT_REQUESTED,
    ):
        assert len(bus._subscribers.get(event, [])) == 1


def test_subscribers_settle_after_tab_priority(status_bar_stub):
    """Status subscribers run at a priority that follows tab subscribers (priority 10).

    The bar reads event payloads after tabs settle their state, so its priority
    must be a larger number than the tab tier.
    """
    for event in (
        Events.DATA_LOADED,
        Events.DATA_CLEARED,
        Events.SELECTION_CHANGED,
        Events.FRAME_CHANGED,
        Events.EXPORT_REQUESTED,
    ):
        priorities = [prio for prio, _cb in bus._subscribers.get(event, [])]
        assert all(prio > 10 for prio in priorities)
