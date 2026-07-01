"""Behavioral contracts for ``tabs/_shared/broadcast``.

These helpers expand an "All" period/team scope into a (period, team)
cross-product, mutate a DataStore one leaf at a time (bypassing the
FloodlightApp wrapper), and emit ``Events.XY_STACK_CHANGED`` exactly once
per call. The DataStore and the singleton event bus are the seams: the
store is a recording double, and emissions are captured by subscribing a
recorder to the singleton bus. The conftest ``_restore_bus_subscribers``
fixture isolates the bus per test.

Behavioral contracts guarded here
---------------------------------
ALL_SENTINEL
  C1  The reserved combo literal is the string ``"All"``.

broadcast_apply_xy_op
  C2  Applies the op once per (period, team) combo across the full
      cross-product, in row-major (period-outer, team-inner) order.
  C3  Emits ``XY_STACK_CHANGED`` exactly once on full success, forwarding
      the ``app`` kwarg, regardless of combo count.
  C4  On a mid-loop failure, rolls back every prior success in reverse
      order, emits the event once, and re-raises the original exception.

broadcast_undo_xy_op
  C5  Undoes only leaves whose op stack is non-empty and emits the event
      once iff at least one leaf was popped (zero emissions otherwise).

broadcast_reset_xy_op
  C6  Resets every targeted leaf and emits the event exactly once.

sentinel rejection (all three broadcasters)
  C7  A scope list containing ``ALL_SENTINEL`` raises ValueError before any
      store mutation and before any emission.

check_multi_xy_model_team_gate
  C8  Raises ValueError only when the model is multi-XY
      (``fit_xy_arity >= 2``) and the team combo holds the "All" sentinel;
      passes for single-XY models or a specific team.

bridge_period_to_internal
  C9  Returns None for the "All" sentinel or an empty value, and otherwise
      delegates to ``period_display_to_internal``.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs._shared.broadcast as bc
from floodlight_gui.core.event_bus import Events
from floodlight_gui.core.event_bus import bus as event_bus_singleton
from floodlight_gui.tabs._shared.broadcast import (
    ALL_SENTINEL,
    bridge_period_to_internal,
    broadcast_apply_xy_op,
    broadcast_reset_xy_op,
    broadcast_undo_xy_op,
    check_multi_xy_model_team_gate,
)


class _RecordingStore:
    """DataStore double recording every mutation the broadcaster makes.

    ``apply_xy_op``/``undo_xy_op``/``reset_xy_ops`` append their (period,
    team) to per-method logs so dispatch order and counts are assertable.
    ``get_xy_ops_stack`` returns a truthy list for leaves seeded as
    non-empty, letting the undo path's non-empty guard be exercised. An
    optional ``fail_on`` makes a single ``apply_xy_op`` combo raise.
    """

    def __init__(self, *, non_empty=None, fail_on=None):
        self.applied: list[tuple[str, str]] = []
        self.undone: list[tuple[str, str]] = []
        self.reset: list[tuple[str, str]] = []
        self._non_empty = set(non_empty or [])
        self._fail_on = fail_on

    def apply_xy_op(self, period, team, op_key, params):
        if (period, team) == self._fail_on:
            raise ValueError(f"simulated failure on {(period, team)}")
        self.applied.append((period, team))

    def undo_xy_op(self, period, team):
        self.undone.append((period, team))

    def reset_xy_ops(self, period, team):
        self.reset.append((period, team))

    def get_xy_ops_stack(self, period, team):
        return ["op"] if (period, team) in self._non_empty else []


@pytest.fixture
def emissions():
    """Capture every ``XY_STACK_CHANGED`` emission and its kwargs.

    Subscribes a recorder to the singleton bus for the test. The conftest
    autouse fixture clears and restores the subscriber map around each
    test, so this recorder does not leak.
    """
    captured: list[dict] = []

    def _rec(**kwargs):
        captured.append(kwargs)

    event_bus_singleton.subscribe(Events.XY_STACK_CHANGED, _rec, priority=-100)
    return captured


# --------------------------------------------------------------------------- #
# ALL_SENTINEL                                                                  #
# --------------------------------------------------------------------------- #


def test_all_sentinel_value():
    """C1: the reserved combo literal is the string ``"All"``."""
    assert ALL_SENTINEL == "All"


# --------------------------------------------------------------------------- #
# broadcast_apply_xy_op                                                         #
# --------------------------------------------------------------------------- #


def test_apply_covers_cross_product_in_order(emissions):
    """C2: apply runs once per combo, period-outer/team-inner."""
    store = _RecordingStore()
    broadcast_apply_xy_op(
        store,
        op_key="smooth",
        params={},
        periods=["firstHalf", "secondHalf"],
        teams=["Home", "Away"],
    )
    assert store.applied == [
        ("firstHalf", "Home"),
        ("firstHalf", "Away"),
        ("secondHalf", "Home"),
        ("secondHalf", "Away"),
    ]


def test_apply_emits_once_on_success(emissions):
    """C3: full success emits the event once and forwards ``app``."""
    store = _RecordingStore()
    sentinel_app = object()
    broadcast_apply_xy_op(
        store,
        op_key="smooth",
        params={},
        periods=["firstHalf", "secondHalf"],
        teams=["Home", "Away"],
        app=sentinel_app,
    )
    assert len(emissions) == 1
    assert emissions[0]["app"] is sentinel_app


def test_apply_rolls_back_and_reraises_on_failure(emissions):
    """C4: a failing combo rolls back prior successes in reverse and re-raises."""
    store = _RecordingStore(fail_on=("firstHalf", "Away"))
    with pytest.raises(ValueError):
        broadcast_apply_xy_op(
            store,
            op_key="smooth",
            params={},
            periods=["firstHalf"],
            teams=["Home", "Away", "Ball"],
        )
    # Only ("firstHalf", "Home") applied before the failure; it is undone.
    assert store.applied == [("firstHalf", "Home")]
    assert store.undone == [("firstHalf", "Home")]
    # One emission to refresh subscribers to the clean post-rollback state.
    assert len(emissions) == 1


# --------------------------------------------------------------------------- #
# broadcast_undo_xy_op                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "non_empty, expected_undone, expected_emits",
    [
        # Two leaves carry ops; both pop, one emission.
        (
            [("firstHalf", "Home"), ("secondHalf", "Away")],
            [("firstHalf", "Home"), ("secondHalf", "Away")],
            1,
        ),
        # No leaf has an op; nothing pops, no emission.
        ([], [], 0),
    ],
)
def test_undo_skips_empty_and_emits_iff_popped(
    emissions, non_empty, expected_undone, expected_emits
):
    """C5: undo touches only non-empty leaves and emits once iff any popped."""
    store = _RecordingStore(non_empty=non_empty)
    broadcast_undo_xy_op(
        store,
        periods=["firstHalf", "secondHalf"],
        teams=["Home", "Away"],
    )
    assert store.undone == expected_undone
    assert len(emissions) == expected_emits


# --------------------------------------------------------------------------- #
# broadcast_reset_xy_op                                                         #
# --------------------------------------------------------------------------- #


def test_reset_clears_every_leaf_and_emits_once(emissions):
    """C6: reset clears all targeted leaves and emits the event once."""
    store = _RecordingStore()
    broadcast_reset_xy_op(
        store,
        periods=["firstHalf", "secondHalf"],
        teams=["Home", "Away"],
    )
    assert store.reset == [
        ("firstHalf", "Home"),
        ("firstHalf", "Away"),
        ("secondHalf", "Home"),
        ("secondHalf", "Away"),
    ]
    assert len(emissions) == 1


# --------------------------------------------------------------------------- #
# sentinel rejection                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fn",
    [broadcast_apply_xy_op, broadcast_undo_xy_op, broadcast_reset_xy_op],
)
@pytest.mark.parametrize(
    "periods, teams",
    [
        ([ALL_SENTINEL], ["Home"]),
        (["firstHalf"], [ALL_SENTINEL]),
    ],
)
def test_sentinel_in_scope_raises_before_mutation(emissions, fn, periods, teams):
    """C7: a scope holding the sentinel raises before any mutation or emit."""
    store = _RecordingStore()
    kwargs = {"periods": periods, "teams": teams}
    if fn is broadcast_apply_xy_op:
        kwargs |= {"op_key": "smooth", "params": {}}
    with pytest.raises(ValueError):
        fn(store, **kwargs)
    assert store.applied == []
    assert store.undone == []
    assert store.reset == []
    assert emissions == []


# --------------------------------------------------------------------------- #
# check_multi_xy_model_team_gate                                               #
# --------------------------------------------------------------------------- #


def test_multi_xy_gate_blocks_all_for_multi_team():
    """C8: a multi-XY model with team combo "All" raises ValueError."""
    descriptor = {"fit_xy_arity": 2, "display_name": "NearestOpponent"}
    with pytest.raises(ValueError):
        check_multi_xy_model_team_gate(descriptor=descriptor, team_combo_value=ALL_SENTINEL)


@pytest.mark.parametrize(
    "descriptor, team_value",
    [
        # Single-XY model: "All" is allowed.
        ({"fit_xy_arity": 1}, ALL_SENTINEL),
        # Arity defaults to 1 when absent: "All" is allowed.
        ({}, ALL_SENTINEL),
        # Multi-XY model but a specific team chosen: allowed.
        ({"fit_xy_arity": 2}, "Home"),
    ],
)
def test_multi_xy_gate_allows(descriptor, team_value):
    """C8: single-XY or a specific team passes the gate without raising."""
    check_multi_xy_model_team_gate(descriptor=descriptor, team_combo_value=team_value)


# --------------------------------------------------------------------------- #
# bridge_period_to_internal                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", [ALL_SENTINEL, "", None])
def test_bridge_returns_none_for_all_or_empty(value):
    """C9: the sentinel or an empty/None value maps to None."""
    assert bridge_period_to_internal(value) is None


def test_bridge_delegates_to_period_mapping(monkeypatch):
    """C9: a real display value is delegated to ``period_display_to_internal``."""
    seen = []

    def _fake(display):
        seen.append(display)
        return "INTERNAL_KEY"

    monkeypatch.setattr(bc, "period_display_to_internal", _fake)
    result = bridge_period_to_internal("First Half")
    assert result == "INTERNAL_KEY"
    assert seen == ["First Half"]
