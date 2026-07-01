"""Behavioral contracts for ``tabs/_shared/array_view``.

The paginated array view turns a DataFrame or ndarray into a nav strip plus a
table, deriving column headers from a shape heuristic and paging through the
rows with clamped offsets. The DPG toolkit is the seam and is replaced with the
shared fake recorder, so the tests assert the rows / cells / labels emitted and
the page arithmetic, never live-widget behavior.

Behavioral contracts guarded here
---------------------------------
render_array_view
  C1  Empty or None inputs (None, empty DataFrame, zero-row ndarray) render the
      "No data to display" placeholder and emit no table.
  C2  A DataFrame keeps its own columns verbatim as table headers; no synthetic
      Frame column is prepended.
  C3  An ndarray column heuristic dispatches on width: (T, 2) -> Frame/X/Y;
      even N>=4 -> Frame + interleaved P{i}_X/P{i}_Y; odd N -> Frame + P{i}.
  C4  A 1-D ndarray is treated as a single data column (Frame + P0).
  C5  A ``columns=`` override replaces the heuristic when its length matches the
      emitted column count, and is ignored (heuristic used) when it does not.
  C6  The first page renders rows ``[0, page_size)`` and the Range label reads
      ``Range: 0-{end-1} of {total}``.
  C7  A 3-D ndarray emits a slice-selector combo and renders the first N2 slice.

_derive_columns
  C8  Column derivation returns the heuristic headers and the include-Frame flag
      for every supported shape.

page arithmetic (_navigate / _jump_to via state)
  C9  Navigation clamps the offset into ``[0, max(0, total - page_size)]`` so
      forward / backward paging never escapes the data, and the Range label
      tracks the clamped page.
  C10 Jump reads the input value, clamps it to the last full page, and re-renders
      at the clamped offset.

_on_slice_change
  C11 Selecting a 3-D slice re-projects ``raw_frame`` on the chosen N2 axis and
      re-renders from row 0; an out-of-range / unparseable selection clamps.

_on_data_loaded
  C12 The DATA_LOADED subscriber clears all pagination state.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import floodlight_gui.tabs._shared.array_view as av
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install the shared fake-DPG recorder as the module's ``dpg`` binding.

    The renderer calls into ``dpg`` at module scope; redirecting that name to
    the recorder lets tests assert which widgets, rows, and cells were emitted
    without a live DPG context. ``mvTable_SizingFixedFit`` is read at module
    import for ``DEFAULT_TABLE_SETTINGS``, so the stub's constant suffices.
    """
    stub = make_dpg_stub()
    monkeypatch.setattr(av, "dpg", stub)
    return stub


@pytest.fixture(autouse=True)
def _clear_state():
    """Clear module-level pagination state around each test for isolation."""
    av._pagination_state.clear()
    yield
    av._pagination_state.clear()


def _row_cell_texts(stub):
    """Return the list of cell strings emitted by ``add_text`` calls.

    Header labels go through ``add_table_column`` (label kwarg), so every
    ``add_text`` call inside the table is a data cell.
    """
    return [c[1][0] for c in stub.calls_of("add_text")]


def _column_labels(stub):
    """Return the ordered table-column header labels."""
    return [c[2].get("label") for c in stub.calls_of("add_table_column")]


# --------------------------------------------------------------------------- #
# render_array_view: empty guard
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "frame",
    [
        None,
        pd.DataFrame(),
        np.empty((0, 4)),
    ],
)
def test_empty_inputs_render_placeholder(dpg_stub, frame):
    """C1: None / empty DataFrame / zero-row ndarray render the placeholder only."""
    av.render_array_view("p", frame)
    texts = [c[1][0] for c in dpg_stub.calls_of("add_text")]
    assert texts == ["No data to display"]
    assert not dpg_stub.calls_of("table_enter")


# --------------------------------------------------------------------------- #
# render_array_view: column derivation
# --------------------------------------------------------------------------- #


def test_dataframe_keeps_own_columns(dpg_stub):
    """C2: a DataFrame uses its own column labels with no synthetic Frame column."""
    df = pd.DataFrame({"alpha": [1.0, 2.0], "beta": [3.0, 4.0]})
    av.render_array_view("p", df)
    assert _column_labels(dpg_stub) == ["alpha", "beta"]


@pytest.mark.parametrize(
    "arr, expected_headers",
    [
        (np.zeros((3, 2)), ["Frame", "X", "Y"]),
        (np.zeros((3, 4)), ["Frame", "P0_X", "P0_Y", "P1_X", "P1_Y"]),
        (np.zeros((3, 3)), ["Frame", "P0", "P1", "P2"]),
    ],
)
def test_ndarray_column_heuristic(dpg_stub, arr, expected_headers):
    """C3: the ndarray width heuristic produces ball / interleave / odd-N headers."""
    av.render_array_view("p", arr)
    assert _column_labels(dpg_stub) == expected_headers


def test_1d_ndarray_single_column(dpg_stub):
    """C4: a 1-D ndarray renders as a single data column with a Frame prefix."""
    av.render_array_view("p", np.array([1.0, 2.0, 3.0]))
    assert _column_labels(dpg_stub) == ["Frame", "P0"]


@pytest.mark.parametrize(
    "override, expected",
    [
        (["F", "x", "y"], ["F", "x", "y"]),  # length matches 1 + 2 -> used
        (["only", "two"], ["Frame", "X", "Y"]),  # wrong length -> heuristic
    ],
)
def test_columns_override_applied_when_length_matches(dpg_stub, override, expected):
    """C5: a columns override is used iff its length matches the emitted count."""
    av.render_array_view("p", np.zeros((2, 2)), columns=override)
    assert _column_labels(dpg_stub) == expected


# --------------------------------------------------------------------------- #
# render_array_view: first-page rows + range label
# --------------------------------------------------------------------------- #


def test_first_page_rows_and_range_label(dpg_stub):
    """C6: the first page renders rows [0, page_size) and the Range label tracks it."""
    arr = np.arange(20, dtype=float).reshape(10, 2)
    av.render_array_view("p", arr, page_size=4)
    # 4 rows x (Frame + X + Y) = 4 row contexts.
    assert len(dpg_stub.calls_of("table_row_enter")) == 4
    # Range label set to the first page span.
    range_sets = [c for c in dpg_stub.calls_of("set_value") if c[1][0] == "p__array_view_range"]
    assert range_sets[-1][1][1] == "Range: 0-3 of 10"


# --------------------------------------------------------------------------- #
# render_array_view: 3-D slice combo
# --------------------------------------------------------------------------- #


def test_3d_input_emits_slice_combo_and_renders_first_slice(dpg_stub):
    """C7: a 3-D ndarray emits a slice combo and renders the (T, N1) first slice."""
    arr = np.zeros((5, 3, 4))  # T=5, N1=3, N2=4
    av.render_array_view("p", arr)
    combos = dpg_stub.calls_of("add_combo")
    assert combos, "expected a slice-selector combo for 3-D input"
    assert combos[0][2]["items"] == ["P0", "P1", "P2", "P3"]
    # The rendered slice is (T, N1=3): an odd-N table -> Frame + P0..P2.
    assert _column_labels(dpg_stub) == ["Frame", "P0", "P1", "P2"]


# --------------------------------------------------------------------------- #
# _derive_columns (direct)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "frame, expected_headers, expected_flag",
    [
        (pd.DataFrame({"a": [1], "b": [2]}), ["a", "b"], False),
        (np.zeros((2, 2)), ["Frame", "X", "Y"], True),
        (np.zeros((2, 4)), ["Frame", "P0_X", "P0_Y", "P1_X", "P1_Y"], True),
        (np.zeros((2, 5)), ["Frame", "P0", "P1", "P2", "P3", "P4"], True),
    ],
)
def test_derive_columns(frame, expected_headers, expected_flag):
    """C8: column derivation returns heuristic headers plus the Frame-column flag."""
    headers, include_frame = av._derive_columns(frame, None)
    assert headers == expected_headers
    assert include_frame is expected_flag


# --------------------------------------------------------------------------- #
# page arithmetic
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "start_offset, direction, expected_offset",
    [
        (0, -1, 0),  # backward from the first page clamps to 0
        (0, 1, 4),  # forward one page
        (4, 10, 8),  # forward overshoot clamps to last full page (12 - 4)
        (8, 1, 8),  # forward from the last page stays put
    ],
)
def test_navigate_clamps_offset(dpg_stub, start_offset, direction, expected_offset):
    """C9: navigation clamps the offset into [0, total - page_size] and re-renders there."""
    arr = np.arange(24, dtype=float).reshape(12, 2)
    av.render_array_view("p", arr, page_size=4)
    av._pagination_state["p"]["offset"] = start_offset
    av._navigate("p", direction)
    assert av._pagination_state["p"]["offset"] == expected_offset


def test_jump_reads_input_clamps_and_rerenders(dpg_stub):
    """C10: jump reads the input value, clamps to the last full page, and re-renders."""
    arr = np.arange(24, dtype=float).reshape(12, 2)
    av.render_array_view("p", arr, page_size=4)
    # Overshoot far past the end; clamps to total - page_size = 8.
    dpg_stub.values["p__array_view_jump"] = 99
    av._jump_to("p")
    assert av._pagination_state["p"]["offset"] == 8


# --------------------------------------------------------------------------- #
# _on_slice_change
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "app_data, expected_idx",
    [
        ("P2", 2),  # valid in-range selection
        ("P9", 3),  # out of range clamps to N2 - 1
        ("garbage", 0),  # unparseable falls back to 0
    ],
)
def test_slice_change_reprojects_and_clamps(dpg_stub, app_data, expected_idx):
    """C11: slice selection re-projects raw_frame on N2, clamping the index."""
    raw = np.arange(5 * 3 * 4, dtype=float).reshape(5, 3, 4)
    av.render_array_view("p", raw)
    av._on_slice_change(None, app_data, "p")
    state = av._pagination_state["p"]
    assert state["slice_idx_for_3d"] == expected_idx
    # frame is the (T, N1) projection of raw on the selected N2 axis.
    assert np.array_equal(state["frame"], raw[:, :, expected_idx])


# --------------------------------------------------------------------------- #
# _on_data_loaded
# --------------------------------------------------------------------------- #


def test_on_data_loaded_clears_state(dpg_stub):
    """C12: the DATA_LOADED subscriber wipes all per-parent_tag pagination state."""
    av.render_array_view("p", np.zeros((3, 2)))
    assert av._pagination_state
    av._on_data_loaded()
    assert av._pagination_state == {}
