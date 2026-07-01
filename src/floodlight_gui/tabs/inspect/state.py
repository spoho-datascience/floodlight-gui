"""Shared mutable state and read-only constants for the inspect tab subpackage.

``app_instance`` is the single source of data for the inspect tab, captured on
APP_INITIALIZED / DATA_LOADED. Sub-modules must read and write it through
``state.app_instance`` so they share the same reference; never import it by
value and never declare ``global``. The read-only constants below may be
imported by value.
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

# App reference - sole data source for the inspect tab (captured on APP_INITIALIZED).
app_instance: Any = None

# Table sizing shared by the event-table and teamsheet-table leaf renderers.
MAX_EVENTS_DISPLAY = 500
TABLE_HEIGHT = 300
TEAM_TABLE_HEIGHT = 200
_TABLE = {
    "policy": dpg.mvTable_SizingFixedFit,
    "borders_innerH": True,
    "borders_outerH": True,
    "borders_innerV": True,
    "borders_outerV": True,
}

# Section layout shapes: how a section's data tree maps to tab structure.
GROUPED = "grouped"  # {period: {entity: payload}}  (Event, Position)
PERIOD = "period"  # {period: payload}            (Possession, Ball Status)
FLAT = "flat"  # {entity: payload}            (Team)
SINGLE = "single"  # payload                      (Pitch)


def _slug(text: str) -> str:
    """Convert a display label to a lowercase, underscore-separated identifier."""
    return text.replace(" ", "_").replace("-", "_").lower()
