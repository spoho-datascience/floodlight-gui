"""Behavioral contracts for ``floodlight_gui.rendering.voronoi_colors``.

Two pure-numpy helpers back the Voronoi overlay. ``build_voronoi_palette``
lays out an RGBA lookup table keyed by controlling-player xID with a trailing
transparent NaN sentinel. ``fill_voronoi_rgba`` gathers a per-cell control
index grid through that palette into a pre-allocated RGBA buffer in place,
routing NaN cells to the sentinel.

Both functions own their math entirely (no collaborators), so values are
asserted exactly: layout, dtype, the sentinel, and the in-place mutation
(same object and same backing memory).

Behavioral contracts guarded here
---------------------------------
build_voronoi_palette
  C1  Returns shape (n_team1 + n_team2 + 1, 4) with dtype float32, across
      several real roster sizes.
  C2  Team-1 rows carry c1 RGB + alpha; team-2 rows carry c2 RGB + alpha; the
      last row is the transparent NaN sentinel [0, 0, 0, 0].

fill_voronoi_rgba
  C3  Mutates ``out_buf`` in place (same object, same backing memory) and
      returns None.
  C4  Each cell's RGBA equals the palette row for its control xID, including
      an asymmetric roster where only some team-2 xIDs appear in the grid.
  C5  NaN cells map to the sentinel (last palette row), which is transparent.
"""

from __future__ import annotations

import numpy as np
import pytest

from floodlight_gui.rendering.voronoi_colors import (
    build_voronoi_palette,
    fill_voronoi_rgba,
)

RED = np.array([1.0, 0.0, 0.0], dtype=np.float32)
BLUE = np.array([0.0, 0.0, 1.0], dtype=np.float32)


@pytest.mark.parametrize("n1, n2", [(11, 11), (7, 6), (1, 1)])
def test_palette_shape_and_dtype(n1, n2):
    """C1: palette is (n1 + n2 + 1, 4) float32 for real roster sizes."""
    palette = build_voronoi_palette(RED, BLUE, 0.5, n1, n2)
    assert palette.shape == (n1 + n2 + 1, 4)
    assert palette.dtype == np.float32


def test_palette_row_layout_and_sentinel():
    """C2: team-1 / team-2 rows carry their RGB+alpha; last row is the sentinel."""
    n1, n2, alpha = 3, 2, 0.5
    palette = build_voronoi_palette(RED, BLUE, alpha, n1, n2)
    # Team 1 rows.
    assert np.all(palette[:n1, 0:3] == RED)
    assert np.all(palette[:n1, 3] == np.float32(alpha))
    # Team 2 rows.
    assert np.all(palette[n1 : n1 + n2, 0:3] == BLUE)
    assert np.all(palette[n1 : n1 + n2, 3] == np.float32(alpha))
    # Sentinel: transparent black.
    assert np.all(palette[-1] == np.array([0.0, 0.0, 0.0, 0.0], dtype=np.float32))


def test_fill_mutates_out_buf_in_place():
    """C3: out_buf is filled in place (same object, same memory) and None is returned."""
    n1, n2 = 2, 2
    palette = build_voronoi_palette(RED, BLUE, 1.0, n1, n2)
    controls = np.array([[0.0, 1.0], [2.0, 3.0]], dtype=np.float64)
    out_buf = np.empty(controls.shape + (4,), dtype=np.float32)
    idx_map = np.empty(controls.shape, dtype=np.float64)
    buf_base = out_buf
    original_ptr = out_buf.__array_interface__["data"][0]

    result = fill_voronoi_rgba(controls, n1, palette, out_buf=out_buf, idx_map=idx_map)

    assert result is None
    assert out_buf is buf_base
    assert out_buf.__array_interface__["data"][0] == original_ptr


def test_fill_gathers_palette_rows_asymmetric_roster():
    """C4: each cell receives its control xID's palette row, asymmetric roster.

    The grid references both team-1 xIDs and only a subset of team-2 xIDs,
    the realistic case where some defenders never control a cell. Every cell
    must still resolve to its own player's color.
    """
    n1, n2 = 3, 4  # 4 team-2 players, but only xIDs 3 and 5 appear below.
    palette = build_voronoi_palette(RED, BLUE, 1.0, n1, n2)
    controls = np.array([[0.0, 3.0], [5.0, 2.0]], dtype=np.float64)
    out_buf = np.empty(controls.shape + (4,), dtype=np.float32)
    idx_map = np.empty(controls.shape, dtype=np.float64)

    fill_voronoi_rgba(controls, n1, palette, out_buf=out_buf, idx_map=idx_map)

    assert np.all(out_buf[0, 0] == palette[0])  # team-1 player 0
    assert np.all(out_buf[0, 1] == palette[3])  # team-2 player 3
    assert np.all(out_buf[1, 0] == palette[5])  # team-2 player 5
    assert np.all(out_buf[1, 1] == palette[2])  # team-1 player 2


def test_fill_nan_cells_map_to_transparent_sentinel():
    """C5: NaN cells resolve to the transparent sentinel row."""
    n1, n2 = 2, 2
    palette = build_voronoi_palette(RED, BLUE, 1.0, n1, n2)
    controls = np.array([[np.nan, 1.0], [3.0, np.nan]], dtype=np.float64)
    out_buf = np.empty(controls.shape + (4,), dtype=np.float32)
    idx_map = np.empty(controls.shape, dtype=np.float64)

    fill_voronoi_rgba(controls, n1, palette, out_buf=out_buf, idx_map=idx_map)

    sentinel = palette[-1]
    assert np.all(out_buf[0, 0] == sentinel)
    assert np.all(out_buf[1, 1] == sentinel)
    # The sentinel is fully transparent.
    assert out_buf[0, 0, 3] == 0.0
    # A controlled neighbor is unaffected.
    assert np.all(out_buf[0, 1] == palette[1])
