"""Matplotlib-based export renderer for screenshots and video clips.

Export-only: never called from the live DPG render loop. Safe to call from
background threads because it makes no Dear PyGui calls.
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from floodlight_gui.core.optional_features import HAS_FFMPEG

logger = logging.getLogger(__name__)


def render_frame(
    pitch,
    xy_data: dict[str, object],
    frame: int,
    teams: list[str],
    team_colors: dict[str, object],
    options: dict[str, object] | None = None,
    overlays: list[dict] | None = None,
) -> Figure:
    """Render a single frame to a matplotlib Figure.

    Parameters
    ----------
    pitch : floodlight.Pitch
        Pitch object defining the coordinate system and markings.
    xy_data : dict of {team_name: floodlight.XY}
        Tracking data per team.
    frame : int
        Frame index to render.
    teams : list of str
        Which teams to include in the plot.
    team_colors : dict of {team_name: matplotlib color}
        Colour specification per team (any format matplotlib accepts).
    options : dict, optional
        Rendering options:
        - ``show_numbers`` (bool): overlay player jersey numbers. Default False.
        - ``show_ball`` (bool): render ball marker if 'Ball' is in *xy_data*. Default True.
        - ``plot_type`` (str): ``"positions"`` or ``"trajectories"``. Default ``"positions"``.
        - ``trajectory_length`` (int): number of trailing frames for trajectories. Default 50.
        - ``color_scheme`` (str): ``"standard"`` or ``"bw"``. Passed to ``pitch.plot()``. Default ``"standard"``.
        - ``figsize`` (tuple): figure size in inches. Default (12, 8).
        - ``dpi`` (int): figure resolution. Default 150.
        - ``marker_size`` (int): scatter marker size. Default 100.
        - ``alpha`` (float): marker / trajectory alpha. Default 0.85.
        - ``title`` (str): optional title placed above the axes.
    overlays : list of dict, optional
        Overlay spec list. Each spec dict carries
        ``{"key", "model", "alpha", "team1_color", "team2_color", "n_team1"}``.
        When non-empty, plotters fire after pitch markings and before player
        scatter (Z-order: pitch <= 2 < voronoi 3 < hull 5 < players 10 <
        numbers 11). When ``None`` or empty, the overlay branch is a no-op.
        The spec-list dispatcher looks up each spec's ``"key"`` in
        ``EXPORT_OVERLAY_REGISTRY``.

    Returns
    -------
    matplotlib.figure.Figure
        The rendered figure. Caller is responsible for closing it.
    """  # noqa: E501 - descriptor / module-level constant
    opts = _default_options(options)

    fig, ax = plt.subplots(
        figsize=opts["figsize"],
        dpi=opts["dpi"],
    )

    # -- pitch markings -------------------------------------------------------
    try:
        pitch.plot(ax=ax, color_scheme=opts["color_scheme"])
    except TypeError:
        # Older floodlight versions may not support color_scheme kwarg
        pitch.plot(ax=ax)

    # -- overlay dispatch ------------------------------------------------------
    # Plotters fire after pitch markings and before players so the resulting
    # Z-order is pitch (<=2) < voronoi (3) < hull (5) < players (10) <
    # numbers (11). When `overlays` is None or empty, this block is a no-op.
    if overlays:
        # Lazy import keeps the overlay module tree out of the module-load
        # import graph; imageio.v3 uses the same pattern in render_video.
        from floodlight_gui.rendering.export_overlays import EXPORT_OVERLAY_REGISTRY

        for spec in overlays:
            plotter = EXPORT_OVERLAY_REGISTRY.get(spec.get("key"))
            if plotter is None:
                logger.warning("Unknown export overlay key %r - skipping", spec.get("key"))
                continue
            try:
                plotter(spec, ax, frame=frame)
            except Exception:  # noqa: BLE001 - export-time render-loop boundary; one bad spec must not abort the whole export
                logger.exception("Export overlay plotter failed for key %r", spec.get("key"))

    # -- player data ----------------------------------------------------------
    for team_name in teams:
        xy = xy_data.get(team_name)
        if xy is None:
            continue

        color = team_colors.get(team_name, "gray")

        if opts["plot_type"] == "trajectories":
            traj_len = opts["trajectory_length"]
            start = max(0, frame - traj_len)
            try:
                xy.plot(
                    t=(start, frame),
                    plot_type="trajectories",
                    ax=ax,
                    color=color,
                    alpha=opts["alpha"] * 0.6,
                )
            except (TypeError, ValueError, AttributeError, IndexError):
                logger.debug(
                    "Trajectory plot failed for %s, falling back to positions",
                    team_name,
                    exc_info=True,
                )
                _scatter_frame(ax, xy, frame, color, opts)
            # Also plot current positions on top of trajectories
            _scatter_frame(ax, xy, frame, color, opts)
        else:
            # positions (default)
            try:
                xy.plot(
                    t=frame,
                    plot_type="positions",
                    ax=ax,
                    color=color,
                    s=opts["marker_size"],
                    alpha=opts["alpha"],
                )
            except (TypeError, ValueError, AttributeError, IndexError):
                logger.debug(
                    "XY.plot failed for %s, falling back to manual scatter",
                    team_name,
                    exc_info=True,
                )
                _scatter_frame(ax, xy, frame, color, opts)

    # -- ball -----------------------------------------------------------------
    if opts["show_ball"] and "Ball" in xy_data and "Ball" not in teams:
        _scatter_frame(ax, xy_data["Ball"], frame, "white", opts, marker="o", edgecolor="black")

    # -- jersey numbers -------------------------------------------------------
    if opts["show_numbers"]:
        _annotate_numbers(ax, xy_data, frame, teams)

    # -- title ----------------------------------------------------------------
    if opts.get("title"):
        ax.set_title(opts["title"], fontsize=12)

    fig.tight_layout()
    return fig


def save_frame(
    path: str | Path,
    pitch,
    xy_data: dict[str, object],
    frame: int,
    teams: list[str],
    team_colors: dict[str, object],
    options: dict[str, object] | None = None,
    overlays: list[dict] | None = None,
) -> None:
    """Render a single frame and save it to a file.

    Supports any format matplotlib can write (PNG, SVG, PDF, etc.).
    The figure is closed automatically after saving.

    Parameters
    ----------
    path : str or Path
        Output file path. The extension determines the format.
    pitch, xy_data, frame, teams, team_colors, options, overlays
        Forwarded to :func:`render_frame`.  Use ``options["dpi"]`` and
        ``options["figsize"]`` to control resolution and size. See
        :func:`render_frame` for the ``overlays`` spec-list contract.
    """
    fig = render_frame(
        pitch,
        xy_data,
        frame,
        teams,
        team_colors,
        options,
        overlays=overlays,
    )
    try:
        dpi = _default_options(options)["dpi"]
        fig.savefig(str(path), dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
        logger.info("Saved frame %d to %s", frame, path)
    finally:
        plt.close(fig)


def render_video(
    path: str | Path,
    pitch,
    xy_data: dict[str, object],
    frame_range: tuple[int, int],
    teams: list[str],
    team_colors: dict[str, object],
    options: dict[str, object] | None = None,
    fps: int = 25,
    progress_callback=None,
    overlays: list[dict] | None = None,
) -> None:
    """Render a sequence of frames to a video file.

    Requires the ``imageio`` package with its ffmpeg plugin. If imageio is not
    installed the function logs a warning and returns without crashing.

    This function is designed to be called from a background thread (it makes
    no Dear PyGui calls).

    Parameters
    ----------
    path : str or Path
        Output video file path (e.g. ``"clip.mp4"``).
    pitch, xy_data, teams, team_colors, options
        Forwarded to :func:`render_frame` for each frame.
        Use ``options["dpi"]`` / ``options["figsize"]`` for resolution/size.
    frame_range : tuple of (int, int)
        Start and end frame indices (inclusive start, exclusive end).
    fps : int
        Frames per second in the output video. Default 25.
    progress_callback : callable, optional
        Called as ``progress_callback(current_frame_index, total_frames)``
        after each frame is written.
    overlays : list of dict, optional
        Spec list dispatched through :data:`EXPORT_OVERLAY_REGISTRY` on every
        frame. Each spec has keys ``{key, model, alpha, team1_color,
        team2_color, n_team1}``. Empty list or ``None`` short-circuits the
        overlay branch in :func:`render_frame` (no overlay code paths run).
    """
    if not HAS_FFMPEG:
        logger.warning("Video export unavailable; install floodlight-gui[video].")
        return
    import imageio.v3 as iio  # boot-probe verified at module import time

    start, end = frame_range
    total = end - start
    if total <= 0:
        logger.warning("Empty frame range (%d, %d), nothing to export.", start, end)
        return

    path = Path(path)
    logger.info("Starting video export: %d frames at %d fps -> %s", total, fps, path)

    # Use imageio writer for streaming frames to disk.
    # HAS_FFMPEG verifies the imageio_ffmpeg plugin specifically; prefer the
    # legacy get_writer path (which uses imageio_ffmpeg) first so we go
    # through the verified backend. Only try pyav if both legacy paths fail.
    writer = None
    _use_imopen = False
    try:
        writer = iio.get_writer(str(path), fps=fps)
    except (AttributeError, ImportError, OSError, ValueError):
        try:
            import imageio as _iio_legacy

            writer = _iio_legacy.get_writer(str(path), fps=fps)
        except (AttributeError, ImportError, OSError, ValueError):
            # Final fallback: pyav plugin via imopen. Catch ImportError too
            # so a venv without pyav reaches this branch only as a last resort.
            try:
                writer = iio.imopen(str(path), "w", plugin="pyav")
                _use_imopen = True
            except (AttributeError, ImportError, OSError, ValueError) as exc:
                raise RuntimeError(
                    "No working imageio video backend. Install one of: "
                    "`imageio_ffmpeg` (recommended) or `pyav`."
                ) from exc

    try:
        for idx, frame_idx in enumerate(range(start, end)):
            fig = render_frame(
                pitch, xy_data, frame_idx, teams, team_colors, options, overlays=overlays
            )
            try:
                rgba = _fig_to_rgba(fig)
                rgb = rgba[:, :, :3]  # drop alpha channel for video

                # Align frame dimensions to macro_block_size=16 boundary so
                # imageio_ffmpeg does not auto-resize and emit a warning.
                # Padding is zero-filled (black), at most 15 px per edge.
                rgb = _pad_to_macroblock(rgb)

                if _use_imopen:
                    writer.write(rgb, codec="libx264", fps=fps)
                else:
                    writer.append_data(rgb)
            finally:
                plt.close(fig)

            if progress_callback is not None:
                progress_callback(idx + 1, total)
    finally:
        writer.close()

    logger.info("Video export complete: %s", path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _default_options(options: dict[str, object] | None) -> dict[str, object]:
    """Merge user options with sensible defaults."""
    defaults = {
        "show_numbers": False,
        "show_ball": True,
        "plot_type": "positions",
        "trajectory_length": 50,
        "color_scheme": "standard",
        "figsize": (12, 8),
        "dpi": 150,
        "marker_size": 100,
        "alpha": 0.85,
        "title": None,
    }
    if options:
        defaults.update(options)
    return defaults


def _scatter_frame(ax, xy, frame, color, opts, marker="o", edgecolor=None):
    """Manual scatter fallback when xy.plot() is unavailable or fails."""
    try:
        row = xy.xy[frame]
    except (AttributeError, IndexError):
        return

    n_players = len(row) // 2
    xs = row[0::2][:n_players]
    ys = row[1::2][:n_players]

    mask = ~(np.isnan(xs) | np.isnan(ys))
    if not np.any(mask):
        return

    ax.scatter(
        xs[mask],
        ys[mask],
        c=color,
        s=opts.get("marker_size", 100),
        alpha=opts.get("alpha", 0.85),
        marker=marker,
        edgecolors=edgecolor or "none",
        zorder=10,
    )


def _annotate_numbers(ax, xy_data, frame, teams):
    """Annotate player positions with jersey numbers (1-based index)."""
    for team_name in teams:
        xy = xy_data.get(team_name)
        if xy is None:
            continue

        try:
            row = xy.xy[frame]
        except (AttributeError, IndexError):
            continue

        n_players = len(row) // 2
        for i in range(n_players):
            x, y = row[2 * i], row[2 * i + 1]
            if np.isnan(x) or np.isnan(y):
                continue
            ax.annotate(
                str(i + 1),
                (x, y),
                fontsize=7,
                ha="center",
                va="center",
                color="white",
                fontweight="bold",
                zorder=11,
            )


def _fig_to_rgba(fig: Figure):
    """Render a matplotlib Figure to an RGBA numpy array."""
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    buf = canvas.buffer_rgba()
    return np.asarray(buf)


def _pad_to_macroblock(frame: np.ndarray, block: int = 16) -> np.ndarray:
    """Pad a frame array so that width and height are multiples of *block*.

    imageio_ffmpeg's default codec requires frame dimensions to be divisible
    by ``macro_block_size=16``.  When matplotlib renders at e.g. 1800x1200 px
    the width is NOT divisible (1800 % 16 == 8), causing imageio_ffmpeg to
    auto-resize and emit a UserWarning:

        "input image is not divisible by macro_block_size=16, resizing from
         (1800, 1200) to (1808, 1200) to ensure video compatibility…"

    This helper pads the right/bottom edge with black pixels (zero-fill) so
    the array is already aligned before it reaches the writer.  The padding is
    minimal: at most ``block - 1`` pixels per dimension.

    Only real numpy arrays are padded.  Non-ndarray inputs (e.g. test mocks)
    are returned unchanged.

    Parameters
    ----------
    frame : numpy.ndarray, shape (H, W, C)
        RGB or RGBA frame array.
    block : int
        Macroblock alignment boundary, default 16.

    Returns
    -------
    numpy.ndarray
        The (possibly padded) frame.  Same dtype and channel count as input.
    """
    if not isinstance(frame, np.ndarray):
        return frame  # test stubs / mocks: pass through unchanged

    h, w = frame.shape[:2]
    pad_h = (block - h % block) % block
    pad_w = (block - w % block) % block

    if pad_h == 0 and pad_w == 0:
        return frame  # already aligned; fast path (no copy)

    return np.pad(
        frame,
        pad_width=((0, pad_h), (0, pad_w), (0, 0)),
        mode="constant",
        constant_values=0,
    )
