"""Model results view: per-leaf renderer, panel feeders, and export payloads.

Renders fitted-model outputs via the ``_shared`` ``ResultsPanel`` engine.
Supplies the render_leaf callback, rebuild/refresh/clear feeders, and the two
no-arg export-payload callables consumed by the export-action widget bundle.

DPG-aware: ``dearpygui`` is imported lazily inside rendering functions so this
module's top-level import does not force a DPG dependency on callers.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from floodlight_gui.core.periods import period_internal_to_display
from floodlight_gui.registry.models import MODEL_REGISTRY
from floodlight_gui.tabs._shared.array_view import render_array_view
from floodlight_gui.tabs._shared.results_panel import ResultsPanel
from floodlight_gui.tabs.model import labels, state

logger = logging.getLogger(__name__)


def make_panel() -> ResultsPanel:
    """Create and return a configured ``ResultsPanel`` for the model tab.

    Returns
    -------
    ResultsPanel
        Panel keyed on model_key, using ``render_leaf`` for leaf rendering
        and ``_counts`` for the header summary.
    """
    return ResultsPanel(
        prefix="model",
        display_name=labels.display_name,
        render_leaf=render_leaf,
        noun="model",
        count_provider=_counts,
    )


def _counts() -> tuple[int, int]:
    """Return (total leaf count, distinct model count) from state."""
    entries = len(state.fitted_models)
    models = len({mk for (_p, _t, mk) in state.fitted_models})
    return entries, models


# --------------------------------------------------------------------------- #
# Panel feeders
# --------------------------------------------------------------------------- #


def _entries() -> list[tuple[str, str, str]]:
    """Return the (model_key, period, team) triples for the current fitted models."""
    # Engine key == model_key; period and team map directly.
    return [(mk, period, team) for (period, team, mk) in state.fitted_models]


def rebuild() -> None:
    """Rebuild the full results panel from the current fitted-model state.

    Notes
    -----
    Calls ``state.panel.rebuild``; no-op when ``state.panel`` is None.
    """
    if state.panel is not None:
        state.panel.rebuild(_entries())


def refresh_leaf(model_key: str, period: str, team: str) -> None:
    """Re-render one fitted leaf in the results panel.

    Parameters
    ----------
    model_key : str
        Registry key for the model.
    period : str
        Internal period string for the leaf.
    team : str
        Team name for the leaf.

    Notes
    -----
    No-op when ``state.panel`` is None.
    """
    if state.panel is not None:
        state.panel.refresh_leaf(model_key, period, team)


def refresh_model_leaves(model_key: str) -> None:
    """Re-render every fitted leaf of *model_key* (output-set changed).

    Parameters
    ----------
    model_key : str
        Registry key for the model whose leaves should be refreshed.

    Notes
    -----
    No-op when ``state.panel`` is None.
    """
    if state.panel is None:
        return
    for period, team, mk in list(state.fitted_models):
        if mk == model_key:
            state.panel.refresh_leaf(model_key, period, team)


def clear() -> None:
    """Clear all leaves from the results panel.

    Notes
    -----
    No-op when ``state.panel`` is None.
    """
    if state.panel is not None:
        state.panel.clear()


# --------------------------------------------------------------------------- #
# Leaf renderer
# --------------------------------------------------------------------------- #


def render_leaf(key, period_internal: str, team: str, leaf_tag: str) -> None:
    """Render one fitted-model leaf body into the DPG container *leaf_tag*.

    Layout per leaf:
    - header line: Model | Period | Team
    - fit-params summary (public keys only, ``_``-prefixed keys are skipped)
    - checked outputs:
        0 checked: prompt to select an output
        1 checked: output rendered inline
        2+ checked: one DPG tab per checked output

    Tuple-valued outputs (e.g. per-team properties) get a nested tab bar whose
    tabs are labelled from ``fit_params["_team_names"]`` when present, or
    ``[i]`` otherwise.

    Parameters
    ----------
    key : str or int
        Model registry key (converted to ``str``).
    period_internal : str
        Internal period string resolved via ``period_internal_to_display``.
    team : str
        Team name, e.g. "Home", "Away", "BothTeams".
    leaf_tag : str
        DPG parent container tag to render into.

    Notes
    -----
    Reads ``state.fitted_models``, ``state.output_checked``, and
    ``state.output_results`` (via ``_compute_output``).
    """
    import dearpygui.dearpygui as dpg

    model_key = str(key)
    entry = state.fitted_models.get((period_internal, team, model_key))
    period_label = period_internal_to_display(period_internal)
    dpg.add_text(
        f"Model: {labels.display_name(model_key)}  |  Period: {period_label}  |  Team: {team}",
        parent=leaf_tag,
    )
    if entry is None:
        dpg.add_text("No fit.", parent=leaf_tag)
        return
    model_obj, fit_params = entry

    # Fit-params summary (skip "_"-prefixed internal keys).
    visible = {k: v for k, v in (fit_params or {}).items() if not str(k).startswith("_")}
    if visible:
        summary = ", ".join(f"{k}={v}" for k, v in visible.items())
        dpg.add_text(f"Params: {summary}", parent=leaf_tag)

    outputs = MODEL_REGISTRY[model_key].get("outputs", {})
    checked = [ok for ok in outputs if state.output_checked.get((model_key, ok), False)]
    if not checked:
        dpg.add_text("No outputs selected — tick one in Step 2.", parent=leaf_tag)
        return

    team_names = (fit_params or {}).get("_team_names")
    if len(checked) == 1:
        _render_output(
            leaf_tag,
            model_key,
            period_internal,
            team,
            model_obj,
            checked[0],
            outputs[checked[0]],
            team_names,
        )
        return

    # 2+ checked outputs get a nested tab bar, one tab per output.
    bar = f"model_leaf_outbar_{_clean(model_key)}_{_clean(period_internal)}_{_clean(team)}"
    with dpg.tab_bar(tag=bar, parent=leaf_tag):
        for ok in checked:
            tab_tag = f"{bar}_{_clean(ok)}"
            with dpg.tab(label=outputs[ok].get("label", ok), tag=tab_tag):
                _render_output(
                    tab_tag,
                    model_key,
                    period_internal,
                    team,
                    model_obj,
                    ok,
                    outputs[ok],
                    team_names,
                )


def _render_output(
    parent_tag, model_key, period, team, model_obj, out_key, out_desc, team_names
) -> None:
    """Render one checked output into *parent_tag* (tuple results get a nested tab bar).

    Parameters
    ----------
    parent_tag : str
        DPG container to render into.
    model_key : str
        Registry key for the model.
    period, team : str
        Leaf coordinates; forwarded to ``_compute_output`` for cache lookup.
    model_obj : object
        Fitted model instance.
    out_key : str
        Output key in ``MODEL_REGISTRY[model_key]["outputs"]``.
    out_desc : dict
        Output descriptor from the registry.
    team_names : list[str] or None
        Per-element labels for tuple-valued outputs; ``None`` falls back to
        positional ``[i]`` labels.
    """
    import dearpygui.dearpygui as dpg

    try:
        result = _compute_output(model_key, period, team, model_obj, out_key, out_desc)
    except Exception as exc:  # noqa: BLE001 -- surface upstream errors in-place
        logger.exception("model: output compute failed for %s/%s", model_key, out_key)
        dpg.add_text(f"Could not compute output: {exc}", parent=parent_tag)
        return

    if isinstance(result, tuple):
        bar = f"{parent_tag}_tuplebar"
        with dpg.tab_bar(tag=bar, parent=parent_tag):
            for i, elem in enumerate(result):
                label = str(team_names[i]) if team_names and i < len(team_names) else f"[{i}]"
                tab_tag = f"{bar}_{i}"
                with dpg.tab(label=label, tag=tab_tag):
                    _render_value(tab_tag, elem)
        return
    _render_value(parent_tag, result)


def _render_value(parent_tag, value) -> None:
    """Project *value* to a frame and render it, or show a type label for non-arrays."""
    import dearpygui.dearpygui as dpg

    frame, columns = _project(value)
    if frame is None:
        dpg.add_text(f"Result: {type(value).__name__}", parent=parent_tag)
        return
    render_array_view(parent_tag, frame, columns=columns)


def _project(value):
    """Return (frame, columns) for the array view.

    Projection preference order:
    ``.to_dataframe()`` -> bare ``DataFrame`` (reset_index) -> ``.property``
    (1-D as one named column, 2-D as-is) -> bare ndarray -> ``(None, None)``
    for non-array types.

    Parameters
    ----------
    value : object
        A fitted-model output value of unknown type.

    Returns
    -------
    frame : pd.DataFrame or np.ndarray or None
        Projectable data, or ``None`` when no projection applies.
    columns : list[str] or None
        Column headers when the frame is a raw ndarray, otherwise ``None``.
    """
    to_df = getattr(value, "to_dataframe", None)
    if callable(to_df):
        try:
            return to_df(), None
        except Exception:  # noqa: BLE001 -- fall through to other projections
            logger.exception("model: to_dataframe() failed")
    if isinstance(value, pd.DataFrame):
        return value.reset_index(), None
    prop = getattr(value, "property", None)
    if isinstance(prop, np.ndarray):
        # Team-level properties (e.g. convex hull area) come back as a 1-D
        # (T,) array; render it as a single named column.
        if prop.ndim == 1:
            name = getattr(value, "name", None) or "Value"
            return prop.reshape(-1, 1), ["Frame", str(name)]
        if prop.ndim == 2:
            cols = ["Frame"] + [f"P{i}" for i in range(prop.shape[1])]
            return prop, cols
    if isinstance(value, np.ndarray):
        return value, None
    return None, None


def _compute_output(model_key, period, team, model_obj, out_key, out_desc):
    """Call the output method (lazily) and cache the result in ``state.output_results``.

    Parameters
    ----------
    model_key, period, team, out_key : str
        Cache key components.
    model_obj : object
        Fitted model instance.
    out_desc : dict
        Output descriptor; ``out_desc["method"]`` names the method to call.

    Returns
    -------
    object
        The return value of ``getattr(model_obj, method_name)()``.

    Notes
    -----
    Writes to ``state.output_results``; re-fitting the same key clears the cache
    via ``execute.py``.
    """
    cache_key = (model_key, period, team, out_key)
    if cache_key not in state.output_results:
        method_name = out_desc.get("method", out_key)
        state.output_results[cache_key] = getattr(model_obj, method_name)()
    return state.output_results[cache_key]


def _clean(s) -> str:
    """Normalise *s* to a DPG-safe tag fragment (lowercase, punctuation to underscores)."""
    out = str(s).lower()
    for ch in (" ", "-", "/", ".", "(", ")", "|", ","):
        out = out.replace(ch, "_")
    return out


# --------------------------------------------------------------------------- #
# Export payloads: no-arg callables that return a list of 7-tuples:
#   (period_internal, team_name, model_obj, selected_player_ids,
#    method_name, fit_params, display_name)
# --------------------------------------------------------------------------- #


def _tuples_for_leaf(model_key, period, team) -> list[tuple]:
    """Return one 7-tuple per checked output of the (model_key, period, team) leaf.

    A "BothTeams" leaf carries no per-leaf player selection, so ``selected`` is
    empty and the export resolver handles per-team player enumeration. A
    single-team leaf with no player ticks selected exports nothing.

    Parameters
    ----------
    model_key : str
        Registry key for the model.
    period, team : str
        Leaf coordinates.

    Returns
    -------
    list[tuple]
        Each tuple: (period, team, model_obj, selected_ids, method_name,
        fit_params, display_name). Empty when no fit exists or no outputs are
        checked and the leaf is not a multi-XY leaf.
    """
    from floodlight_gui.tabs.model import select as selectors

    entry = state.fitted_models.get((period, team, model_key))
    if entry is None:
        return []
    model_obj, fit_params = entry
    selected = selectors.selected_player_ids(team)
    is_multi = team == "BothTeams"
    if not selected and not is_multi:
        return []
    outputs = MODEL_REGISTRY[model_key].get("outputs", {})
    name = labels.display_name(model_key)
    out: list[tuple] = []
    for out_key, out_desc in outputs.items():
        if not state.output_checked.get((model_key, out_key), False):
            continue
        method_name = out_desc.get("method", out_key)
        out.append((period, team, model_obj, selected, method_name, fit_params, name))
    return out


def single_payload() -> list[tuple]:
    """Return the checked outputs of the active leaf (empty when none is visible).

    Returns
    -------
    list[tuple]
        7-tuples for the currently active leaf; empty list when no leaf is active.
    """
    if state.panel is None:
        return []
    active = state.panel.active_leaf()
    if active is None:
        return []
    model_key, period, team = str(active[0]), active[1], active[2]
    return _tuples_for_leaf(model_key, period, team)


def broadcast_payload() -> list[tuple]:
    """Return the checked outputs across every fitted leaf of the active model.

    Returns
    -------
    list[tuple]
        7-tuples for all (period, team) leaves of the currently active model key.
        Each leaf uses its own team's player ticks.
    """
    if state.panel is None:
        return []
    active = state.panel.active_leaf()
    if active is None:
        return []
    model_key = str(active[0])
    out: list[tuple] = []
    for period, team, mk in state.fitted_models:
        if mk == model_key:
            # Each leaf exports with its own team's player ticks.
            out.extend(_tuples_for_leaf(model_key, period, team))
    return out
