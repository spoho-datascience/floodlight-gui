"""Single source of truth for visualization-tab color resolution (pure, DPG-free).

Owns the team palette, the fallback color cycle, the Code-timeline band palette,
the ball-token detection, and the overlay alpha default. Every color decision in
the viz tab routes through here, so there is exactly one place to change a color
and exactly one seam for the future user-settings system to override.

Resolution order for a named team:
  1. A per-team user override (keyed by team name), populated by a future settings
     layer via :func:`set_team_color_overrides`.
  2. A named-team default (Home -> red, Away -> blue, Ball -> orange).
  3. The deterministic ``COLOR_CYCLE`` indexed by the team's position among the
     non-ball teams (so anonymous handball ``teamA``/``teamB`` get stable,
     distinct colors).

The override store defaults to empty, so with no settings applied this module
reproduces the default behavior exactly. A future settings tab only has to call
``set_team_color_overrides({team_name: rgba})`` (or pass ``overrides=`` per call)
and every consumer (live render, model overlays, and export) picks the choice up
through the same path.

Pure module: no GUI toolkit import, safe in headless tests.
"""

from __future__ import annotations

__all__ = [
    "BAND_COLORS",
    "TEAM_COLORS",
    "COLOR_CYCLE",
    "DEFAULT_OVERLAY_ALPHA",
    "BALL_TOKENS",
    "is_ball_team",
    "color_for_token",
    "resolve_team_color_by_index",
    "team_color_for",
    "set_team_color_overrides",
    "set_team_color_override",
    "clear_team_color_overrides",
    "team_color_overrides",
]

# --------------------------------------------------------------------------- #
# Default palettes (these are DEFAULTS; the override store wins over them)
# --------------------------------------------------------------------------- #

# Deterministic Code-timeline band palette, indexed by token position.
# DFL tokens are float64, so token comparison uses float() in color_for_token.
BAND_COLORS = [
    (70, 130, 180, 200),  # steel blue
    (205, 92, 92, 200),  # indian red
    (128, 128, 128, 200),  # grey
    (255, 165, 0, 200),  # orange
    (144, 238, 144, 200),  # light green
]

# Named-team default colors [R, G, B, A].
TEAM_COLORS = {
    "Home": [220, 50, 50, 255],
    "Away": [50, 100, 220, 255],
    "Ball": [255, 165, 0, 255],
}

# Fallback alpha for overlay specs when an adapter exposes no per-instance alpha.
DEFAULT_OVERLAY_ALPHA = 0.3

# Fallback cycle for un-named teams (handball teamA/teamB, 3rd team, etc.).
COLOR_CYCLE = [
    [220, 50, 50, 255],
    [50, 100, 220, 255],
    [50, 180, 80, 255],
    [180, 80, 200, 255],
    [255, 140, 0, 255],
    [100, 60, 40, 255],
    [230, 120, 180, 255],
    [128, 128, 128, 255],
    [0, 200, 200, 255],
    [200, 0, 200, 255],
]

# A team whose name contains any of these whole tokens is treated as the ball.
BALL_TOKENS = {"ball"}


# --------------------------------------------------------------------------- #
# User override store (the future-settings seam)
# --------------------------------------------------------------------------- #

# Empty by default. A future settings layer populates this keyed by team name.
# Per-call `overrides=` takes precedence over this store.
_TEAM_COLOR_OVERRIDES: dict[str, list[int]] = {}


def set_team_color_overrides(mapping: dict[str, list[int]] | None) -> None:
    """Replace the global team-color overrides (keyed by team name).

    Intended for the future settings system: ``set_team_color_overrides(
    {"Home": [0, 0, 0, 255], ...})``. Pass ``None``/``{}`` to clear.

    Parameters
    ----------
    mapping : dict[str, list[int]] or None
        Team-name to RGBA mapping. ``None`` or empty dict clears all overrides.
    """
    _TEAM_COLOR_OVERRIDES.clear()
    if mapping:
        _TEAM_COLOR_OVERRIDES.update({k: list(v) for k, v in mapping.items()})


def set_team_color_override(team_name: str, rgba: list[int]) -> None:
    """Set (or replace) the override color for a single team.

    Parameters
    ----------
    team_name : str
        Team name as it appears in the data (e.g. "Home", "Away").
    rgba : list[int]
        Four-element RGBA list, values 0-255.
    """
    _TEAM_COLOR_OVERRIDES[team_name] = list(rgba)


def clear_team_color_overrides() -> None:
    """Drop all team-color overrides (revert to the named/cycle defaults)."""
    _TEAM_COLOR_OVERRIDES.clear()


def team_color_overrides() -> dict[str, list[int]]:
    """Return a copy of the active global override map.

    Returns
    -------
    dict[str, list[int]]
        A shallow copy; mutations do not affect the module-level store.
    """
    return {k: list(v) for k, v in _TEAM_COLOR_OVERRIDES.items()}


def _active_overrides(overrides: dict[str, list[int]] | None) -> dict[str, list[int]]:
    """Return the effective override map: per-call arg wins over the module store."""
    return overrides if overrides is not None else _TEAM_COLOR_OVERRIDES


# --------------------------------------------------------------------------- #
# Pure resolution helpers
# --------------------------------------------------------------------------- #


def is_ball_team(team_name: str) -> bool:
    """Return True if *team_name* denotes a ball entity (whole-token match).

    Splits on whitespace / ``-`` / ``_`` so "Football" / "Volleyball" are NOT
    treated as the ball, but "Ball" / "team ball" are.

    Parameters
    ----------
    team_name : str
        Team name string from the loaded dataset.

    Returns
    -------
    bool
        True when any whitespace/dash/underscore token in *team_name* (lowercased)
        is a member of ``BALL_TOKENS``.
    """
    tokens = set(team_name.lower().replace("-", " ").replace("_", " ").split())
    return bool(tokens & BALL_TOKENS)


def color_for_token(token, token_list):
    """Return the band RGBA for a Code token, deterministic by token position.

    Tokens are compared as floats (DFL tokens are float64). An unknown token or a
    non-numeric list falls back to the first band color.

    Parameters
    ----------
    token : numeric
        The Code token value to look up.
    token_list : sequence
        Ordered list of all token values for the Code object.

    Returns
    -------
    tuple[int, int, int, int]
        RGBA tuple from ``BAND_COLORS``.
    """
    try:
        normalized = [float(t) for t in token_list]
        idx = normalized.index(float(token)) % len(BAND_COLORS)
    except (ValueError, TypeError):
        idx = 0
    return BAND_COLORS[idx]


def resolve_team_color_by_index(
    idx: int,
    team_names: list[str] | None,
    *,
    overrides: dict[str, list[int]] | None = None,
) -> list[int]:
    """Return the RGBA for the *idx*-th non-ball team (name-agnostic).

    Resolution order: user override (by name), named default (Home/Away/Ball),
    then ``COLOR_CYCLE`` by index. An out-of-range *idx* (or an all-ball / empty
    names list) falls through to the cycle. Returns a fresh list each call.

    Parameters
    ----------
    idx : int
        Zero-based position among the non-ball teams in encounter order.
    team_names : list[str] or None
        Full ordered list of team names, including ball teams.
    overrides : dict[str, list[int]] or None
        Per-call override map; takes precedence over the module-level store.

    Returns
    -------
    list[int]
        Four-element RGBA list, values 0-255.
    """
    ov = _active_overrides(overrides)
    non_ball = [t for t in (team_names or []) if not is_ball_team(t)]
    if 0 <= idx < len(non_ball):
        name = non_ball[idx]
        if name in ov:
            return list(ov[name])
        if name in TEAM_COLORS:
            return list(TEAM_COLORS[name])
    return list(COLOR_CYCLE[idx % len(COLOR_CYCLE)])


def team_color_for(
    team_name: str,
    order_index: int,
    *,
    overrides: dict[str, list[int]] | None = None,
) -> tuple[list[int], bool]:
    """Resolve a color for a specific team by name; return ``(rgba, used_cycle)``.

    Resolution order: user override (by name), named default, then ``COLOR_CYCLE``
    by *order_index*. ``used_cycle`` is True only when the cycle fallback fires,
    so callers that assign cycle slots in encounter order advance their index only
    then. Override and named-team hits must not consume a cycle slot, preserving
    stable color assignment across repeated calls.

    Parameters
    ----------
    team_name : str
        Team name as it appears in the data.
    order_index : int
        Caller-managed cycle index, incremented only when ``used_cycle`` is True.
    overrides : dict[str, list[int]] or None
        Per-call override map; takes precedence over the module-level store.

    Returns
    -------
    tuple[list[int], bool]
        ``(rgba, used_cycle)`` where ``rgba`` is a four-element RGBA list and
        ``used_cycle`` signals whether the caller should advance *order_index*.
    """
    ov = _active_overrides(overrides)
    if team_name in ov:
        return list(ov[team_name]), False
    if team_name in TEAM_COLORS:
        return list(TEAM_COLORS[team_name]), False
    return list(COLOR_CYCLE[order_index % len(COLOR_CYCLE)]), True
