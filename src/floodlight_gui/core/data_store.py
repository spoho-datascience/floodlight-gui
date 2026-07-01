"""Centralized data store for all loaded match data and XY-ops state.

DPG-free at module scope: this module must import without dearpygui.
Upholds the single-source-of-truth invariant: every field written here is the
canonical live value; tabs read from the store, never from their own cached copy.
XY access always replays from the pristine loaded data through the applied-op
stack; derived XY is rebuilt on demand and never cached across loads.
DataStore must not emit EventBus events (events must come from FloodlightApp).
"""

from __future__ import annotations

import logging
from copy import deepcopy

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.engine.apply_transforms import apply_xy_op

logger = logging.getLogger(__name__)

__all__ = ["DataStore"]


class DataStore:
    """DPG-free centralized store for loaded match data, metadata, and XY-ops state.

    All methods are pure Python with no Dear PyGui dependency.
    Owns player slots, XY-ops stack, and derived XY. FloodlightApp holds the
    single DataStore instance and emits EventBus events on its behalf.
    """

    def __init__(self):
        """Initialize an empty store and subscribe to DATA_LOADED for XY-ops cleanup."""
        # Primary loaded-data tuple: (pitch, event_data, position_data, teamsheet)
        self.loaded_data = None

        # Convenience references into loaded_data components
        self.pitch = None
        self.event_data = None
        self.position_data = None
        self.teamsheet = None
        self.possession_data = None
        self.ball_status = None

        # Provider metadata: format type, temporal divisions, teams, etc.
        self.data_metadata = {}

        # Framerate extracted from the XY objects; default until data is loaded.
        self.original_fps = 25

        # XY-column-indexed player slots keyed by team name.
        # Populated by store_loaded_data; DataStore is the canonical owner.
        self.player_slots: dict[str, list] = {}

        # Applied XY-ops stack per (period, team): [(op_key, params_dict), ...]
        self.xy_ops_stack: dict[tuple[str, str], list[tuple[str, dict]]] = {}
        # Derived XY per (period, team), rebuilt by _replay_stack on demand.
        self.xy_derived: dict[tuple[str, str], object] = {}

        # Priority 0 so the store resets before any tab subscriber runs.
        bus.subscribe(Events.DATA_LOADED, self._on_data_loaded_clear_xy_ops, priority=0)

    @staticmethod
    def _xy_dict(position_data):
        """Return the period/team-keyed XY dict from a stored ``position_data`` value.

        ``position_data`` is the 3rd slot of ``loaded_data``: in practice always a
        tuple ``(xy_dict, [possession], [ballstatus])``. The bare-dict and
        ``{"position_data": ...}`` forms are handled defensively.
        Returns ``{}`` when nothing is resolvable.

        Parameters
        ----------
        position_data : tuple or dict or object
            Raw value from ``self.position_data`` / ``self.loaded_data[2]``.

        Returns
        -------
        dict
            Period/team-keyed XY mapping, or ``{}`` if not resolvable.
        """
        if isinstance(position_data, tuple):
            return position_data[0] if position_data else {}
        if isinstance(position_data, dict):
            return position_data.get("position_data", position_data)
        return {}

    def extract_fps_from_position_data(self, position_data):
        """Extract framerate from the first XY object found in position_data.

        Parameters
        ----------
        position_data : tuple or dict or object
            Raw position data (same shape as ``self.position_data``).

        Returns
        -------
        float
            Framerate from the first XY that exposes ``.framerate``, or
            ``self.original_fps`` if none is found.
        """
        try:
            xy_data = self._xy_dict(position_data)
            # Walk into nested dicts until we find an object with .framerate
            for val in xy_data.values() if isinstance(xy_data, dict) else [xy_data]:
                if isinstance(val, dict):
                    for xy in val.values():
                        if hasattr(xy, "framerate") and xy.framerate is not None:
                            return float(xy.framerate)
                elif hasattr(val, "framerate") and val.framerate is not None:
                    return float(val.framerate)
        except (AttributeError, TypeError, ValueError):
            pass
        return self.original_fps

    def get_fps(self):
        """Return the original match framerate.

        Returns
        -------
        float
            ``self.original_fps`` as set by the most recent ``store_loaded_data`` call.
        """
        logger.debug("get_fps() called, returning: %s", self.original_fps)
        return self.original_fps

    def get_player_slots(self, team: str) -> list:
        """Return stable, col_index-ordered player slots for a team.

        Parameters
        ----------
        team : str
            Team name, e.g. "Home", "Away", or "Ball".

        Returns
        -------
        list of PlayerSlot
            Ordered by column index. Empty list when the team is unknown or no
            data has been loaded.
        """
        return list(self.player_slots.get(team, []))

    def get_temporal_divisions(self):
        """Return the temporal division keys from metadata.

        Returning ``[]`` when metadata is absent makes wiring failures visible
        rather than masking them with a hardcoded fallback.

        Returns
        -------
        list of str
            Period keys, e.g. ``["firstHalf", "secondHalf"]``, or ``[]`` if
            no data has been loaded.
        """
        return list(self.data_metadata.get("temporal_divisions", []))

    def get_team_names(self):
        """Return team names from metadata, including "Ball" when present.

        Returning ``[]`` when metadata is absent makes wiring failures visible
        rather than masking them with a hardcoded fallback.

        Returns
        -------
        list of str
            Team names from ``data_metadata["teams"]``, or ``[]`` if no data
            has been loaded.
        """
        return list(self.data_metadata.get("teams", []))

    def get_data_format(self):
        """Return the detected data format string.

        Returns
        -------
        str
            Value of ``data_metadata["format_type"]``, or ``"unknown"`` if not set.
        """
        return self.data_metadata.get("format_type", "unknown")

    def has_ball_data(self):
        """Return whether ball tracking data is available.

        Returns
        -------
        bool
        """
        return self.data_metadata.get("has_ball", False)

    def is_single_period(self):
        """Return whether the loaded data has exactly one temporal period.

        Returns
        -------
        bool
        """
        temporal_divisions = self.get_temporal_divisions()
        return len(temporal_divisions) == 1

    def get_position_data_structure(self):
        """Return the period/team-keyed XY dict for direct iteration.

        Returns
        -------
        dict
            Unwrapped XY dict, or ``{}`` if no data has been loaded.
        """
        if not self.position_data:
            return {}

        return self._xy_dict(self.position_data)

    # ------------------------------------------------------------------ #
    # Spatial XY ops: pristine access, stack mutation, replay
    # ------------------------------------------------------------------ #

    def _get_pristine_xy(self, period, team):
        """Return the unmodified XY for (period, team) directly from loaded_data.

        Handles both nested ``{period: {team: XY}}`` and flat ``{team: XY}``
        provider layouts.

        Parameters
        ----------
        period : str
        team : str

        Returns
        -------
        XY or None
        """
        if not self.loaded_data:
            return None
        try:
            position_data = self.loaded_data[2]
        except (TypeError, IndexError):
            return None
        xy_dict = self._xy_dict(position_data)
        if not isinstance(xy_dict, dict):
            return None
        node = xy_dict.get(period)
        if isinstance(node, dict):
            return node.get(team)
        # Flat layout (e.g. Kinexon single-period)
        return xy_dict.get(team)

    def get_active_xy(self, period, team):
        """Return the current XY for (period, team).

        Returns the derived (ops-applied) XY when any ops have been pushed;
        otherwise returns the pristine loaded XY. Reads never mutate pristine data.

        Parameters
        ----------
        period : str
        team : str

        Returns
        -------
        XY or None
            ``None`` when no data is loaded for this (period, team) combination.
        """
        key = (period, team)
        if key in self.xy_derived:
            return self.xy_derived[key]
        return self._get_pristine_xy(period, team)

    def _replay_stack(self, period, team):
        """Rebuild the derived XY by deep-copying pristine and replaying every stacked op.

        Always replays from the pristine source; never chains derived-on-derived to
        avoid accumulated floating-point drift.

        Parameters
        ----------
        period : str
        team : str

        Returns
        -------
        XY or None
            Updated derived XY, or ``None`` when the pristine XY is missing.
        """
        key = (period, team)
        stack = self.xy_ops_stack.get(key, [])
        pristine = self._get_pristine_xy(period, team)
        if pristine is None:
            self.xy_derived.pop(key, None)
            return None
        if not stack:
            self.xy_derived.pop(key, None)
            return pristine
        xy = deepcopy(pristine)
        for op_key, params in stack:
            xy = apply_xy_op(xy, op_key, params)
        self.xy_derived[key] = xy
        return xy

    def apply_xy_op(self, period, team, op_key, params):
        """Push a new op onto the stack for (period, team) and rebuild the derived XY.

        Mutates ``xy_ops_stack`` and ``xy_derived`` only. Emission of
        ``Events.XY_STACK_CHANGED`` is the caller's responsibility
        (``FloodlightApp.apply_xy_op`` wraps this method and emits the event).
        DataStore has no back-reference to FloodlightApp and must not emit.

        If replay raises, the just-pushed op is rolled back and the exception
        is re-raised so the caller can surface it.

        Parameters
        ----------
        period : str
        team : str
        op_key : str
            Key into ``TRANSFORM_REGISTRY``.
        params : dict
            Keyword arguments for the transform function.

        Returns
        -------
        XY or None
            Newly derived XY, or ``None`` if the pristine XY is missing.

        Raises
        ------
        Exception
            Re-raises any transform error after rolling back the failed op.
        """
        key = (period, team)
        self.xy_ops_stack.setdefault(key, []).append((op_key, dict(params or {})))
        try:
            derived = self._replay_stack(period, team)
        except Exception as e:  # noqa: BLE001 - replay can raise any transform error; roll back and re-raise
            # Roll the just-pushed op back if replay fails
            self.xy_ops_stack[key].pop()
            self._replay_stack(period, team)
            logger.warning("apply_xy_op failed for %s %s: %s", key, op_key, e)
            raise
        return derived

    def undo_xy_op(self, period, team):
        """Pop the last op from (period, team)'s stack and rebuild the derived XY.

        Mutates ``xy_ops_stack`` and ``xy_derived`` only. Emission lives on
        FloodlightApp.

        Parameters
        ----------
        period : str
        team : str

        Returns
        -------
        XY or None
            Updated derived XY after undo, or ``None`` when the stack was empty
            or the pristine XY is missing.
        """
        key = (period, team)
        stack = self.xy_ops_stack.get(key)
        if not stack:
            return None
        stack.pop()
        derived = self._replay_stack(period, team)
        return derived

    def reset_xy_ops(self, period=None, team=None):
        """Clear the XY-ops stack for one (period, team) or for all keys.

        Mutates ``xy_ops_stack`` and ``xy_derived`` only. Emission lives on
        FloodlightApp.

        Parameters
        ----------
        period : str or None
            When both ``period`` and ``team`` are ``None``, all keys are cleared.
        team : str or None
        """
        if period is None and team is None:
            self.xy_ops_stack.clear()
            self.xy_derived.clear()
            return
        key = (period, team)
        self.xy_ops_stack.pop(key, None)
        self.xy_derived.pop(key, None)

    def get_xy_ops_stack(self, period, team):
        """Return a copy of the applied-op stack for (period, team).

        Parameters
        ----------
        period : str
        team : str

        Returns
        -------
        list of tuple
            Sequence of ``(op_key, params_dict)`` pairs, oldest first.
            Empty list when no ops have been applied.
        """
        return list(self.xy_ops_stack.get((period, team), []))

    def _on_data_loaded_clear_xy_ops(self, **_):
        """Bus subscriber: wipe XY-ops state whenever new data is loaded.

        Subscribed at priority 0 so the store resets before any tab subscriber runs.
        """
        self.xy_ops_stack.clear()
        self.xy_derived.clear()

    def close(self):
        """Unsubscribe this store's callbacks from the event bus.

        Each ``DataStore()`` subscribes a bound method to ``Events.DATA_LOADED``.
        Because bound methods on distinct instances are distinct callables, the
        bus dedup-by-identity check cannot collapse them. Long-lived test sessions
        that construct ``DataStore`` repeatedly (e.g. the ``conftest.data_store``
        fixture) would otherwise accumulate stale subscribers. Fixtures and owners
        must call ``close()`` during teardown; the single-instance production app
        can ignore this.
        """
        bus.unsubscribe(Events.DATA_LOADED, self._on_data_loaded_clear_xy_ops)

    def store_loaded_data(self, event_data, position_data, teamsheet, pitch=None, metadata=None):
        """Store all components from a data-load and update derived metadata.

        Writes ``event_data``, ``position_data``, ``teamsheet``, ``pitch``,
        ``data_metadata``, ``player_slots``, ``original_fps``,
        ``possession_data``, and ``ball_status``. DataStore is the canonical
        owner of all these fields; no other site should write them directly.

        The pitch field is always overwritten (including to ``None``) so that
        loading a provider without pitch data after one that has it does not
        silently preserve the stale pitch object.

        ``possession_data`` and ``ball_status`` are cleared before extraction
        for the same reason: a provider without them, loaded after one with them,
        must not inherit the prior dataset's values.

        Parameters
        ----------
        event_data : object
            Event data in whatever shape the provider returns.
        position_data : tuple or dict
            Position tracking data; typically ``(xy_dict, possession, ballstatus)``.
        teamsheet : dict or object
            Team and player information.
        pitch : object or None
            Pitch dimensions object, or ``None`` when the provider does not supply one.
        metadata : dict or None
            Provider metadata dict (format_type, temporal_divisions, teams, has_ball,
            etc.). When ``None`` the existing ``data_metadata`` is preserved.
        """
        self.event_data = event_data
        self.position_data = position_data
        self.teamsheet = teamsheet

        if metadata is not None:
            self.data_metadata = dict(metadata)

        # DataStore is the canonical owner of player_slots; not computed in data_loading.
        from floodlight_gui.core.player_mapping import build_player_slots

        xy_for_slots = self._xy_dict(position_data)
        self.player_slots = build_player_slots(
            teamsheet if isinstance(teamsheet, dict) else None,
            xy_for_slots,
        )

        # Always overwrite pitch (including None) to avoid stale pitch leaking
        # across loads when a provider does not supply one.
        self.pitch = pitch

        # Extract FPS
        try:
            self.original_fps = self.extract_fps_from_position_data(position_data)
            logger.debug("Extracted FPS from data: %s", self.original_fps)
        except (AttributeError, TypeError, ValueError) as e:
            logger.warning("Error extracting FPS: %s", e)

        # Clear first so a provider without possession/ball-status data does not
        # inherit values from the previously loaded dataset.
        self.possession_data = None
        self.ball_status = None

        # Extract possession and ball status
        if isinstance(position_data, tuple) and len(position_data) > 1:
            if len(position_data) > 1:
                self.possession_data = position_data[1]
            if len(position_data) > 2:
                self.ball_status = position_data[2]
        elif isinstance(position_data, dict):
            if "possession_data" in position_data:
                self.possession_data = position_data["possession_data"]
            if "ballstatus_data" in position_data:
                self.ball_status = position_data["ballstatus_data"]

    def compute_summary(self):
        """Compute a human-readable summary of the loaded data.

        Returns
        -------
        dict
            Keys: ``format`` (str), ``temporal`` (list), ``events`` (int),
            ``frames`` (int), ``teams`` (list or str).
        """
        position_count = 0
        try:
            temporal_divisions = self.get_temporal_divisions()
            team_names = self.get_team_names()
            pos_structure = self.get_position_data_structure()

            if pos_structure:
                if len(temporal_divisions) == 1 and temporal_divisions[0] == "fullMatch":
                    for team in team_names:
                        if team in pos_structure and hasattr(pos_structure[team], "xy"):
                            position_count += pos_structure[team].xy.shape[0]
                            break
                else:
                    for period in temporal_divisions:
                        if period in pos_structure:
                            period_data = pos_structure[period]
                            if isinstance(period_data, dict):
                                for team in team_names:
                                    if team in period_data and hasattr(period_data[team], "xy"):
                                        position_count += period_data[team].xy.shape[0]
                                        break
        except Exception:  # noqa: BLE001 - data structure is variable
            logger.exception("Error calculating position count")
            position_count = 0

        # event_data may be a 3-tuple (events_dict, teamsheets, pitch) for providers
        # like DFL/IDSSE. Walk the nested {period: {team: Events}} structure and sum
        # len(events_obj.events) rather than len() of the outer container, which
        # returns the tuple length or half count.
        event_count = 0
        if self.event_data is not None:
            events_dict = None
            if (
                isinstance(self.event_data, tuple)
                and len(self.event_data) > 0
                and isinstance(self.event_data[0], dict)
            ):
                # DFL/IDSSE upstream shape: (events_dict, teamsheets, pitch)
                events_dict = self.event_data[0]
            elif isinstance(self.event_data, dict):
                # Fallback: bare nested dict (some providers may return this directly)
                events_dict = self.event_data

            if events_dict is not None:
                for period_data in events_dict.values():
                    if isinstance(period_data, dict):
                        for events_obj in period_data.values():
                            if hasattr(events_obj, "events") and hasattr(
                                events_obj.events, "__len__"
                            ):
                                event_count += len(events_obj.events)
                            elif hasattr(events_obj, "__len__"):
                                event_count += len(events_obj)
            elif hasattr(self.event_data, "events") and hasattr(self.event_data.events, "__len__"):
                # Legacy: flat Events object (rare; most providers nest by period)
                event_count = len(self.event_data.events)

        team_info = "Unknown"
        if self.teamsheet and isinstance(self.teamsheet, dict):
            team_info = list(self.teamsheet.keys())

        return {
            "format": self.get_data_format(),
            "temporal": self.get_temporal_divisions(),
            "events": event_count,
            "frames": position_count,
            "teams": team_info,
        }
