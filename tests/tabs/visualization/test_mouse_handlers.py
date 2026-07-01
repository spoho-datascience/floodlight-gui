"""Behavioral contracts for ``visualization.mouse_handlers``.

The mouse move/click callbacks are DPG hit-test orchestration over the player
renderer; their decisions are dominated by renderer calls and DPG widget writes.
The self-contained logic worth guarding is player-identity assembly: from a
teamsheet row, build the ordered ``(label, value)`` pairs with xID always first.
``teamsheet_row_for`` (the teamsheet accessor) is the seam and is stubbed so the
test asserts only the assembly order and the no-teamsheet fallback, not the
accessor's own parsing.

Behavioral contracts guarded here
---------------------------------
_player_identity
  C1  xID is always the first pair; the remaining teamsheet columns follow
      verbatim and in order, with no duplicate xID entry.
  C2  With no teamsheet row, the result is a single xID pair defaulting to the
      stringified player index.

_get_player_info_from_teamsheet
  C3  Returns the identity pairs as a dict with xID always present.
"""

from __future__ import annotations

import pytest

from floodlight_gui.tabs.visualization import mouse_handlers, state


@pytest.fixture
def stub_app(monkeypatch):
    """Give the state module a non-None app so the teamsheet branch is reached."""
    monkeypatch.setattr(state, "app_instance", type("App", (), {"teamsheet": {}})())


@pytest.fixture
def stub_row(monkeypatch):
    """Stub the teamsheet-row accessor; return a setter for the verbatim pairs.

    ``teamsheet_row_for`` lives in core.player_mapping and is imported lazily
    inside ``_player_identity``; patching it there intercepts the call.
    """
    import floodlight_gui.core.player_mapping as player_mapping

    def _set(pairs):
        monkeypatch.setattr(player_mapping, "teamsheet_row_for", lambda *a, **k: pairs)

    return _set


def test_player_identity_puts_xid_first_then_verbatim(stub_app, stub_row):
    """C1: xID leads; remaining columns follow verbatim with no xID duplication."""
    stub_row([("xID", "7"), ("player", "Alice"), ("position", "MID")])

    pairs = mouse_handlers._player_identity("Home", 7)

    assert pairs[0] == ("xID", "7")
    assert pairs == [("xID", "7"), ("player", "Alice"), ("position", "MID")]


def test_player_identity_no_teamsheet_yields_index_xid(stub_app, stub_row):
    """C2: with no teamsheet row, only an index-derived xID pair is returned."""
    stub_row([])

    pairs = mouse_handlers._player_identity("Ball", 3)

    assert pairs == [("xID", "3")]


def test_get_player_info_returns_dict_with_xid(stub_app, stub_row):
    """C3: the SELECTION_CHANGED payload is a dict whose xID is always present."""
    stub_row([("xID", "11"), ("jID", "9")])

    info = mouse_handlers._get_player_info_from_teamsheet("Away", 11)

    assert isinstance(info, dict)
    assert info["xID"] == "11"
    assert info["jID"] == "9"
