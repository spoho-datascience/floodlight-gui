"""Global status bar: GUI-shell chrome built by ``app.create_ui``.

Renders a persistent 4-cell footer (data | selection | frame | last-action)
anchored as the last direct child of ``primary_window`` after the tab bar.
All five subscriptions fire at priority 20 so tab subscribers (priority 10)
settle their state before the bar reads event payloads.

DPG-aware: this module imports ``dearpygui`` at module scope and must not be
imported from DPG-free backend modules.
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus

__all__ = ["create_status_bar", "FOOTER_HEIGHT_PX"]


# Viewport-layout footer height reserved at the bottom of primary_window.
# Non-viz tabs pass ``height=-FOOTER_HEIGHT_PX`` to their content wrap so the
# status bar (mounted as the last child of primary_window) is never obscured.
# 30 px is a one-row estimate (4 add_text cells at DPG default row height ~22 px
# plus padding). The exact value is theme- and font-dependent; bump this constant
# if a live-render check reveals clipping or a visible gap.
FOOTER_HEIGHT_PX: int = 30


_CELL_DATA_TAG = "statusbar_cell_data"
_CELL_SELECTION_TAG = "statusbar_cell_selection"
_CELL_FRAME_TAG = "statusbar_cell_frame"
_CELL_ACTION_TAG = "statusbar_cell_action"

_DEFAULT_DATA = "No data"
_DEFAULT_SELECTION = "No selection"
_DEFAULT_FRAME = "-"  # ASCII hyphen (status bar is ASCII-only)
_DEFAULT_ACTION = "Ready"


def create_status_bar() -> None:
    """Build and wire the persistent bottom status bar inside the current DPG container.

    Renders a separator followed by a horizontal group of four ``add_text`` cells
    tagged ``statusbar_cell_{data,selection,frame,action}``, then subscribes the
    five private event handlers to the bus at priority 20.

    Must be called from ``app.create_ui()`` after the tab bar block, inside the
    ``with dpg.window(... primary_window):`` body.

    Notes
    -----
    Subscriptions are registered inside this function rather than at module scope
    so that test fixtures using a bus-snapshot autouse pattern get fresh
    registrations on every ``create_status_bar()`` call. The bus deduplicates by
    callback identity, so repeated calls are safe in production where
    ``create_status_bar()`` is invoked exactly once.

    Subscribes to
        Events.DATA_LOADED, Events.DATA_CLEARED, Events.SELECTION_CHANGED,
        Events.FRAME_CHANGED, Events.EXPORT_REQUESTED (all at priority 20).

    DPG tags owned
        ``statusbar_cell_data``, ``statusbar_cell_selection``,
        ``statusbar_cell_frame``, ``statusbar_cell_action``.
    """
    dpg.add_separator()
    with dpg.group(horizontal=True):
        dpg.add_text(_DEFAULT_DATA, tag=_CELL_DATA_TAG)
        dpg.add_text(" | ")
        dpg.add_text(_DEFAULT_SELECTION, tag=_CELL_SELECTION_TAG)
        dpg.add_text(" | ")
        dpg.add_text(_DEFAULT_FRAME, tag=_CELL_FRAME_TAG)
        dpg.add_text(" | ")
        dpg.add_text(_DEFAULT_ACTION, tag=_CELL_ACTION_TAG)

    bus.subscribe(Events.DATA_LOADED, _on_data_loaded, priority=20)
    bus.subscribe(Events.DATA_CLEARED, _on_data_cleared, priority=20)
    bus.subscribe(Events.SELECTION_CHANGED, _on_selection_changed, priority=20)
    bus.subscribe(Events.FRAME_CHANGED, _on_frame_changed, priority=20)
    bus.subscribe(Events.EXPORT_REQUESTED, _on_export_requested, priority=20)


def _safe_set(tag: str, value: str) -> None:
    """Call ``dpg.set_value`` only when the tag exists, suppressing SystemError."""
    try:
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, value)
    except SystemError:
        pass


def _on_data_loaded(app=None, provider=None, format=None, teams=None, **_) -> None:
    """Update the data cell when a dataset is loaded."""
    n_teams = len(teams) if teams else 0
    if format:
        label = f"Loaded: {format} --- {n_teams} teams"
    elif provider:
        label = f"Loaded: {provider} --- {n_teams} teams"
    else:
        label = f"Loaded --- {n_teams} teams"
    _safe_set(_CELL_DATA_TAG, label)


def _on_data_cleared(**_) -> None:
    """Reset all four cells to their default text when data is cleared."""
    _safe_set(_CELL_DATA_TAG, _DEFAULT_DATA)
    _safe_set(_CELL_SELECTION_TAG, _DEFAULT_SELECTION)
    _safe_set(_CELL_FRAME_TAG, _DEFAULT_FRAME)
    _safe_set(_CELL_ACTION_TAG, _DEFAULT_ACTION)


def _on_selection_changed(**payload) -> None:
    """Update the selection cell from the event payload.

    Prefers the ``summary`` key; falls back to counting items across a
    ``selections`` dict; falls back to a generic label.
    """
    summary = payload.get("summary")
    if summary:
        _safe_set(_CELL_SELECTION_TAG, str(summary))
        return
    selections = payload.get("selections")
    if isinstance(selections, dict):
        total = sum(len(v) if hasattr(v, "__len__") else 0 for v in selections.values())
        _safe_set(_CELL_SELECTION_TAG, f"Selection: {total} items")
        return
    _safe_set(_CELL_SELECTION_TAG, "Selection updated")


def _on_frame_changed(frame=None, **_) -> None:
    """Update the frame cell with the current frame number."""
    if frame is None:
        _safe_set(_CELL_FRAME_TAG, _DEFAULT_FRAME)
    else:
        _safe_set(_CELL_FRAME_TAG, f"Frame {frame}")


def _on_export_requested(kind=None, target=None, base_name=None, **_) -> None:
    """Update the last-action cell after an export completes.

    Synthesizes display text from the ``kind`` and ``target`` payload keys.
    No new kwargs are added to existing EXPORT_REQUESTED emit sites.
    """
    if kind and target:
        label = f"Exported {kind}: {target}"
    elif kind:
        label = f"Exported {kind}"
    else:
        label = "Exported"
    _safe_set(_CELL_ACTION_TAG, label)
