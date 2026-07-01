"""Global playback keyboard handler for the visualization tab.

Mounts a DPG handler_registry with five key bindings (Space, Left, Right,
Home, End). Every handler is gated by two conditions: no known text-input
widget has DPG focus (focus guard), and the visualization tab is the active
main tab. Shortcuts call into the visualization subpackage directly.

Layering: DPG-aware (imports dearpygui at module scope). Lives under tabs/
so the DPG-free constraint does not apply here.
"""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.tabs import visualization as _viz

__all__ = ["register_global_handlers", "on_main_tab_changed"]

logger = logging.getLogger(__name__)


# Known text-input widget tags whose focus must swallow keys (focus guard).
# Covers the stable filename and jump inputs that create the primary UX
# risk (a user typing in a filename field accidentally toggling playback).
# Dynamic per-descriptor tags are not enumerable as a fixed tuple and are
# therefore not included.
_TEXT_INPUT_TAGS_KNOWN: tuple[str, ...] = (
    "event_jump",  # inspect: event row jump input
    "metrics_export_filename",  # metrics: CSV export filename
    "model_export_filename",  # model: CSV export filename
)


def _any_text_input_focused() -> bool:
    """Return True if any known text-input widget currently has DPG focus."""
    for tag in _TEXT_INPUT_TAGS_KNOWN:
        try:
            if dpg.does_item_exist(tag) and dpg.is_item_focused(tag):
                return True
        except SystemError:
            # Tag may have been deleted between the existence check and the focus check.
            continue
    return False


# Active main-tab tracking. DPG's is_item_visible("viz_tab") returns True for
# the tab strip item regardless of which tab is selected, so it cannot serve as
# the "viz tab is active" predicate. The tab_bar callback fires reliably on
# switch and is the authoritative signal.
_active_main_tab: dict[str, object] = {"value": None}


def on_main_tab_changed(sender, app_data, user_data=None):
    """Record the newly-selected main tab and dispatch the viz-focus event.

    Wired to ``main_tab_bar.callback`` in ``app.create_ui``. DPG passes the
    newly-selected tab's identifier as ``app_data`` (alias string when the tab
    carries a ``tag=...``, otherwise the integer item-id). Stashing it lets
    ``_viz_tab_is_active`` compare without calling ``get_value`` or
    ``is_item_visible``, which behave inconsistently across DPG versions for
    tab items.

    When the viz tab becomes active, dispatches to
    ``_viz._on_viz_tab_focused()`` so player-circle radii are recomputed after
    a tab switch.

    Parameters
    ----------
    sender : int or str
        DPG sender (the tab_bar tag); not used.
    app_data : str or int
        Tag or item-id of the newly-selected tab.
    user_data : object, optional
        Not used.

    Notes
    -----
    Side-effect: writes ``_active_main_tab["value"]`` and may call
    ``_viz._on_viz_tab_focused()``.
    """
    _active_main_tab["value"] = app_data
    # DPG passes the tag string "viz_tab" for a tab declared with tag="viz_tab".
    # Avoid dpg.get_alias_id here: it raises AccessViolation outside a live DPG
    # context and would break unit tests that call this callback directly.
    if isinstance(app_data, str) and app_data == "viz_tab":
        try:
            _viz._on_viz_tab_focused()
        except Exception:  # noqa: BLE001 -- tab-focus callback boundary; must not crash
            logger.debug("on_main_tab_changed: _on_viz_tab_focused dispatch failed", exc_info=True)


def _viz_tab_is_active() -> bool:
    """Return True only when viz_tab is the currently selected main tab.

    Reads the flag maintained by ``on_main_tab_changed``. Falls back to
    polling the tab_bar value (handling both string and int return types) so
    the first key press before the user has switched tabs still works when DPG
    exposes the selection.

    Notes
    -----
    tab_bar.get_value returns the active child tab's TAG (string alias or
    integer item-id), not the label.
    """
    active = _active_main_tab["value"]
    if active is not None:
        if isinstance(active, str) and active == "viz_tab":
            return True
        if not isinstance(active, str):
            try:
                if active == dpg.get_alias_id("viz_tab"):
                    return True
            except SystemError:
                pass
        return False
    # Initial state: no tab-change callback has fired yet. Best-effort poll.
    try:
        if not dpg.does_item_exist("main_tab_bar") or not dpg.does_item_exist("viz_tab"):
            return False
        v = dpg.get_value("main_tab_bar")
        if isinstance(v, str):
            return v == "viz_tab"
        try:
            return v == dpg.get_alias_id("viz_tab")
        except SystemError:
            return False
    except SystemError:
        return False


def _shortcut_should_fire() -> bool:
    """Return True when no text input is focused and the viz tab is active."""
    if _any_text_input_focused():
        return False
    return _viz_tab_is_active()


def _on_space(sender, app_data):
    """Toggle play/pause when the shortcut guard passes (DPG key-press callback)."""
    if not _shortcut_should_fire():
        return
    try:
        _viz._toggle_play_pause()
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("Keyboard handler _on_space failed")


def _on_left(sender, app_data):
    """Step one frame backward when the shortcut guard passes (DPG key-press callback)."""
    if not _shortcut_should_fire():
        return
    try:
        _viz._jump_frames(-1)
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("Keyboard handler _on_left failed")


def _on_right(sender, app_data):
    """Step one frame forward when the shortcut guard passes (DPG key-press callback)."""
    if not _shortcut_should_fire():
        return
    try:
        _viz._jump_frames(1)
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("Keyboard handler _on_right failed")


def _on_home(sender, app_data):
    """Jump to the start of the current period when the shortcut guard passes (DPG callback)."""
    if not _shortcut_should_fire():
        return
    try:
        _viz._jump_to_period_start()
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("Keyboard handler _on_home failed")


def _on_end(sender, app_data):
    """Jump to the end of the current period when the shortcut guard passes (DPG callback)."""
    if not _shortcut_should_fire():
        return
    try:
        _viz._jump_to_period_end()
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("Keyboard handler _on_end failed")


def register_global_handlers() -> None:
    """Mount the global key-press handler registry. Call once from ``app.initialize()``.

    Registers five key bindings (Space, Left, Right, Home, End) under the
    ``"floodlight_gui_global_keyboard"`` handler_registry tag. Each binding
    fires only when the viz tab is active and no tracked text input is focused.

    Must be called after ``dpg.create_viewport()`` + ``dpg.show_viewport()``
    and after the viz tab is created (so ``_viz._toggle_play_pause`` etc.
    exist). The canonical call site is ``app.initialize()`` after
    ``bus.emit(Events.APP_INITIALIZED)``.

    Notes
    -----
    Side-effect: creates DPG items under the handler_registry tag
    ``"floodlight_gui_global_keyboard"``.
    """
    with dpg.handler_registry(tag="floodlight_gui_global_keyboard"):
        dpg.add_key_press_handler(key=dpg.mvKey_Spacebar, callback=_on_space)
        dpg.add_key_press_handler(key=dpg.mvKey_Left, callback=_on_left)
        dpg.add_key_press_handler(key=dpg.mvKey_Right, callback=_on_right)
        dpg.add_key_press_handler(key=dpg.mvKey_Home, callback=_on_home)
        dpg.add_key_press_handler(key=dpg.mvKey_End, callback=_on_end)
    logger.info("Global keyboard handlers registered (5 keys)")
