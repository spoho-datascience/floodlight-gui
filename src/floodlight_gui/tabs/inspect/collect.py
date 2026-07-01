"""Data-collection layer for the Inspect tab.

Each public collector receives the live app object and returns either a
typed tree dict ready for the SECTIONS viewer or ``None`` when the relevant
data is absent. Per-format shape quirks are isolated here. DPG-aware: lives
under ``tabs/`` and may reference tab-layer state, but does not call DPG APIs
directly.

Invariant: ``state.app_instance`` is always read through the module reference
(``state.app_instance``); importing it by value would capture ``None`` at
import time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from floodlight_gui.tabs.inspect import state
from floodlight_gui.tabs.inspect.controls import _position_source, _selected_event_types


def _events_for(event_data: Any, period: str, team: str, selected: list[str]) -> pd.DataFrame:
    """Extract and filter the event DataFrame for one (period, team) cell.

    Parameters
    ----------
    event_data : Any
        Raw ``app.event_data`` tuple as stored in the app.
    period : str
        Display-name period key (e.g. "First Half").
    team : str
        Team display name (e.g. "Home", "Away").
    selected : list[str]
        Event IDs to keep. An empty list returns an empty DataFrame (no
        selection means nothing to show).

    Returns
    -------
    pd.DataFrame
        Filtered event rows sorted by gameclock, or an empty DataFrame when
        the period/team is absent or no events match the selection.
    """
    if not isinstance(event_data, tuple) or len(event_data) == 0:
        return pd.DataFrame()
    events = event_data[0]
    if period not in events or team not in events[period]:
        return pd.DataFrame()
    team_events = events[period][team]
    if not hasattr(team_events, "events"):
        return pd.DataFrame()
    df = team_events.events.copy()
    if selected and "eID" in df.columns:
        df = df[df["eID"].isin(selected)]
    elif not selected:
        return pd.DataFrame()
    if "gameclock" in df.columns:
        df = df.sort_values("gameclock").reset_index(drop=True)
    return df


def _collect_event(app) -> dict | None:
    """Collect event data into a period-keyed, team-keyed dict of DataFrames.

    Applies the active event-type filter from the controls layer. Returns
    ``None`` when ``app.event_data`` is absent or empty.

    Parameters
    ----------
    app
        Live app object exposing ``event_data``, ``get_temporal_divisions()``,
        and ``get_team_names()``.

    Returns
    -------
    dict or None
        ``{period: {team: pd.DataFrame}}`` or ``None``.
    """
    event_data = app.event_data
    if not isinstance(event_data, tuple) or len(event_data) == 0:
        return None
    selected = _selected_event_types()
    out: dict[str, dict[str, pd.DataFrame]] = {}
    for period in app.get_temporal_divisions():
        out[period] = {
            team: _events_for(event_data, period, team, selected) for team in app.get_team_names()
        }
    return out or None


def _collect_position(app) -> dict | None:
    """Collect position (XY) data into a period-keyed, entity-keyed dict.

    Handles two layout shapes from ``app.get_position_data_structure()``:
    the common ``{period: {entity: data}}`` and the single-period flat
    ``{entity: data}`` shape. The active position source (raw vs post-stack)
    is recorded per leaf so ``_resolve_xy`` can dispatch correctly.

    Parameters
    ----------
    app
        Live app object exposing ``position_data``,
        ``get_position_data_structure()``, ``get_temporal_divisions()``,
        and ``get_team_names()``.

    Returns
    -------
    dict or None
        ``{period: {entity: {"entity", "data", "period_internal", "source"}}}``
        or ``None`` when no position data is loaded.
    """
    position_data = app.position_data
    if not position_data or (
        isinstance(position_data, (list, tuple, dict)) and len(position_data) == 0
    ):
        return None
    source = _position_source()
    structure = app.get_position_data_structure()
    divisions = app.get_temporal_divisions()
    teams = app.get_team_names()
    single = len(divisions) == 1
    out: dict[str, dict[str, dict]] = {}
    for period in divisions:
        entities: dict[str, dict] = {}
        if period in structure:
            present = [e for e in teams if e in structure[period]]
        elif single and period == divisions[0]:
            present = [e for e in teams if e in structure]
        else:
            present = []
        for entity in present:
            if period in structure and entity in structure[period]:
                data = structure[period][entity]
            elif single and entity in structure:
                data = structure[entity]
            else:
                data = None
            entities[entity] = {
                "entity": entity,
                "data": data,
                "period_internal": period,
                "source": source,
            }
        out[period] = entities
    return out or None


def _collect_team(app) -> dict | None:
    """Collect teamsheet data into a team-keyed dict of DataFrames.

    Parameters
    ----------
    app
        Live app object exposing ``teamsheet`` and ``get_team_names()``.

    Returns
    -------
    dict or None
        ``{team: pd.DataFrame | None}`` or ``None`` when no teamsheet is loaded.
    """
    teamsheet = app.teamsheet
    if not teamsheet or (isinstance(teamsheet, (list, tuple, dict)) and len(teamsheet) == 0):
        return None
    out = {team: _team_df(teamsheet[team]) for team in app.get_team_names() if team in teamsheet}
    return out or None


def _team_df(team_data: Any) -> pd.DataFrame | None:
    """Extract a DataFrame from a teamsheet object, handling multiple provider shapes."""
    if hasattr(team_data, "teamsheet"):
        return team_data.teamsheet
    if hasattr(team_data, "data"):
        return team_data.data
    if isinstance(team_data, pd.DataFrame):
        return team_data
    return None


def _collect_code(get_data: Callable[[Any], Any]) -> Callable[[Any], dict | None]:
    """Return a collector that keys Code objects by temporal division.

    Code objects are stored under the same period keys the event and position
    collectors use, so each division is looked up directly in ``get_data(app)``.

    Parameters
    ----------
    get_data : Callable[[Any], Any]
        Zero-parameter-beyond-app accessor, e.g. ``lambda app: app.possession_data``.

    Returns
    -------
    Callable[[Any], dict or None]
        A collector ``(app) -> {period_display: Code} | None``.
    """

    def collect(app) -> dict | None:
        """Collect Code objects keyed by period display name, or None if absent."""
        data = get_data(app)
        if not data or not isinstance(data, dict) or len(data) == 0:
            return None
        out = {}
        for period in app.get_temporal_divisions():
            if period in data:
                out[period] = data[period]
        return out or None

    return collect


def _collect_pitch(app) -> Any:
    """Return the loaded Pitch object, or None when none is available."""
    return app.pitch


def _resolve_xy(entity: str, data: Any, source: str, period_internal: str | None):
    """Resolve the XY array, shape, and framerate for one position leaf.

    When ``source`` is ``"post_stack"`` and ``state.app_instance`` is set,
    returns the active (post-transform) XY from the app; otherwise returns the
    raw XY stored on ``data``.

    Parameters
    ----------
    entity : str
        Team or entity name used to look up the active XY on the app.
    data : Any
        Raw position data object (must have a ``.xy`` attribute).
    source : str
        ``"post_stack"`` to prefer the active (post-transform) XY, or any
        other value to use ``data.xy`` directly.
    period_internal : str or None
        Internal-format period key passed to ``app.get_active_xy``.

    Returns
    -------
    tuple
        ``(xy_array, shape, framerate)`` where ``framerate`` is the numeric
        framerate or the string ``"N/A"`` when unavailable.
    """
    if source == "post_stack" and state.app_instance is not None and period_internal is not None:
        active = state.app_instance.get_active_xy(period_internal, entity)
        if active is not None and hasattr(active, "xy"):
            framerate = getattr(active, "framerate", getattr(data, "framerate", "N/A"))
            return active.xy, active.xy.shape, framerate
    return data.xy, data.xy.shape, getattr(data, "framerate", "N/A")
