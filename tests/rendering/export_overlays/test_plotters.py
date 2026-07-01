"""Export plotter silent-contract tests: delegation shape, z-order, y-orientation.

C1 plot_hull forwards t=frame, fill=True, fill_alpha to the model, and the hex
   voronoi branch delegates to upstream DiscreteVoronoiModel.plot with the right
   t / team_colors / alpha / ec / zorder. A wrong frame or alpha exports the
   wrong overlay.
C2 plot_hull raises only the artists it adds to zorder=5 and never bumps
   pre-existing axes artists, which would reorder the pitch/players in the file.
C3 the square voronoi pcolormesh is not y-flipped: top pixel is team-1, bottom
   is team-2. An upside-down export only shows up when you watch the file.
"""

from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.collections import QuadMesh
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon

# ---------------------------------------------------------------------------
# Hull plotter — delegation shape + z-order
# ---------------------------------------------------------------------------


class _StubHullModel:
    """Mimics ConvexHullModel.plot side effects: appends one Polygon + one Line2D per call."""

    def __init__(self):
        self.calls: list[dict] = []

    def plot(self, t, ax, fill, fill_alpha, **kwargs):
        self.calls.append({"t": t, "ax": ax, "fill": fill, "fill_alpha": fill_alpha, **kwargs})
        ax.add_line(Line2D([0, 1], [0, 1]))
        if fill:
            ax.add_patch(Polygon([(0, 0), (1, 0), (1, 1)], alpha=fill_alpha))


def _make_hull_spec(stub: _StubHullModel, alpha: float = 0.42) -> dict:
    return {
        "key": "hull",
        "model": stub,
        "alpha": alpha,
        "team1_color": [0, 0, 0, 0],
        "team2_color": [0, 0, 0, 0],
        "n_team1": 0,
    }


def test_hull_delegates_to_upstream():
    """plot_hull calls model.plot(t=frame, ax=ax, fill=True,
    fill_alpha=spec['alpha']). A wrong t exports the wrong frame's hull."""
    from floodlight_gui.rendering.export_overlays.hull import plot_hull

    stub = _StubHullModel()
    fig = Figure()
    ax = fig.add_subplot(111)
    plot_hull(_make_hull_spec(stub, alpha=0.42), ax, frame=7)

    assert len(stub.calls) == 1, f"expected 1 upstream call, got {len(stub.calls)}"
    call = stub.calls[0]
    assert call["t"] == 7
    assert call["ax"] is ax
    assert call["fill"] is True
    assert call["fill_alpha"] == 0.42


def test_hull_zorder_is_5_and_robust_to_pre_existing_artists():
    """plot_hull raises only the artists it adds to zorder=5, leaving
    pre-existing pitch/player artists untouched. Bumping the wrong artists
    reorders the exported scene."""
    from floodlight_gui.rendering.export_overlays.hull import plot_hull

    stub = _StubHullModel()
    fig = Figure()
    ax = fig.add_subplot(111)
    # Pre-existing artists at low z-order that must NOT be bumped.
    pre_line = Line2D([0, 1], [0, 1])
    pre_line.set_zorder(2)
    ax.add_line(pre_line)
    pre_patch = Polygon([(0, 0), (1, 0), (1, 1)])
    pre_patch.set_zorder(1)
    ax.add_patch(pre_patch)

    n_patches_before = len(ax.patches)
    n_lines_before = len(ax.lines)
    plot_hull(_make_hull_spec(stub, alpha=0.3), ax, frame=0)
    new_patches = ax.patches[n_patches_before:]
    new_lines = ax.lines[n_lines_before:]
    assert len(new_patches) == 1 and len(new_lines) == 1
    assert all(p.get_zorder() == 5 for p in new_patches), "new hull patch must be zorder 5"
    assert all(ln.get_zorder() == 5 for ln in new_lines), "new hull line must be zorder 5"
    assert pre_line.get_zorder() == 2, "pre-existing line was wrongly bumped"
    assert pre_patch.get_zorder() == 1, "pre-existing patch was wrongly bumped"


# ---------------------------------------------------------------------------
# Voronoi plotter — y-orientation + hex delegation
# ---------------------------------------------------------------------------


def _make_voronoi_spec(model, alpha: float = 0.5) -> dict:
    return {
        "key": "voronoi",
        "model": model,
        "alpha": alpha,
        "team1_color": [255, 0, 0, 255],
        "team2_color": [0, 0, 255, 255],
        "n_team1": 1,
    }


def test_voronoi_export_is_not_y_flipped():
    """The pcolormesh y-orientation matches floodlight's top-down mesh
    convention: top pixel is team-1 (red), bottom is team-2 (blue). An
    upside-down export only surfaces when you watch the file."""
    from floodlight_gui.rendering.export_overlays.voronoi import plot_voronoi

    class _Stub:
        _meshx_ = np.tile(np.linspace(5.0, 95.0, 4), (4, 1))
        _meshy_ = np.tile(np.linspace(55.0, 5.0, 4)[:, None], (1, 4))
        _xpolysize_ = 30.0
        _ypolysize_ = 16.67
        _N1_ = 1
        _cell_controls_ = np.array(
            [[[0, 0, 0, 0], [0, 0, 0, 0], [1, 1, 1, 1], [1, 1, 1, 1]]],
            dtype=float,
        )

    spec = {
        "key": "voronoi",
        "model": _Stub(),
        "alpha": 1.0,
        "team1_color": [255, 0, 0, 255],
        "team2_color": [0, 0, 255, 255],
        "n_team1": 1,
    }
    fig = Figure(figsize=(2, 2), dpi=50)
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_position([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 60)
    ax.set_axis_off()
    plot_voronoi(spec, ax, frame=0)
    canvas.draw()
    buf = np.frombuffer(canvas.tostring_argb(), dtype=np.uint8).reshape(100, 100, 4)

    top_pixel = buf[10, 50]
    bottom_pixel = buf[90, 50]
    assert top_pixel[1] > top_pixel[3], (
        f"y-flipped: top pixel should be red, got ARGB {tuple(top_pixel)}"
    )
    assert bottom_pixel[3] > bottom_pixel[1], (
        f"y-flipped: bottom pixel should be blue, got ARGB {tuple(bottom_pixel)}"
    )


class _SpyHexVoronoiModel:
    """Hexagonal-mesh spy recording the upstream plot() delegation call.

    The hex export branch delegates to upstream DiscreteVoronoiModel.plot, which
    owns the geometry and NaN-skip; this spy captures the delegation call shape.
    """

    _mesh_type = "hexagonal"
    _N1_ = 1

    def __init__(self):
        self.calls: list[dict] = []

    def plot(self, **kwargs):
        self.calls.append(kwargs)


def test_voronoi_hexagonal_delegates_to_model_plot():
    """A hex mesh makes plot_voronoi delegate to upstream
    DiscreteVoronoiModel.plot with t=frame, two RGB(0-1) team colors, the ax,
    alpha, ec='none', and the voronoi z-order, and not run the local square
    pcolormesh path. A broken delegation mis-renders the hex export."""
    from floodlight_gui.rendering.export_overlays.voronoi import plot_voronoi

    model = _SpyHexVoronoiModel()
    fig = Figure(figsize=(2, 2), dpi=50)
    ax = fig.add_subplot(111)

    plot_voronoi(_make_voronoi_spec(model, alpha=0.5), ax, frame=2)

    assert len(model.calls) == 1, "hex must delegate to exactly one model.plot() call"
    kw = model.calls[0]
    assert kw["t"] == 2
    assert kw["ax"] is ax
    assert kw["alpha"] == 0.5
    assert kw["ec"] == "none"
    assert kw["zorder"] == 3, "hex cells must sit at voronoi z-order 3"
    tc = kw["team_colors"]
    assert len(tc) == 2 and all(len(c) == 3 for c in tc), "team_colors = 2 RGB tuples"
    assert tuple(round(v, 3) for v in tc[0]) == (1.0, 0.0, 0.0)
    assert tuple(round(v, 3) for v in tc[1]) == (0.0, 0.0, 1.0)
    # The local square path (pcolormesh) must NOT run for a hex model.
    assert not any(isinstance(c, QuadMesh) for c in ax.collections)
