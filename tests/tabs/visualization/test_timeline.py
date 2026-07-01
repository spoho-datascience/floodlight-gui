"""Behavioral contracts for ``visualization.timeline``.

This module owns the Code-band timeline strip. Most of it is visible the moment
it draws: the width fallback and the click-x -> frame mapping both surface as the
cursor landing where you can see it, so a wrong value is caught by eye and is not
guarded here. The one silent-but-corrupting contract is the cursor-only-redraw:
the per-tick path must touch exactly one DPG item (an in-place configure when the
cursor exists, a single draw_line when it does not). A regression to a full
per-tick redraw would tank playback FPS with no visible tell on a single frame.
DPG is stubbed at the module seam.

Behavioral contracts guarded here
---------------------------------
_redraw_frame_cursor (the cursor-only-redraw contract)
  C5  When the cursor item already exists, the per-tick redraw issues exactly
      one in-place configure and no draw_line (steady-state cheap path).
  C6  When the cursor item is absent, the redraw creates it with one draw_line
      carrying the stable cursor tag.
"""

from __future__ import annotations

import pytest

from floodlight_gui.tabs.visualization import state, timeline
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def fresh_state(monkeypatch):
    """Install a fresh ViewerState as the module singleton for the test.

    Returns the new state so the test can seed timeline fields without leaking
    into other tests.
    """
    new_state = state.ViewerState()
    monkeypatch.setattr(state, "viz_state", new_state)
    return new_state


# --------------------------------------------------------------------------- #
# _redraw_frame_cursor (cursor-only-redraw contract)                            #
# --------------------------------------------------------------------------- #


def _seed_timeline_for_cursor(st):
    """Seed the minimal state the cursor redraw reads (total + one code row)."""
    st.timeline_total_frames = 100
    st.current_frame = 50
    st.timeline_width = 1000
    st.code_objects = [("possession", object())]


def test_redraw_cursor_configures_in_place_when_present(fresh_state, monkeypatch):
    """C5: an existing cursor is updated with one configure and no draw_line."""
    _seed_timeline_for_cursor(fresh_state)
    # Both the drawlist and the cursor item already exist.
    stub = make_dpg_stub(existing_items={timeline._TIMELINE_TAG, timeline._TIMELINE_CURSOR_TAG})
    stub.draw_line = lambda *a, **k: stub.calls.append(("draw_line", a, k))
    monkeypatch.setattr(timeline, "dpg", stub)

    timeline._redraw_frame_cursor()

    assert len(stub.calls_of("configure_item")) == 1
    assert stub.calls_of("draw_line") == []


def test_redraw_cursor_draws_line_when_absent(fresh_state, monkeypatch):
    """C6: a missing cursor is created with a single draw_line on the stable tag."""
    _seed_timeline_for_cursor(fresh_state)
    # The drawlist exists but the cursor item does not yet.
    stub = make_dpg_stub(existing_items={timeline._TIMELINE_TAG})
    stub.draw_line = lambda *a, **k: stub.calls.append(("draw_line", a, k))
    monkeypatch.setattr(timeline, "dpg", stub)

    timeline._redraw_frame_cursor()

    draw_calls = stub.calls_of("draw_line")
    assert len(draw_calls) == 1
    assert draw_calls[0][2]["tag"] == timeline._TIMELINE_CURSOR_TAG
    assert stub.calls_of("configure_item") == []
