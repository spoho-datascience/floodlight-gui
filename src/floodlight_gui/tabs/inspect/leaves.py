"""Leaf renderers for the Inspect tab: each function accepts (parent_tag, payload)
and emits DPG widgets under that parent.

DPG-aware layer: imports ``dearpygui`` at module scope.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import dearpygui.dearpygui as dpg
import numpy as np
import pandas as pd

from floodlight_gui.tabs._shared.array_view import render_array_view
from floodlight_gui.tabs.inspect.collect import _resolve_xy
from floodlight_gui.tabs.inspect.state import (
    _TABLE,
    MAX_EVENTS_DISPLAY,
    TABLE_HEIGHT,
    TEAM_TABLE_HEIGHT,
)
from floodlight_gui.theme import INFO


def _leaf_event_table(parent: str, df: pd.DataFrame | None) -> None:
    """Render a scrollable event table capped at MAX_EVENTS_DISPLAY rows.

    Parameters
    ----------
    parent : str
        DPG container tag to render into.
    df : pd.DataFrame or None
        Events DataFrame; if None or empty a placeholder text is shown.
    """
    if df is None or df.empty:
        dpg.add_text("No events match current filters", parent=parent)
        return
    total = len(df)
    end = min(MAX_EVENTS_DISPLAY, total)
    dpg.add_text(f"Showing 0-{end - 1} of {total} events", parent=parent, color=INFO)
    with dpg.table(parent=parent, header_row=True, height=TABLE_HEIGHT, scrollY=True, **_TABLE):
        for col in ("Event Type", "Game Clock", "Player ID", "Outcome"):
            dpg.add_table_column(label=col, width=100)
        for _, row in df.iloc[:end].iterrows():
            with dpg.table_row():
                dpg.add_text(str(row.get("eID", "N/A"))[:15])
                dpg.add_text(f"{row.get('gameclock', 0):.1f}")
                dpg.add_text(str(row.get("pID", "N/A"))[-8:])
                dpg.add_text(str(row.get("outcome", "N/A")))


def _leaf_position(parent: str, payload: dict) -> None:
    """Render shape/framerate metadata and the shared paginated array view.

    Resolves the active XY first so the Shape label reflects the post-stack
    frame count (after resample, subtract_centroid, etc.) when the user has
    the source selector on "Post-stack".

    Parameters
    ----------
    parent : str
        DPG container tag to render into.
    payload : dict
        Leaf payload with keys: ``data`` (XY object), ``entity`` (display name),
        ``source`` ("raw" or "post-stack"), ``period_internal`` (period key).
    """
    data = payload["data"]
    if data is None or not hasattr(data, "xy"):
        dpg.add_text(f"No position data available for {payload['entity']}", parent=parent)
        return
    xy_array, shape, framerate = _resolve_xy(
        payload["entity"], data, payload["source"], payload["period_internal"]
    )
    dpg.add_text(f"Shape: {shape}", parent=parent)
    dpg.add_text(f"Framerate: {framerate}", parent=parent)
    render_array_view(parent, xy_array)


def _leaf_dataframe(parent: str, df: pd.DataFrame | None) -> None:
    """Render a scrollable table for a generic DataFrame payload.

    Parameters
    ----------
    parent : str
        DPG container tag to render into.
    df : pd.DataFrame or None
        DataFrame to display; if None a placeholder text is shown.
    """
    if df is None:
        dpg.add_text("No valid data", parent=parent)
        return
    with dpg.table(
        parent=parent, header_row=True, height=TEAM_TABLE_HEIGHT, scrollY=True, **_TABLE
    ):
        for col in df.columns:
            dpg.add_table_column(label=str(col), width=100)
        for _, row in df.iterrows():
            with dpg.table_row():
                for col in df.columns:
                    dpg.add_text(str(row[col]))


def _leaf_code(value_map: dict[int, str]) -> Callable[[str, Any], None]:
    """Build a Code-summary leaf renderer bound to a value map.

    One renderer serves both Possession and Ball Status sections; they differ
    only by their value map.

    Parameters
    ----------
    value_map : dict[int, str]
        Maps integer code values to human-readable descriptions.

    Returns
    -------
    Callable[[str, Any], None]
        A ``(parent, code_obj)`` renderer that emits a DPG text summary.
    """

    def render(parent: str, code_obj: Any) -> None:
        """Render a frame-count summary and sample for a floodlight Code object."""
        if not hasattr(code_obj, "code"):
            dpg.add_text("No data available - missing 'code' attribute", parent=parent)
            return
        code = code_obj.code
        frames = len(code)
        lines = [f"Summary ({frames} frames):"]
        for key, desc in value_map.items():
            count = int(np.sum(code == key))
            pct = (count / frames) * 100 if frames > 0 else 0
            lines.append(f"{desc}: {count} frames ({pct:.1f}%)")
        sample = ", ".join(str(int(x)) for x in code[:20])
        defs = "\n".join(f"{k} = {v}" for k, v in value_map.items())
        body = "\n".join(lines)
        text = f"{body}\n\nSample (first 20 frames): {sample}\n\nDefinitions:\n{defs}"
        dpg.add_text(text, parent=parent)

    return render


def _leaf_pitch(parent: str, pitch: Any) -> None:
    """Render key Pitch attributes as a text block.

    Parameters
    ----------
    parent : str
        DPG container tag to render into.
    pitch : floodlight.core.pitch.Pitch
        Pitch object; missing attributes fall back to "N/A".
    """
    rows = [
        ("X limits", "xlim"),
        ("Y limits", "ylim"),
        ("Length", "length"),
        ("Width", "width"),
        ("Unit", "unit"),
        ("Sport", "sport"),
    ]
    text = "Pitch Information:\n" + "\n".join(
        f"{label}: {getattr(pitch, attr, 'N/A')}" for label, attr in rows
    )
    dpg.add_text(text, parent=parent)
