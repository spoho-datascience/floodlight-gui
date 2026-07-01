"""Behavioral contracts for ``visualization.keyboard``.

This module maps five playback shortcuts (Space, Left, Right, Home, End) to viz
actions, gated by two guards: no tracked text input is focused, and the viz tab
is the active main tab. The shortcut->action mapping itself is loud (a dead key
is noticed the instant you press it) and is not guarded here. The guards are the
silent surprises worth catching: a shortcut firing while you type in a text field,
or while another tab is up, is a quiet corruption of the frame you are editing.
The viz action functions and DPG are the seams; both are stubbed so the tests
assert only the guard decisions and the tab-tracking that backs them. The
handler-registry mounting (``register_global_handlers``) is a pure DPG side
effect and is not exercised here.

Behavioral contracts guarded here
---------------------------------
focus guard
  C2  When a tracked text-input widget has focus, every handler is suppressed
      (no viz action fires).

active-tab guard
  C3  When the viz tab is not the active main tab, every handler is suppressed.

on_main_tab_changed
  C4  Records the newly-selected tag and dispatches the viz-focus callback only
      when the selected tag is the viz tab (this backs the active-tab guard).

_viz_tab_is_active
  C5  Returns True for the stashed viz-tab tag and False for any other stashed
      tag (tab_bar value carries the active child TAG, not its label); this is
      the predicate the active-tab guard reads.
"""

from __future__ import annotations

import pytest

from floodlight_gui.tabs.visualization import keyboard
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def viz_calls(monkeypatch):
    """Replace the viz action functions with recorders and return the call log.

    Each recorder appends ``(name, args)`` so a handler's dispatch decision is
    observable without invoking real playback.
    """
    calls = []

    def _rec(name):
        def _fn(*args):
            calls.append((name, args))

        return _fn

    monkeypatch.setattr(keyboard._viz, "_toggle_play_pause", _rec("toggle"))
    monkeypatch.setattr(keyboard._viz, "_jump_frames", _rec("jump"))
    monkeypatch.setattr(keyboard._viz, "_jump_to_period_start", _rec("start"))
    monkeypatch.setattr(keyboard._viz, "_jump_to_period_end", _rec("end"))
    monkeypatch.setattr(keyboard._viz, "_on_viz_tab_focused", _rec("focused"))
    return calls


# --------------------------------------------------------------------------- #
# guards                                                                        #
# --------------------------------------------------------------------------- #


_ALL_HANDLERS = [
    keyboard._on_space,
    keyboard._on_left,
    keyboard._on_right,
    keyboard._on_home,
    keyboard._on_end,
]


@pytest.mark.parametrize("handler", _ALL_HANDLERS)
def test_focus_guard_suppresses_all_handlers(monkeypatch, viz_calls, handler):
    """C2: a focused text input suppresses every shortcut."""
    monkeypatch.setattr(keyboard, "_any_text_input_focused", lambda: True)
    monkeypatch.setattr(keyboard, "_viz_tab_is_active", lambda: True)
    handler(sender=None, app_data=None)
    assert viz_calls == []


@pytest.mark.parametrize("handler", _ALL_HANDLERS)
def test_inactive_tab_guard_suppresses_all_handlers(monkeypatch, viz_calls, handler):
    """C3: an inactive viz tab suppresses every shortcut."""
    monkeypatch.setattr(keyboard, "_any_text_input_focused", lambda: False)
    monkeypatch.setattr(keyboard, "_viz_tab_is_active", lambda: False)
    handler(sender=None, app_data=None)
    assert viz_calls == []


# --------------------------------------------------------------------------- #
# on_main_tab_changed                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "selected_tag, expect_focus",
    [
        ("viz_tab", True),  # selecting the viz tab dispatches the focus callback
        ("load_tab", False),  # selecting another tab records but does not dispatch
    ],
)
def test_on_main_tab_changed_records_and_dispatches(
    monkeypatch, viz_calls, selected_tag, expect_focus
):
    """C4: the selected tag is stashed; the focus callback fires only for viz_tab."""
    monkeypatch.setattr(keyboard, "_active_main_tab", {"value": None})
    keyboard.on_main_tab_changed(sender="main_tab_bar", app_data=selected_tag)
    assert keyboard._active_main_tab["value"] == selected_tag
    assert (("focused", ()) in viz_calls) is expect_focus


# --------------------------------------------------------------------------- #
# _viz_tab_is_active                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "stashed_tag, expected",
    [
        ("viz_tab", True),  # tab_bar value is the active child TAG
        ("inspect_tab", False),
    ],
)
def test_viz_tab_is_active_reads_stashed_tag(monkeypatch, stashed_tag, expected):
    """C5: activeness is decided by the stashed active-tab TAG string."""
    monkeypatch.setattr(keyboard, "_active_main_tab", {"value": stashed_tag})
    monkeypatch.setattr(keyboard, "dpg", make_dpg_stub())
    assert keyboard._viz_tab_is_active() is expected
