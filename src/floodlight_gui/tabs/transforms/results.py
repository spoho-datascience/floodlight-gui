"""Transforms tab results view: renders the applied-op stack (history) per period/team leaf.

DPG-aware: imports ``dearpygui`` at module scope (lives under ``tabs/``, the DPG layer).
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.periods import period_internal_to_display
from floodlight_gui.registry.transforms import (
    TRANSFORM_REGISTRY,
    format_stack_param_value,
)
from floodlight_gui.tabs._shared.broadcast import ALL_SENTINEL, bridge_period_to_internal
from floodlight_gui.tabs.transforms import state
from floodlight_gui.theme import INFO

logger = logging.getLogger(__name__)

__all__ = [
    "_refresh_stack_display",
    "_mk_stack_clean",
    "render_stack_leaf",
]


def _mk_stack_clean(key: str) -> str:
    """Return a DPG-tag-safe version of a period/team key for the stack display."""
    return key.replace(" ", "_").replace("-", "_").replace("/", "_").lower()


def render_stack_leaf(
    period_internal: str,
    team_name: str,
    parent_tag: str,
    *,
    app,
) -> None:
    """Render one Applied-Stack leaf body (compact ops-summary header).

    Contract (locked):
      - Empty stack: ONE ``add_text`` call,
        ``"Period: {p} | Team: {t} | Stack: (no transforms applied)"``.
      - N-op stack (N >= 1): N+1 ``add_text`` calls: a header line
        ``"Period: {p}  |  Team: {t}"`` (no "Stack:" prefix) followed by one
        ``"{display_name}({k=v, ...})"`` line per op.

    The vertical list (one line per op) avoids column overflow once multiple
    ops accumulate.

    Parameters
    ----------
    period_internal : str
        Internal period key (e.g. ``"HT1"``); converted to display form for the
        header via ``period_internal_to_display``.
    team_name : str
        Team display name as stored in the ops-stack key.
    parent_tag : str
        DPG tag of the parent ``tab`` item this leaf renders into (so
        ``dpg.add_text(parent=...)`` resolves correctly).
    app : FloodlightApp
        App instance (keyword-only); used for ``get_xy_ops_stack`` (header summary).
    """
    stack = (
        app.get_xy_ops_stack(period_internal, team_name) if hasattr(app, "get_xy_ops_stack") else []
    )
    period_display = period_internal_to_display(period_internal)

    if not stack:
        dpg.add_text(
            f"Period: {period_display}  |  Team: {team_name}  |  Stack: (no transforms applied)",
            parent=parent_tag,
            color=INFO,
        )
        return

    # Populated stack: header line + N op lines (N+1 add_text calls total).
    dpg.add_text(
        f"Period: {period_display}  |  Team: {team_name}",
        parent=parent_tag,
        color=INFO,
    )
    for op_key, params in stack:
        display = TRANSFORM_REGISTRY.get(op_key, {}).get("display_name", op_key)
        if params:
            pstr = ", ".join(f"{k}={format_stack_param_value(v)}" for k, v in params.items())
            line = f"  {display}({pstr})"
        else:
            line = f"  {display}"
        dpg.add_text(line, parent=parent_tag, color=INFO)


def _refresh_stack_display():
    """Refresh the Applied Stack display (single rebuild path).

    Always rebuilds the broadcast tab bar regardless of the current period/team
    selection. Specific picks land as a 1x1 cross product (one period tab, one
    team tab, one leaf); ALL_SENTINEL picks expand to the full cross product.

    Empty-stack leaves render via ``render_stack_leaf``, which shows a
    "(no transforms applied)" header when the stack is empty.

    Invariant: never call ``add_text`` with the tab-bar tag as parent; header
    text must target the tab-item ``parent_tag``, not the tab bar itself.
    """
    # Guard on the broadcast tab bar, not a legacy widget that no longer exists.
    if not dpg.does_item_exist("transforms_stack_broadcast_tab_bar"):
        return

    if state.app_instance is None or not state.app_instance.loaded_data:
        with contextlib.suppress(SystemError):
            dpg.delete_item("transforms_stack_broadcast_tab_bar", children_only=True)
            dpg.configure_item("transforms_stack_broadcast_tab_bar", show=False)
        return

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

    # Compute cross product (1x1 when both specific; full cross when both ALL).
    periods_list = (
        state.app_instance.get_temporal_divisions()
        if raw_period == ALL_SENTINEL
        else [bridge_period_to_internal(raw_period)]
    )
    teams_list = state.app_instance.get_team_names() if raw_team == ALL_SENTINEL else [raw_team]

    # Clear and rebuild children of the broadcast tab bar (always-rebuild pattern).
    dpg.delete_item("transforms_stack_broadcast_tab_bar", children_only=True)
    dpg.configure_item("transforms_stack_broadcast_tab_bar", show=True)

    for p_internal in periods_list:
        p_display = period_internal_to_display(p_internal)
        p_clean = _mk_stack_clean(p_internal)
        period_tab_tag = f"transforms_stack_period_tab_{p_clean}"
        team_bar_tag = f"transforms_stack_team_bar_{p_clean}"
        with dpg.tab(
            label=p_display,
            tag=period_tab_tag,
            parent="transforms_stack_broadcast_tab_bar",
        ):
            dpg.add_tab_bar(tag=team_bar_tag)
            for team_name in teams_list:
                t_clean = _mk_stack_clean(team_name)
                team_leaf_tag = f"transforms_stack_team_leaf_{p_clean}_{t_clean}"
                with dpg.tab(
                    label=team_name,
                    tag=team_leaf_tag,
                    parent=team_bar_tag,
                ):
                    render_stack_leaf(
                        p_internal,
                        team_name,
                        team_leaf_tag,
                        app=state.app_instance,
                    )
