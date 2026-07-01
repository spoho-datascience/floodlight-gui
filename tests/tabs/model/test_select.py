"""Behavioral contracts for ``floodlight_gui.tabs.model.select``.

The select layer decides WHICH model and WHICH (period, team) scope reach the
fit producer. Most of its surface is visible build chrome (the help/outputs/
params cascade, the player-checkbox tabs, the slot labels) and is dropped here.
What stays are the silent-corrupting decisions: the active-model-key resolution
(a wrong key fits the wrong model), the arity-aware selector rebuild and the
distinct-per-slot multi-team default (a stale team count or a duplicated slot
silently fits a team against itself), the output-toggle EMIT contract, and the
cached internal period scope. The DPG toolkit is the seam; the selector widget,
the category resolver, and the params/results sub-modules are stubbed.

Behavioral contracts guarded here
---------------------------------
active resolution
  C1  ``active_category`` returns the resolved category, or the default when the
      bar resolves to nothing; ``active_model_key`` reads the per-category map
      (the key that reaches the fit producer).

_on_output_toggle
  C5  Writes the new tick to state, refreshes that model's leaves, and emits
      ``MODEL_OUTPUTS_CHANGED`` (emit-routing contract).

arity-aware selector
  C7  ``ensure_arity`` re-renders only when the model arity differs from the
      mounted count, updating the mounted count to the new arity (a stale count
      silently mounts the wrong number of team slots).

refresh
  C9  Multi-team combos default each slot to a DISTINCT team so an out-of-the-box
      multi-team fit never feeds the same team to two XY slots (fitting a team
      against itself produces invalid overlap results).

on_selection_change
  C10 Caches the bridged internal period scope (None for the "All" sentinel).

_slot_identifier
  C11 Resolves a player slot to an identifier using the priority
      pid > xid > col_index. A wrong identifier silently feeds the wrong
      player's data into the resolver/export, so the chosen field for every
      present/absent combination is guarded.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import floodlight_gui.tabs.model.select as select_mod
from floodlight_gui.core.event_bus import Events
from floodlight_gui.tabs._shared.broadcast import ALL_SENTINEL
from floodlight_gui.tabs.model import labels, select, state
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def stub(monkeypatch):
    """Install a fake DPG into the select module and return it."""
    s = make_dpg_stub()
    monkeypatch.setattr(select_mod, "dpg", s)
    return s


@pytest.fixture
def patch_selector(monkeypatch):
    """Stub the period/team selector and record its (parent, prefix, team_count) calls."""
    calls: list[dict] = []

    def _sel(parent, period_cb, team_cb, tag_prefix, team_count=1):
        calls.append({"parent": parent, "prefix": tag_prefix, "team_count": team_count})

    monkeypatch.setattr(select_mod, "period_team_selector", _sel)
    return calls


def _app(divisions=("firstHalf",), teams=("Home", "Away")):
    """Return an app double exposing the methods select.py reads."""
    return SimpleNamespace(
        get_temporal_divisions=lambda: list(divisions),
        get_team_names=lambda: list(teams),
    )


# --------------------------------------------------------------------------- #
# active resolution (which model key reaches the fit producer)                 #
# --------------------------------------------------------------------------- #


def test_active_category_resolves_or_defaults(stub, monkeypatch):
    """C1: active_category returns the resolved bar value, else the default; key reads state."""
    monkeypatch.setattr(select_mod, "resolve_active_category_from_bar", lambda *a, **k: "geometry")
    assert select.active_category() == "geometry"
    monkeypatch.setattr(select_mod, "resolve_active_category_from_bar", lambda *a, **k: None)
    assert select.active_category() == labels.DEFAULT_CATEGORY

    state.current_model_by_category["geometry"] = "centroid"
    monkeypatch.setattr(select_mod, "resolve_active_category_from_bar", lambda *a, **k: "geometry")
    assert select.active_model_key() == "centroid"


# --------------------------------------------------------------------------- #
# _on_output_toggle (emit-routing)                                             #
# --------------------------------------------------------------------------- #


def test_on_output_toggle_writes_state_refreshes_and_emits(stub, monkeypatch):
    """C5: toggling an output writes state, refreshes that model's leaves, and emits once."""
    import floodlight_gui.tabs.model.results as results_mod

    refreshed: list = []
    emitted: list = []
    monkeypatch.setattr(results_mod, "refresh_model_leaves", lambda mk: refreshed.append(mk))
    monkeypatch.setattr(select_mod.bus, "emit", lambda ev, **kw: emitted.append((ev, kw)))

    select._on_output_toggle(None, False, ("velocity", "velocity"))

    assert state.output_checked[("velocity", "velocity")] is False
    assert refreshed == ["velocity"]
    assert emitted == [(Events.MODEL_OUTPUTS_CHANGED, {"model_key": "velocity"})]


# --------------------------------------------------------------------------- #
# arity-aware selector (a stale count mounts the wrong team-slot count)         #
# --------------------------------------------------------------------------- #


def test_ensure_arity_rebuilds_only_on_change(stub, patch_selector, monkeypatch):
    """C7: ensure_arity re-renders only when arity differs, updating the mounted count."""
    monkeypatch.setattr(select, "refresh", lambda on_change: None)
    stub.existing_items.add(select.SELECTOR_PARENT)
    select_mod._mounted_team_count = 1

    # Same arity (velocity is single-team) -> no re-render.
    select.ensure_arity("velocity", lambda: None)
    assert patch_selector == []

    # Different arity (nearest_opponent is fit_xy_arity=2) -> re-render at 2.
    select.ensure_arity("nearest_opponent", lambda: None)
    assert select_mod._mounted_team_count == 2
    assert patch_selector[-1]["team_count"] == 2


# --------------------------------------------------------------------------- #
# refresh (distinct multi-team default -> never fit a team against itself)      #
# --------------------------------------------------------------------------- #


def test_refresh_multi_team_slots_default_to_distinct_teams(stub):
    """C9: multi-team slots exclude "All" and default to distinct teams per slot.

    A same-team default in both slots would silently fit a team against itself,
    producing invalid overlap results. Slot i must default to teams[i].
    """
    state.app_instance = _app(divisions=("firstHalf", "secondHalf"), teams=("Home", "Away"))
    for tag in ("model_team_combo_a", "model_team_combo_b"):
        stub.existing_items.add(tag)

    select.refresh(lambda: None)

    cfg = {c[1][0]: c[2] for c in stub.calls_of("configure_item")}
    assert ALL_SENTINEL not in cfg["model_team_combo_a"]["items"]
    assert ALL_SENTINEL not in cfg["model_team_combo_b"]["items"]
    assert cfg["model_team_combo_a"]["default_value"] == "Home"
    assert cfg["model_team_combo_b"]["default_value"] == "Away"


# --------------------------------------------------------------------------- #
# on_selection_change (cached period scope)                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "period_value, expected_internal",
    [
        ("First Half", "firstHalf"),
        (ALL_SENTINEL, None),
    ],
    ids=["specific", "all-sentinel"],
)
def test_on_selection_change_caches_internal_period(stub, period_value, expected_internal):
    """C10: the selection-change handler caches the bridged internal period scope."""
    stub.existing_items.add("model_period_combo")
    stub.values["model_period_combo"] = period_value
    select.on_selection_change()
    assert state.selected_period_internal == expected_internal


# --------------------------------------------------------------------------- #
# _slot_identifier (priority pid > xid > col_index)                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "pid, xid, col_index, expected",
    [
        # pid present always wins, regardless of the lower-priority fields.
        ("P7", 3, 5, "P7"),
        # pid absent (None or empty) falls through to xid as "x{xid}".
        (None, 3, 5, "x3"),
        ("", 3, 5, "x3"),
        # xid of 0 is a valid identifier (only None/"" skip the field).
        (None, 0, 5, "x0"),
        # pid and xid both absent fall through to "col_{col_index}".
        (None, None, 5, "col_5"),
        (None, "", 5, "col_5"),
        # col_index defaults to 0 when the slot lacks the attribute entirely.
        (None, None, None, "col_0"),
    ],
    ids=[
        "pid-wins",
        "xid-when-pid-none",
        "xid-when-pid-empty",
        "xid-zero-valid",
        "col-when-none",
        "col-when-xid-empty",
        "col-default-zero",
    ],
)
def test_slot_identifier_priority(pid, xid, col_index, expected):
    """C11: _slot_identifier picks pid > xid > col_index, the resolver priority order.

    A None or empty value at one level falls through to the next; an integer
    zero (xid or col_index) is a real identifier and does not fall through.
    """
    kwargs = {}
    if pid is not None:
        kwargs["pid"] = pid
    if xid is not None:
        kwargs["xid"] = xid
    if col_index is not None:
        kwargs["col_index"] = col_index
    slot = SimpleNamespace(**kwargs)
    assert select._slot_identifier(slot) == expected
