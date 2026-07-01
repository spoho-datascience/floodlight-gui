"""Mouse interaction handlers for the visualization tab drawlist.

Owns drawlist mouse move/click callbacks, player hit-testing, and the
player-info text update helpers.

DPG-aware: this module imports ``dearpygui`` at module scope because it lives
under ``tabs/`` (the DPG-aware layer); backend modules must not.

``_get_player_info_from_teamsheet`` reads ``state.app_instance`` (the
session-scoped app reference held in the visualization state module).

``_register_mouse_handlers`` imports timeline handlers from ``timeline.py``
directly so that both pitch and timeline callbacks are registered in the same
handler registry.
"""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.tabs.visualization import state

logger = logging.getLogger(__name__)

__all__ = [
    "_register_mouse_handlers",
    "_on_mouse_move",
    "_on_mouse_click",
    "_mouse_to_drawlist_local",
    "_is_visualization_tab_active",
    "_update_player_info_text",
    "_get_player_info_from_teamsheet",
]

# ---------------------------------------------------------------------------
# Drawlist tag (must match the tag used when the drawlist is created in controls.py)
# ---------------------------------------------------------------------------
_DRAWLIST_TAG = "viz_drawlist"


# ---------------------------------------------------------------------------
# Tab-active guard
# ---------------------------------------------------------------------------


def _is_visualization_tab_active() -> bool:
    """Return True when the Visualization tab content is visible."""
    try:
        if dpg.does_item_exist("viz_tab") and not dpg.is_item_shown("viz_tab"):
            return False

        # Direct visibility check is more reliable than tab-bar value matching
        # because DPG versions differ in the value type returned for the selected tab.
        if dpg.does_item_exist(_DRAWLIST_TAG):
            return dpg.is_item_shown(_DRAWLIST_TAG)

        return True
    except SystemError:  # DPG raises SystemError for missing or invalid items
        return True


# ---------------------------------------------------------------------------
# Coordinate mapping
# ---------------------------------------------------------------------------


def _mouse_to_drawlist_local(mouse_x, mouse_y):
    """Convert screen mouse coordinates to drawlist-local pixel coordinates.

    Parameters
    ----------
    mouse_x : float
        Screen-space horizontal position from DPG.
    mouse_y : float
        Screen-space vertical position from DPG.

    Returns
    -------
    tuple[float, float] or None
        ``(local_x, local_y)`` relative to the drawlist top-left, or ``None``
        when the drawlist does not exist, is hidden, or the mouse is outside it.
    """
    try:
        if not dpg.does_item_exist(_DRAWLIST_TAG):
            return None
        if not dpg.is_item_shown(_DRAWLIST_TAG):
            return None

        dl_min = dpg.get_item_rect_min(_DRAWLIST_TAG)
        dl_max = dpg.get_item_rect_max(_DRAWLIST_TAG)
        local_x = mouse_x - dl_min[0]
        local_y = mouse_y - dl_min[1]

        width = dl_max[0] - dl_min[0]
        height = dl_max[1] - dl_min[1]
        if local_x < 0 or local_y < 0 or local_x > width or local_y > height:
            return None

        return (local_x, local_y)
    except SystemError:  # DPG raises SystemError when querying missing/invalid items
        return None


# ---------------------------------------------------------------------------
# Player info helpers
# ---------------------------------------------------------------------------


def _player_identity(team, player_index):
    """Return ordered (label, value) identity pairs for a player from the teamsheet.

    xID is always the first pair. Remaining pairs are verbatim teamsheet columns
    (no renaming, no fabrication). When there is no teamsheet (e.g. the ball or
    a dataset without one), only xID is returned.

    Parameters
    ----------
    team : str
        Team identifier ("Home", "Away", or "Ball").
    player_index : int
        Column index into the XY array.

    Returns
    -------
    list[tuple[str, str]]
        Ordered ``(column_name, value)`` pairs; xID is always the first entry.
    """
    from floodlight_gui.core.player_mapping import teamsheet_row_for

    teamsheet = getattr(state.app_instance, "teamsheet", None) if state.app_instance else None
    row = teamsheet_row_for(teamsheet, team, player_index)  # verbatim (col, value) pairs
    xid = dict(row).get("xID", str(player_index))
    return [("xID", xid)] + [(col, value) for col, value in row if col != "xID"]


def _update_player_info_text(hit, selected=False):
    """Update the ``viz_player_info`` widget with identity fields for the given hit.

    Parameters
    ----------
    hit : dict or None
        Player hit dict from ``PlayerRenderer.get_player_at`` (keys: ``team``,
        ``player_index``), or ``None`` to clear the display.
    selected : bool, default False
        When True, prepends "[SELECTED]" to the displayed text.
    """
    try:
        if hit is None:
            dpg.set_value("viz_player_info", "Hover over a player for details.")
            return
        prefix = "[SELECTED] " if selected else ""
        pairs = _player_identity(hit["team"], hit["player_index"])
        lines = [str(hit["team"])] + [f"{label}: {value}" for label, value in pairs]
        dpg.set_value("viz_player_info", prefix + "\n".join(lines))
    except SystemError as e:
        logger.warning("Error updating player info (DPG item missing): %s", e)


def _get_player_info_from_teamsheet(team, player_index):
    """Return a dict of available identity fields for a player; xID always present.

    Only fields the teamsheet actually carries are included (no fabricated
    placeholders). Used as the ``player_info`` payload on ``SELECTION_CHANGED``.

    Parameters
    ----------
    team : str
        Team identifier ("Home", "Away", or "Ball").
    player_index : int
        Column index into the XY array.

    Returns
    -------
    dict[str, str]
        Verbatim teamsheet column names mapped to their values; xID is always
        present.

    Notes
    -----
    Emitted as the ``player_info`` key in ``Events.SELECTION_CHANGED`` by
    ``_on_mouse_click``.
    """
    return dict(_player_identity(team, player_index))


# ---------------------------------------------------------------------------
# Mouse event callbacks
# ---------------------------------------------------------------------------


def _on_mouse_move(sender, app_data):
    """Handle mouse movement: update hover highlight and player-info widget (DPG callback).

    Clears the highlight when the cursor leaves the drawlist or stops hitting a
    player. Has no effect when the renderer is not yet initialized or when the
    Visualization tab is not visible.

    Parameters
    ----------
    sender : int or str
        DPG handler registry item (unused).
    app_data : tuple[float, float]
        Current screen-space mouse position ``(x, y)`` from DPG.
    """
    if not state.viz_state["initialized"] or state.viz_state["player_renderer"] is None:
        return
    if not _is_visualization_tab_active():
        return

    mouse_x, mouse_y = app_data
    local = _mouse_to_drawlist_local(mouse_x, mouse_y)

    renderer = state.viz_state["player_renderer"]

    if local is None:
        if state.viz_state["last_hover"] is not None:
            renderer.clear_highlight()
            state.viz_state["last_hover"] = None
            _update_player_info_text(None)
        return

    lx, ly = local
    hit = renderer.get_player_at(lx, ly)

    if hit is not None:
        team = hit["team"]
        idx = hit["player_index"]
        key = (team, idx)
        if state.viz_state["last_hover"] != key:
            renderer.highlight_player(team, idx)
            state.viz_state["last_hover"] = key
            _update_player_info_text(hit)
    else:
        if state.viz_state["last_hover"] is not None:
            renderer.clear_highlight()
            state.viz_state["last_hover"] = None
            _update_player_info_text(None)


def _on_mouse_click(sender, app_data):
    """Handle left-button mouse click: select a player and emit SELECTION_CHANGED (DPG callback).

    Ignores non-left-button clicks (``app_data != 0``). Deselects the current
    player and clears the info widget when the click misses all players.

    Parameters
    ----------
    sender : int or str
        DPG handler registry item (unused).
    app_data : int
        Mouse button index from DPG (0 = left button).

    Notes
    -----
    Emits ``Events.SELECTION_CHANGED`` with ``team``, ``player_index``, and
    ``player_info`` when a player is hit.
    """
    if not state.viz_state["initialized"] or state.viz_state["player_renderer"] is None:
        return
    if not _is_visualization_tab_active():
        return

    if app_data != 0:
        return

    mouse_pos = dpg.get_mouse_pos()
    local = _mouse_to_drawlist_local(mouse_pos[0], mouse_pos[1])
    if local is None:
        return

    lx, ly = local
    renderer = state.viz_state["player_renderer"]
    hit = renderer.get_player_at(lx, ly)

    if hit is not None:
        team = hit["team"]
        idx = hit["player_index"]
        renderer.select_player(team, idx)
        _update_player_info_text(hit, selected=True)

        player_info = _get_player_info_from_teamsheet(team, idx)
        bus.emit(
            Events.SELECTION_CHANGED,
            team=team,
            player_index=idx,
            player_info=player_info,
        )
    else:
        renderer.deselect_player()
        _update_player_info_text(None)


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


def _register_mouse_handlers():
    """Create the global DPG handler registry for drawlist mouse interaction.

    Registers pitch and timeline mouse callbacks in a single ``viz_mouse_handler``
    registry so they coexist without interfering. Timeline handlers perform their
    own rect-based hit tests and only act when the cursor is over the timeline.

    Notes
    -----
    Must be called exactly once during tab setup. Calling it a second time will
    raise a DPG duplicate-tag error.
    """
    # Import timeline handlers here (not at module scope) to avoid a circular
    # dependency between mouse_handlers and timeline at import time.
    from floodlight_gui.tabs.visualization.timeline import (
        _on_timeline_mouse_click,
        _on_timeline_mouse_move,
    )

    with dpg.handler_registry(tag="viz_mouse_handler"):
        dpg.add_mouse_move_handler(callback=_on_mouse_move)
        dpg.add_mouse_click_handler(callback=_on_mouse_click)
        # Timeline handlers gate on their own rect bounds and coexist with pitch handlers.
        dpg.add_mouse_click_handler(callback=_on_timeline_mouse_click)
        dpg.add_mouse_move_handler(callback=_on_timeline_mouse_move)
