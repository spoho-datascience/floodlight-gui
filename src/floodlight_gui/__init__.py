"""Public API surface and entry point for floodlight-gui.

Exposes the frozen public ``__all__`` (15 entries): 4 registries, 4 register_*
extension helpers, validate_all, player-mapping helpers, EventBus + Events,
and the ``run()`` entry point.

Every import at module scope is DPG-free. ``run()`` lazy-imports
dearpygui inside its body so that ``import floodlight_gui`` never pulls in the
GPU/window stack.

``__all__`` is the frozen public surface. Add or remove entries only with a
deliberate API-break decision.
"""

from __future__ import annotations

import contextlib
import logging
import os

# --------------------------------------------------------------------------- #
# Public API re-exports (frozen public surface -- 15 entries).
#
# All imports below are DPG-free: registry, core.event_bus,
# core.player_mapping, and core.xy_access never touch dearpygui at module scope.
# --------------------------------------------------------------------------- #
from floodlight_gui.core.event_bus import EventBus, Events
from floodlight_gui.core.player_mapping import PlayerSlot, build_player_slots
from floodlight_gui.core.xy_access import get_xy_for_period_team
from floodlight_gui.registry import validate_all
from floodlight_gui.registry.io import IO_REGISTRY, register_io_provider
from floodlight_gui.registry.metrics import METRICS_REGISTRY, register_metric
from floodlight_gui.registry.models import MODEL_REGISTRY, register_model
from floodlight_gui.registry.transforms import TRANSFORM_REGISTRY, register_transform

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure root logging format and level, silencing noisy third-party loggers."""
    level = logging.DEBUG if os.environ.get("FLOODLIGHT_GUI_DEBUG") else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def _create_data_loader(app):
    """Return a loader callable bound to *app* that dispatches to any registered provider.

    Parameters
    ----------
    app : FloodlightApp
        The live application instance; the loader is bound to it at startup and
        must not be called before ``app.initialize`` completes.

    Returns
    -------
    callable
        ``loader(provider_key, file_paths, **extra_params)`` -- returns the raw
        loaded-data tuple on success, or ``None`` when position data is absent.
    """
    from floodlight_gui.engine import load_data

    def loader(provider_key, file_paths, **extra_params):
        """Dispatch a load request to the named provider and validate position data."""
        logger.debug("Loading data from provider: %s", provider_key)
        loaded_data = load_data.load_provider_data(provider_key, file_paths, **extra_params)
        if loaded_data is None:
            logger.warning("%s data loading failed", provider_key)
            return None
        # pitch=None is not a load-failure signal: some adapters (e.g. Kinexon)
        # deliberately return pitch=None when the parser supplies no pitch.
        # The load tab surfaces a "Create Pitch" widget when DATA_LOADED arrives
        # with pitch=None. A successful load requires position_data (index 2)
        # to be non-None; pitch may legitimately be None.
        if loaded_data[2] is None:
            logger.warning("%s data loading failed (no position data)", provider_key)
            return None
        return loaded_data

    return loader


def _windows_high_resolution_timer():
    """Raise Windows multimedia timer resolution to 1 ms for playback timing.

    Windows' default system timer granularity is 15.625 ms.
    ``Event.wait`` and ``time.sleep`` snap to that grid, so the playback
    worker's per-frame sleep caps effective playback at roughly 32 fps
    regardless of the target rate. ``timeBeginPeriod(1)`` raises the
    granularity to 1 ms so the producer can hit the requested rate within
    normal OS scheduling jitter.

    The 1 ms period slightly increases system-wide CPU wake rates. The
    setting persists only for the lifetime of this process; ``timeEndPeriod``
    is called from the ``finally`` block in ``run()``.

    Returns
    -------
    int or None
        The period that was set (pass back to ``_windows_release_timer``),
        or ``None`` when the call failed or the platform is not Windows.
    """
    import platform

    if platform.system() != "Windows":
        return None
    try:
        import ctypes

        winmm = ctypes.WinDLL("winmm")
        # 1 ms is the minimum portable period; some drivers support 0.5 ms
        # via timeBeginPeriodEx, but 1 ms is the portable ceiling.
        period = 1
        if winmm.timeBeginPeriod(period) == 0:  # TIMERR_NOERROR
            logger.info("High-resolution timer enabled (%d ms period)", period)
            return period
        logger.warning("timeBeginPeriod(%d) failed; playback FPS may cap at ~32", period)
    except Exception as e:  # noqa: BLE001 -- must not crash app launch
        logger.warning("Could not enable high-resolution timer: %s", e)
    return None


def _windows_release_timer(period):
    """Release the Windows multimedia timer period set by ``_windows_high_resolution_timer``.

    Parameters
    ----------
    period : int or None
        The period value returned by ``_windows_high_resolution_timer``.
        A ``None`` value is a no-op.
    """
    if period is None:
        return
    try:
        import ctypes

        ctypes.WinDLL("winmm").timeEndPeriod(period)
    except Exception:  # noqa: BLE001 -- shutdown path; must not raise
        pass


def run() -> None:
    """Build and launch the floodlight-gui DPG application.

    Creates the DPG context, constructs ``FloodlightApp``, wires the data
    loader, opens the viewport, runs the render loop, and cleans up on exit.

    On Windows, raises the multimedia timer resolution to 1 ms before the
    render loop so the playback worker can meet its per-frame sleep target.
    The setting is released in the ``finally`` block.

    The ``FGUI_NO_VSYNC=1`` environment variable disables vsync on the DPG
    viewport. Vsync is on by default (render locked to display refresh).
    Disabling it allows profiling throughput beyond the display refresh ceiling
    at the cost of tearing and higher CPU/GPU usage.

    Notes
    -----
    All heavy imports (dearpygui, FloodlightApp, tab modules) are deferred to
    this function body so that ``import floodlight_gui`` stays DPG-free.
    """
    # Lazy imports keep `import floodlight_gui` DPG-free.
    import dearpygui.dearpygui as dpg

    from floodlight_gui.app import FloodlightApp

    try:
        from floodlight_gui.tabs.visualization import _playback_tick
    except ImportError:
        _playback_tick = None

    # Raise Windows timer resolution before the render loop so the playback
    # worker thread can hit its per-frame sleep within OS scheduling jitter.
    # Released in the finally block below. No-op on Linux/macOS.
    _hires_period = _windows_high_resolution_timer()

    _configure_logging()
    try:
        logger.info("Starting Floodlight GUI application...")
        dpg.create_context()
        app = FloodlightApp()
        loader = _create_data_loader(app)
        app.initialize(loader)
        _vsync = not bool(os.environ.get("FGUI_NO_VSYNC"))
        dpg.create_viewport(
            title="Floodlight Data Viewer",
            width=1200,
            height=800,
            vsync=_vsync,
        )
        if not _vsync:
            logger.info("Vsync disabled via FGUI_NO_VSYNC=1 — DPG render uncapped")
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("primary_window", True)
        logger.info("Starting DearPyGui main loop...")
        while dpg.is_dearpygui_running():
            if _playback_tick is not None:
                _playback_tick()
            dpg.render_dearpygui_frame()
    except Exception as e:  # noqa: BLE001 -- top-level safety net; must not crash silently
        logger.exception("Fatal error in run(): %s", e)
    finally:
        logger.info("Cleaning up DearPyGui context...")
        with contextlib.suppress(SystemError):
            dpg.destroy_context()
        _windows_release_timer(_hires_period)
        logger.info("Application ended.")


# --------------------------------------------------------------------------- #
# Public API surface (frozen -- 15 entries).
#
# 4 registries + 4 register_* helpers + validate_all +
# PlayerSlot + build_player_slots + get_xy_for_period_team + EventBus + Events
# + run.
#
# ``get_xy_for_period_team`` is sourced from ``core.xy_access``,
# not ``core.player_mapping`` (which owns only PlayerSlot and build_player_slots).
# --------------------------------------------------------------------------- #
__all__ = [
    # Registries (4)
    "IO_REGISTRY",
    "MODEL_REGISTRY",
    "TRANSFORM_REGISTRY",
    "METRICS_REGISTRY",
    # Registration helpers (4)
    "register_io_provider",
    "register_model",
    "register_transform",
    "register_metric",
    # Validation
    "validate_all",
    # Player mapping (3)
    "PlayerSlot",
    "build_player_slots",
    "get_xy_for_period_team",
    # Events (2)
    "EventBus",
    "Events",
    # Entry point
    "run",
]
