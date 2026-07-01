"""Export writer factories for the visualization tab.

Each factory returns a ``writer(filepath)`` callable that the generic
export-panel helper (``render_binary_export_action``) invokes in the GUI thread
at click time. Image export is synchronous; video export snapshots its inputs in
the GUI thread and renders on a daemon background thread.

This module is import-time DPG-free. Dear PyGui, matplotlib, the heavy
render functions, and floodlight are all imported lazily inside the writer bodies
so that ``import floodlight_gui`` never pulls in a GUI backend.

Shared preamble (``_build_scene``) and the error-boundary tail
(``_report_failure``) are factored out of the two writers; the writers keep only
their genuinely divergent logic (sync savefig vs. background-threaded clip
render with frame-range math).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from floodlight_gui.tabs.visualization import state

__all__ = ["_make_image_writer", "_make_video_writer", "clip_frame_range"]

logger = logging.getLogger(__name__)

_STATUS_TAG = "viz_export_status"
_VIZ_TAB_TAG = "viz_tab"
_CLIP_LENGTH_TAG = "viz_video_clip_length"
_DEFAULT_CLIP_SECONDS = 5


@dataclass(frozen=True)
class _ExportScene:
    """Immutable snapshot of everything a render call needs.

    Built once in the GUI thread by :func:`_build_scene` so that the video
    writer can hand a frozen copy to its background thread without touching
    live viz state from off the GUI thread (overlay-race invariant).
    """

    pitch: object
    xy_data: dict[str, object]
    team_colors: dict[str, tuple[float, float, float, float]]
    teams: list[str]
    overlays: list[dict]


def _set_status(text: str) -> None:
    """Best-effort write to the export status widget.

    Guards the dpg import (so error reporting works even with no GUI backend)
    and suppresses ``SystemError`` so a missing widget never breaks an export.
    """
    try:
        import dearpygui.dearpygui as dpg
    except ImportError:
        return
    with contextlib.suppress(SystemError):
        dpg.set_value(_STATUS_TAG, text)


def _report_failure(exc: Exception, *, context: str, suggested_fix: str) -> None:
    """Common error-boundary tail: log, surface in the status bar, show modal.

    Used at every callback / thread boundary. DPG calls here may themselves
    raise ``SystemError`` (missing widget); those are suppressed so error
    reporting is always best-effort.
    """
    from floodlight_gui.tabs._shared.error_helpers import friendly_error_message, show_error_modal

    logger.exception("%s", context)
    _set_status(f"Status: Export failed -- {friendly_error_message(exc)}")
    with contextlib.suppress(SystemError):
        show_error_modal(_VIZ_TAB_TAG, exc, context=context, suggested_fix=suggested_fix)


def _build_scene(*, error_noun: str) -> _ExportScene:
    """Resolve and snapshot the shared render inputs in the GUI thread.

    *error_noun* fills the guard message (``"image"`` / ``"video"``). Raises
    ``RuntimeError`` when no data is loaded.
    """
    from floodlight_gui.core.xy_access import get_xy_for_period_team
    from floodlight_gui.tabs.visualization import colors as _colors
    from floodlight_gui.tabs.visualization.overlay_dispatch import (
        _build_overlay_specs_for_export,
    )

    app = state.app_instance
    if not app or not app.loaded_data:
        raise RuntimeError(f"No data loaded -- cannot export {error_noun}.")

    pitch = app.pitch
    if pitch is None:
        raise RuntimeError(
            f"No pitch attached -- cannot export {error_noun}. "
            'Attach one via the Load tab\'s "Build / Replace Pitch" panel first.'
        )

    half = state.viz_state.get("selected_half", "firstHalf")
    team_names = state.viz_state.get("cached_team_names", []) or []
    selected_teams = state.viz_state.get("selected_teams", {}) or {}

    xy_data: dict[str, object] = {}
    for team in team_names:
        # Honor the live view's team-visibility toggles (mirrors render_loop's
        # `selected_teams.get(team, True)` gate): a team hidden in the live view
        # must NOT reappear in the exported screenshot / video.
        if not selected_teams.get(team, True):
            continue
        xy = get_xy_for_period_team(app, half, team)
        if xy is not None:
            xy_data[team] = xy

    team_colors: dict[str, tuple[float, float, float, float]] = {}
    color_idx = 0
    for team in team_names:
        rgba, used_cycle = _colors.team_color_for(team, color_idx)
        if used_cycle:
            color_idx += 1
        team_colors[team] = tuple(channel / 255.0 for channel in rgba)

    teams = [t for t in team_names if t in xy_data]
    overlays = _build_overlay_specs_for_export()

    return _ExportScene(
        pitch=pitch,
        xy_data=xy_data,
        team_colors=team_colors,
        teams=teams,
        overlays=overlays,
    )


def _clamp_frame(frame: int) -> int:
    """Clamp *frame* into ``[0, max_frames - 1]``."""
    max_frames = state.viz_state.get("max_frames", 1)
    return max(0, min(int(frame), max(max_frames - 1, 0)))


def _make_image_writer(ext: str):
    """Return a synchronous writer that saves the current frame to *filepath*.

    Parameters
    ----------
    ext : str
        Accepted for call-signature symmetry with callers that pass "png" /
        "svg" / "pdf"; ``fig.savefig`` infers the format from the *filepath*
        extension, so this value is not forwarded to the writer body.

    Returns
    -------
    Callable[[str], None]
        A writer that renders the current frame via matplotlib and saves it.
        Raises no exceptions to the caller; errors are caught, logged, and
        surfaced in the status widget and an error modal.
    """

    def writer(filepath: str) -> None:
        """Render the current frame and save it to *filepath*."""
        try:
            from matplotlib import pyplot as plt

            from floodlight_gui.rendering.export_renderer import render_frame

            scene = _build_scene(error_noun="image")
            frame = _clamp_frame(state.viz_state.get("current_frame", 0))

            fig = render_frame(
                pitch=scene.pitch,
                xy_data=scene.xy_data,
                frame=frame,
                teams=scene.teams,
                team_colors=scene.team_colors,
                options=None,
                overlays=scene.overlays,
            )
            try:
                fig.savefig(filepath, bbox_inches="tight", dpi=150)
                logger.info("Export image: saved %s", filepath)
                _set_status(f"Status: Saved {Path(filepath).name}")
            finally:
                plt.close(fig)
        except Exception as exc:  # noqa: BLE001 -- DPG callback boundary; must not propagate
            _report_failure(
                exc,
                context="Single-frame image export failed.",
                suggested_fix="Verify the output path is writable and the data is loaded.",
            )

    return writer


def _coerce_mp4(filepath: str) -> str:
    """Force *filepath* to a ``.mp4`` extension."""
    path = Path(filepath)
    if path.suffix.lower() == ".mp4":
        return filepath
    if "." not in path.name:
        return f"{filepath}.mp4"
    return str(path.with_suffix(".mp4"))


def _read_clip_seconds() -> int:
    """Read the clip-length widget, defaulting to 5s and flooring at 1s."""
    import dearpygui.dearpygui as dpg

    try:
        clip_seconds = int(dpg.get_value(_CLIP_LENGTH_TAG))
    except SystemError:
        clip_seconds = _DEFAULT_CLIP_SECONDS
    return max(1, clip_seconds)


def clip_frame_range() -> tuple[int, int]:
    """Compute ``(start_frame, end_frame)`` for a video clip from ``viz_state``.

    Single source of the clip frame-range math, called by both the video writer
    at render time and the export panel's click-time ``n_provider`` /
    ``end_provider`` so both always agree on the range.

    ``start_frame`` is the current frame clamped to ``[0, max_frames-1]``.
    ``end_frame = min(max_frames, start_frame + clip_seconds * base_fps)`` where
    ``base_fps = original_fps or play_speed`` and ``clip_seconds`` is the clamped
    clip-length widget value (default 5s, floored at 1s).

    Returns
    -------
    tuple[int, int]
        ``(start_frame, end_frame)`` both clamped to ``[0, max_frames]``.
    """
    fps = int(state.viz_state.get("play_speed", 25))
    base_fps = int(state.viz_state.get("original_fps") or fps)
    start_frame = _clamp_frame(state.viz_state.get("current_frame", 0))
    max_frames = int(state.viz_state.get("max_frames", 1))
    end_frame = min(max_frames, start_frame + _read_clip_seconds() * base_fps)
    return start_frame, end_frame


def _make_video_writer():
    """Return a writer that renders a clip to MP4 on a daemon background thread.

    All shared render inputs are snapshotted in the GUI thread (overlay-race
    invariant): the background closure renders from the frozen
    :class:`_ExportScene` and never re-reads live viz state.

    Returns
    -------
    Callable[[str], None]
        A writer that snapshots scene state, starts the background render, and
        returns immediately. Video-render errors are caught on the background
        thread, logged, and surfaced in the status widget and an error modal.

    Notes
    -----
    Side-effect: spawns a daemon ``threading.Thread`` named ``"video-export"``.
    The filepath is coerced to ``.mp4`` regardless of the caller-supplied extension.
    """

    def writer(filepath: str) -> None:
        """Snapshot scene state, then spawn a background thread to render the clip."""
        try:
            filepath = _coerce_mp4(filepath)
            scene = _build_scene(error_noun="video")

            # play_speed is the live viewer speed (2x => MP4 renders at 2x native);
            # original_fps is the dataset's native rate used to size the source
            # window, falling back to play_speed pre-data-load. The clip range is
            # single-sourced in clip_frame_range() so the writer and the export
            # panel's click-time providers can never disagree.
            fps = int(state.viz_state.get("play_speed", 25))
            start_frame, end_frame = clip_frame_range()
            if end_frame <= start_frame:
                logger.warning(
                    "Export video: empty frame range [%d, %d); aborting",
                    start_frame,
                    end_frame,
                )
                return

            _set_status(f"Status: Rendering clip (frames {start_frame}-{end_frame}) ...")

            def _do_render() -> None:
                """Render and write the video clip; called on the background thread."""
                try:
                    from floodlight_gui.rendering.export_renderer import render_video

                    render_video(
                        path=filepath,
                        pitch=scene.pitch,
                        xy_data=scene.xy_data,
                        frame_range=(start_frame, end_frame),
                        teams=scene.teams,
                        team_colors=scene.team_colors,
                        options=None,
                        fps=fps,
                        overlays=scene.overlays,
                    )
                    logger.info(
                        "Export video: saved %s (%d frames)",
                        filepath,
                        end_frame - start_frame,
                    )
                    _set_status(f"Status: Saved {Path(filepath).name}")
                except Exception as exc:  # noqa: BLE001 -- background-thread boundary
                    _report_failure(
                        exc,
                        context="Video render failed in background thread.",
                        suggested_fix=(
                            "Check available disk space and that the output directory is writable."
                        ),
                    )

            threading.Thread(target=_do_render, name="video-export", daemon=True).start()
        except Exception as exc:  # noqa: BLE001 -- DPG callback boundary; must not propagate
            _report_failure(
                exc,
                context="Video export setup failed.",
                suggested_fix="Check that the output directory is writable.",
            )

    return writer
