"""Metrics tab select layer: resolve the active metric descriptor and the
(period, team) data slice.

Layering: DPG-aware (reads combo widget values); called from controls and
execute. Backend modules must not import from this module.
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from floodlight_gui.registry.metrics import METRICS_REGISTRY
from floodlight_gui.tabs._shared.broadcast import ALL_SENTINEL, bridge_period_to_internal

# --------------------------------------------------------------------------- #
# Metric picker helpers
# --------------------------------------------------------------------------- #


def metric_display_names() -> list[str]:
    """Return the ordered list of display names for all registered metrics.

    Returns
    -------
    list[str]
        One entry per METRICS_REGISTRY entry, in insertion order.
    """
    return [d["display_name"] for d in METRICS_REGISTRY.values()]


def key_for_display(display: str) -> str | None:
    """Return the METRICS_REGISTRY key matching *display*, or None if not found.

    Parameters
    ----------
    display : str
        The ``display_name`` value to look up.

    Returns
    -------
    str or None
        The matching registry key, or None when no entry matches.
    """
    for key, desc in METRICS_REGISTRY.items():
        if desc["display_name"] == display:
            return key
    return None


# --------------------------------------------------------------------------- #
# Period / team scope resolution
# --------------------------------------------------------------------------- #


def _step1_scope():
    """Read the current period and team combo selections as a 4-tuple.

    Returns a tuple of (filter_period, period_internal, filter_team, raw_team).
    Each filter flag is False when the corresponding axis is set to "All".
    Combo widgets may not exist at cold start; defaults to "All" (no filter) in
    that case.

    Returns
    -------
    tuple
        (filter_period: bool, period_internal: str | None,
         filter_team: bool, raw_team: str)
    """
    raw_period = (
        dpg.get_value("metrics_period_combo")
        if dpg.does_item_exist("metrics_period_combo")
        else ALL_SENTINEL
    )
    raw_team = (
        dpg.get_value("metrics_team_combo")
        if dpg.does_item_exist("metrics_team_combo")
        else ALL_SENTINEL
    )
    period_internal = bridge_period_to_internal(raw_period)  # None when "All"
    return (
        period_internal is not None,
        period_internal,
        raw_team not in (ALL_SENTINEL, None, ""),
        raw_team,
    )
