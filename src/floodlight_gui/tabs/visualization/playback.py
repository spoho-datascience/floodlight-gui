"""Playback engine for the visualization tab.

DPG-aware module: imports ``dearpygui`` at module scope (lives under ``tabs/``).

Thread-safety invariant: the worker thread only writes ``state._pending_frame``;
``_playback_tick`` (main thread) reads and clears it. No DPG calls on the
worker thread.

Cross-module coupling: ``_render_current_frame`` and ``_check_drawlist_resize``
live in ``render_loop.py``; ``_update_frame_info`` and ``_update_fps_info`` live
in ``controls.py``. Both are accessed via lazy in-function imports to avoid
circular import at module scope.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.tabs.visualization import state
from floodlight_gui.tabs.visualization.playback_clock import END as _END_OF_PLAYBACK
from floodlight_gui.tabs.visualization.playback_clock import PlaybackClock

__all__ = [
    "_toggle_play_pause",
    "_jump_frames",
    "_jump_to_period_start",
    "_jump_to_period_end",
    "_on_speed_changed",
    "_set_speed_multiplier",
    "_start_playback",
    "_stop_playback",
    "_stop_playback_thread",
    "_stop_playback_if_playing",
    "_playback_worker",
    "_on_frame_slider",
    "_playback_tick",
    "_emit_fps_window_sample",
    "_init_fps_overlay",
    "_FPS_SAMPLE_FRAMES",
    "_fps_frame_count",
    "_fps_start_time",
    "_TICK_TIMING",
    "_FPS_OVERLAY_ENABLED",
]

# FPS instrumentation counter.
# Emits logger.debug every _FPS_SAMPLE_FRAMES frames during playback.
# Run with --log-level=DEBUG to observe sustained rate.
_FPS_SAMPLE_FRAMES = 60
_fps_frame_count = 0
_fps_start_time = 0.0

# Env-var-gated visual FPS overlay.
# Cached at module import time so the tick path reads _FPS_OVERLAY_ENABLED
# rather than os.environ on every frame. Any non-empty string activates it.
_FPS_OVERLAY_DRAWLIST_TAG = "__fps_overlay_drawlist"
_FPS_OVERLAY_TEXT_TAG = "__fps_overlay_text"
_FPS_OVERLAY_ENABLED = bool(os.environ.get("FGUI_DEBUG_FPS"))

# Second overlay line: Voronoi cache-skip + upload-ms.
# Same "__" system-tag convention as the primary line; same parent drawlist.
_FPS_OVERLAY_VORONOI_TEXT_TAG = "__fps_overlay_voronoi_text"

# Per-window snapshots of the Voronoi adapter's cumulative counters.
# The overlay second line shows the DELTA since the last window write, not the
# cumulative total (mirrors the primary line's window-based FPS sampling).
_last_voronoi_uploaded_count = 0
_last_voronoi_skipped_count = 0

# Per-segment timing accumulators inside _playback_tick + _render_current_frame.
# Surfaced as a third overlay line when FGUI_DEBUG_FPS=1. Accumulators are summed
# over _FPS_SAMPLE_FRAMES and divided in _emit_fps_window_sample to produce
# per-frame averages.
_FPS_OVERLAY_TICK_TEXT_TAG = "__fps_overlay_tick_text"
_TICK_TIMING = {
    "resize": 0.0,
    "render_pull": 0.0,
    "render_players": 0.0,
    "render_overlays": 0.0,
    "ui_sync": 0.0,
    "bus_emit": 0.0,
    "total": 0.0,
}

logger = logging.getLogger(__name__)

# Owns the playback worker thread and the worker-to-pump frame handoff.
# Drift-free scheduling policy lives in _playback_worker below.
_clock = PlaybackClock()


def _on_frame_slider(sender, app_data):
    """Handle a user drag on the frame slider (DPG callback).

    Stops playback if running, updates the current frame in state, refreshes
    the frame-info label, triggers a re-render, and emits
    ``Events.FRAME_CHANGED``.

    Notes
    -----
    Side-effects: writes ``state.viz_state["current_frame"]`` and
    ``state.viz_state["last_slider_sync_frame"]``, calls
    ``controls._update_frame_info``, ``render_loop._render_current_frame``,
    and emits ``Events.FRAME_CHANGED``.
    """
    _stop_playback_if_playing("Slider moved")

    state.viz_state["current_frame"] = app_data
    state.viz_state["last_slider_sync_frame"] = app_data

    from floodlight_gui.tabs.visualization import controls as _controls

    _controls._update_frame_info()
    from floodlight_gui.tabs.visualization import render_loop as _rl

    _rl._render_current_frame()

    bus.emit(Events.FRAME_CHANGED, frame=app_data)


def _on_speed_changed(sender, app_data):
    """Handle a user change to the playback-speed slider (DPG callback).

    Notes
    -----
    Side-effects: writes ``state.viz_state["play_speed"]`` and calls
    ``controls._update_fps_info``.
    """
    state.viz_state["play_speed"] = app_data

    from floodlight_gui.tabs.visualization import controls as _controls

    _controls._update_fps_info()


def _set_speed_multiplier(multiplier: float) -> None:
    """Set playback speed to a multiple of the original data framerate.

    Computes ``round(original_fps * multiplier)``, clamped to [1, 60].
    Falls back to the current slider value when no data has been loaded yet.

    Parameters
    ----------
    multiplier : float
        Speed factor relative to the original framerate (e.g. 0.5 = half speed,
        2.0 = double speed).

    Notes
    -----
    Side-effects: writes ``state.viz_state["play_speed"]``, sets the
    ``"viz_play_speed"`` DPG widget value, calls ``controls._update_fps_info``.
    """
    base = state.viz_state.get("original_fps") or state.viz_state.get("play_speed", 25)
    new_speed = max(1, min(60, int(round(base * multiplier))))
    state.viz_state["play_speed"] = new_speed
    with contextlib.suppress(SystemError):
        dpg.set_value("viz_play_speed", new_speed)

    from floodlight_gui.tabs.visualization import controls as _controls

    _controls._update_fps_info()


def _jump_frames(delta):
    """Jump the current frame by *delta* positions.

    Stops playback if running, clamps the result to [0, max_frames], syncs the
    slider, and re-renders.

    Parameters
    ----------
    delta : int
        Number of frames to advance (positive) or rewind (negative).

    Notes
    -----
    Side-effects: writes ``state.viz_state["current_frame"]`` and
    ``state.viz_state["last_slider_sync_frame"]``, sets the
    ``"viz_frame_slider"`` DPG widget value, calls
    ``controls._update_frame_info`` and ``render_loop._render_current_frame``,
    emits ``Events.FRAME_CHANGED``.
    """
    _stop_playback_if_playing("Frame jump")

    new_frame = max(0, min(state.viz_state["max_frames"], state.viz_state["current_frame"] + delta))
    state.viz_state["current_frame"] = new_frame
    state.viz_state["last_slider_sync_frame"] = new_frame
    dpg.set_value("viz_frame_slider", new_frame)

    from floodlight_gui.tabs.visualization import controls as _controls

    _controls._update_frame_info()
    from floodlight_gui.tabs.visualization import render_loop as _rl

    _rl._render_current_frame()

    bus.emit(Events.FRAME_CHANGED, frame=new_frame)


def _jump_to_period_start():
    """Jump to global frame 0 (Home key handler).

    Period-bounded semantics (jumping to the start of the active period rather
    than frame 0) are not implemented; the jump targets the global frame 0.
    """
    _jump_frames(-state.viz_state["current_frame"])


def _jump_to_period_end():
    """Jump to the global maximum frame (End key handler).

    Period-bounded semantics (jumping to the end of the active period rather
    than the global max) are not implemented; the jump targets the global max.
    """
    _jump_frames(state.viz_state["max_frames"] - state.viz_state["current_frame"])


def _toggle_play_pause(sender=None, app_data=None):
    """Toggle between playing and paused states (DPG callback).

    Refuses to start playback when the tab is not yet initialized (no data
    loaded or no pitch attached) to avoid a misleading Playing state on an
    empty scene.

    Notes
    -----
    Side-effects: writes ``state.viz_state["playing"]``, configures the
    ``"viz_play_pause"`` DPG button label, sets ``"viz_status"`` text, calls
    ``_start_playback`` or ``_stop_playback``, and on pause triggers
    ``render_loop._render_current_frame``.
    """
    # Gate on initialization: no data loaded or no pitch attached leaves the tab
    # uninitialised (see render_loop._show_no_pitch_state). Refuse to play an
    # empty scene rather than flip the button to a misleading Playing state.
    if not state.viz_state.get("initialized"):
        dpg.set_value(
            "viz_status",
            "Load data and attach a pitch before playing.",
        )
        return
    state.viz_state["playing"] = not state.viz_state["playing"]

    if state.viz_state["playing"]:
        dpg.configure_item("viz_play_pause", label="||")
        dpg.set_value("viz_status", "Playing...")
        _start_playback()
    else:
        dpg.configure_item("viz_play_pause", label=">")
        dpg.set_value("viz_status", "Paused")
        _stop_playback()

        from floodlight_gui.tabs.visualization import render_loop as _rl

        _rl._render_current_frame()


# ---------------------------------------------------------------------------
# Playback: thread-safe architecture
#
# The background thread (_playback_worker) only writes to state._pending_frame.
# A DPG render-loop callback (_playback_tick) runs on the main thread,
# reads state._pending_frame, and performs all DPG and rendering calls.
# ---------------------------------------------------------------------------


def _start_playback():
    """Launch a background thread for frame-advance playback.

    Notes
    -----
    Side-effects: writes ``state.viz_state["last_slider_sync_frame"]``, calls
    ``PlaybackClock.start`` which stops any existing worker, clears the frame
    handoff, then spawns ``_playback_worker`` on a fresh daemon thread.
    """
    state.viz_state["last_slider_sync_frame"] = state.viz_state["current_frame"]
    _clock.start(_playback_worker)


def _stop_playback():
    """Signal the playback thread to stop.

    Notes
    -----
    Side-effects: writes ``state.viz_state["playing"] = False``, calls
    ``PlaybackClock.stop``.
    """
    state.viz_state["playing"] = False
    _clock.stop()


def _stop_playback_thread():
    """Signal and join the playback thread (thin wrapper over the clock)."""
    _clock.stop()


def _stop_playback_if_playing(reason=""):
    """Stop playback and update UI if currently playing.

    Parameters
    ----------
    reason : str, optional
        Short label appended to the status text, e.g. "Slider moved".

    Notes
    -----
    Side-effects: writes ``state.viz_state["playing"] = False``, calls
    ``PlaybackClock.stop``, configures ``"viz_play_pause"`` label and
    ``"viz_status"`` text when the items exist.
    """
    if state.viz_state["playing"]:
        state.viz_state["playing"] = False
        _clock.stop()
        try:
            dpg.configure_item("viz_play_pause", label=">")
            dpg.set_value("viz_status", f"Paused ({reason})" if reason else "Paused")
        except SystemError:  # DPG raises SystemError for missing items during teardown
            pass


def _playback_worker():
    """Advance ``state._pending_frame`` at the configured FPS (background thread).

    Runs on a daemon thread managed by ``PlaybackClock``. Never calls any DPG
    function; all DPG and rendering work happens in ``_playback_tick`` on the
    main thread.

    Scheduling uses absolute deadlines to eliminate drift. The naive approach
    (``stop_event.wait(timeout=1/play_speed)``) accumulates ~1-2 ms of GIL and
    dict overhead per cycle, causing the produced rate to drift below the target
    (e.g. target 25 fps, produced ~24 fps). Absolute-deadline scheduling anchors
    cycle N to ``start + N * period`` so per-cycle overhead shortens the next
    sleep rather than adding to the total wall-clock lag. When the target FPS
    changes mid-playback the deadline base resets so the new rate applies from
    the current moment rather than inheriting accumulated cycles at the old
    period.

    Notes
    -----
    Side-effects: calls ``PlaybackClock.post`` with the next frame number or
    ``_END_OF_PLAYBACK`` sentinel; reads ``state.viz_state["play_speed"]``,
    ``state.viz_state["current_frame"]``, and ``state.viz_state["max_frames"]``.
    """
    stop_event = _clock.stop_event

    cycle_start = time.perf_counter()
    cycles = 0
    last_play_speed = max(1, state.viz_state["play_speed"])

    while not stop_event.is_set():
        play_speed = max(1, state.viz_state["play_speed"])
        # Re-anchor the deadline base when the user changes the target FPS
        # mid-playback so the new rate applies from "now" rather than
        # inheriting accumulated cycles at the old period.
        if play_speed != last_play_speed:
            cycle_start = time.perf_counter()
            cycles = 0
            last_play_speed = play_speed

        target_period = 1.0 / play_speed
        cycles += 1
        next_deadline = cycle_start + cycles * target_period

        sleep_for = next_deadline - time.perf_counter()
        if sleep_for > 0 and stop_event.wait(timeout=sleep_for):
            break
        # Behind schedule (main thread held GIL too long, or target_period is
        # below OS sleep granularity): fire immediately and let subsequent cycles
        # catch up against the absolute deadline.

        if not state.viz_state["playing"]:
            break

        new_frame = state.viz_state["current_frame"] + 1
        if new_frame > state.viz_state["max_frames"]:
            # Signal end-of-playback for the main thread to pick up.
            _clock.post(_END_OF_PLAYBACK)
            break

        _clock.post(new_frame)


def _emit_fps_window_sample() -> bool:
    """Emit one FPS-window measurement and reset the counter.

    Operates on the module-level counters ``_fps_frame_count`` and
    ``_fps_start_time``. Called from ``_playback_tick`` whenever
    ``_fps_frame_count >= _FPS_SAMPLE_FRAMES``. The counter resets
    unconditionally at the end of the window (even on a discarded sample) so
    the next window starts fresh.

    A hitch-discard gate drops windows where ``elapsed > 2.5 * expected``
    because such windows are unrepresentative (e.g. app was backgrounded).
    Discarded windows skip the ``logger.debug`` call and the overlay write, but
    still reset the counter.

    Returns
    -------
    bool
        True when the window produced a valid sample (logger.debug fired and,
        if ``_FPS_OVERLAY_ENABLED``, the overlay text was updated).
        False when the window was discarded (elapsed <= 0 edge case, or
        hitch-discard gate fired).

    Notes
    -----
    Side-effects: resets ``_fps_frame_count`` and ``_fps_start_time``; when
    ``_FPS_OVERLAY_ENABLED`` is set, calls ``dpg.configure_item`` on the
    overlay draw-text tags and resets all ``_TICK_TIMING`` accumulators.
    """
    global _fps_frame_count, _fps_start_time

    elapsed = time.monotonic() - _fps_start_time if _fps_start_time > 0 else 0.0
    wrote = False
    if elapsed > 0:
        expected = _FPS_SAMPLE_FRAMES / max(state.viz_state.get("play_speed", 25), 1)
        if elapsed > 2.5 * expected:
            # Hitch-discard: window unrepresentative; drop the sample.
            pass
        else:
            fps = _fps_frame_count / elapsed
            logger.debug("Playback FPS: %.1f (last %d frames)", fps, _FPS_SAMPLE_FRAMES)
            if _FPS_OVERLAY_ENABLED:
                # draw_text items mutate via configure_item(tag, text=...).
                # set_value targets input widgets and silently no-ops on draw items.
                with contextlib.suppress(SystemError):
                    dpg.configure_item(
                        _FPS_OVERLAY_TEXT_TAG,
                        text=f"PB: {fps:.2f}fps | DPG: {dpg.get_frame_rate():.2f}fps",
                    )
                # Second overlay line: Voronoi cache-skip + upload-ms.
                # Poll the active Voronoi adapter for cumulative counters and
                # write the window delta. configure_item (not set_value) per
                # draw-text mutation contract above.
                global _last_voronoi_uploaded_count, _last_voronoi_skipped_count
                vor_adapter = (state.viz_state.get("active_adapters") or {}).get("voronoi")
                if vor_adapter is not None:
                    cur_up = vor_adapter.frames_uploaded
                    cur_sk = vor_adapter.frames_skipped
                    delta_sk = cur_sk - _last_voronoi_skipped_count
                    upload_ms = vor_adapter.last_upload_ms
                    _last_voronoi_uploaded_count = cur_up
                    _last_voronoi_skipped_count = cur_sk
                    with contextlib.suppress(SystemError):
                        dpg.configure_item(
                            _FPS_OVERLAY_VORONOI_TEXT_TAG,
                            text=f"VOR: {delta_sk} skipped, {upload_ms:.1f} ms upload",
                        )
                # Third overlay line: per-segment tick timing.
                # The overlay write is best-effort (the tag may be absent when
                # _init_fps_overlay ran before this code and the idempotent guard
                # short-circuited re-registration). The logger output is the
                # reliable channel; enable with FGUI_DEBUG_FPS=1. Per-frame
                # averages in ms (sum over window / _FPS_SAMPLE_FRAMES).
                n = max(_FPS_SAMPLE_FRAMES, 1)
                res_ms = _TICK_TIMING["resize"] * 1000.0 / n
                pull_ms = _TICK_TIMING["render_pull"] * 1000.0 / n
                plr_ms = _TICK_TIMING["render_players"] * 1000.0 / n
                ovl_ms = _TICK_TIMING["render_overlays"] * 1000.0 / n
                ui_ms = _TICK_TIMING["ui_sync"] * 1000.0 / n
                bus_ms = _TICK_TIMING["bus_emit"] * 1000.0 / n
                tot_ms = _TICK_TIMING["total"] * 1000.0 / n
                tick_text = (
                    f"TICK {tot_ms:.1f}ms: rs{res_ms:.1f} "
                    f"xy{pull_ms:.1f} plr{plr_ms:.1f} "
                    f"ovl{ovl_ms:.1f} ui{ui_ms:.1f} bus{bus_ms:.1f}"
                )
                logger.info(
                    "PHASE14_DIAG %s | PB=%.1f DPG=%.1f", tick_text, fps, dpg.get_frame_rate()
                )
                # On-demand tag creation: if _init_fps_overlay ran before
                # the tick-text tag was added, create the draw_text item now
                # so future windows update normally.
                with contextlib.suppress(SystemError):
                    if not dpg.does_item_exist(_FPS_OVERLAY_TICK_TEXT_TAG):
                        if dpg.does_item_exist(_FPS_OVERLAY_DRAWLIST_TAG):
                            dpg.draw_text(
                                pos=[950, 80],
                                text=tick_text,
                                color=[255, 255, 0, 255],
                                size=14,
                                parent=_FPS_OVERLAY_DRAWLIST_TAG,
                                tag=_FPS_OVERLAY_TICK_TEXT_TAG,
                            )
                    else:
                        dpg.configure_item(
                            _FPS_OVERLAY_TICK_TEXT_TAG,
                            text=tick_text,
                        )
                for k in _TICK_TIMING:
                    _TICK_TIMING[k] = 0.0
            wrote = True
    _fps_frame_count = 0
    _fps_start_time = time.monotonic()
    return wrote


def _init_fps_overlay() -> None:
    """Register the dev-mode FPS overlay drawlist (idempotent).

    No-op when ``FGUI_DEBUG_FPS`` is not set (``_FPS_OVERLAY_ENABLED`` is
    False). When enabled, creates a single ``add_viewport_drawlist(front=True)``
    hosting three draw_text items:

    - Primary line (y=40): playback FPS and DPG frame rate.
    - Voronoi line (y=60): cache-skip count and upload latency.
    - Tick timing line (y=80): per-segment breakdown from ``_TICK_TIMING``.

    Text is refreshed every ``_FPS_SAMPLE_FRAMES`` frames from
    ``_emit_fps_window_sample``. The overlay sits at x=950 (upper-right corner)
    to stay clear of the tab-bar labels that overlap the upper-left region.

    Notes
    -----
    Side-effects: adds DPG items tagged ``_FPS_OVERLAY_DRAWLIST_TAG``,
    ``_FPS_OVERLAY_TEXT_TAG``, ``_FPS_OVERLAY_VORONOI_TEXT_TAG``, and
    ``_FPS_OVERLAY_TICK_TEXT_TAG`` to the viewport drawlist.
    """
    if not _FPS_OVERLAY_ENABLED:
        return
    with contextlib.suppress(SystemError):
        if dpg.does_item_exist(_FPS_OVERLAY_DRAWLIST_TAG):
            return
        dpg.add_viewport_drawlist(tag=_FPS_OVERLAY_DRAWLIST_TAG, front=True)
        dpg.draw_text(
            pos=[950, 40],
            text="PB: --fps | DPG: --fps",
            color=[255, 255, 0, 255],
            size=14,
            parent=_FPS_OVERLAY_DRAWLIST_TAG,
            tag=_FPS_OVERLAY_TEXT_TAG,
        )
        # Voronoi line: y=60 sits 20 px below the primary line at y=40.
        dpg.draw_text(
            pos=[950, 60],
            text="VOR: -- skipped, --.- ms upload",
            color=[255, 255, 0, 255],
            size=14,
            parent=_FPS_OVERLAY_DRAWLIST_TAG,
            tag=_FPS_OVERLAY_VORONOI_TEXT_TAG,
        )
        # Tick timing line: y=80 sits 20 px below the Voronoi line.
        dpg.draw_text(
            pos=[950, 80],
            text="TICK --ms: rs-- xy-- plr-- ovl-- ui-- bus--",
            color=[255, 255, 0, 255],
            size=14,
            parent=_FPS_OVERLAY_DRAWLIST_TAG,
            tag=_FPS_OVERLAY_TICK_TEXT_TAG,
        )


def _playback_tick():
    """Apply a pending frame from the playback thread (DPG render-loop callback).

    Runs on the main thread. Register with ``dpg.set_frame_callback`` or call
    from the main loop.

    Each call: checks for a drawlist resize (must happen even when paused, see
    below), takes the pending frame from ``_clock``, renders it, throttles the
    control-panel sync, emits ``Events.FRAME_CHANGED``, and updates the FPS
    counter.

    The drawlist resize check runs before the early-return on ``frame is None``
    so the drawlist resizes even when playback is paused or not yet started.
    The viewport-resize callback covers OS-window resizes, but the first frame
    after viewport-show also needs this path.

    Notes
    -----
    Side-effects: calls ``render_loop._check_drawlist_resize`` and
    ``render_loop._render_current_frame``; writes
    ``state.viz_state["current_frame"]`` and
    ``state.viz_state["last_slider_sync_frame"]``; configures
    ``"viz_play_pause"`` label and ``"viz_status"`` text on end-of-playback;
    calls ``controls._update_frame_info``; emits ``Events.FRAME_CHANGED``;
    updates ``_TICK_TIMING`` accumulators when ``_FPS_OVERLAY_ENABLED``.
    """
    global _fps_frame_count, _fps_start_time

    timing_on = _FPS_OVERLAY_ENABLED
    t_tick_start = time.perf_counter() if timing_on else 0.0

    # Resize check before the pending-frame early-return: the drawlist must
    # resize even when playback is paused (see docstring above).
    # Lazy import avoids circular import at module scope (render_loop imports
    # controls which lazy-imports this module).
    from floodlight_gui.tabs.visualization import render_loop as _rl

    _rl._check_drawlist_resize()

    if timing_on:
        t_after_resize = time.perf_counter()
        _TICK_TIMING["resize"] += t_after_resize - t_tick_start

    frame = _clock.take_pending()

    if frame is None:
        if timing_on:
            _TICK_TIMING["total"] += time.perf_counter() - t_tick_start
        return

    if frame == _END_OF_PLAYBACK:
        # End of playback
        state.viz_state["playing"] = False
        dpg.configure_item("viz_play_pause", label=">")
        dpg.set_value("viz_status", "Playback completed")
        # Ensure UI catches up at playback end.
        from floodlight_gui.tabs.visualization import controls as _controls

        _controls._update_frame_info()
        if timing_on:
            _TICK_TIMING["total"] += time.perf_counter() - t_tick_start
        return

    state.viz_state["current_frame"] = frame
    from floodlight_gui.tabs.visualization import render_loop as _rl

    _rl._render_current_frame()

    if timing_on:
        t_after_render = time.perf_counter()

    # Throttle control-panel updates while playing to reduce UI overhead.
    from floodlight_gui.tabs.visualization import render_loop as _rl

    last_sync = state.viz_state.get("last_slider_sync_frame", -1)
    if (frame - last_sync) >= _rl._PLAYBACK_UI_SYNC_EVERY_N_FRAMES or frame >= state.viz_state[
        "max_frames"
    ]:
        with contextlib.suppress(SystemError):  # DPG raises SystemError for missing items
            dpg.set_value("viz_frame_slider", frame)
        from floodlight_gui.tabs.visualization import controls as _controls

        _controls._update_frame_info()
        state.viz_state["last_slider_sync_frame"] = frame

    if timing_on:
        t_after_ui = time.perf_counter()
        _TICK_TIMING["ui_sync"] += t_after_ui - t_after_render

    bus.emit(Events.FRAME_CHANGED, frame=frame)

    if timing_on:
        t_after_bus = time.perf_counter()
        _TICK_TIMING["bus_emit"] += t_after_bus - t_after_ui
        _TICK_TIMING["total"] += t_after_bus - t_tick_start

    # FPS instrumentation: window-boundary emission is in _emit_fps_window_sample
    # (hitch-discard gate + overlay write are unit-testable there in isolation).
    # REUSE the module-level _fps_frame_count / _fps_start_time; no parallel counter.
    _fps_frame_count += 1
    if _fps_frame_count >= _FPS_SAMPLE_FRAMES:
        _emit_fps_window_sample()
