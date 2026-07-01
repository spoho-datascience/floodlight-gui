"""DPG-aware application shell: owns the DataStore and EventBus, builds the UI.

FloodlightApp is the sole EventBus emitter for all app-wide events.
DataStore never emits. All stores writes go through explicit methods on this
class, which then call bus.emit(..., app=self).
"""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.data_store import DataStore
from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.tabs.inspect import create_inspect_tab
from floodlight_gui.tabs.load import create_load_tab
from floodlight_gui.tabs.metrics import create_metrics_tab
from floodlight_gui.tabs.model import create_model_tab
from floodlight_gui.tabs.transforms import create_transforms_tab

# Visualization tab depends on DPG drawlist and may be absent in minimal installs.
_viz_import_error: Exception | None = None
try:
    from floodlight_gui.tabs.visualization import create_visualization_tab

    VISUALIZATION_TAB_AVAILABLE = True
except ImportError as e:
    VISUALIZATION_TAB_AVAILABLE = False
    _viz_import_error = e

logger = logging.getLogger(__name__)

if _viz_import_error is not None:
    logger.warning("Could not import visualization tab: %s", _viz_import_error)


class FloodlightApp:
    """DPG application shell: owns a DataStore and is the sole EventBus emitter.

    All app-wide EventBus events (DATA_LOADED, DATA_CLEARED, XY_STACK_CHANGED,
    APP_INITIALIZED, EXPORT_REQUESTED) are emitted from methods on this class.
    DataStore must not emit.

    Attribute delegation: ``__getattr__`` forwards reads to ``self.store`` so
    tab code can call ``app.get_player_slots(team)`` or ``app.get_active_xy()``
    without knowing they live on DataStore. There is NO ``__setattr__`` companion
    for this delegation, which means ``app.some_key = value`` writes to the
    FloodlightApp instance dict and silently misses the store. Route all store
    writes through explicit methods (``commit_loaded``, ``store.store_loaded_data``,
    etc.) to avoid this footgun.

    Parameters
    ----------
    (no parameters; constructed by the entry point)

    Notes
    -----
    Single emitter invariant: every bus.emit call in this codebase must originate
    from FloodlightApp, never from DataStore or tab modules.
    """

    def __init__(self):
        self.store = DataStore()
        self.load_data_callback = None

    def __getattr__(self, name):
        """Delegate attribute reads to DataStore.

        Tabs call ``app.get_player_slots(team)``, ``app.get_active_xy()``, etc.
        This forwards those reads to ``self.store`` transparently.

        The lookup uses ``vars(store)`` and ``vars(type(store).__mro__)`` rather
        than a plain ``hasattr`` to distinguish two cases:
        - attribute not present on store: raise AttributeError with a clear message;
        - attribute present but its getter raised AttributeError internally (e.g. a
          broken ``@property``): propagate the original error unchanged so the true
          cause is not hidden.

        Parameters
        ----------
        name : str
            Attribute name being looked up.

        Raises
        ------
        AttributeError
            When neither the store instance dict nor any class in its MRO exposes
            ``name``.
        """
        # Avoid infinite recursion: 'store' itself must not trigger __getattr__.
        store = object.__getattribute__(self, "store")
        attr_present = name in vars(store) or any(name in vars(cls) for cls in type(store).__mro__)
        if not attr_present:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return getattr(store, name)

    def set_load_callback(self, callback):
        """Register the generic data-loading callback used by ``load_provider_data``.

        Parameters
        ----------
        callback : callable
            A function ``(provider_key, file_paths, **extra_params) -> loaded_data | None``.
        """
        self.load_data_callback = callback

    def load_provider_data(self, provider_key, file_paths, **extra_params):
        """Load data from a registered provider and commit it to the store.

        Calls the registered ``load_data_callback``, extracts metadata, then
        delegates to ``commit_loaded`` (the single DATA_LOADED producer path).

        Parameters
        ----------
        provider_key : str
            Key into ``IO_REGISTRY`` identifying the provider.
        file_paths : list[str]
            Paths to the files the provider requires.
        **extra_params
            Forwarded verbatim to ``load_data_callback``.

        Returns
        -------
        bool
            True if loading and committing succeeded; False otherwise.

        Notes
        -----
        Emits ``Events.DATA_LOADED`` (via ``commit_loaded``) on success.
        """
        if not self.load_data_callback:
            return False
        logger.debug("Loading %s data...", provider_key)
        result = self.load_data_callback(provider_key, file_paths, **extra_params)
        if result:
            # Metadata extraction happens here, not in DataStore, because DataStore
            # has no back-reference to the provider registry.
            from floodlight_gui.engine.load_data import extract_metadata

            metadata = extract_metadata(provider_key, result)

            self.commit_loaded(result, metadata=metadata, provider=provider_key)
            return True
        return False

    def commit_loaded(self, loaded_data, *, metadata, provider):
        """Single mutate-then-emit path for a completed data load.

        All load sites (provider file load, async dataset load, create-pitch) must
        go through this method. The sequence is:

        1. Write ``store.loaded_data`` (the canonical 4-tuple read by
           ``DataStore._get_pristine_xy``).
        2. Call ``update_data_info``: emits ``DATA_CLEARED`` when replacing existing
           data, stores components and metadata, updates the ``data_info`` widget.
        3. Emit ``Events.DATA_LOADED`` with ``app=self``.

        Parameters
        ----------
        loaded_data : tuple
            The ``(pitch, event_data, position_data, teamsheet)`` 4-tuple.
        metadata : dict
            Provider metadata; ``format`` and ``teams`` in the DATA_LOADED payload
            are read from it.
        provider : str
            The DATA_LOADED ``provider`` field (a registry key for file/dataset
            loads; ``format_type`` for the create-pitch re-fire).

        Notes
        -----
        Emits ``Events.DATA_CLEARED`` (when replacing existing data, via
        ``update_data_info``) then ``Events.DATA_LOADED`` with ``app=self``.
        Writes ``store.loaded_data`` and ``store.data_metadata``.
        """
        pitch, event_data, position_data, teamsheet = loaded_data
        self.store.loaded_data = loaded_data
        self.update_data_info(event_data, position_data, teamsheet, pitch, metadata=metadata)
        bus.emit(
            Events.DATA_LOADED,
            app=self,
            provider=provider,
            format=metadata.get("format_type", "unknown"),
            teams=metadata.get("teams", []),
        )

    def replace_pitch(self, pitch):
        """Swap only the pitch in the current 4-tuple and re-fire DATA_LOADED.

        Thin wrapper over ``commit_loaded`` for the Create-Pitch flow. Assumes
        data is already loaded; callers guard on ``store.loaded_data``. Reuses the
        existing ``store.data_metadata`` as both the metadata and the ``provider``
        source so the re-fired event is consistent with the original load.

        Parameters
        ----------
        pitch : floodlight.core.pitch.Pitch
            The new pitch to install.

        Notes
        -----
        Emits ``Events.DATA_CLEARED`` then ``Events.DATA_LOADED`` (via
        ``commit_loaded``). Writes ``store.loaded_data`` and ``store.data_metadata``.
        """
        _old_pitch, event_data, position_data, teamsheet = self.store.loaded_data[:4]
        metadata = self.store.data_metadata
        self.commit_loaded(
            (pitch, event_data, position_data, teamsheet),
            metadata=metadata,
            provider=metadata.get("format_type", "unknown"),
        )

    def update_data_info(self, event_data, position_data, teamsheet, pitch=None, metadata=None):
        """Store data components, emit DATA_CLEARED if replacing, and update the info widget.

        Parameters
        ----------
        event_data : floodlight.core.events.Events or None
            Event data for the loaded dataset.
        position_data : tuple or None
            Position data tuple as returned by the loader.
        teamsheet : floodlight.core.teamsheet.Teamsheet or None
            Teamsheet for the loaded dataset.
        pitch : floodlight.core.pitch.Pitch or None
            Pitch object; optional on the first load.
        metadata : dict or None
            Provider metadata forwarded to ``store.store_loaded_data``.

        Notes
        -----
        Emits ``Events.DATA_CLEARED`` before storing when ``store.loaded_data`` is
        already set (replacing existing data). Writes ``store.loaded_data`` and
        ``store.data_metadata`` (via ``store.store_loaded_data``). Updates the
        ``data_info`` DPG widget.
        """
        # Emit DATA_CLEARED before storing so subscribers see the old state as they clean up.
        # Guard avoids a spurious DATA_CLEARED on the very first load.
        if self.store.loaded_data is not None:
            bus.emit(Events.DATA_CLEARED)

        self.store.store_loaded_data(event_data, position_data, teamsheet, pitch, metadata=metadata)

        summary = self.store.compute_summary()
        info_text = (
            f"Data format: {summary['format']}\n"
            f"Temporal structure: {summary['temporal']}\n"
            f"Event data: {summary['events']} events\n"
            f"Position data: {summary['frames']} frames\n"
            f"Teams: {summary['teams']}"
        )
        dpg.set_value("data_info", info_text)
        logger.info(
            "Data loaded: format=%s  teams=%s  frames=%d  events=%d",
            summary["format"],
            summary["teams"],
            summary["frames"],
            summary["events"],
        )

    # ----------------------------------------------------------------- #
    # XY-op stack wrappers
    #
    # DataStore is DPG-free and has no back-reference to FloodlightApp.
    # These wrappers delegate the state mutation to self.store and emit
    # XY_STACK_CHANGED from here (DataStore must not emit).
    # ----------------------------------------------------------------- #
    def apply_xy_op(self, period, team, op_key, params):
        """Push one op onto the (period, team) stack and notify subscribers.

        Parameters
        ----------
        period, team : str
            Identifies the stack to mutate.
        op_key : str
            Key into ``TRANSFORM_REGISTRY``.
        params : dict
            Collected widget values for the op's parameters.

        Returns
        -------
        floodlight.core.xy.XY
            The derived XY after applying the op.

        Notes
        -----
        Emits ``Events.XY_STACK_CHANGED`` with ``app=self``.
        Writes ``store.xy_ops[period][team]``.
        """
        derived = self.store.apply_xy_op(period, team, op_key, params)
        bus.emit(Events.XY_STACK_CHANGED, app=self)
        return derived

    def undo_xy_op(self, period, team):
        """Pop the last op from the (period, team) stack and notify subscribers.

        Parameters
        ----------
        period, team : str
            Identifies the stack to mutate.

        Returns
        -------
        floodlight.core.xy.XY
            The derived XY after undoing the last op.

        Notes
        -----
        Emits ``Events.XY_STACK_CHANGED`` with ``app=self``.
        Writes ``store.xy_ops[period][team]``.
        """
        derived = self.store.undo_xy_op(period, team)
        bus.emit(Events.XY_STACK_CHANGED, app=self)
        return derived

    def reset_xy_ops(self, period=None, team=None):
        """Clear XY-op state (all stacks or one targeted stack) and notify subscribers.

        Parameters
        ----------
        period : str or None
            When provided together with ``team``, clears only that stack.
            When None, clears all stacks.
        team : str or None
            See ``period``.

        Notes
        -----
        Emits ``Events.XY_STACK_CHANGED`` with ``app=self``.
        Writes ``store.xy_ops``.
        """
        self.store.reset_xy_ops(period=period, team=team)
        bus.emit(Events.XY_STACK_CHANGED, app=self)

    def create_ui(self):
        """Build the main DPG window: tab bar with all six tabs, then the status bar.

        Tab-bar ``callback=on_main_tab_changed`` tells keyboard.py which tab is
        active so visualization playback shortcuts (Space, arrows, Home, End) only
        fire when the viz tab is visible.

        Notes
        -----
        Creates DPG items tagged ``primary_window`` and ``main_tab_bar``.
        Each tab creation call may emit EventBus subscriptions internally. The
        status bar flows below the tab bar and reserves ``FOOTER_HEIGHT_PX``
        (see ``status_bar``) at the bottom of the window.
        """
        from floodlight_gui.status_bar import create_status_bar
        from floodlight_gui.tabs.visualization.keyboard import on_main_tab_changed

        with dpg.window(
            label="Floodlight Data Viewer",
            tag="primary_window",
            width=1000,
            height=700,
        ):
            # Tab bar is pinned at the top of primary_window by DPG's natural layout.
            # Each tab's content scrolls independently via its per-section child_window.
            with dpg.tab_bar(tag="main_tab_bar", callback=on_main_tab_changed):
                create_load_tab()
                create_inspect_tab()
                create_transforms_tab()
                create_model_tab()
                create_metrics_tab()

                if VISUALIZATION_TAB_AVAILABLE:
                    try:
                        create_visualization_tab()
                    except Exception as e:  # noqa: BLE001 -- tab creation can fail for many reasons
                        logger.exception("Error creating visualization tab: %s", e)

            # Persistent global status bar: 4 cells, 5 subscribed events, priority=20.
            create_status_bar()

    def initialize(self, load_callback):
        """Initialize the application: register the loader, build the UI, and wire keyboard.

        Call sequence:
        1. Register ``load_callback`` via ``set_load_callback``.
        2. Build the DPG widget tree via ``create_ui``.
        3. Emit ``Events.APP_INITIALIZED`` so tabs can store the app reference.
        4. Register global keyboard handlers (must happen after APP_INITIALIZED so
           viz-tab callbacks like ``_toggle_play_pause`` are already wired).
        5. Bind the primary window to the viewport size via a resize callback.

        Parameters
        ----------
        load_callback : callable
            Forwarded to ``set_load_callback``.

        Notes
        -----
        Emits ``Events.APP_INITIALIZED`` with ``app=self``.
        Registers a DPG viewport-resize callback via ``_register_primary_window_resize_anchor``.
        """
        self.set_load_callback(load_callback)

        self.create_ui()

        bus.emit(Events.APP_INITIALIZED, app=self)

        # Global keyboard handler registered after APP_INITIALIZED so all tab
        # callbacks (e.g. viz tab's _toggle_play_pause) exist before binding.
        from floodlight_gui.tabs.visualization import keyboard

        keyboard.register_global_handlers()

        # DPG set_primary_window(True) is supposed to auto-track the viewport,
        # but on Windows DPG 2.x retains the construction-time width/height.
        # The resize callback below keeps primary_window filling the viewport
        # so tab content is not capped at the initial 1000x700 dimensions.
        self._register_primary_window_resize_anchor()

        logger.info("FloodlightApp initialized")

    @staticmethod
    def _register_primary_window_resize_anchor() -> None:
        """Register a viewport-resize callback that keeps primary_window filling the viewport."""

        def _on_viewport_resized(sender=None, app_data=None, user_data=None):
            """Resize primary_window to match the current viewport client area."""
            try:
                vp_w = dpg.get_viewport_client_width()
                vp_h = dpg.get_viewport_client_height()
            except SystemError:
                return
            if vp_w <= 0 or vp_h <= 0:
                return
            try:  # noqa: SIM105 -- keep explicit try/except so the defensive comment stays adjacent
                dpg.configure_item("primary_window", width=vp_w, height=vp_h)
            except SystemError:
                # primary_window not yet created (shouldn't happen here, but be defensive).
                pass

        try:
            dpg.set_viewport_resize_callback(_on_viewport_resized)
        except SystemError:
            logger.debug(
                "set_viewport_resize_callback unavailable; primary_window will not auto-track viewport"  # noqa: E501 - inline status string / long dpg call kept readable
            )
