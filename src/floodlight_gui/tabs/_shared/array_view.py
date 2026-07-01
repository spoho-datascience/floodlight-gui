"""Paginated array-view widget shared across Models, Metrics, Transforms, and
Visualization Results panels.

Public surface: ``render_array_view`` (see ``__all__``).

DPG carve-out: this module imports ``dearpygui`` at module scope because it
lives under ``tabs/`` (the DPG-aware layer); backend modules must not.

BLE001 convention: every DPG callback wraps its body in
``try / except Exception: logger.exception(...)`` with a ``# noqa: BLE001``
marker so a callback error can never crash the render loop.

Pagination state: ``_pagination_state`` is module-level and keyed by
``parent_tag``. It is cleared wholesale by the ``Events.DATA_LOADED``
subscriber registered at the bottom of this module (priority=5), which fires
after the data-store clears (priority=0) and before tab-refresh subscribers
rebuild their UI (priority=10).

Input contract: accepts ``pd.DataFrame | np.ndarray | None`` only. Callers
must project domain objects (XY, PlayerProperty, TeamProperty, DyadicProperty)
to primitive arrays before calling. The widget is self-defensive on empty
and None inputs; callers do not need to guard.

Column header heuristic for ndarray inputs:
  - (T, 2): ['Frame', 'X', 'Y'] (ball / single-player path).
  - (T, even N>=4): ['Frame', 'P0_X', 'P0_Y', ...] (XY-interleave default).
  - (T, odd N): ['Frame', 'P0', ..., 'P{N-1}'].
  - ``columns=`` kwarg overrides the heuristic when length matches.

3-D ndarray inputs (T, N1, N2 - DyadicProperty shape) render a
slice-selector dropdown above the nav strip; the dropdown selects the N2
("to-player") axis and the table renders the resulting (T, N1) slice.

Nav strip always rendered and always enabled. Navigation callbacks clamp via
``max(0, min(...))`` so they are safe no-ops on single-page data.
"""

from __future__ import annotations

import logging
from typing import Any

import dearpygui.dearpygui as dpg
import numpy as np
import pandas as pd

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.theme import INFO

__all__ = ["render_array_view"]

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Module-level constants
# --------------------------------------------------------------------------- #

# Default rows per page; matches the Inspect tab page size so results panels
# display the same density as the canonical depth target.
MAX_FRAMES_DISPLAY = 1000

# Table height in pixels; matches the Inspect tab.
TABLE_HEIGHT = 300

# Table sizing and border flags shared across all table renders in this module.
DEFAULT_TABLE_SETTINGS = {
    "policy": dpg.mvTable_SizingFixedFit,
    "borders_innerH": True,
    "borders_outerH": True,
    "borders_innerV": True,
    "borders_outerV": True,
}


# --------------------------------------------------------------------------- #
# Module-private pagination state
# --------------------------------------------------------------------------- #

# Single source of truth for paginated array-view state. Keyed by parent_tag
# (the DPG container tag the caller passed in). Cleared wholesale on
# Events.DATA_LOADED via _on_data_loaded.
_pagination_state: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------- #
# Public renderer
# --------------------------------------------------------------------------- #


def render_array_view(
    parent_tag: str,
    frame: pd.DataFrame | np.ndarray | None,
    *,
    page_size: int = 1000,
    columns: list[str] | None = None,
) -> None:
    """Render a paginated array view (nav strip + table) into ``parent_tag``.

    Parameters
    ----------
    parent_tag : str
        DPG container tag to render into. Used as the pagination state key
        and as the prefix for all derived DPG widget tags owned by this call.
    frame : pd.DataFrame or np.ndarray or None
        Data to render. A ``pd.DataFrame`` keeps its columns verbatim; a
        ``np.ndarray`` runs through the column heuristic (ball, interleave,
        odd-N, or 3-D slice). ``None``, empty DataFrame, or zero-length
        ndarray renders a "No data to display" placeholder and returns.
    page_size : int, default 1000
        Rows per page.
    columns : list[str] or None
        Optional column-label override. Applied when length matches the
        emitted column count; otherwise falls back to the heuristic with a
        logged warning.

    Notes
    -----
    Side-effects:

    - Writes an entry to ``_pagination_state[parent_tag]`` before emitting
      any DPG widgets (callbacks read state, so it must exist first).
    - Creates the following DPG widget tags under ``parent_tag``:
        ``{parent_tag}__array_view_slice_combo`` (3-D inputs only),
        ``{parent_tag}__array_view_jump``,
        ``{parent_tag}__array_view_range``,
        ``{parent_tag}__array_view_table``.
    - Does not emit any EventBus events.
    """
    # Self-defensive empty/None guard. Single source of truth; callers
    # do not need to guard before calling.
    if frame is None:
        dpg.add_text("No data to display", parent=parent_tag, color=INFO)
        return
    if isinstance(frame, pd.DataFrame) and frame.empty:
        dpg.add_text("No data to display", parent=parent_tag, color=INFO)
        return
    if isinstance(frame, np.ndarray) and (frame.size == 0 or frame.shape[0] == 0):
        dpg.add_text("No data to display", parent=parent_tag, color=INFO)
        return

    # Reshape 1-D ndarray so all downstream code sees at least 2 dimensions.
    raw_frame = frame
    if isinstance(frame, np.ndarray) and frame.ndim == 1:
        frame = frame[:, None]

    # 3-D ndarray (T, N1, N2): emit a slice-selector dropdown above the nav
    # strip so the user can pick which N2 ("to-player") axis to view.
    is_3d = isinstance(raw_frame, np.ndarray) and raw_frame.ndim == 3
    initial_slice_idx = 0
    if is_3d:
        n2 = raw_frame.shape[2]
        combo_tag = f"{parent_tag}__array_view_slice_combo"
        dpg.add_combo(
            items=[f"P{i}" for i in range(n2)],
            default_value="P0",
            tag=combo_tag,
            label="View dyads to",
            parent=parent_tag,
            width=120,
            callback=_on_slice_change,
            user_data=parent_tag,
        )
        # Project the 3-D array onto the initial N2 slice for the first render.
        frame = raw_frame[:, :, initial_slice_idx]

    # Compute total rows and initial offset.
    total_rows = frame.shape[0] if isinstance(frame, np.ndarray) else len(frame)
    offset = 0
    table_tag = f"{parent_tag}__array_view_table"

    # Write state before emitting any DPG widgets so nav callbacks that fire
    # immediately can read a consistent snapshot.
    _pagination_state[parent_tag] = {
        "frame": frame,
        "raw_frame": raw_frame,
        "offset": offset,
        "table_tag": table_tag,
        "total_rows": total_rows,
        "page_size": page_size,
        "columns": columns,
        "slice_idx_for_3d": initial_slice_idx,
    }

    # Emit nav strip then initial table.
    _emit_nav_strip(parent_tag, total_rows)
    _render_table(parent_tag, offset)


# --------------------------------------------------------------------------- #
# Module-private helpers
# --------------------------------------------------------------------------- #


def _emit_nav_strip(parent_tag: str, total_rows: int) -> None:
    """Emit the 4-button page-navigation pad, Jump input, and Range label row.

    The pad provides -10 pages, -1 page, +1 page, and +10 pages buttons so
    large datasets can be navigated without the Jump input. Navigation is
    always enabled; ``_navigate`` clamps via ``max(0, min(...))`` so multi-page
    skips at the edges are safe no-ops.
    """
    with dpg.group(horizontal=True, parent=parent_tag):
        dpg.add_button(
            label="<<",
            callback=lambda s, a, u: _navigate(parent_tag, -10),
            width=40,
        )
        dpg.add_button(
            label="<",
            callback=lambda s, a, u: _navigate(parent_tag, -1),
            width=40,
        )
        dpg.add_button(
            label=">",
            callback=lambda s, a, u: _navigate(parent_tag, 1),
            width=40,
        )
        dpg.add_button(
            label=">>",
            callback=lambda s, a, u: _navigate(parent_tag, 10),
            width=40,
        )
        dpg.add_input_int(
            tag=f"{parent_tag}__array_view_jump",
            width=100,
            min_value=0,
            max_value=max(0, total_rows - 1),
        )
        dpg.add_button(
            label="Jump",
            callback=lambda s, a, u: _jump_to(parent_tag),
            width=50,
        )
        dpg.add_text("Range: 0-0", tag=f"{parent_tag}__array_view_range")


def _derive_columns(frame, columns_override) -> tuple[list[str], bool]:
    """Resolve column headers and whether to prepend a synthetic Frame column.

    Returns ``(headers, include_frame_col)``.

    Parameters
    ----------
    frame : pd.DataFrame or np.ndarray
        The current data slice (after any 3-D projection).
    columns_override : list[str] or None
        Caller-supplied override; applied when length matches expected count.

    Returns
    -------
    headers : list[str]
        Column labels for the table.
    include_frame_col : bool
        True when the first column is a synthetic row-index column named
        "Frame" (ndarray paths); False for DataFrames (index is implicit).
    """
    if isinstance(frame, pd.DataFrame):
        headers = list(columns_override) if columns_override is not None else list(frame.columns)
        return (headers, False)

    n_cols = frame.shape[1] if frame.ndim == 2 else 1
    if columns_override is not None:
        # Validate length matches emitted columns (frame col + n_cols data cols).
        expected = 1 + n_cols
        if len(columns_override) == expected:
            return (list(columns_override), True)
        logger.warning(
            "array-view: columns= override length %d != expected %d; falling back to heuristic.",
            len(columns_override),
            expected,
        )

    if n_cols == 2:
        return (["Frame", "X", "Y"], True)
    if n_cols >= 4 and n_cols % 2 == 0:
        half = n_cols // 2
        return (
            ["Frame"] + [f"P{i}_{axis}" for i in range(half) for axis in ("X", "Y")],
            True,
        )
    return (["Frame"] + [f"P{i}" for i in range(n_cols)], True)


def _render_table(parent_tag: str, offset: int) -> None:
    """Delete and rebuild the table widget at the given row offset.

    Reads state from ``_pagination_state[parent_tag]``. Clamps ``offset``
    so it is always within bounds. Updates the Range label after clamping.
    """
    try:
        state = _pagination_state.get(parent_tag)
        if state is None:
            return
        frame = state["frame"]
        page_size = state["page_size"]
        columns_override = state["columns"]
        total_rows = state["total_rows"]
        table_tag = state["table_tag"]

        # Delete the existing table before rebuilding; DPG tags must be unique.
        if dpg.does_item_exist(table_tag):
            dpg.delete_item(table_tag)

        # Clamp offset so it stays within the last full page.
        offset = max(0, min(offset, max(0, total_rows - page_size)))
        state["offset"] = offset
        start = offset
        end = min(start + page_size, total_rows)

        # Update the Range label to reflect the current page.
        range_tag = f"{parent_tag}__array_view_range"
        if dpg.does_item_exist(range_tag):
            dpg.set_value(range_tag, f"Range: {start}-{end - 1} of {total_rows}")

        # Derive columns.
        headers, include_frame_col = _derive_columns(frame, columns_override)

        with dpg.table(
            parent=parent_tag,
            tag=table_tag,
            header_row=True,
            height=TABLE_HEIGHT,
            scrollY=True,
            scrollX=True,
            **DEFAULT_TABLE_SETTINGS,
        ):
            # Column widths: "Frame" narrower, X/Y standard, player cols compact.
            for label in headers:
                if label == "Frame":
                    dpg.add_table_column(label=label, width=60)
                elif label in ("X", "Y"):
                    dpg.add_table_column(label=label, width=80)
                else:
                    dpg.add_table_column(label=label, width=70)

            # Row emission.
            if isinstance(frame, pd.DataFrame):
                for row_idx in range(start, end):
                    with dpg.table_row():
                        for col in frame.columns:
                            val = frame.iloc[row_idx][col]
                            if isinstance(val, (float, np.floating)) and np.isnan(val):
                                dpg.add_text("NaN")
                            elif isinstance(val, (float, np.floating)):
                                dpg.add_text(f"{val:.2f}")
                            else:
                                dpg.add_text(str(val))
            else:
                # ndarray path: dispatch on column layout determined above.
                n_cols = frame.shape[1] if frame.ndim == 2 else 1
                is_ball = n_cols == 2
                is_even_interleave = (n_cols >= 4) and (n_cols % 2 == 0)
                for frame_idx in range(start, end):
                    with dpg.table_row():
                        if include_frame_col:
                            dpg.add_text(str(frame_idx))
                        if is_ball:
                            x_val = frame[frame_idx, 0]
                            y_val = frame[frame_idx, 1]
                            dpg.add_text(f"{x_val:.2f}" if not np.isnan(x_val) else "NaN")
                            dpg.add_text(f"{y_val:.2f}" if not np.isnan(y_val) else "NaN")
                        elif is_even_interleave:
                            n_players = n_cols // 2
                            for p in range(n_players):
                                x_val = frame[frame_idx, p * 2]
                                y_val = frame[frame_idx, p * 2 + 1]
                                dpg.add_text(f"{x_val:.2f}" if not np.isnan(x_val) else "NaN")
                                dpg.add_text(f"{y_val:.2f}" if not np.isnan(y_val) else "NaN")
                        else:
                            for p in range(n_cols):
                                v = frame[frame_idx, p]
                                if isinstance(v, (float, np.floating)) and np.isnan(v):
                                    dpg.add_text("NaN")
                                elif isinstance(v, (float, np.floating)):
                                    dpg.add_text(f"{v:.2f}")
                                else:
                                    dpg.add_text(str(v))
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("array-view: _render_table failed for parent_tag=%s", parent_tag)


def _navigate(parent_tag: str, direction: int) -> None:
    """Page-navigation callback. Moves ``direction`` pages and re-renders.

    Parameters
    ----------
    parent_tag : str
        State key identifying the widget instance.
    direction : int
        Signed page count to move (e.g. -1 for previous page, +10 for skip).
        The offset is clamped, so this is a safe no-op on single-page data.
    """
    try:
        state = _pagination_state.get(parent_tag)
        if state is None:
            return
        new_offset = max(
            0,
            min(
                state["offset"] + direction * state["page_size"],
                max(0, state["total_rows"] - state["page_size"]),
            ),
        )
        _render_table(parent_tag, new_offset)
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("array-view: _navigate failed for parent_tag=%s", parent_tag)


def _jump_to(parent_tag: str) -> None:
    """Jump-to-row callback. Reads the input_int value, clamps, and re-renders."""
    try:
        state = _pagination_state.get(parent_tag)
        if state is None:
            return
        jump_tag = f"{parent_tag}__array_view_jump"
        if not dpg.does_item_exist(jump_tag):
            return
        target = dpg.get_value(jump_tag)
        _render_table(parent_tag, max(0, int(target)))
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("array-view: _jump_to failed for parent_tag=%s", parent_tag)


def _on_slice_change(sender, app_data, user_data) -> None:  # noqa: ARG001 -- DPG callback signature
    """3-D slice-selector dropdown callback.

    Re-slices ``raw_frame`` on the N2 axis using the dropdown selection
    ("P0", "P1", ...) and re-renders the table against the resulting (T, N1)
    slice from row 0.

    Parameters
    ----------
    sender : Any
        DPG sender (unused).
    app_data : str
        Selected combo value, e.g. "P0" or "P3".
    user_data : str
        ``parent_tag`` of the owning array-view instance.
    """
    try:
        parent_tag = user_data
        state = _pagination_state.get(parent_tag)
        if state is None:
            return
        raw = state["raw_frame"]
        if not (isinstance(raw, np.ndarray) and raw.ndim == 3):
            return
        # Parse the numeric index from the combo value ("P0" -> 0).
        try:
            slice_idx = int(str(app_data).lstrip("P"))
        except ValueError:
            slice_idx = 0
        slice_idx = max(0, min(slice_idx, raw.shape[2] - 1))
        state["slice_idx_for_3d"] = slice_idx
        state["frame"] = raw[:, :, slice_idx]
        state["total_rows"] = state["frame"].shape[0]
        _render_table(parent_tag, 0)
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("array-view: _on_slice_change failed")


# --------------------------------------------------------------------------- #
# Module-scope EventBus wiring
# --------------------------------------------------------------------------- #


def _on_data_loaded(**_payload: Any) -> None:
    """Clear all per-parent_tag pagination state when a new dataset is loaded.

    Registered at priority=5, which fires after the data-store clears its own
    state (priority=0) and before tab-refresh subscribers rebuild their UI
    (priority=10), so the dict is empty by the time any tab re-renders.
    """
    try:
        _pagination_state.clear()
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("array-view: _on_data_loaded clear failed")


# Priority=5: fires after data-store (priority=0) and before tab-refresh
# subscribers (priority=10) so the state dict is empty when consumer tabs
# rebuild their UI.
bus.subscribe(Events.DATA_LOADED, _on_data_loaded, priority=5)
