"""Metrics results panel: wiring, leaf renderer, and export payload builders.

Reuses the shared ``ResultsPanel`` engine from ``tabs/_shared/results_panel.py``.
The metric/period/team hierarchy maps exactly onto the engine's key/period/team/leaf
shape; the non-XY composite-source label rides in the team slot as a string, which
is all the panel needs for tags and active-leaf ``user_data``.

DPG carve-out: ``_render_leaf`` imports ``dearpygui`` locally (not at module scope)
because it is called from DPG widget callbacks inside the render loop. All other
functions in this module are DPG-free.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from floodlight_gui.core.periods import period_internal_to_display
from floodlight_gui.registry.metrics import METRICS_REGISTRY
from floodlight_gui.tabs._shared.array_view import render_array_view
from floodlight_gui.tabs._shared.results_panel import ResultsPanel
from floodlight_gui.tabs.metrics import state

logger = logging.getLogger(__name__)

PLACEHOLDER = "metrics_results_placeholder"
INFO_TAG = "metrics_results_info"


def _display_name(metric_key) -> str:
    """Return the registry display name for *metric_key*, falling back to the key itself."""
    return METRICS_REGISTRY.get(str(metric_key), {}).get("display_name", str(metric_key))


def make_panel() -> ResultsPanel:
    """Construct the metrics ``ResultsPanel`` bound to the ``metrics_`` tag namespace.

    Returns
    -------
    ResultsPanel
        A new panel instance; the owning DPG container is created by ``controls``.

    Notes
    -----
    The returned panel is stored in ``state.panel`` by the caller in ``controls``.
    """
    return ResultsPanel(
        prefix="metrics",
        display_name=_display_name,
        render_leaf=_render_leaf,
        noun="metric",
    )


# --------------------------------------------------------------------------- #
# Leaf renderer
# --------------------------------------------------------------------------- #


def _render_leaf(key, period_internal: str, team: str, leaf_tag: str) -> None:
    """Render a single result leaf into the DPG container *leaf_tag*.

    Writes a header line (metric, period, team/source), then either a scalar
    text line or a paginated array view, depending on the cached result shape.

    Parameters
    ----------
    key : str
        METRICS_REGISTRY key for the computed metric.
    period_internal : str
        Internal period identifier (converted to display label via
        ``period_internal_to_display``).
    team : str
        Team name or composite-source label stored in the period/team slot.
    leaf_tag : str
        DPG container tag to render into (created by ``ResultsPanel``).
    """
    import dearpygui.dearpygui as dpg

    result = state.results.get((str(key), period_internal, team))
    period_label = period_internal_to_display(period_internal)
    dpg.add_text(
        f"Metric: {_display_name(key)}  |  Period: {period_label}  |  Team/Source: {team}",
        parent=leaf_tag,
    )

    if result is None:
        dpg.add_text("No result.", parent=leaf_tag)
        return

    if "value" in result:
        dpg.add_text(f"Value: {result['value']}", parent=leaf_tag)
        return

    frame = result.get("dataframe")
    columns = None
    # Property 2-D arrays use an explicit column list to avoid the XY-interleave default.
    if isinstance(frame, np.ndarray) and frame.ndim == 2:
        columns = ["Frame"] + [f"P{i}" for i in range(frame.shape[1])]
    render_array_view(leaf_tag, frame, columns=columns)


# --------------------------------------------------------------------------- #
# Panel feeders
# --------------------------------------------------------------------------- #


def _entries() -> list[tuple[str, str, str]]:
    """Return all (metric_key, period, team) triples currently in ``state.results``."""
    return [(k, p, t) for (k, p, t) in state.results]


def rebuild() -> None:
    """Rebuild the full results panel from the current cache.

    Notes
    -----
    No-ops when ``state.panel`` is ``None`` (panel not yet created).
    """
    if state.panel is not None:
        state.panel.rebuild(_entries())


def refresh_leaf(metric_key: str, period_internal: str, team: str) -> None:
    """Refresh a single leaf in the results panel after a new compute result arrives.

    Parameters
    ----------
    metric_key : str
        METRICS_REGISTRY key of the updated metric.
    period_internal : str
        Internal period identifier of the updated leaf.
    team : str
        Team name or composite-source label of the updated leaf.

    Notes
    -----
    No-ops when ``state.panel`` is ``None``. Passes the current entry and key
    counts to the panel engine so it can update the section header.
    """
    if state.panel is None:
        return
    metric_count = len({k for (k, _p, _t) in state.results})
    state.panel.refresh_leaf(
        metric_key,
        period_internal,
        team,
        entries_count=len(state.results),
        key_count=metric_count,
    )


def clear() -> None:
    """Clear all cached results and reset the panel to its empty state.

    Notes
    -----
    Writes ``state.results = {}``. No-ops on the panel when ``state.panel``
    is ``None``.
    """
    state.results = {}
    if state.panel is not None:
        state.panel.clear()


# --------------------------------------------------------------------------- #
# Export payloads (no-arg callables returning lists of 7-tuples)
# --------------------------------------------------------------------------- #
#
# 7-tuple shape: (period, team, payload_obj, None, metric_key, None, display_name)
# payload_obj is a projected DataFrame for array results or a dict for scalars.


def _project(result: dict) -> object:
    """Convert a cached result dict to a DataFrame or scalar dict for export.

    Parameters
    ----------
    result : dict
        Entry from ``state.results`` (may be ``None``).

    Returns
    -------
    pd.DataFrame or dict
        ``pd.DataFrame()`` for a missing result; ``{"value": ...}`` for a scalar;
        a ``DataFrame`` wrapping the array otherwise.
    """
    if result is None:
        return pd.DataFrame()
    if "value" in result:
        return {"value": result["value"]}
    frame = result.get("dataframe")
    if isinstance(frame, np.ndarray):
        return pd.DataFrame(frame)
    return frame


def _tuple_for(metric_key: str, period: str, team: str) -> tuple | None:
    """Build a single export 7-tuple for the given (metric_key, period, team).

    Returns ``None`` when no result is cached for the combination.
    """
    result = state.results.get((metric_key, period, team))
    if result is None:
        return None
    return (period, team, _project(result), None, metric_key, None, _display_name(metric_key))


def single_payload() -> list[tuple]:
    """Return a one-element export list for the currently active leaf.

    Returns
    -------
    list[tuple]
        A list containing one 7-tuple for the active leaf, or ``[]`` when no
        leaf is active or no result is cached.
    """
    if state.panel is None:
        return []
    active = state.panel.active_leaf()
    if active is None:
        return []
    key, period, team = active
    t = _tuple_for(str(key), period, team)
    return [t] if t is not None else []


def broadcast_payload() -> list[tuple]:
    """Return export 7-tuples for every cached entry under the active metric.

    Returns
    -------
    list[tuple]
        One 7-tuple per (period, team) combination that has a cached result for
        the active metric key, or ``[]`` when no leaf is active.
    """
    if state.panel is None:
        return []
    active = state.panel.active_leaf()
    if active is None:
        return []
    metric_key = str(active[0])
    out: list[tuple] = []
    for k, period, team in state.results:
        if k != metric_key:
            continue
        t = _tuple_for(k, period, team)
        if t is not None:
            out.append(t)
    return out
