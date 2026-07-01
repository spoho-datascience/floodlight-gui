"""Transforms tab selection layer: resolve the active op and the (period, team) slice.

Responsibilities:
- Determine the active transform op key from the category tab_bar.
- Resolve the period/team scope (single leaf or broadcast) for apply/undo/reset.
- Populate the period/team combos on data load.
- Update the target-summary label for user feedback.

Layering: DPG-aware (imports ``dearpygui`` at module scope). The active-category
resolution reads ``tab_bar.get_value``, which returns the active child tab's TAG
(str or integer alias-id), not the display label. Every call site that reads the
tab_bar must go through ``resolve_active_category`` to handle both return shapes.
"""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.periods import period_internal_to_display
from floodlight_gui.tabs._shared.broadcast import ALL_SENTINEL, bridge_period_to_internal
from floodlight_gui.tabs._shared.tab_bar import resolve_active_category
from floodlight_gui.tabs.transforms import state

logger = logging.getLogger(__name__)

__all__ = [
    "_on_category_tab_changed",
    "_get_active_op_key",
    "refresh_transforms_display",
    "_get_target",
    "_resolve_scope",
    "_update_target_summary",
]


def _resolve_scope():
    """Resolve the period/team selection into its broadcast shape and concrete leaf lists.

    All three apply/undo/reset callbacks need the same answer: which (period, team)
    leaves the current selection addresses, and whether this is a broadcast ("All")
    or a single leaf. This is the single source of that computation.

    CRITICAL ordering: read the RAW combo values and test against ALL_SENTINEL
    BEFORE bridging "All" to None. Bridging first turns a broadcast into an empty
    single-leaf and silently no-ops.

    Returns
    -------
    is_broadcast : bool
        True when either combo holds ALL_SENTINEL.
    period_internal : str or None
        Single bridged period for single-leaf scope; None in broadcast scope.
    team : str or None
        Single team for single-leaf scope; None in broadcast scope.
    periods : list of str
        Expanded internal-period list (no sentinel).
    teams : list of str
        Expanded team list (no sentinel).
    """
    app = state.app_instance
    raw_period = (
        dpg.get_value("transforms_period_combo")
        if dpg.does_item_exist("transforms_period_combo")
        else None
    )
    raw_team = (
        dpg.get_value("transforms_team_combo")
        if dpg.does_item_exist("transforms_team_combo")
        else None
    )

    period_is_all = raw_period == ALL_SENTINEL
    team_is_all = raw_team == ALL_SENTINEL
    is_broadcast = period_is_all or team_is_all

    if period_is_all:
        periods = app.get_temporal_divisions()
    else:
        internal = bridge_period_to_internal(raw_period)
        periods = [internal] if internal else app.get_temporal_divisions()
    teams = app.get_team_names() if team_is_all else [raw_team]

    period_internal = None if period_is_all else bridge_period_to_internal(raw_period)
    team = None if team_is_all else raw_team
    return is_broadcast, period_internal, team, periods, teams


def _on_category_tab_changed(sender, app_data):
    """Rebuild the params panel when the active category tab changes.

    DPG fires this callback on the ``tab_bar`` whenever the active child tab
    changes. ``app_data`` is the new active tab's tag (str) or alias-id (int):
    ``tab_bar.get_value`` returns the child tag, not the display label. We
    resolve it to a category key and re-trigger ``_on_op_changed`` so the
    params container reflects the new category's selected op.

    Parameters
    ----------
    sender : int or str
        DPG tag of the tab_bar widget (unused).
    app_data : str or int
        Tag (or alias-id) of the newly active tab as returned by DPG.
    """
    from floodlight_gui.tabs.transforms.controls import _cat_ops
    from floodlight_gui.tabs.transforms.params import _on_op_changed

    category = resolve_active_category(app_data, prefix="transforms", valid_categories=_cat_ops)
    if category is None:
        return
    _on_op_changed(f"transforms_op_combo_{category}", None)


def _get_active_op_key() -> str | None:
    """Return the op key currently selected in the active category tab.

    Reads ``tab_bar.get_value``, which returns the active child tab's TAG as a
    string (e.g. ``"transforms_category_filter_tab"``) or as an integer alias-id.
    Both shapes are handled by ``resolve_active_category``; the category key is
    extracted from the tag suffix and used to look up ``_current_op_key``.

    Returns
    -------
    str or None
        The op key for the active category, or None when the tab_bar is not yet
        constructed or the active tag is unrecognized.
    """
    from floodlight_gui.tabs.transforms.controls import _cat_ops, _current_op_key

    bar_tag = "transforms_category_tab_bar"
    if not dpg.does_item_exist(bar_tag):
        return None
    raw = dpg.get_value(bar_tag)
    category = resolve_active_category(raw, prefix="transforms", valid_categories=_cat_ops)
    if category is None:
        category = "filter"  # cold-start fallback: tab_bar not yet rendered
    return _current_op_key.get(category)


def refresh_transforms_display():
    """Populate the period/team combos from the currently loaded dataset.

    Configures ``transforms_period_combo`` (display-form period labels) and
    ``transforms_team_combo`` from the app's temporal divisions and team names.
    Both combos prepend "All" as the first item so the user can broadcast an op
    across the full (period x team) cross-product; "All" is also the default
    selection on every DATA_LOADED render.

    Notes
    -----
    ``transforms_period_combo`` items use display-form labels via
    ``period_internal_to_display``; ``bridge_period_to_internal`` converts them
    back to internal keys before any downstream call.
    Calls ``_update_target_summary`` and ``results._refresh_stack_display``
    after combo population.
    """
    if state.app_instance is None or not dpg.does_item_exist("transforms_period_combo"):
        return

    divs = state.app_instance.get_temporal_divisions() if state.app_instance.loaded_data else []
    teams = state.app_instance.get_team_names() if state.app_instance.loaded_data else []

    assert ALL_SENTINEL == "All"
    if divs:
        period_items = ["All"] + [period_internal_to_display(d) for d in divs]
    else:
        period_items = ["No data loaded"]
    team_items = ["All"] + list(teams) if teams else ["No data loaded"]

    if dpg.does_item_exist("transforms_period_combo"):
        dpg.configure_item("transforms_period_combo", items=period_items)
        dpg.set_value("transforms_period_combo", period_items[0])
    if dpg.does_item_exist("transforms_team_combo"):
        dpg.configure_item("transforms_team_combo", items=team_items)
        dpg.set_value("transforms_team_combo", team_items[0])

    _update_target_summary()
    from floodlight_gui.tabs.transforms import results as _results

    _results._refresh_stack_display()


def _get_target():
    """Return the (period_internal, team) tuple for apply/undo/reset callbacks.

    Reads the display-form value from ``transforms_period_combo`` and bridges it
    to the internal period key expected by ``app_instance`` calls.

    Returns
    -------
    period_internal : str or None
        Internal period key, or None when the combo holds "All" (broadcast scope).
    team : str
        Team name as selected in ``transforms_team_combo``.
    """
    period_display = dpg.get_value("transforms_period_combo")
    team = dpg.get_value("transforms_team_combo")
    # bridge_period_to_internal returns None for "All" (broadcast) and the
    # internal key for specific periods.
    period_internal = bridge_period_to_internal(period_display)
    return period_internal, team


def _update_target_summary():
    """Refresh the ``transforms_target_summary`` label from the current combo selection.

    Shows frame/player counts for a single-leaf selection, or a broadcast notice
    when either combo holds ALL_SENTINEL. Shows "Target: -" when no data is loaded.
    """
    period_display = (
        dpg.get_value("transforms_period_combo")
        if dpg.does_item_exist("transforms_period_combo")
        else None
    )
    team = (
        dpg.get_value("transforms_team_combo")
        if dpg.does_item_exist("transforms_team_combo")
        else None
    )
    if not state.app_instance or not state.app_instance.loaded_data:
        dpg.set_value("transforms_target_summary", "Target: -")
        return

    # Broadcast scope: no single XY to summarize; state the scope plainly.
    if period_display == ALL_SENTINEL or team == ALL_SENTINEL:
        dpg.set_value(
            "transforms_target_summary",
            f"Target: {period_display}, {team}  -  broadcast (all matching leaves)",
        )
        return

    period_internal = bridge_period_to_internal(period_display) if period_display else None
    xy = (
        state.app_instance.get_active_xy(period_internal, team)
        if hasattr(state.app_instance, "get_active_xy") and period_internal
        else None
    )
    if xy is None:
        dpg.set_value("transforms_target_summary", f"Target: {period_display}, {team} (no XY)")
        return
    try:
        n_frames, n_cols = xy.xy.shape
        n_players = n_cols // 2
        dpg.set_value(
            "transforms_target_summary",
            f"Target: {period_display}, {team}  -  {n_frames} frames, {n_players} players",
        )
    except (AttributeError, TypeError, ValueError):
        dpg.set_value("transforms_target_summary", f"Target: {period_display}, {team}")
