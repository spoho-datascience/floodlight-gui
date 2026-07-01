"""Active-XY resolver: canonical accessor for XY objects across all provider structures.

DPG-free at module scope: this module imports only ``floodlight_gui.core``
and stdlib. It sits in the backend layer and is imported by tabs and other backend
modules; it must never pull in ``dearpygui``.
"""

from __future__ import annotations

import logging

from floodlight_gui.core.periods import period_display_to_internal

logger = logging.getLogger(__name__)


def get_xy_for_period_team(app, period: str, team: str):
    """Return the XY object for a given period and team, or ``None`` if unavailable.

    Canonical accessor for XY data across all provider structures. Routes through
    ``app.get_active_xy()`` when present so spatial-ops transforms apply. Normalises
    the ``period`` argument via :func:`period_display_to_internal` so callers can pass
    either a display name or an internal key.

    Parameters
    ----------
    app : FloodlightApp or None
        The live app instance. Accessed via ``get_active_xy`` (preferred) or
        ``loaded_data`` (fallback). Passing ``None`` or an object without
        ``loaded_data`` returns ``None``.
    period : str
        Display name or internal key for the period (e.g. "First Half" or "HT1").
        Empty strings are passed through without normalisation.
    team : str
        Team name matching the key used in the loaded position data (e.g. "Home").

    Returns
    -------
    floodlight.core.xy.XY or None
        The XY object for the requested period and team, or ``None`` when:
        - ``app`` has no loaded data or the data tuple has fewer than three elements,
        - the position data container is ``None`` or not a dict,
        - the requested period or team is not present in the container.

    Notes
    -----
    Two provider data-structure variants are handled in the fallback path:

    - DFL-style: nested ``{period: {team: XY}}`` (position_data[0] is a dict whose
      values are also dicts).
    - Kinexon-style: flat ``{team: XY}`` (position_data[0] is a dict whose values
      are XY objects directly, with no period nesting).
    """
    internal_period = period_display_to_internal(period) if period else period

    # Preferred path: route through the app's spatial-ops-aware accessor.
    getter = getattr(app, "get_active_xy", None)
    if callable(getter):
        xy = getter(internal_period, team)
        if xy is not None:
            return xy

    # Fallback: direct extraction from loaded_data for call sites that run
    # before FloodlightApp is fully initialised or without a live app instance.
    loaded = getattr(app, "loaded_data", None)
    if not loaded or len(loaded) < 3:
        return None
    position_data = loaded[2]
    if position_data is None:
        return None

    xy_container = position_data[0] if isinstance(position_data, tuple) else position_data
    if not isinstance(xy_container, dict):
        return None

    # DFL-style: nested {period: {team: XY}}
    if internal_period in xy_container and isinstance(xy_container[internal_period], dict):
        return xy_container[internal_period].get(team)

    # Kinexon-style: flat {team: XY}
    return xy_container.get(team)
