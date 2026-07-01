"""Broadcast helpers: "All"-combo cross-product loop with transactional rollback.

DPG-free: this module operates directly on a DataStore and the singleton event bus;
it never imports dearpygui.

Emit-once invariant: every public function emits ``Events.XY_STACK_CHANGED`` exactly
once per call (or zero times when no leaf was mutated), regardless of how many
(period, team) combos are in scope.

"All" sentinel: ``ALL_SENTINEL`` is a reserved combo-item literal. Providers must not
emit it as a real period or team name; every broadcast function validates this before
mutating any state.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.core.periods import period_display_to_internal

__all__ = [
    "ALL_SENTINEL",
    "broadcast_apply_xy_op",
    "broadcast_undo_xy_op",
    "broadcast_reset_xy_op",
    "check_multi_xy_model_team_gate",
    "bridge_period_to_internal",
]

logger = logging.getLogger(__name__)

# Reserved combo-item literal for "all combos"; providers must never emit
# this string as a real period or team name.
ALL_SENTINEL: str = "All"


def broadcast_apply_xy_op(
    store,
    *,
    op_key: str,
    params: dict,
    periods: Iterable[str],
    teams: Iterable[str],
    app=None,
) -> None:
    """Apply ``op_key`` across the cross-product of ``periods`` x ``teams``.

    Halt-and-rollback semantics: if any combo's ``store.apply_xy_op`` raises,
    every successfully-applied prior combo is rolled back via
    ``store.undo_xy_op`` in reverse order, ``Events.XY_STACK_CHANGED`` is
    emitted once to refresh subscribers to the clean state, and the original
    exception is re-raised so the caller's BLE001 boundary can surface a
    friendly error.

    On full success the event is emitted once after the final combo.

    Parameters
    ----------
    store :
        DataStore instance. Calls ``store.apply_xy_op`` directly, bypassing
        the FloodlightApp wrapper, so that ``XY_STACK_CHANGED`` is not emitted
        per iteration.
    op_key : str
        Key into XY_OPS_REGISTRY (validated upstream by callers).
    params : dict
        Parameter dict for the op, collected from the tab's UI.
    periods : Iterable[str]
        Expanded list of internal-form period keys (e.g. "firstHalf"). Must not
        contain the reserved sentinel "All".
    teams : Iterable[str]
        Expanded list of team names (e.g. "Home", "Away", "Ball"). Must not
        contain the reserved sentinel "All".
    app :
        Optional FloodlightApp shell passed as the ``app=`` kwarg on
        ``bus.emit``. Defaults to None.

    Raises
    ------
    ValueError
        If ``periods`` or ``teams`` contains the reserved sentinel "All".
    Exception
        Re-raised from ``store.apply_xy_op`` after rolling back any
        successfully-applied prior combos.

    Notes
    -----
    Emits ``Events.XY_STACK_CHANGED`` exactly once per call (on success or
    after rollback).
    """
    periods_list = list(periods)
    teams_list = list(teams)

    # Reject the sentinel before mutating any state: a provider emitting "All"
    # as a real period/team name would silently produce wrong cross-products.
    if ALL_SENTINEL in periods_list or ALL_SENTINEL in teams_list:
        raise ValueError(
            f"Period/team list contains the reserved sentinel "
            f"{ALL_SENTINEL!r}. Providers must not emit {ALL_SENTINEL!r} "
            f"as a real period or team name."
        )

    combos = [(p, t) for p in periods_list for t in teams_list]
    successfully_applied: list[tuple[str, str]] = []

    try:
        for p, t in combos:
            store.apply_xy_op(p, t, op_key, params)
            successfully_applied.append((p, t))
    except Exception:
        # Halt-and-rollback: undo every prior success in reverse
        # order. DataStore.apply_xy_op self-rolls-back the just-pushed entry
        # on replay failure, so only the k-1 successful combos need undoing.
        for p, t in reversed(successfully_applied):
            try:
                store.undo_xy_op(p, t)
            except Exception:  # noqa: BLE001 -- best-effort rollback
                logger.exception(
                    "Rollback failed for combo (%s, %s); continuing",
                    p,
                    t,
                )
        # Emit once so subscribers refresh to the clean post-rollback state.
        bus.emit(Events.XY_STACK_CHANGED, app=app)
        raise

    # Full success: emit once after the loop.
    bus.emit(Events.XY_STACK_CHANGED, app=app)


def broadcast_undo_xy_op(
    store,
    *,
    periods: Iterable[str],
    teams: Iterable[str],
    app=None,
) -> None:
    """Undo the most-recent op across the cross-product of ``periods`` x ``teams``.

    Only mutates leaves whose stack is non-empty; emits ``Events.XY_STACK_CHANGED``
    once if at least one leaf was popped. If all leaves in scope are empty, no event
    is emitted.

    Calls ``store.undo_xy_op`` directly, bypassing the FloodlightApp wrapper, to
    preserve the emit-once contract (the wrapper emits per call).

    Parameters
    ----------
    store :
        DataStore instance. Calls ``store.undo_xy_op`` directly, bypassing
        the FloodlightApp wrapper.
    periods : Iterable[str]
        Expanded list of internal-form period keys. Must not contain the reserved
        sentinel "All".
    teams : Iterable[str]
        Expanded list of team names. Must not contain the reserved sentinel "All".
    app :
        Optional FloodlightApp shell passed as the ``app=`` kwarg on
        ``bus.emit``. Defaults to None.

    Raises
    ------
    ValueError
        If ``periods`` or ``teams`` contains the reserved sentinel "All".

    Notes
    -----
    Emits ``Events.XY_STACK_CHANGED`` at most once per call (zero times when no
    leaf had an op to undo).
    """
    periods_list = list(periods)
    teams_list = list(teams)

    # Reject the sentinel before mutating any state (mirrors apply path).
    if ALL_SENTINEL in periods_list or ALL_SENTINEL in teams_list:
        raise ValueError(
            f"Period/team list contains the reserved sentinel "
            f"{ALL_SENTINEL!r}. Providers must not emit {ALL_SENTINEL!r} "
            f"as a real period or team name."
        )

    # Only mutate non-empty leaves; emit once iff any pop succeeded.
    popped_any = False
    for p in periods_list:
        for t in teams_list:
            if store.get_xy_ops_stack(p, t):
                store.undo_xy_op(p, t)  # NOT app.undo_xy_op (wrapper emits per call)
                popped_any = True

    if popped_any:
        bus.emit(Events.XY_STACK_CHANGED, app=app)


def broadcast_reset_xy_op(
    store,
    *,
    periods: Iterable[str],
    teams: Iterable[str],
    app=None,
) -> None:
    """Reset (clear) the op stack across the cross-product of ``periods`` x ``teams``.

    Clears every targeted leaf via ``store.reset_xy_ops`` directly, bypassing the
    FloodlightApp wrapper (which emits ``XY_STACK_CHANGED`` per call), then emits
    the event once at the end.

    Parameters
    ----------
    store :
        DataStore instance. Calls ``store.reset_xy_ops`` directly, bypassing
        the FloodlightApp wrapper.
    periods : Iterable[str]
        Expanded list of internal-form period keys. Must not contain the reserved
        sentinel "All".
    teams : Iterable[str]
        Expanded list of team names. Must not contain the reserved sentinel "All".
    app :
        Optional FloodlightApp shell passed as the ``app=`` kwarg on
        ``bus.emit``. Defaults to None.

    Raises
    ------
    ValueError
        If ``periods`` or ``teams`` contains the reserved sentinel "All".

    Notes
    -----
    Emits ``Events.XY_STACK_CHANGED`` exactly once per call.
    """
    periods_list = list(periods)
    teams_list = list(teams)

    # Reject the sentinel before mutating any state (mirrors apply/undo paths).
    if ALL_SENTINEL in periods_list or ALL_SENTINEL in teams_list:
        raise ValueError(
            f"Period/team list contains the reserved sentinel "
            f"{ALL_SENTINEL!r}. Providers must not emit {ALL_SENTINEL!r} "
            f"as a real period or team name."
        )

    for p in periods_list:
        for t in teams_list:
            store.reset_xy_ops(p, t)  # NOT app.reset_xy_ops (wrapper emits per call)

    # Emit once after clearing every targeted leaf.
    bus.emit(Events.XY_STACK_CHANGED, app=app)


def check_multi_xy_model_team_gate(
    *,
    descriptor: dict,
    team_combo_value: str,
) -> None:
    """Raise if the active model is multi-XY and the team combo holds the "All" sentinel.

    The gate applies only to the single-team combo. Period "All" is independently
    allowed. Multi-team combos (team_combo_a / team_combo_b) never receive "All"
    and are out of scope here.

    Parameters
    ----------
    descriptor : dict
        MODEL_REGISTRY entry for the currently selected model.
    team_combo_value : str
        Current value of the single-team combo widget.

    Raises
    ------
    ValueError
        When ``descriptor['fit_xy_arity'] >= 2`` and
        ``team_combo_value == ALL_SENTINEL``.
    """
    arity = int(descriptor.get("fit_xy_arity", 1))
    if arity >= 2 and team_combo_value == ALL_SENTINEL:
        display_name = descriptor.get("display_name", "This model")
        raise ValueError(
            f"{display_name} requires {arity} distinct teams. "
            f"Pick specific Team A and Team B from the combo; "
            f"{ALL_SENTINEL!r} is not a valid target for this model."
        )


def bridge_period_to_internal(display_value):
    """Return the internal period key for a display value, or None for the "All" sentinel.

    When the period combo holds ``ALL_SENTINEL``, returns None to signal "no single
    internal period". Otherwise delegates to ``period_display_to_internal``.

    Parameters
    ----------
    display_value : str or None
        Display-form period value from a combo widget.

    Returns
    -------
    str or None
        Internal period key, or None when ``display_value`` is the "All"
        sentinel or empty.
    """
    if display_value == ALL_SENTINEL:
        return None
    return period_display_to_internal(display_value) if display_value else None
