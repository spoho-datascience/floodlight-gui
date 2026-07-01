"""Per-frame DPG render loop for the Visualization tab.

Owns the drawlist lifecycle: layer initialization, teardown, resize detection,
per-frame rendering, data cache, team color config, and the focus-invalidation
handler. All shared mutable state lives in ``tabs.visualization.state``; this
module drives the render path that reads and updates it.

DPG-bearing: every function in this module may issue DPG draw calls.
BLE001 convention: DPG callbacks and render-loop boundaries wrap their body in
``try / except Exception: logger.exception(...)`` with a ``# noqa: BLE001``
marker so a per-frame error never crashes the process.
"""

from __future__ import annotations

import contextlib
import logging
import time

import dearpygui.dearpygui as dpg
import numpy as np

from floodlight_gui.core.event_bus import (  # noqa: F401 -- re-exported / used by subscriptions
    Events,
    bus,
)
from floodlight_gui.core.xy_access import get_xy_for_period_team
from floodlight_gui.rendering.coordinate_mapper import CoordinateMapper
from floodlight_gui.rendering.pitch_renderer import PitchRenderer, resolve_sport_from_pitch
from floodlight_gui.rendering.player_renderer import PlayerRenderer
from floodlight_gui.tabs.visualization import state

logger = logging.getLogger(__name__)

__all__ = [
    "initialize_visualization",
    "_on_viz_tab_focused",
    "_check_drawlist_resize",
    "_render_current_frame",
    "_refresh_data_cache",
    "_teardown_renderers",
    "_build_team_configs",
    "_init_drawlist_layers",
    "_on_xy_stack_changed",
    "_on_data_loaded",
    "_query_widget_height",
    "_compute_drawlist_size_from_viewport",
    "_CONTROL_PANEL_WIDTH",
    "_DRAWLIST_TAG",
    "_DRAWLIST_INITIAL_WIDTH",
    "_DRAWLIST_INITIAL_HEIGHT",
    "_DRAWLIST_CONTAINER_TAG",
    "_PLAYBACK_UI_SYNC_EVERY_N_FRAMES",
    "_PITCH_LAYER_TAG",
    "_PLAYER_LAYER_TAG",
    "_OVERLAY_LAYER_TAG",
    "_PANEL_PADDING_PX",
    "_TAB_BAR_HEIGHT_FALLBACK",
    "_STATUS_BAR_HEIGHT_FALLBACK",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DRAWLIST_TAG = "viz_drawlist"
# These are construction-time sizes only; the drawlist is resized
# every frame via _check_drawlist_resize.
_DRAWLIST_INITIAL_WIDTH = 900
_DRAWLIST_INITIAL_HEIGHT = 650
_DRAWLIST_CONTAINER_TAG = "viz_plot_container"  # parent container; queried for resize

# controls.py imports this constant and must not redefine it.
_CONTROL_PANEL_WIDTH = 350

_PLAYBACK_UI_SYNC_EVERY_N_FRAMES = 4

# Drawlist Z-order layer tags. Created once per data load by
# _init_drawlist_layers() (idempotent via does_item_exist guard).
# Creation order determines DPG draw order: pitch (back) -> player (middle)
# -> overlay (front), so overlays render above player circles.
# The double-underscore prefix follows the system-tag convention used by
# __pitch_img_{id}, __pitch_tex_{id}, __player_{team}_{idx}_{uid}, etc.
_PITCH_LAYER_TAG = "__pitch_layer"
_PLAYER_LAYER_TAG = "__player_layer"
_OVERLAY_LAYER_TAG = "__overlay_layer"

_PANEL_PADDING_PX = 20  # combined window + child_window padding allowance

# Fallback heights used before chrome widgets are realized on the first frame.
# _compute_drawlist_size_from_viewport replaces these with live rect_size queries
# once the widgets exist.
_TAB_BAR_HEIGHT_FALLBACK = 40
_STATUS_BAR_HEIGHT_FALLBACK = 30


# ---------------------------------------------------------------------------
# Focus handler
# ---------------------------------------------------------------------------


def _on_viz_tab_focused() -> None:
    """Invalidate the drawlist-size cache when the Visualization tab regains focus.

    DPG may report a stale or zero container size while a tab is hidden,
    leaving the player-radius cache (``mapper.scale_distance``) at a too-small
    value. Zeroing ``_last_drawlist_size`` here forces the next
    ``_playback_tick`` call to run the full resize path via
    ``_check_drawlist_resize(force=True)``, rescaling player radii correctly.

    This function performs only pure-Python state mutations (no DPG calls) so
    it is safe to call from unit tests that do not have a live DPG context.

    Notes
    -----
    Called by ``keyboard.on_main_tab_changed`` when the viz tab becomes active.
    """
    # Zero the cache so the next _check_drawlist_resize sees a mismatch and
    # runs the full recompute even when the viewport size is unchanged.
    state._last_drawlist_size[0] = 0
    state._last_drawlist_size[1] = 0


# ---------------------------------------------------------------------------
# Drawlist layer init
# ---------------------------------------------------------------------------


def _init_drawlist_layers(drawlist_tag: str = _DRAWLIST_TAG) -> None:
    """Create the three named draw-layer items in fixed Z-order inside *drawlist_tag*.

    Layers are added in the order pitch -> player -> overlay so DPG renders
    them bottom-up. Renderer constructors pass ``parent=<layer_tag>`` on their
    ``draw_*`` calls, anchoring each renderer's items in the correct layer.

    The call is idempotent: ``does_item_exist`` guards each layer so repeated
    calls on DATA_LOADED re-fires are safe.

    This MUST be the first DPG-affecting call in ``initialize_visualization``,
    before any renderer constructor: renderer ``__init__`` methods issue
    ``draw_*(parent=<layer_tag>)``, which raises ``SystemError: Parent item not
    found`` if the layer does not exist yet.

    Parameters
    ----------
    drawlist_tag : str
        DPG tag of the drawlist to add layers to.
    """
    for tag in (_PITCH_LAYER_TAG, _PLAYER_LAYER_TAG, _OVERLAY_LAYER_TAG):
        if not dpg.does_item_exist(tag):
            dpg.add_draw_layer(tag=tag, parent=drawlist_tag)


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def initialize_visualization(*_args, **_kwargs):
    """Initialize (or re-initialize) the drawlist visualization.

    Called on every ``DATA_LOADED`` EventBus event and by the manual
    "Initialize Visualization" button. Tears down existing renderers, rebuilds
    all three draw layers, constructs a ``CoordinateMapper``, ``PitchRenderer``,
    and ``PlayerRenderer``, then renders the first frame.

    If no data is loaded or no ``Pitch`` is attached, the function shows an
    appropriate placeholder and returns without initializing, leaving
    ``state.viz_state["initialized"]`` as ``False`` so playback stays gated.

    Notes
    -----
    Side-effects: writes ``state.viz_state["mapper"]``,
    ``state.viz_state["pitch_renderer"]``, ``state.viz_state["player_renderer"]``,
    ``state.viz_state["active_adapters"]``, ``state.viz_state["initialized"]``,
    ``state.viz_state["original_fps"]``, and ``state.viz_state["play_speed"]``.
    Viewport-resize tracking is handled by the per-frame
    ``_check_drawlist_resize()`` call inside ``_playback_tick``; DPG only
    supports one global viewport-resize callback and ``app.py`` owns that slot.
    """
    if not state.app_instance or not state.app_instance.loaded_data:
        dpg.set_value("viz_status", "Error: No data loaded")
        return

    # A Pitch is required to render a meaningful scene. Show the placeholder
    # and leave the tab uninitialized; attaching a pitch re-fires DATA_LOADED,
    # which re-enters here and renders normally.
    if state.app_instance.pitch is None:
        _show_no_pitch_state()
        return

    try:
        dpg.set_value("viz_status", "Initializing visualization...")

        # Stop any running playback
        from floodlight_gui.tabs.visualization import playback as _playback

        _playback._stop_playback()

        # Lazy import: controls imports render_loop, so use in-function import
        # to avoid a module-scope circular dependency.
        from floodlight_gui.tabs.visualization import controls as _controls

        _controls._update_controls_from_data()

        original_fps = int(state.app_instance.get_fps())
        # Slider capped at 60: above 60 is unreachable due to DPG render-loop
        # and compositor caps. The true value is recorded in "original_fps" so
        # speed-multiplier buttons compute against it; the slider clamps display.
        dpg.configure_item("viz_play_speed", default_value=min(60, original_fps), max_value=60)
        dpg.set_value("viz_play_speed", min(60, original_fps))
        state.viz_state["play_speed"] = min(60, original_fps)
        state.viz_state["original_fps"] = original_fps
        _controls._update_fps_info()

        if dpg.does_item_exist("viz_plot_placeholder"):
            dpg.configure_item("viz_plot_placeholder", show=False)
        dpg.configure_item(_DRAWLIST_TAG, show=True)

        _teardown_renderers()

        # Layer init MUST run before renderer construction: renderer __init__
        # methods issue draw_*(parent=<layer_tag>), which raises SystemError if
        # the layer does not exist yet. _init_drawlist_layers is idempotent.
        _init_drawlist_layers(_DRAWLIST_TAG)

        # Pitch is guaranteed non-None here (guarded above).
        pitch = state.app_instance.pitch

        # 4 px padding: a hairline margin so the pitch never clips the drawlist
        # border. Resized to actual viewport dimensions by _check_drawlist_resize.
        mapper = CoordinateMapper(
            pitch, _DRAWLIST_INITIAL_WIDTH, _DRAWLIST_INITIAL_HEIGHT, padding=4
        )
        state.viz_state["mapper"] = mapper

        # Sport is derived from the loaded Pitch object so handball data renders
        # the correct pitch image.
        pitch_renderer = PitchRenderer(
            _DRAWLIST_TAG,
            mapper,
            sport=resolve_sport_from_pitch(pitch),
            parent_layer_tag=_PITCH_LAYER_TAG,
        )
        pitch_renderer.draw()
        state.viz_state["pitch_renderer"] = pitch_renderer

        # Overlay adapters are created lazily by _on_model_fitted; reset the
        # dict here so stale adapters from a previous data load don't persist.
        state.viz_state["active_adapters"] = {}

        team_configs = _build_team_configs()
        player_renderer = PlayerRenderer(
            _DRAWLIST_TAG,
            mapper,
            team_configs,
            parent_layer_tag=_PLAYER_LAYER_TAG,
        )
        state.viz_state["player_renderer"] = player_renderer

        _refresh_data_cache()
        _controls._update_frame_range()

        state.viz_state["initialized"] = True
        _render_current_frame()

        dpg.set_value(
            "viz_status",
            f"Ready. Frames: 0-{state.viz_state['max_frames']}, FPS: {original_fps}",
        )

    except Exception as e:  # noqa: BLE001 -- init callback boundary; DPG loop must not crash
        logger.exception("Error initializing visualization: %s", e)
        dpg.set_value("viz_status", f"Error initializing: {e}")


def _show_no_pitch_state():
    """Show the 'no pitch attached' placeholder and leave the tab uninitialized.

    Called when ``DATA_LOADED`` carries no ``Pitch`` (e.g. Kinexon data). Stops
    playback, tears down any prior renderers, hides the drawlist, and prompts the
    user to attach a pitch. Leaves ``state.viz_state["initialized"]`` as ``False``
    so playback stays gated until a pitch is attached and ``DATA_LOADED`` re-fires.
    """
    from floodlight_gui.tabs.visualization import playback as _playback

    with contextlib.suppress(Exception):
        _playback._stop_playback()
    _teardown_renderers()
    state.viz_state["initialized"] = False
    if dpg.does_item_exist("viz_plot_placeholder"):
        with contextlib.suppress(SystemError):
            dpg.set_value(
                "viz_plot_placeholder",
                "This data has no pitch, so there is nothing to visualise yet.\n\n"
                'Use the Load tab\'s "Build / Replace Pitch" panel to attach one — '
                "the visualization will then render automatically.",
            )
            dpg.configure_item("viz_plot_placeholder", show=True)
    if dpg.does_item_exist(_DRAWLIST_TAG):
        with contextlib.suppress(SystemError):
            dpg.configure_item(_DRAWLIST_TAG, show=False)
    with contextlib.suppress(SystemError):
        dpg.set_value(
            "viz_status",
            "No pitch attached — create one in the Load tab to visualise this data.",
        )


def _teardown_renderers():
    """Clean up existing pitch, player renderers, and any active overlay adapters."""
    for key in ("pitch_renderer", "player_renderer"):
        renderer = state.viz_state[key]
        if renderer is not None:
            try:
                renderer.clear()
            except SystemError as e:
                logger.warning("Error clearing %s: %s", key, e)
            state.viz_state[key] = None

    # Tear down every active overlay adapter.
    for adapter_key, adapter in list(state.viz_state.get("active_adapters", {}).items()):
        try:
            adapter.clear()
        except SystemError as e:
            logger.warning("Error clearing %s adapter: %s", adapter_key, e)
    state.viz_state["active_adapters"] = {}

    state.viz_state["mapper"] = None
    state.viz_state["initialized"] = False
    state.viz_state["last_hover"] = None


def _refresh_data_cache():
    """Cache data references that don't change between frames.

    Called at init and on DATA_LOADED / period change.
    """
    if not state.app_instance or not state.app_instance.loaded_data:
        state.viz_state["cached_pos_data"] = None
        state.viz_state["cached_team_names"] = None
        state.viz_state["cached_is_single"] = False
        return

    state.viz_state["cached_pos_data"] = state.app_instance.get_position_data_structure()
    state.viz_state["cached_team_names"] = state.app_instance.get_team_names()
    state.viz_state["cached_is_single"] = state.app_instance.is_single_period()


def _build_team_configs():
    """Build ``team_configs`` dict expected by PlayerRenderer from app data."""
    from floodlight_gui.tabs.visualization import colors as _colors

    team_names = state.app_instance.get_team_names()
    temporal_divisions = state.app_instance.get_temporal_divisions()

    configs = {}
    color_idx = 0

    for team in team_names:
        is_ball = _colors.is_ball_team(team)

        n_players = 1
        # Try each period until we find XY for this team (for player count)
        xy_obj = None
        for period in temporal_divisions:
            xy_obj = get_xy_for_period_team(state.app_instance, period, team)
            if xy_obj is not None:
                break
        if xy_obj is not None and hasattr(xy_obj, "xy"):
            n_players = xy_obj.xy.shape[1] // 2

        # Only cycle-color fallbacks advance color_idx; named/overridden teams
        # must not consume a cycle slot so the assignment order stays stable.
        color, used_cycle = _colors.team_color_for(team, color_idx)
        if used_cycle:
            color_idx += 1

        configs[team] = {
            "color": color,
            "n_players": n_players,
            "is_ball": is_ball,
        }

    return configs


# ---------------------------------------------------------------------------
# Frame rendering
# ---------------------------------------------------------------------------


def _render_current_frame():
    """Push current-frame XY data to the PlayerRenderer.

    Uses :func:`get_xy_for_period_team` so spatial-ops transforms are
    honoured.  The accessor is a cheap dict lookup; safe for 60 FPS.
    """
    if not state.viz_state["initialized"] or state.viz_state["player_renderer"] is None:
        return

    team_names = state.viz_state["cached_team_names"]
    if team_names is None:
        return

    # Per-segment timing accumulators, active only when FGUI_DEBUG_FPS=1.
    # perf_counter is always sampled (~50 ns/call) to keep the branch cheap.
    from floodlight_gui.tabs.visualization import playback as _pb

    timing_on = _pb._FPS_OVERLAY_ENABLED
    t_pull_start = time.perf_counter() if timing_on else 0.0

    try:
        half = state.viz_state["selected_half"]
        frame = state.viz_state["current_frame"]

        team_xy = {}
        ball_xy = None

        from floodlight_gui.tabs.visualization.overlay_dispatch import _is_ball_team

        for team in team_names:
            if not state.viz_state["selected_teams"].get(team, True):
                continue

            xy_obj = get_xy_for_period_team(state.app_instance, half, team)

            if xy_obj is None or not hasattr(xy_obj, "xy"):
                continue

            if frame >= xy_obj.xy.shape[0]:
                continue

            frame_data = xy_obj.xy[frame]

            if _is_ball_team(team) and len(frame_data) >= 2:
                bx, by = float(frame_data[0]), float(frame_data[1])
                if not (np.isnan(bx) or np.isnan(by)):
                    ball_xy = (bx, by)
            else:
                team_xy[team] = frame_data

        if timing_on:
            t_players_start = time.perf_counter()
            _pb._TICK_TIMING["render_pull"] += t_players_start - t_pull_start

        state.viz_state["player_renderer"].update_positions(team_xy, ball_xy=ball_xy)

        if timing_on:
            t_overlays_start = time.perf_counter()
            _pb._TICK_TIMING["render_players"] += t_overlays_start - t_players_start

        for adapter in state.viz_state.get("active_adapters", {}).values():
            try:
                adapter.update_frame(frame)
            except Exception:  # noqa: BLE001 -- render loop safety
                logger.exception("Adapter update_frame failed; skipping")

        if timing_on:
            _pb._TICK_TIMING["render_overlays"] += time.perf_counter() - t_overlays_start

    except Exception:  # noqa: BLE001 -- render loop must never crash; exception logged for triage
        logger.exception("Error in _render_current_frame")


# ---------------------------------------------------------------------------
# Resize helpers
# ---------------------------------------------------------------------------


def _query_widget_height(tag: str, fallback: int) -> int:
    """Return the rendered height of *tag*, or *fallback* when the query fails.

    Tab-bar and status-bar heights are derived from live widgets so layout math
    does not rely on hardcoded pixel offsets. Not all DPG widget types expose
    ``rect_size`` (``tab_bar`` raises ``KeyError`` under DPG 2.x), so any
    failure returns the caller-supplied fallback.

    Parameters
    ----------
    tag : str
        DPG item tag to query.
    fallback : int
        Value to return when the item is absent or the query raises.

    Returns
    -------
    int
        Rendered height in pixels, or *fallback*.
    """
    try:
        if not dpg.does_item_exist(tag):
            return fallback
        _, h = dpg.get_item_rect_size(tag)
        return int(h) if h > 0 else fallback
    except (SystemError, KeyError, TypeError, ValueError):
        return fallback


def _compute_drawlist_size_from_viewport() -> tuple[int, int] | None:
    """Compute (drawlist_w, drawlist_h) from the OS viewport dimensions.

    ``dpg.group(horizontal=True)`` wrapping the left sidebar and right drawlist
    container does not propagate ``width=-1`` / ``height=-1`` reliably to its
    child windows in DPG 2.x (the group sizes to its children's content, so a
    child requesting ``-1`` hits a circular reference). Querying the viewport
    directly via ``dpg.get_viewport_client_width/height()`` sidesteps DPG
    auto-layout and produces deterministic letterbox resize.

    Chrome reservations (tab bar, status bar) are queried at runtime via
    ``_query_widget_height`` so changes to those widgets do not silently break
    the layout math.

    Returns
    -------
    tuple[int, int] or None
        ``(drawlist_w, drawlist_h)`` in pixels, or ``None`` if the viewport is
        not yet visible (initial-frame race condition).
    """
    try:
        vp_w = dpg.get_viewport_client_width()
        vp_h = dpg.get_viewport_client_height()
    except SystemError:
        return None
    if vp_w <= 0 or vp_h <= 0:
        return None
    # "main_tab_bar" is the global tag set in app.create_ui.
    # "statusbar_cell_data" is the leftmost status-bar text cell; it is used as
    # a proxy for the full row height because the row is a dpg.group with no tag.
    tab_bar_h = _query_widget_height("main_tab_bar", _TAB_BAR_HEIGHT_FALLBACK)
    status_h = _query_widget_height("statusbar_cell_data", _STATUS_BAR_HEIGHT_FALLBACK)
    # Right pane = viewport minus sidebar minus combined paddings.
    container_w = max(1, vp_w - _CONTROL_PANEL_WIDTH - _PANEL_PADDING_PX)
    container_h = max(1, vp_h - tab_bar_h - status_h - _PANEL_PADDING_PX)
    # Reserve space for the timeline strip and its caption header.
    from floodlight_gui.tabs.visualization.timeline import _TIMELINE_ROW_HEIGHT

    drawlist_w = max(1, container_w - 20)
    drawlist_h = max(1, container_h - _TIMELINE_ROW_HEIGHT - 60)
    return drawlist_w, drawlist_h


def _check_drawlist_resize(*, force: bool = False) -> None:
    """Detect viewport resize and update the mapper, container, and drawlist.

    Called from two sites: the top of ``_playback_tick`` on every frame
    (catches DPG shrink events that do not fire the resize callback) and the
    viewport-resize callback registered via ``dpg.set_viewport_resize_callback``.
    Both paths short-circuit if the container does not exist or sizes are
    unchanged (unless *force* is True).

    Target size comes from ``dpg.get_viewport_client_width/height()`` rather
    than ``dpg.get_item_rect_size(_DRAWLIST_CONTAINER_TAG)``; the container
    sits inside a ``dpg.group(horizontal=True)`` that does not propagate
    ``width=-1`` reliably, so querying the viewport directly is deterministic.

    After a size change:

    1. ``_DRAWLIST_CONTAINER_TAG`` is configured to the right-pane size so DPG
       auto-layout does not pin it at construction-time dimensions.
    2. ``mapper.update(drawlist_width=w, drawlist_height=h)`` recomputes the
       letterbox math (``CoordinateMapper._recalculate``).
    3. ``dpg.configure_item(_DRAWLIST_TAG, width=w, height=h)`` resizes the
       drawlist item so DPG renders into the new bounds.

    Parameters
    ----------
    force : bool, optional
        When True, bypasses the ``_last_drawlist_size`` short-circuit so a
        recompute runs even when the viewport size appears unchanged. Used by
        ``_on_viz_tab_focused`` to rescale player radii after a tab-switch.
    """
    if not dpg.does_item_exist(_DRAWLIST_CONTAINER_TAG):
        return
    sizes = _compute_drawlist_size_from_viewport()
    if sizes is None:
        return
    drawlist_w, drawlist_h = sizes
    if (
        not force
        and state._first_resize_done[0]
        and (drawlist_w, drawlist_h) == (state._last_drawlist_size[0], state._last_drawlist_size[1])
    ):
        return
    state._last_drawlist_size[0], state._last_drawlist_size[1] = drawlist_w, drawlist_h
    state._first_resize_done[0] = True

    # primary_window resize is handled by
    # FloodlightApp._register_primary_window_resize_anchor (app.py); this
    # handler stays focused on the viz-specific container, drawlist, and mapper.
    #
    # Resize the container and inner drawlist unconditionally (do not gate on
    # mapper) so the canvas tracks the window from the first frame, before
    # "Initialize Visualization" has been called.
    from floodlight_gui.tabs.visualization.timeline import _TIMELINE_ROW_HEIGHT

    with contextlib.suppress(SystemError):
        dpg.configure_item(
            _DRAWLIST_CONTAINER_TAG,
            width=drawlist_w + 20,
            height=drawlist_h + _TIMELINE_ROW_HEIGHT + 60,
        )
    with contextlib.suppress(SystemError):
        dpg.configure_item(_DRAWLIST_TAG, width=drawlist_w, height=drawlist_h)

    mapper = state.viz_state.get("mapper")
    if mapper is None:
        return
    try:
        mapper.update(drawlist_width=drawlist_w, drawlist_height=drawlist_h)
    except Exception as exc:  # noqa: BLE001 -- render-loop boundary; must not crash
        logger.exception("Resize: mapper.update failed: %s", exc)
        return

    _reanchor_timeline(mapper)
    if state.viz_state.get("initialized"):
        _resize_redraw(mapper)


def _reanchor_timeline(mapper) -> None:
    """Reposition the Code-bar timeline strip to match the visible pitch (resize path).

    The strip tracks the pitch's left edge and visible width so bands line up
    directly under their pitch coordinates. Bands are redrawn only when the
    pixel width actually changed.
    """
    from floodlight_gui.tabs.visualization.timeline import (
        _TIMELINE_CONTAINER_TAG,
        _TIMELINE_TAG,
        _TIMELINE_WIDTH_INITIAL,
        _draw_code_timeline,
    )

    # Capture the prior width before overwriting, for the band-redraw gate.
    prev_timeline_w = state.viz_state.get("timeline_width") or _TIMELINE_WIDTH_INITIAL
    pitch_left_x = int(mapper.pitch_origin_px[0])
    pitch_right_x = int(mapper.pitch_end_px[0])
    pitch_visible_width = max(1, pitch_right_x - pitch_left_x)
    pitch_render_bottom = int(mapper.pitch_pixel_bottom)
    state.viz_state["timeline_width"] = pitch_visible_width
    with contextlib.suppress(SystemError):
        if dpg.does_item_exist(_TIMELINE_CONTAINER_TAG):
            dpg.configure_item(
                _TIMELINE_CONTAINER_TAG,
                width=pitch_visible_width + 4,
                pos=[pitch_left_x, pitch_render_bottom + 4],
            )
    with contextlib.suppress(SystemError):
        if dpg.does_item_exist(_TIMELINE_TAG):
            dpg.configure_item(_TIMELINE_TAG, width=pitch_visible_width)
    if prev_timeline_w != pitch_visible_width and (state.viz_state.get("code_objects") or []):
        with contextlib.suppress(Exception):  # render-loop boundary
            _draw_code_timeline()


def _resize_redraw(mapper) -> None:
    """Redraw pitch, rescale players and adapters, then render (resize path).

    Runs only when the tab is initialized. ``pitch_renderer.update_position``
    mutates the existing ``draw_image`` via ``configure_item`` to preserve
    Z-order; it falls back to ``update_mapper`` if the image item is missing.
    Player radii and mapper-derived adapters are rescaled via ``configure_item``
    only (no ``delete_item``). The final ``_render_current_frame`` push delivers
    the redrawn frame.
    """
    pitch_renderer = state.viz_state.get("pitch_renderer")
    if pitch_renderer is not None:
        try:
            if not pitch_renderer.update_position(mapper):
                pitch_renderer.update_mapper(mapper)
        except Exception as exc:  # noqa: BLE001 -- render-loop boundary
            logger.exception("Resize: pitch_renderer redraw failed: %s", exc)
    player_renderer = state.viz_state.get("player_renderer")
    if player_renderer is not None:
        try:
            player_renderer.update_mapper(mapper)
        except Exception as exc:  # noqa: BLE001 -- render-loop boundary
            logger.exception("Resize: player_renderer.update_mapper failed: %s", exc)
    # Only adapters that implement update_mapper participate; those that do not
    # (e.g. HullAdapter, which re-maps polygons per-frame via update_frame) skip.
    for adapter in state.viz_state.get("active_adapters", {}).values():
        if hasattr(adapter, "update_mapper"):
            try:
                adapter.update_mapper(mapper)
            except Exception as exc:  # noqa: BLE001 -- render-loop boundary
                logger.exception("Resize: adapter.update_mapper failed: %s", exc)
    try:
        _render_current_frame()
    except Exception as exc:  # noqa: BLE001 -- render-loop boundary
        logger.exception("Resize: _render_current_frame failed: %s", exc)


# ---------------------------------------------------------------------------
# EventBus handlers
# ---------------------------------------------------------------------------


def _on_xy_stack_changed(**_kwargs):
    """Re-initialize visualization when a transform op fires ``XY_STACK_CHANGED``.

    Subscribed so that applying a slice, filter, or spatial op updates the live
    playback XY data without requiring the user to manually reselect the period
    combo. Gated on ``state.viz_state["initialized"]`` so a cold-start (no data
    loaded) is a no-op.

    The event payload carries only ``app=app`` (no period/team info), so
    ``initialize_visualization()`` is called unconditionally, which re-reads
    ``app.get_active_xy(period, team)`` for the currently selected slice.
    Playback frame position resets to 0; this is acceptable because the
    underlying XY shape may have changed (e.g. a slice produces fewer frames).
    """
    if not state.viz_state.get("initialized"):
        return
    from floodlight_gui.tabs.visualization import playback as _playback

    _playback._stop_playback_if_playing("Transform applied")
    initialize_visualization()


def _on_data_loaded(**_data):
    """Auto-initialize the viz tab on every ``DATA_LOADED`` event.

    Initializing on every load (not just re-loads) ensures that ``mapper`` is
    set before any ``MODEL_FITTED`` event tries to bind an overlay. The explicit
    "Initialize Visualization" button still works as a manual reset.

    Notes
    -----
    Side-effects: resets Voronoi playback counters in ``playback``, zeros the
    drawlist-size cache so the next ``_playback_tick`` runs a full resize against
    the new pitch dimensions, hides overlay checkboxes, calls
    ``initialize_visualization()``, forces a resize recompute so the first
    post-init render uses actual viewport dimensions rather than the
    construction-time constants, and repopulates the "Player label" combo.
    """
    # Reset per-window Voronoi delta counters so a fresh data load starts from
    # zero. The VoronoiAdapter's own counters are reset in adapter.init/clear;
    # these are the viz-tab-side mirrors used for delta computation.
    from floodlight_gui.tabs.visualization import playback as _pb

    _pb._last_voronoi_uploaded_count = 0
    _pb._last_voronoi_skipped_count = 0

    # Zero the drawlist-size cache so the next _playback_tick runs a full
    # resize against the new pitch dimensions. A cross-dataset load can leave
    # the player-radius cache pointing at the previous pitch's scale_distance;
    # zeroing here prevents _last_drawlist_size from short-circuiting the fix.
    state._last_drawlist_size[0] = 0
    state._last_drawlist_size[1] = 0

    # Reset overlay checkboxes to their "no models fitted" state.
    for tag in ("viz_overlay_hull", "viz_overlay_voronoi"):
        if dpg.does_item_exist(tag):
            with contextlib.suppress(SystemError):
                dpg.set_value(tag, False)
                dpg.configure_item(tag, show=False)
    for tag in (
        "viz_voronoi_alpha_spacer",
        "viz_voronoi_alpha_label",
        "viz_voronoi_alpha",
    ):
        if dpg.does_item_exist(tag):
            with contextlib.suppress(SystemError):
                dpg.configure_item(tag, show=False)
    if dpg.does_item_exist("viz_overlay_placeholder"):
        with contextlib.suppress(SystemError):
            dpg.configure_item("viz_overlay_placeholder", show=True)

    with contextlib.suppress(SystemError):  # DPG raises SystemError if UI items not yet created
        initialize_visualization()

    # Force a resize recompute immediately after init so the new mapper and
    # player_renderer use the actual current drawlist dimensions rather than the
    # _DRAWLIST_INITIAL_WIDTH/HEIGHT constants. Without this, a paused playback
    # shows the stale construction-time radius until the next _playback_tick.
    with contextlib.suppress(Exception):  # defensive -- render-loop boundary
        _check_drawlist_resize(force=True)

    # Repopulate the "Player label" combo from whichever PlayerSlot fields the
    # new teamsheets actually populate. initialize_visualization() must run first
    # so the renderer exists before this wiring fires.
    from floodlight_gui.tabs.visualization import controls as _controls  # noqa: F811

    with contextlib.suppress(Exception):  # defensive -- combo refresh is non-critical
        _controls._populate_label_field_combo()
