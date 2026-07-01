"""Live data-context header widget mounted at the top of each tab body.

``add_tab_header(tab_name)`` creates a DPG text widget inside the current
container and registers it in a module-level registry so that module-scope
``DATA_LOADED`` / ``DATA_CLEARED`` subscribers can update every mounted header
simultaneously. The registry is the authoritative list of currently-alive header
tags across all tabs.

Module-level state: ``_TAB_HEADERS`` (tag -> tab_name) and ``_EMPTY_TEXT``
persist for the lifetime of the application session and survive across pytest
runs; tests that mount headers should clean up or use isolated DPG contexts.

Subscriber priority: 10 (matches the tab-layer convention; the status bar uses
priority 20 and fires after tabs have refreshed).
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus

__all__ = ["add_tab_header"]


# Registry of currently-mounted headers: DPG tag -> tab_name.
# The module-scope subscriber walks this dict to update all live headers at once.
_TAB_HEADERS: dict[str, str] = {}

_EMPTY_TEXT = "No data loaded"


def _header_tag(tab_name: str) -> str:
    """Return the stable DPG widget tag for a tab header."""
    return f"_tab_header_{tab_name}"


def add_tab_header(tab_name: str) -> None:
    """Mount a data-context header text widget inside the current DPG container.

    Creates a DPG text widget tagged ``f"_tab_header_{tab_name}"`` and registers
    it in ``_TAB_HEADERS`` so the module-scope ``DATA_LOADED`` / ``DATA_CLEARED``
    subscribers update it automatically.

    Parameters
    ----------
    tab_name : str
        Identifier for the tab (e.g. ``"model"``, ``"load"``). Used to compose
        the stable widget tag ``f"_tab_header_{tab_name}"`` and to derive the
        human-readable title prefix shown in the header text.

    Notes
    -----
    Side-effects:

    - Creates DPG widget with tag ``f"_tab_header_{tab_name}"``.
    - Writes an entry into the module-level ``_TAB_HEADERS`` registry.
    """
    tag = _header_tag(tab_name)
    title = tab_name.replace("_", " ").title()
    # The subscriber rewrites only the suffix; the title prefix stays constant.
    dpg.add_text(f"{title} - {_EMPTY_TEXT}", tag=tag)
    _TAB_HEADERS[tag] = tab_name


def _format_summary(
    format: str | None = None,
    teams: list | None = None,
    periods: list | None = None,
    **_,
) -> str:
    """Build a compact data-context summary string from ``DATA_LOADED`` payload fields.

    Parameters
    ----------
    format : str or None
        Provider format name (e.g. ``"DFL"``).
    teams : list or None
        Loaded team names; only the count is shown.
    periods : list or None
        Loaded period identifiers; only the count is shown.
    **_ : object
        Unknown payload keys absorbed for forward compatibility.

    Returns
    -------
    str
        A dash-separated summary string such as ``"DFL - 2 teams - 2 periods"``,
        or ``"Data loaded"`` when no recognized fields are present.
    """
    parts: list[str] = []
    if format:
        parts.append(str(format))
    if teams:
        n = len(teams)
        parts.append(f"{n} teams")
    if periods:
        n = len(periods)
        parts.append(f"{n} periods")
    return " - ".join(parts) if parts else "Data loaded"


def _on_data_loaded(**payload) -> None:
    """Update all mounted tab headers with the new data-context summary.

    Subscribed to ``Events.DATA_LOADED`` at priority 10. Walks ``_TAB_HEADERS``
    and sets each live widget's text to ``"{Title} - {summary}"``.

    Parameters
    ----------
    **payload : object
        ``DATA_LOADED`` event payload; forwarded verbatim to ``_format_summary``.
    """
    summary = _format_summary(**payload)
    for tag, tab_name in _TAB_HEADERS.items():
        try:
            if dpg.does_item_exist(tag):
                title = tab_name.replace("_", " ").title()
                dpg.set_value(tag, f"{title} - {summary}")
        except SystemError:
            # Tag may have been deleted between the exists-check and set_value.
            continue


def _on_data_cleared(**_) -> None:
    """Reset all mounted tab headers to the empty-state text.

    Subscribed to ``Events.DATA_CLEARED`` at priority 10. Walks ``_TAB_HEADERS``
    and restores each live widget's text to ``"{Title} - No data loaded"``.
    """
    for tag, tab_name in _TAB_HEADERS.items():
        try:
            if dpg.does_item_exist(tag):
                title = tab_name.replace("_", " ").title()
                dpg.set_value(tag, f"{title} - {_EMPTY_TEXT}")
        except SystemError:
            continue


# Register module-scope subscribers once at import time (priority 10 - tab convention).
bus.subscribe(Events.DATA_LOADED, _on_data_loaded, priority=10)
bus.subscribe(Events.DATA_CLEARED, _on_data_cleared, priority=10)
