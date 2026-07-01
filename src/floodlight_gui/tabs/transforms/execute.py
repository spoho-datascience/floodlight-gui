"""Transforms tab producer: Apply, Undo, Reset callbacks and broadcast routing.

DPG-aware module (imports dearpygui at module scope); lives under tabs/.

Broadcast scope is resolved once by ``select._resolve_scope`` (the single
point for ALL_SENTINEL detection and cross-product expansion). Broadcast scope
routes through the ``broadcast_*`` helpers, which emit XY_STACK_CHANGED once
per action; single scope routes through the ``app.*`` wrappers, which also
emit once each.
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.registry.transforms import TRANSFORM_REGISTRY
from floodlight_gui.tabs._shared.broadcast import (
    broadcast_apply_xy_op,
    broadcast_reset_xy_op,
    broadcast_undo_xy_op,
)
from floodlight_gui.tabs._shared.error_helpers import friendly_error_message, show_error_modal
from floodlight_gui.tabs._shared.state_views import render_empty, render_error
from floodlight_gui.tabs.transforms import state

logger = logging.getLogger(__name__)

__all__ = [
    "_apply_clicked",
    "_undo_clicked",
    "_reset_target_clicked",
    "_reset_all_clicked",
]

_STATE_VIEW = "transforms_state_view_container"


# ---------------------------------------------------------------------------
# Shared state-view and error primitives
# ---------------------------------------------------------------------------
def _show_empty(message: str) -> None:
    """Show the state-view container with an empty-state banner."""
    if dpg.does_item_exist(_STATE_VIEW):
        try:
            dpg.configure_item(_STATE_VIEW, show=True)
            render_empty(_STATE_VIEW, message)
        except SystemError:
            pass


def _clear_state_view() -> None:
    """Hide and clear the state-view container after a successful action."""
    if dpg.does_item_exist(_STATE_VIEW):
        try:
            dpg.delete_item(_STATE_VIEW, children_only=True)
            dpg.configure_item(_STATE_VIEW, show=False)
        except SystemError:
            pass


def _surface_error(e: Exception, *, context: str, suggested_fix: str) -> None:
    """Update status, render an error banner, and open the error modal.

    Called at the DPG callback boundary on both the broadcast and single apply
    paths. Never re-raises (the render loop must not crash).

    Parameters
    ----------
    e : Exception
        The caught exception.
    context : str
        Short description of the failing operation (shown in the modal header).
    suggested_fix : str
        Actionable hint shown in the error banner and modal body.
    """
    logger.exception("Transform apply failed: %s", e)
    dpg.set_value("transforms_status", f"Error: {friendly_error_message(e)}")
    if dpg.does_item_exist(_STATE_VIEW):
        try:
            dpg.configure_item(_STATE_VIEW, show=True)
            render_error(_STATE_VIEW, e, suggested_fix=suggested_fix)
        except SystemError:
            pass
    with contextlib.suppress(SystemError):
        show_error_modal("transforms_tab", e, context=context, suggested_fix=suggested_fix)


def _post_action_refresh() -> None:
    """Refresh the target summary and Applied Stack after any action."""
    from floodlight_gui.tabs.transforms.results import _refresh_stack_display
    from floodlight_gui.tabs.transforms.select import _update_target_summary

    _update_target_summary()
    _refresh_stack_display()


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def _apply_clicked(sender, app_data):
    """Apply the selected XY op to the targeted (period, team) scope (DPG callback).

    Reads the active op key from ``select._get_active_op_key``, resolves
    broadcast vs single scope via ``select._resolve_scope``, collects widget
    params via ``params._collect_params``, then dispatches:

    - Broadcast scope: ``broadcast_apply_xy_op`` applies to all
      (period, team) combinations and emits XY_STACK_CHANGED once.
    - Single scope: ``app_instance.apply_xy_op`` applies to one leaf and
      emits XY_STACK_CHANGED once.

    Notes
    -----
    Side-effects: mutates the XY op stack in the data store for each targeted
    (period, team) leaf; emits XY_STACK_CHANGED via the broadcast helper or
    the app wrapper.
    """
    if state.app_instance is None or not state.app_instance.loaded_data:
        dpg.set_value("transforms_status", "No data loaded")
        _show_empty("No data loaded -- open the Load tab and pick a file or sample dataset.")
        return
    from floodlight_gui.tabs.transforms.params import _collect_params
    from floodlight_gui.tabs.transforms.select import (
        _get_active_op_key,
        _resolve_scope,
    )

    op_key = _get_active_op_key()
    if op_key is None:
        return

    is_broadcast, period_internal, team, periods_list, teams_list = _resolve_scope()
    params = _collect_params()
    display_name = TRANSFORM_REGISTRY[op_key]["display_name"]

    if is_broadcast:
        # Broadcast guard: route through the shared helper so XY_STACK_CHANGED
        # is emitted exactly once. "All teams" includes Ball.
        n_combos = len(periods_list) * len(teams_list)
        try:
            broadcast_apply_xy_op(
                state.app_instance.store,
                op_key=op_key,
                params=params,
                periods=periods_list,
                teams=teams_list,
                app=state.app_instance,
            )
            dpg.set_value("transforms_status", f"Applied {display_name} to {n_combos} combos")
            _clear_state_view()
        except Exception as e:  # noqa: BLE001 -- broadcast callback boundary
            _surface_error(
                e,
                context=f"Transform op {op_key!r} broadcast failed.",
                suggested_fix=(
                    "Broadcast halted on the first failing combo; "
                    "all prior combos were rolled back."
                ),
            )
        _post_action_refresh()
        return

    # Single-leaf scope.
    if not period_internal or period_internal == "No data loaded":
        dpg.set_value("transforms_status", "No data selected")
        return
    if state.app_instance._get_pristine_xy(period_internal, team) is None:
        dpg.set_value("transforms_status", f"No XY for {period_internal}, {team}")
        return
    try:
        state.app_instance.apply_xy_op(period_internal, team, op_key, params)
        dpg.set_value(
            "transforms_status",
            f"Applied {display_name} to {period_internal}, {team}",
        )
        _clear_state_view()
    except Exception as e:  # noqa: BLE001 -- DPG callback boundary; render loop must not crash
        _surface_error(
            e,
            context=f"Transform op {op_key!r} failed.",
            suggested_fix="Check op parameters; some transforms reject out-of-range values.",
        )
        return
    _post_action_refresh()


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------
def _undo_clicked(sender, app_data):
    """Undo the most recent XY op for the targeted (period, team) scope (DPG callback).

    Resolves broadcast vs single scope via ``select._resolve_scope``.

    - Broadcast scope: ``broadcast_undo_xy_op`` undoes the last op on all
      targeted leaves and emits XY_STACK_CHANGED once.
    - Single scope: ``app_instance.undo_xy_op`` undoes for one leaf and
      emits XY_STACK_CHANGED once.

    Notes
    -----
    Side-effects: mutates the XY op stack in the data store for each targeted
    leaf; emits XY_STACK_CHANGED via the broadcast helper or the app wrapper.
    "All teams" includes Ball in broadcast scope.
    """
    if state.app_instance is None:
        return
    from floodlight_gui.tabs.transforms.select import _resolve_scope

    is_broadcast, period_internal, team, periods_list, teams_list = _resolve_scope()
    if is_broadcast:
        # Broadcast scope: "All teams" includes Ball.
        try:
            broadcast_undo_xy_op(
                state.app_instance.store,
                periods=periods_list,
                teams=teams_list,
                app=state.app_instance,
            )
            dpg.set_value("transforms_status", "Undid last op for affected leaves")
        except Exception as e:  # noqa: BLE001 -- broadcast callback boundary
            logger.exception("Transform broadcast undo failed: %s", e)
            dpg.set_value("transforms_status", f"Error: {friendly_error_message(e)}")
        _post_action_refresh()
        return

    # Single-leaf scope.
    if not state.app_instance.get_xy_ops_stack(period_internal, team):
        dpg.set_value("transforms_status", "Nothing to undo")
        return
    state.app_instance.undo_xy_op(period_internal, team)
    dpg.set_value("transforms_status", f"Undid last op for {period_internal}, {team}")
    _post_action_refresh()


# ---------------------------------------------------------------------------
# Reset target
# ---------------------------------------------------------------------------
def _reset_target_clicked(sender, app_data):
    """Reset XY ops for the targeted (period, team) leaves (DPG callback).

    Broadcast detection (period or team == ALL_SENTINEL) is resolved by
    ``_resolve_scope``, which reads the raw combo values before any
    "All -> None" bridging; bridging first would make reset_xy_ops silently
    no-op. Broadcast expands across periods_list x teams_list; specific picks
    hit a single leaf.

    Routes through ``broadcast_reset_xy_op`` in broadcast scope so
    XY_STACK_CHANGED is emitted exactly once (a per-combo loop would emit N
    times).

    Notes
    -----
    Side-effects: clears the XY op stack in the data store for each targeted
    leaf; emits XY_STACK_CHANGED via the broadcast helper or the app wrapper.
    """
    if state.app_instance is None:
        return
    from floodlight_gui.tabs.transforms.select import _resolve_scope

    is_broadcast, period_internal, team, periods_list, teams_list = _resolve_scope()
    if is_broadcast:
        # Route through the shared helper so Reset Target emits XY_STACK_CHANGED
        # once, consistent with apply/undo.
        n_combos = len(periods_list) * len(teams_list)
        try:
            broadcast_reset_xy_op(
                state.app_instance.store,
                periods=periods_list,
                teams=teams_list,
                app=state.app_instance,
            )
            dpg.set_value("transforms_status", f"Reset ops for {n_combos} combos")
        except Exception as e:  # noqa: BLE001 -- broadcast callback boundary
            logger.exception("Transform broadcast reset failed: %s", e)
            dpg.set_value("transforms_status", f"Error: {friendly_error_message(e)}")
        _post_action_refresh()
        return

    # Single-leaf scope.
    if not period_internal or period_internal == "No data loaded":
        dpg.set_value("transforms_status", "No data selected")
        return
    state.app_instance.reset_xy_ops(period_internal, team)
    dpg.set_value("transforms_status", f"Reset ops for {period_internal}, {team}")
    _post_action_refresh()


# ---------------------------------------------------------------------------
# Reset all
# ---------------------------------------------------------------------------
def _reset_all_clicked(sender, app_data):
    """Reset all XY ops across every (period, team) leaf (DPG callback).

    Notes
    -----
    Side-effects: clears the entire XY op stack in the data store; emits
    XY_STACK_CHANGED via ``app_instance.reset_xy_ops``.
    """
    if state.app_instance is None:
        return
    state.app_instance.reset_xy_ops()
    dpg.set_value("transforms_status", "Reset all transform ops")
    _post_action_refresh()
