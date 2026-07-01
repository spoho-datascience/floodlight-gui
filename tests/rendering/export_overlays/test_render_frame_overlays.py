"""render_frame overlay dispatch: silent export-correctness contracts.

A wrong spec, z-order, or skip exports the wrong file, caught only when you
watch the export later.

C1 overlays=None / [] is a true no-op (no QuadMesh added).
C2 the voronoi QuadMesh (zorder=3) sits below hull artists (zorder=5); a
   swapped layer buries one overlay.
C3 an unknown overlay key logs and skips, and a failing plotter is caught;
   either raising would abort the entire export.
C4 save_frame and render_video forward the overlays kwarg; without it the
   export drops every overlay.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from matplotlib.lines import Line2D
from matplotlib.patches import Polygon


def _make_minimal_pitch():
    """Minimal stub `Pitch`; its `plot(ax=ax)` adds nothing to the axes."""
    p = MagicMock()
    p.plot = MagicMock(return_value=None)
    p.xlim = (0, 100)
    p.ylim = (0, 60)
    return p


def _make_voronoi_spec():
    class _Stub:
        _meshx_ = np.tile(np.linspace(5.0, 95.0, 4), (4, 1))
        _meshy_ = np.tile(np.linspace(55.0, 5.0, 4)[:, None], (1, 4))
        _xpolysize_ = 30.0
        _ypolysize_ = 16.67
        _N1_ = 1
        _cell_controls_ = np.zeros((1, 4, 4), dtype=float)

    return {
        "key": "voronoi",
        "model": _Stub(),
        "alpha": 0.5,
        "team1_color": [255, 0, 0, 255],
        "team2_color": [0, 0, 255, 255],
        "n_team1": 1,
    }


def _make_hull_spec_with_stub():
    class _HullStub:
        def plot(self, t, ax, fill, fill_alpha, **kwargs):
            ax.add_line(Line2D([0, 1], [0, 1]))
            if fill:
                ax.add_patch(Polygon([(0, 0), (1, 0), (1, 1)], alpha=fill_alpha))

    return {
        "key": "hull",
        "model": _HullStub(),
        "alpha": 0.3,
        "team1_color": [0, 0, 0, 0],
        "team2_color": [0, 0, 0, 0],
        "n_team1": 0,
    }


@pytest.mark.parametrize("overlays", [None, []])
def test_render_frame_no_overlays_is_no_op(overlays):
    """overlays=None / [] is a true no-op: no QuadMesh is added. A regression
    here silently mutates the exported frame."""
    from floodlight_gui.rendering.export_renderer import render_frame

    fig = render_frame(
        pitch=_make_minimal_pitch(),
        xy_data={},
        frame=0,
        teams=[],
        team_colors={},
        options=None,
        overlays=overlays,
    )
    quadmesh = [c for c in fig.axes[0].collections if type(c).__name__ == "QuadMesh"]
    assert len(quadmesh) == 0


def test_multi_overlay_render_zorder():
    """The voronoi QuadMesh at zorder=3 sits below hull artists at zorder=5. A
    swapped z-order buries one overlay under the other in the exported file."""
    from floodlight_gui.rendering.export_renderer import render_frame

    fig = render_frame(
        pitch=_make_minimal_pitch(),
        xy_data={},
        frame=0,
        teams=[],
        team_colors={},
        options=None,
        overlays=[_make_voronoi_spec(), _make_hull_spec_with_stub()],
    )
    ax = fig.axes[0]
    quadmesh = [c for c in ax.collections if type(c).__name__ == "QuadMesh"]
    assert len(quadmesh) == 1
    assert quadmesh[0].get_zorder() == 3, (
        f"voronoi QuadMesh zorder must be 3, got {quadmesh[0].get_zorder()}"
    )
    hull_polygons = [p for p in ax.patches if isinstance(p, Polygon)]
    hull_lines = [ln for ln in ax.lines if isinstance(ln, Line2D)]
    assert len(hull_polygons) >= 1, "expected at least 1 hull polygon"
    assert all(p.get_zorder() == 5 for p in hull_polygons), (
        f"hull polygon zorder must be 5, got {[p.get_zorder() for p in hull_polygons]}"
    )
    assert all(ln.get_zorder() == 5 for ln in hull_lines), (
        f"hull line zorder must be 5, got {[ln.get_zorder() for ln in hull_lines]}"
    )


def test_render_frame_unknown_overlay_key_logs_and_skips():
    """An unknown overlay key logs a warning and skips; it must not raise, or
    the whole export aborts silently."""
    from floodlight_gui.rendering.export_renderer import render_frame

    fake_spec = {
        "key": "bogus_key_does_not_exist",
        "model": None,
        "alpha": 1.0,
        "team1_color": [0] * 4,
        "team2_color": [0] * 4,
        "n_team1": 0,
    }
    fig = render_frame(
        pitch=_make_minimal_pitch(),
        xy_data={},
        frame=0,
        teams=[],
        team_colors={},
        options=None,
        overlays=[fake_spec],
    )
    assert fig is not None


def test_render_frame_failing_plotter_does_not_abort_export():
    """One plotter exception is caught and logged; the export still produces a
    figure rather than losing the whole file."""
    from floodlight_gui.rendering.export_renderer import render_frame

    class _ExplodingHull:
        def plot(self, *a, **kw):
            raise RuntimeError("intentional test failure")

    bad_spec = {
        "key": "hull",
        "model": _ExplodingHull(),
        "alpha": 0.5,
        "team1_color": [0] * 4,
        "team2_color": [0] * 4,
        "n_team1": 0,
    }
    fig = render_frame(
        pitch=_make_minimal_pitch(),
        xy_data={},
        frame=0,
        teams=[],
        team_colors={},
        options=None,
        overlays=[bad_spec],
    )
    assert fig is not None


@pytest.mark.parametrize("fn_name", ["save_frame", "render_video"])
def test_export_entrypoint_forwards_overlays_kwarg(fn_name):
    """save_frame and render_video expose an ``overlays`` kwarg defaulting to
    None. Without it the export drops every overlay even when the caller built
    specs."""
    import inspect

    import floodlight_gui.rendering.export_renderer as er

    sig = inspect.signature(getattr(er, fn_name))
    assert "overlays" in sig.parameters, f"{fn_name} must expose overlays kwarg"
    assert sig.parameters["overlays"].default is None
