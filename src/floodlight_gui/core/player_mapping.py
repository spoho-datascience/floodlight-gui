"""Teamsheet-row to XY-column mapping for all loaded teams.

Responsibility: build and expose ``PlayerSlot`` instances that bind a player's
teamsheet identity (xID, pID, jersey, position, name) to their 0-based XY column
index.

Module-level invariants:
- DPG-free: this module contains no ``dearpygui`` import and must stay importable
  without a display. Part of the ``core/`` backend layer.
- Column-index contract: ``PlayerSlot.col_index`` == ``i`` means the player occupies
  XY columns ``[2*i, 2*i+1]``. Every list returned by ``build_player_slots`` is
  length ``XY.N`` and ordered by col_index so callers can index by position directly.
- Read-only slots: ``PlayerSlot`` is a frozen dataclass; fields must not be mutated
  after construction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlayerSlot:
    """A single XY-column-indexed player identity record.

    This is the cross-layer contract between the data-loading pipeline
    and the tab layer (inspect, model, metrics, visualization). Its module path
    and field names must not change without updating every consumer.

    Fields are read-only (frozen dataclass). Optional fields are ``None`` when
    the upstream teamsheet data is absent or unparseable.

    Column-index mapping: player at ``col_index=i`` occupies XY columns
    ``[2*i, 2*i+1]``. Lists returned by ``build_player_slots`` have length
    ``XY.N`` and are ordered by ``col_index``, so callers may index by player
    position directly without a separate lookup.

    Parameters
    ----------
    col_index : int
        0-based player index; XY columns are ``[2*col_index, 2*col_index+1]``.
    team : str
        Team label, e.g. "Home", "Away", or "Ball".
    xid : int or None
        floodlight xID for the player.
    pid : str or None
        Provider-native player ID.
    jersey : str or None
        Jersey number as a string (may encode non-numeric values).
    position : str or None
        Position label from the teamsheet.
    name : str or None
        Player display name from the teamsheet.
    """

    col_index: int
    team: str
    xid: int | None
    pid: str | None
    jersey: str | None
    position: str | None
    name: str | None


def _stringify(value: Any) -> str | None:
    """Coerce a teamsheet cell to ``str`` or ``None``.

    Returns ``None`` for pandas NA, empty strings, and whitespace-only values.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return s if s else None


def _intify(value: Any) -> int | None:
    """Coerce a teamsheet cell to ``int`` or ``None``.

    Returns ``None`` for pandas NA and values that cannot be cast to int.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _xy_N(xy_obj: Any) -> int:
    """Return the number of players encoded in an XY (or XY-like) object.

    Derived from ``xy.xy.shape[1] // 2``. Returns 0 when the attribute or
    shape is absent.
    """
    arr = getattr(xy_obj, "xy", None)
    if arr is None:
        return 0
    shape = getattr(arr, "shape", None)
    if not shape or len(shape) < 2:
        return 0
    return shape[1] // 2


def _extract_teamsheet_df(team_data: Any) -> pd.DataFrame | None:
    """Unwrap a floodlight Teamsheet or plain DataFrame into a bare DataFrame."""
    if team_data is None:
        return None
    if hasattr(team_data, "teamsheet"):
        df = team_data.teamsheet
    elif hasattr(team_data, "data"):
        df = team_data.data
    elif isinstance(team_data, pd.DataFrame):
        df = team_data
    else:
        return None
    return df if isinstance(df, pd.DataFrame) else None


def _ball_slots(n: int) -> list[PlayerSlot]:
    """Build the slot list for the Ball team.

    Conventionally ``n == 1``, but the actual XY.N is honoured to support
    sports or providers where the ball XY carries multiple columns (e.g.
    EIGD-H handball). When ``n > 1``, ``pid`` and ``name`` are suffixed per
    slot so downstream consumers get unique DPG widget tags and combo labels.
    The ``n == 1`` case produces plain "ball" / "Ball" for backward
    compatibility.

    Parameters
    ----------
    n : int
        Number of ball XY columns (XY.N for the ball team).

    Returns
    -------
    list[PlayerSlot]
        Slots for indices ``0..n-1`` with ``team="Ball"``.
    """
    return [
        PlayerSlot(
            col_index=i,
            team="Ball",
            xid=None,
            pid="ball" if n == 1 else f"ball_{i}",
            jersey=None,
            position=None,
            name="Ball" if n == 1 else f"Ball {i + 1}",
        )
        for i in range(n)
    ]


def _iter_team_xy(xy_dict: Any) -> dict[str, Any]:
    """Flatten an xy_dict into ``{team: XY}``, using the first period when nested.

    Handles two shapes:
    - Flat: ``{team: XY}`` - returned as-is.
    - DFL-nested: ``{period: {team: XY}}`` - the first period's slice is used
      because only XY.N per team is needed, not data content.

    Parameters
    ----------
    xy_dict : any
        The raw xy dictionary from the loaded data store.

    Returns
    -------
    dict[str, Any]
        A flat ``{team: XY}`` mapping, or ``{}`` when the input is not a dict.
    """
    if not isinstance(xy_dict, dict):
        return {}
    first_val = next(iter(xy_dict.values()), None)
    if isinstance(first_val, dict):
        # DFL nested: {period: {team: XY}}
        first_period_key = next(iter(xy_dict))
        first_period = xy_dict[first_period_key]
        if isinstance(first_period, dict):
            return dict(first_period)
        return {}
    return dict(xy_dict)


def _get_row_value(row: pd.Series, columns: list, *fallback_keys: str) -> Any:
    """Return the first present, non-null cell from ``row`` among ``fallback_keys``.

    Only keys that exist in ``columns`` are checked; missing keys are skipped
    rather than returning the pandas default.

    Parameters
    ----------
    row : pd.Series
        A single teamsheet row.
    columns : list
        Column names present in the parent DataFrame.
    *fallback_keys : str
        Column names to try in order of preference.

    Returns
    -------
    Any
        The first non-null cell value found, or ``None`` if none qualify.
    """
    for key in fallback_keys:
        if key in columns:
            val = row.get(key)
            if val is not None:
                try:
                    if pd.isna(val):
                        continue
                except (TypeError, ValueError):
                    pass
                return val
    return None


def build_player_slots(
    teamsheet: dict | None,
    xy_dict: dict | None,
) -> dict[str, list[PlayerSlot]]:
    """Build ``{team: [PlayerSlot, ...]}`` from a loaded teamsheet and XY dict.

    Each list is ordered by ``col_index`` (0..XY.N-1) so callers may index
    directly by player column position.

    Contract:
    - Every team present in ``xy_dict`` gets a list of length ``XY.N``
      (``xy.xy.shape[1] // 2``).
    - Teamsheet row ``i`` populates ``PlayerSlot.col_index=i`` with the row's
      xid/pid/jersey/position/name.
    - When the teamsheet is shorter than ``XY.N``, the trailing slots have all
      ``None`` identifier fields.
    - When the teamsheet is longer than ``XY.N``, the extra rows are discarded.
    - When the teamsheet is missing entirely, all slots have ``None`` identifier
      fields.
    - Teams whose name is "ball" (case-insensitive) always get ball slots via
      ``_ball_slots(XY.N)`` regardless of teamsheet content.
    - Teams present only in the teamsheet but absent from ``xy_dict`` are not
      included; the mapping is anchored to XY columns, and teams without XY
      have no valid ``col_index``.

    Parameters
    ----------
    teamsheet : dict or None
        Mapping of team name to a floodlight Teamsheet object or DataFrame.
    xy_dict : dict or None
        Mapping of team name (or period name) to XY objects; supports both flat
        and DFL-nested shapes (see ``_iter_team_xy``).

    Returns
    -------
    dict[str, list[PlayerSlot]]
        ``{team_name: [PlayerSlot]}`` for every team in ``xy_dict``.
    """
    flat_xy = _iter_team_xy(xy_dict)
    teamsheet = teamsheet or {}

    slots: dict[str, list[PlayerSlot]] = {}

    for team_name, xy_obj in flat_xy.items():
        n = _xy_N(xy_obj)
        if n <= 0:
            slots[team_name] = []
            continue

        if team_name.lower() == "ball":
            slots[team_name] = _ball_slots(n)
            continue

        df = _extract_teamsheet_df(teamsheet.get(team_name))
        cols = list(df.columns) if df is not None else []

        team_slots: list[PlayerSlot] = []
        for i in range(n):
            if df is not None and i < len(df):
                row = df.iloc[i]
                xid_raw = _get_row_value(row, cols, "xID")
                pid_raw = _get_row_value(row, cols, "pID")
                jersey_raw = _get_row_value(row, cols, "jID", "jersey_number", "number")
                position_raw = _get_row_value(row, cols, "position")
                name_raw = _get_row_value(row, cols, "player", "name")
                team_slots.append(
                    PlayerSlot(
                        col_index=i,
                        team=team_name,
                        xid=_intify(xid_raw),
                        pid=_stringify(pid_raw),
                        jersey=_stringify(jersey_raw),
                        position=_stringify(position_raw),
                        name=_stringify(name_raw),
                    )
                )
            else:
                team_slots.append(
                    PlayerSlot(
                        col_index=i,
                        team=team_name,
                        xid=None,
                        pid=None,
                        jersey=None,
                        position=None,
                        name=None,
                    )
                )
        slots[team_name] = team_slots

    return slots


# ---------------------------------------------------------------------------
# Teamsheet-column accessors (visualization on-canvas label combo + hover panel)
# ---------------------------------------------------------------------------
# These read the ACTUAL teamsheet DataFrame columns verbatim; the GUI never
# renames or fabricates them. Teams without a real teamsheet (e.g. the ball, or
# providers that do not supply one) contribute nothing, so the label set collapses
# to just the always-available xID (the XY column index) when no teamsheet is
# loaded. Row i corresponds to PlayerSlot.col_index=i (build_player_slots
# contract), so callers index by the player's col_index.
# ---------------------------------------------------------------------------


def teamsheet_columns_for(teamsheet: dict | None, teams: list[str]) -> list[str]:
    """Return ordered, de-duplicated column labels across the real teamsheets of *teams*.

    Parameters
    ----------
    teamsheet : dict or None
        Mapping of team name to Teamsheet or DataFrame.
    teams : list[str]
        Team names whose columns should be included.

    Returns
    -------
    list[str]
        Column labels in first-seen order, deduplicated across teams.
    """
    teamsheet = teamsheet if isinstance(teamsheet, dict) else {}
    columns: list[str] = []
    for team in teams or []:
        df = _extract_teamsheet_df(teamsheet.get(team))
        if df is None:
            continue
        for col in df.columns:
            label = str(col)
            if label not in columns:
                columns.append(label)
    return columns


def teamsheet_row_for(teamsheet: dict | None, team: str, col_index: int) -> list[tuple[str, str]]:
    """Return verbatim ``(column, value)`` pairs for one player's teamsheet row.

    Only non-null cells are included, in the teamsheet's own column order.
    No fabricated placeholders are added.

    Parameters
    ----------
    teamsheet : dict or None
        Mapping of team name to Teamsheet or DataFrame.
    team : str
        Team name to look up.
    col_index : int
        0-based player column index (matches ``PlayerSlot.col_index``).

    Returns
    -------
    list[tuple[str, str]]
        ``(column_name, cell_value)`` pairs for non-null cells, or ``[]`` when
        the team has no teamsheet or ``col_index`` is out of range.
    """
    teamsheet = teamsheet if isinstance(teamsheet, dict) else {}
    df = _extract_teamsheet_df(teamsheet.get(team))
    if df is None or not (0 <= col_index < len(df)):
        return []
    row = df.iloc[col_index]
    pairs: list[tuple[str, str]] = []
    for col in df.columns:
        value = _stringify(row[col])
        if value is not None:
            pairs.append((str(col), value))
    return pairs


def teamsheet_column_values(teamsheet: dict | None, team: str, column: str) -> list[str | None]:
    """Return stringified values of *column* for *team*, indexed by col_index.

    Allows callers to precompute a per-team lookup once (e.g. the per-frame
    label resolver) instead of touching the DataFrame on every frame.

    Parameters
    ----------
    teamsheet : dict or None
        Mapping of team name to Teamsheet or DataFrame.
    team : str
        Team name to look up.
    column : str
        Column name to extract.

    Returns
    -------
    list[str or None]
        One entry per teamsheet row (index matches ``col_index``), or ``[]``
        when the team has no teamsheet or lacks *column*.
    """
    teamsheet = teamsheet if isinstance(teamsheet, dict) else {}
    df = _extract_teamsheet_df(teamsheet.get(team))
    if df is None or column not in df.columns:
        return []
    return [_stringify(df.iloc[i][column]) for i in range(len(df))]
