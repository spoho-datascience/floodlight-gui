"""Code-object timeline strip for the visualization tab (DPG-aware).

Owns the code-band drawing, cursor-only per-tick redraw, click/hover handlers,
and the ``_current_timeline_width`` width helper.

DPG carve-out: this module imports ``dearpygui`` at module scope because it
lives under ``tabs/`` (the DPG-aware layer); backend modules must not.

Facade call pattern: ``_on_frame_changed_timeline`` calls
``_redraw_frame_cursor`` directly rather than through any facade reference so
the frozen timeline-perf test mock can intercept it via ``patch.object``.

``_BAND_COLORS`` is imported from ``overlay_dispatch`` (single source of truth
for band colors across timeline drawing and overlay dispatch).

``_TIMELINE_WIDTH_INITIAL`` is the literal 900, matching
``_DRAWLIST_INITIAL_WIDTH = 900`` in ``controls.py``; inlined to avoid
importing a render-loop constant that lives in a sibling module.
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus  # noqa: F401 -- re-exported via __all__
from floodlight_gui.tabs.visualization import state
from floodlight_gui.tabs.visualization.overlay_dispatch import (
    _BAND_COLORS,  # noqa: F401 -- re-exported via __all__; single source of truth
    _color_for_token,
)

logger = logging.getLogger(__name__)

__all__ = [
    "_on_data_loaded_timeline",
    "_on_frame_changed_timeline",
    "_draw_code_timeline",
    "_redraw_frame_cursor",
    "_redraw_cursor_only_on_frame",  # alias -- test probe target
    "_on_timeline_mouse_click",
    "_on_timeline_mouse_move",
    "_timeline_click_to_frame",
    "_current_timeline_width",
]

# ---------------------------------------------------------------------------
# Timeline tag constants
# ---------------------------------------------------------------------------

_TIMELINE_TAG = "viz_code_timeline"
_TIMELINE_CONTAINER_TAG = "viz_timeline_container"
_TIMELINE_HOVER_TAG = "viz_timeline_hover_info"
_TIMELINE_CURSOR_TAG = (
    "viz_code_timeline_cursor"  # stable tag enables cursor-only in-place configure
)
_TIMELINE_ROW_HEIGHT = 22  # pixels per Code row (stacked rows)
_TIMELINE_WIDTH_INITIAL = 900  # construction-time default; matches _DRAWLIST_INITIAL_WIDTH


# ---------------------------------------------------------------------------
# Width helper
# ---------------------------------------------------------------------------


def _current_timeline_width() -> int:
    """Return the current timeline drawlist width in pixels.

    The pitch canvas resizes with the viewport; the code-object timeline strip
    tracks the same width via ``state.viz_state["timeline_width"]``. All
    band-drawing and hit-test math reads through this helper so resize and
    rendering stay coherent.

    Returns
    -------
    int
        Current timeline width, or ``_TIMELINE_WIDTH_INITIAL`` if the state
        key is absent or non-positive.
    """
    w = state.viz_state.get("timeline_width")
    if isinstance(w, int) and w > 0:
        return w
    return _TIMELINE_WIDTH_INITIAL


# ---------------------------------------------------------------------------
# Timeline drawing
# ---------------------------------------------------------------------------


def _draw_code_timeline():
    """Render Code object bands and the frame cursor onto the timeline drawlist.

    Reads ``state.viz_state["code_objects"]`` (list of ``(name, Code)``) and
    ``state.viz_state["current_frame"]``. Colors are token-deterministic via
    ``_color_for_token``; no inline labels are drawn inside bands. The click
    handler maps X to frame; this function only draws.

    Notes
    -----
    Side-effects: deletes all children of the ``_TIMELINE_TAG`` drawlist,
    redraws band rectangles and row labels, then calls ``_redraw_frame_cursor``
    to add the cursor on top.
    """
    if not dpg.does_item_exist(_TIMELINE_TAG):
        return
    dpg.delete_item(_TIMELINE_TAG, children_only=True)

    codes = state.viz_state.get("code_objects") or []
    if not codes:
        return
    total_frames = state.viz_state.get("timeline_total_frames") or 0
    if total_frames <= 0:
        return

    timeline_w = _current_timeline_width()
    for row_idx, (code_name, code_obj) in enumerate(codes):
        sequences = code_obj.find_sequences(return_type="list")
        for start, end, token in sequences:
            x0 = int(start / total_frames * timeline_w)
            x1 = int(end / total_frames * timeline_w)
            y0 = row_idx * _TIMELINE_ROW_HEIGHT + 1
            y1 = y0 + _TIMELINE_ROW_HEIGHT - 3
            color = _color_for_token(token, code_obj.token)
            dpg.draw_rectangle(
                (x0, y0),
                (x1, y1),
                color=color,
                fill=color,
                parent=_TIMELINE_TAG,
            )
        # Row label at left edge, not inside the band, to avoid overlap with colored segments.
        dpg.draw_text(
            (4, row_idx * _TIMELINE_ROW_HEIGHT + 4),
            code_name,
            size=11,
            color=(220, 220, 220, 255),
            parent=_TIMELINE_TAG,
        )

    _redraw_frame_cursor()


def _redraw_frame_cursor():
    """Update the vertical cursor line on the timeline drawlist.

    Cheap per-tick path: bands are drawn once on DATA_LOADED via
    ``_draw_code_timeline``; this helper updates only the cursor line tracking
    ``state.viz_state["current_frame"]``. Uses the stable ``_TIMELINE_CURSOR_TAG``
    so the cursor is configured in-place (1 DPG call) rather than rebuilt each tick.

    Notes
    -----
    Steady-state cost: one ``configure_item`` call when the cursor item already
    exists. First-tick cost after a band rebuild (``children_only=True``
    deletion removes the cursor): one ``draw_line`` call with the stable tag.
    """
    if not dpg.does_item_exist(_TIMELINE_TAG):
        return
    total = state.viz_state.get("timeline_total_frames") or 0
    cur = state.viz_state.get("current_frame") or 0
    if total <= 0:
        return
    x = int(cur / total * _current_timeline_width())
    n_rows = max(1, len(state.viz_state.get("code_objects") or []))
    height = n_rows * _TIMELINE_ROW_HEIGHT

    # Steady-state: cursor item exists, update position in-place.
    if dpg.does_item_exist(_TIMELINE_CURSOR_TAG):
        with contextlib.suppress(SystemError):
            dpg.configure_item(_TIMELINE_CURSOR_TAG, p1=(x, 0), p2=(x, height))
        return

    # First tick after bands were rebuilt: create with the stable tag.
    dpg.draw_line(
        (x, 0),
        (x, height),
        color=(255, 255, 255, 220),
        thickness=1.5,
        parent=_TIMELINE_TAG,
        tag=_TIMELINE_CURSOR_TAG,
    )


# Alias for test introspection: the Wave 0 timeline-perf test probes for this
# name. Same callable as _redraw_frame_cursor; two names for two audiences
# (executor call sites and test surface).
_redraw_cursor_only_on_frame = _redraw_frame_cursor


# ---------------------------------------------------------------------------
# EventBus handlers
# ---------------------------------------------------------------------------


def _on_data_loaded_timeline(**_kwargs):
    """Extract Code objects from loaded data and set up the timeline strip.

    Reads ``state.app_instance.loaded_data``, extracts possession and ball-status
    Code objects for the currently selected period, and stores them in
    ``state.viz_state["code_objects"]``. Hides the container when no Code objects
    are available; shows and resizes it otherwise.

    Notes
    -----
    Side-effects: writes ``state.viz_state["code_objects"]`` and
    ``state.viz_state["timeline_total_frames"]``; configures the
    ``_TIMELINE_CONTAINER_TAG`` and ``_TIMELINE_TAG`` DPG items; calls
    ``_draw_code_timeline``.
    """
    if state.app_instance is None or state.app_instance.loaded_data is None:
        with contextlib.suppress(SystemError):
            dpg.configure_item(_TIMELINE_CONTAINER_TAG, show=False)
        return

    loaded = state.app_instance.loaded_data
    # loaded_data shape: (pitch, event_data, position_data, teamsheets).
    # position_data for providers with Code objects: (xy_dict, possession, ballstatus).
    # Providers without Code objects (e.g. Kinexon): (xy_dict,) with length 1.
    if len(loaded) < 3 or not isinstance(loaded[2], tuple):
        state.viz_state["code_objects"] = []
        with contextlib.suppress(SystemError):
            dpg.configure_item(_TIMELINE_CONTAINER_TAG, show=False)
        return

    position_data = loaded[2]
    possession = position_data[1] if len(position_data) > 1 else None
    ballstatus = position_data[2] if len(position_data) > 2 else None

    # Resolve current period from state or default to first available.
    period = state.viz_state.get("selected_half")
    codes = []
    for name, codes_dict in (("possession", possession), ("ballstatus", ballstatus)):
        if not isinstance(codes_dict, dict):
            continue
        chosen_period = period if period in codes_dict else next(iter(codes_dict), None)
        if chosen_period is None:
            continue
        code_obj = codes_dict.get(chosen_period)
        if code_obj is not None:
            codes.append((name, code_obj))

    if not codes:
        state.viz_state["code_objects"] = []
        with contextlib.suppress(SystemError):
            dpg.configure_item(_TIMELINE_CONTAINER_TAG, show=False)
        return

    state.viz_state["code_objects"] = codes
    state.viz_state["timeline_total_frames"] = max(len(c) for _, c in codes)

    # Resize container to accommodate stacked rows plus hover-text space.
    new_height = len(codes) * _TIMELINE_ROW_HEIGHT + 30
    with contextlib.suppress(SystemError):
        dpg.configure_item(_TIMELINE_CONTAINER_TAG, height=new_height, show=True)
        dpg.configure_item(_TIMELINE_TAG, height=len(codes) * _TIMELINE_ROW_HEIGHT)

    _draw_code_timeline()


def _on_frame_changed_timeline(**_kwargs):
    """Cursor-only redraw on every FRAME_CHANGED tick.

    Bands are drawn once on DATA_LOADED via ``_on_data_loaded_timeline``.
    Per-tick cost is a single ``configure_item`` or ``draw_line`` call (one DPG
    item touched) rather than a full delete-children and per-sequence loop.
    """
    codes = state.viz_state.get("code_objects") or []
    if not codes:
        return
    _redraw_frame_cursor()


# ---------------------------------------------------------------------------
# Mouse / click handlers
# ---------------------------------------------------------------------------


def _timeline_click_to_frame(click_x):
    """Map a click X-coordinate (relative to timeline drawlist) to a frame index.

    Updates ``state.viz_state["current_frame"]``, syncs the ``viz_frame_slider``
    DPG widget, calls ``_controls._update_frame_info`` and
    ``_rl._render_current_frame``, and emits ``Events.FRAME_CHANGED`` to notify
    all FRAME_CHANGED subscribers (including the cursor redraw).

    Parameters
    ----------
    click_x : int or float
        X position in timeline-local pixels (0 at left edge of drawlist).

    Notes
    -----
    Side-effects: writes ``state.viz_state["current_frame"]``; sets
    ``viz_frame_slider`` DPG value; emits ``Events.FRAME_CHANGED``.
    """
    total = state.viz_state.get("timeline_total_frames") or 0
    timeline_w = _current_timeline_width()
    if total <= 0 or timeline_w <= 0:
        return
    frame = int(click_x / timeline_w * total)
    frame = max(0, min(frame, total - 1))
    state.viz_state["current_frame"] = frame
    # Sync the slider widget to the new frame before triggering the render.
    with contextlib.suppress(SystemError):
        dpg.set_value("viz_frame_slider", frame)
    from floodlight_gui.tabs.visualization import controls as _controls

    _controls._update_frame_info()
    from floodlight_gui.tabs.visualization import render_loop as _rl

    _rl._render_current_frame()
    # Emit after state and render are updated so subscribers see the new frame.
    bus.emit(Events.FRAME_CHANGED, frame=frame)


def _on_timeline_mouse_click(sender, app_data):
    """Mouse click handler scoped to the timeline drawlist (click-to-frame).

    Guards against clicks outside the drawlist bounding box. Stops playback
    before seeking so the playback loop does not race with the seek.
    """
    if not dpg.does_item_exist(_TIMELINE_TAG):
        return
    if not dpg.is_item_shown(_TIMELINE_TAG):
        return
    try:
        mouse_pos = dpg.get_mouse_pos(local=False)
    except SystemError:
        return
    try:
        rect_min = dpg.get_item_rect_min(_TIMELINE_TAG)
        rect_max = dpg.get_item_rect_max(_TIMELINE_TAG)
    except SystemError:
        return
    mx, my = mouse_pos
    if not (rect_min[0] <= mx <= rect_max[0] and rect_min[1] <= my <= rect_max[1]):
        return
    local_x = mx - rect_min[0]
    from floodlight_gui.tabs.visualization.playback import _stop_playback_if_playing

    _stop_playback_if_playing("Timeline click")
    _timeline_click_to_frame(local_x)


def _on_timeline_mouse_move(sender, app_data):
    """Update the hover-info text with the Code value and frame range under the cursor.

    Hides ``_TIMELINE_HOVER_TAG`` when the cursor leaves the drawlist or when
    no Code objects are loaded.
    """
    if not dpg.does_item_exist(_TIMELINE_TAG):
        return
    if not dpg.is_item_shown(_TIMELINE_TAG):
        return
    codes = state.viz_state.get("code_objects") or []
    total = state.viz_state.get("timeline_total_frames") or 0
    if not codes or total <= 0:
        with contextlib.suppress(SystemError):
            dpg.configure_item(_TIMELINE_HOVER_TAG, show=False)
        return
    try:
        mouse_pos = dpg.get_mouse_pos(local=False)
    except SystemError:
        return
    try:
        rect_min = dpg.get_item_rect_min(_TIMELINE_TAG)
        rect_max = dpg.get_item_rect_max(_TIMELINE_TAG)
    except SystemError:
        return
    mx, my = mouse_pos
    if not (rect_min[0] <= mx <= rect_max[0] and rect_min[1] <= my <= rect_max[1]):
        with contextlib.suppress(SystemError):
            dpg.configure_item(_TIMELINE_HOVER_TAG, show=False)
        return
    local_x = mx - rect_min[0]
    local_y = my - rect_min[1]
    row_idx = int(local_y / _TIMELINE_ROW_HEIGHT)
    if not (0 <= row_idx < len(codes)):
        with contextlib.suppress(SystemError):
            dpg.configure_item(_TIMELINE_HOVER_TAG, show=False)
        return
    code_name, code_obj = codes[row_idx]
    frame = int(local_x / _current_timeline_width() * total)
    frame = max(0, min(frame, len(code_obj) - 1))
    token = code_obj.code[frame]
    # Find the sequence that contains this frame to report its full range.
    seqs = code_obj.find_sequences(return_type="list")
    seq_start, seq_end = frame, frame
    for s, e, t in seqs:
        if s <= frame <= e and float(t) == float(token):
            seq_start, seq_end = s, e
            break
    framerate = code_obj.framerate or 1
    msg = (
        f"{code_name} = {token} (frames {seq_start}-{seq_end} | "
        f"t {seq_start / framerate:.2f}s-{seq_end / framerate:.2f}s)"
    )
    with contextlib.suppress(SystemError):
        dpg.configure_item(_TIMELINE_HOVER_TAG, default_value=msg, show=True)
