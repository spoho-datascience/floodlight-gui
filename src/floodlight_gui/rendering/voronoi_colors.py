"""Pure-numpy palette and RGBA-fill helpers for the Voronoi overlay.

DPG-free and matplotlib-free at module scope: this module is imported by both
the live DPG adapter (``rendering/adapters/``) and the matplotlib export overlay
(``rendering/export_overlays/``), so it must not pull either framework as a
transitive dependency.
"""

from __future__ import annotations

import numpy as np


def build_voronoi_palette(
    c1: np.ndarray,
    c2: np.ndarray,
    alpha: float,
    n_team1: int,
    n_team2: int,
) -> np.ndarray:
    """Return an (n_team1 + n_team2 + 1, 4) float32 RGBA palette array.

    Row index matches the controlling player xID. The last row is a
    transparent NaN sentinel (``[0, 0, 0, 0]``), used by ``fill_voronoi_rgba``
    to represent cells with no controlling player.

    Intended to be rebuilt only when the alpha or color settings change, not
    per frame.

    Parameters
    ----------
    c1 : np.ndarray
        RGB color for team 1, shape (3,) or broadcastable to (n_team1, 3).
    c2 : np.ndarray
        RGB color for team 2, shape (3,) or broadcastable to (n_team2, 3).
    alpha : float
        Opacity applied to all player rows (0.0 = fully transparent,
        1.0 = fully opaque).
    n_team1 : int
        Number of players on team 1.
    n_team2 : int
        Number of players on team 2.

    Returns
    -------
    np.ndarray
        Float32 array of shape (n_team1 + n_team2 + 1, 4). Rows 0..n_team1-1
        are team 1; rows n_team1..n_team1+n_team2-1 are team 2; the last row
        is the NaN sentinel.
    """
    palette = np.zeros((n_team1 + n_team2 + 1, 4), dtype=np.float32)
    # Team 1 rows: RGB from c1, alpha column = alpha.
    palette[:n_team1, 0:3] = c1
    palette[:n_team1, 3] = float(alpha)
    # Team 2 rows: RGB from c2, alpha column = alpha.
    palette[n_team1 : n_team1 + n_team2, 0:3] = c2
    palette[n_team1 : n_team1 + n_team2, 3] = float(alpha)
    # Last row stays [0, 0, 0, 0] (NaN sentinel), already initialized by np.zeros.
    return palette


def fill_voronoi_rgba(
    controls_frame: np.ndarray,
    n_team1: int,
    palette: np.ndarray,
    *,
    out_buf: np.ndarray,
    idx_map: np.ndarray,
) -> None:
    """Populate ``out_buf`` in place with RGBA colors gathered from ``palette``.

    Each cell in ``controls_frame`` holds the xID of the controlling player,
    or NaN for uncontrolled cells. NaN cells are mapped to the sentinel row
    (last row of ``palette``), which is transparent.

    The palette-gather is done via ``np.take`` rather than boolean fancy
    indexing: this form avoids one temporary boolean array per team and reads
    the palette in a single contiguous pass.

    Parameters
    ----------
    controls_frame : np.ndarray
        2-D float array of controlling player xIDs (NaN where uncontrolled).
    n_team1 : int
        Number of players on team 1. The palette layout encodes team
        classification (rows 0..n_team1-1 = team 1, remaining rows = team 2),
        so this parameter is kept in the signature for callers that need it
        for adjacent classification work.
    palette : np.ndarray
        Float32 RGBA palette of shape (n_players + 1, 4) as returned by
        ``build_voronoi_palette``.
    out_buf : np.ndarray
        Pre-allocated output array, same shape as ``controls_frame`` with a
        trailing size-4 axis. Mutated in place by ``np.take``.
    idx_map : np.ndarray
        Pre-allocated scratch array, same shape as ``controls_frame``. Its
        contents are overwritten on every call; the caller need not initialise
        it. Passing it in avoids a per-call allocation for the gather index.

    Returns
    -------
    None
        Mutates ``out_buf`` in place.
    """
    # NaN sentinel index = last row of palette.
    n_total = palette.shape[0] - 1
    # Map NaN cells to the sentinel index; cast to int64 for np.take.
    # np.take fills out_buf in one contiguous palette read.
    idx_map = np.where(np.isnan(controls_frame), n_total, controls_frame).astype(np.int64)
    np.take(palette, idx_map, axis=0, out=out_buf)
