"""Matplotlib Voronoi cell overlay writer for the static/video export pipeline.

Renders DiscreteVoronoiModel cell-control data onto a matplotlib Axes. DPG-free
at module scope (export-tree invariant: rendering/export_overlays/ never imports
dearpygui). Shares ``rendering/voronoi_colors.py`` with the live VoronoiAdapter
as the single source of truth for cell-color classification.

The plotter is stateless: ``plot_voronoi(spec, ax, *, frame)`` renders directly
onto a caller-supplied Axes; no init/clear/set_visible lifecycle is needed because
the Axes is created fresh per export frame.
"""

from __future__ import annotations

import numpy as np

from floodlight_gui.rendering.voronoi_colors import (
    build_voronoi_palette,
    fill_voronoi_rgba,
)

# Z-order: voronoi sits at zorder 3 (pitch < voronoi < hull < players < numbers).
_VORONOI_ZORDER = 3


def plot_voronoi(spec: dict, ax, *, frame: int) -> None:
    """Render a DiscreteVoronoiModel cell-control frame on ``ax``.

    Parameters
    ----------
    spec : dict
        Overlay spec with keys ``"model"``, ``"alpha"``, ``"team1_color"``,
        ``"team2_color"``, ``"n_team1"``.
    ax : matplotlib.axes.Axes
        Axes to render into. Caller is responsible for axis limits and
        orientation (``floodlight.Pitch.plot()`` sets these correctly without
        calling ``ax.invert_yaxis()``).
    frame : int, keyword-only
        Frame index into ``model._cell_controls_``.

    Notes
    -----
    Dispatches on ``model._mesh_type``:

    * ``"hexagonal"``: delegates to ``DiscreteVoronoiModel.plot()``. The upstream
      method draws staggered hexagons and skips NaN cells; it colors by
      ``_N1_``/``_N2_``, which equal ``n_team1``/``n_team2`` from the spec.
    * ``"square"``: renders locally as a single ``pcolormesh`` quad-grid using
      explicit ``meshx``/``meshy`` arrays (not ``extent=``). Uses the shared
      ``voronoi_colors`` helper for RGBA classification, the same source of truth
      as the live VoronoiAdapter.
    """
    model = spec["model"]

    # Normalize 0-255 color components to 0-1.
    c1 = np.asarray(spec["team1_color"][:3], dtype=np.float32) / 255.0
    c2 = np.asarray(spec["team2_color"][:3], dtype=np.float32) / 255.0
    alpha = float(spec["alpha"])

    if getattr(model, "_mesh_type", "square") == "hexagonal":
        # Delegate hex rendering to upstream DiscreteVoronoiModel.plot; it draws
        # staggered hexagons per controlled cell and skips NaN cells. Only ec and
        # zorder are added here.
        model.plot(
            t=frame,
            team_colors=(tuple(float(v) for v in c1), tuple(float(v) for v in c2)),
            ax=ax,
            alpha=alpha,
            ec="none",
            zorder=_VORONOI_ZORDER,
        )
        return

    # Square mesh: per-cell RGBA via the shared palette helper, painted as one
    # pcolormesh quad-grid (shading='nearest' centers a quad on each mesh point).
    meshx = model._meshx_
    meshy = model._meshy_
    n_team1 = int(spec["n_team1"])
    controls = model._cell_controls_
    cmax = int(np.nanmax(controls)) if controls.size else 0
    n_team2 = max(0, cmax + 1 - n_team1)
    palette = build_voronoi_palette(c1, c2, alpha, n_team1, n_team2)
    frame_data = controls[frame]
    rgba = np.zeros((*frame_data.shape, 4), dtype=np.float32)
    idx_map = np.zeros(frame_data.shape, dtype=np.int64)
    fill_voronoi_rgba(frame_data, n_team1, palette, out_buf=rgba, idx_map=idx_map)
    ax.pcolormesh(
        meshx,
        meshy,
        rgba,
        shading="nearest",
        antialiased=False,
        edgecolors="none",
        zorder=_VORONOI_ZORDER,
    )
