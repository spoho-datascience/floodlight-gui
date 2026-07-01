"""Metrics tab params layer: build per-param/input widgets and collect call kwargs.

Sits between ``select.py`` (what to operate on) and ``execute.py`` (the compute
callbacks). DPG-aware: imports ``dearpygui`` at module scope. Backend modules must
not import this module.

Model-output inputs are scoped by the Step-1 period/team selection in
``select.py``; the collected kwargs are passed verbatim to the upstream floodlight
metric function without modification.
"""

from __future__ import annotations

import importlib
import logging

import dearpygui.dearpygui as dpg
import numpy as np

from floodlight_gui.registry.metrics import METRICS_REGISTRY
from floodlight_gui.registry.models import MODEL_REGISTRY
from floodlight_gui.tabs._shared.descriptor_widgets import build_param_widget
from floodlight_gui.tabs._shared.state_views import render_empty
from floodlight_gui.tabs.metrics import state
from floodlight_gui.tabs.metrics.select import _step1_scope
from floodlight_gui.theme import INFO

logger = logging.getLogger(__name__)

INPUTS_CONTAINER = "metrics_inputs_container"
PARAMS_CONTAINER = "metrics_params_container"
STATE_VIEW = "metrics_state_view_container"

# Param types build_param_widget does NOT handle -- we render these ourselves.
_SELF_RENDERED_PARAM_TYPES = {"list[tuple[float, float]]", "zone_list", "ndarray", "list[str]"}

_MODEL_OUTPUT_TYPES = {"sig", "PlayerProperty|TeamProperty"}

# Only outputs returning a floodlight Property are queryable as metric inputs;
# scalar-returning outputs (e.g. nearest_mate team_spread -> float) would crash
# the `.property` collectors, so they never populate the model-output combos.
_PROPERTY_RETURN_TYPES = frozenset({"PlayerProperty", "TeamProperty", "DyadicProperty"})

# fit slots that are team-agnostic (arity>1 fits store under "BothTeams") --
# they survive the Step-1 team filter so a specific-team selection still sees them.
_TEAM_AGNOSTIC_SLOTS = ("BothTeams",)


# --------------------------------------------------------------------------- #
# Model-output discovery (cross-tab READ -- import-guarded)
# --------------------------------------------------------------------------- #


def _model_state():
    """Return the Models tab state module, or None on ImportError."""
    try:
        from floodlight_gui.tabs.model import state as model_state
    except ImportError:
        return None
    return model_state


def available_outputs() -> list[dict]:
    """Return model outputs queryable as metric inputs, scoped by Step 1.

    Reads ``model.state.fitted_models`` and ``model.state.output_checked`` to
    enumerate outputs eligible as metric data inputs. The current Step-1
    period/team selection (from ``select._step1_scope``) scopes which fitted
    leaves are returned; "All" on an axis drops that axis's filter. Records are
    deduped by label (first wins) so an "All" selection that collapses N leaves
    to one label never produces duplicate DPG combo items. Only
    Property-returning outputs are eligible; scalar outputs would crash the
    ``.property`` collectors.

    Each record in the returned list has the shape::

        {
            "period": period_internal,
            "team": team_or_BothTeams,
            "model_key": str,
            "output_key": str,
            "model_obj": obj,
            "label": "Model -> Output",
            "value_key": "period|team|model_key|output_key",
        }

    Returns
    -------
    list[dict]
        One record per eligible (period, team, model, output) leaf, deduped by
        label. Empty when no models are fitted, no outputs are checked, or the
        model state module is unavailable.
    """
    ms = _model_state()
    if ms is None:
        return []
    fitted = getattr(ms, "fitted_models", {}) or {}
    checked = getattr(ms, "output_checked", {}) or {}

    filter_period, period_internal, filter_team, raw_team = _step1_scope()

    records: list[dict] = []
    seen_labels: set[str] = set()
    for (period, team, model_key), fit_entry in fitted.items():
        descriptor = MODEL_REGISTRY.get(model_key)
        if descriptor is None:
            continue
        # Step-1 scope filter (axis skipped when the combo holds "All").
        if filter_period and period != period_internal:
            continue
        if filter_team and team != raw_team and team not in _TEAM_AGNOSTIC_SLOTS:
            continue
        model_obj = fit_entry[0] if isinstance(fit_entry, tuple) else fit_entry
        model_display = descriptor.get("display_name", model_key)
        for output_key, output_desc in descriptor.get("outputs", {}).items():
            if not checked.get((model_key, output_key), False):
                continue
            if output_desc.get("returns", "") not in _PROPERTY_RETURN_TYPES:
                continue
            out_label = output_desc.get("label", output_key)
            label = f"{model_display} -> {out_label}"
            if label in seen_labels:
                continue
            seen_labels.add(label)
            records.append(
                {
                    "period": period,
                    "team": team,
                    "model_key": model_key,
                    "output_key": output_key,
                    "model_obj": model_obj,
                    "label": label,
                    "value_key": f"{period}|{team}|{model_key}|{output_key}",
                }
            )
    return records


def scoped_output_leaves(descriptor: dict) -> list[tuple[str, str]]:
    """Return (period_internal, team) leaves to broadcast a non-XY metric over on "All".

    Inspects the FIRST model-output input in *descriptor* to find which fitted
    leaves match the currently picked (model, output) within the current Step-1
    scope. "All" on an axis expands to every leaf on that axis; BothTeams slots
    always survive the team filter. Used by the broadcast-compute path so
    selecting "All" computes the metric once per leaf instead of collapsing to a
    single result.

    Parameters
    ----------
    descriptor : dict
        METRICS_REGISTRY entry for the active metric.

    Returns
    -------
    list[tuple[str, str]]
        Ordered (period_internal, team) pairs from the matched fitted leaves.
        Empty when the metric has no model-output input, nothing is picked, or
        the model state is unavailable.
    """
    ms = _model_state()
    if ms is None:
        return []
    fitted = getattr(ms, "fitted_models", {}) or {}
    checked = getattr(ms, "output_checked", {}) or {}
    filter_period, period_internal, filter_team, raw_team = _step1_scope()
    records = available_outputs()

    for input_name, input_desc in descriptor.get("inputs", {}).items():
        if input_desc.get("type") not in _MODEL_OUTPUT_TYPES:
            continue
        widget = state.input_widgets.get(input_name, {})
        label = dpg.get_value(widget["source_combo"]) if widget.get("source_combo") else ""
        rec = next((r for r in records if r["label"] == label), None)
        if rec is None:
            return []
        model_key, output_key = rec["model_key"], rec["output_key"]
        if not checked.get((model_key, output_key), False):
            return []
        leaves: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for period, team, mk in fitted:
            if mk != model_key:
                continue
            if filter_period and period != period_internal:
                continue
            if filter_team and team != raw_team and team not in _TEAM_AGNOSTIC_SLOTS:
                continue
            if (period, team) not in seen:
                seen.add((period, team))
                leaves.append((period, team))
        return leaves
    return []


def _output_record_by_value_key(value_key: str) -> dict | None:
    """Return the available-output record matching *value_key*, or None."""
    for rec in available_outputs():
        if rec["value_key"] == value_key:
            return rec
    return None


def _resolve_output_property(record: dict):
    """Call the fitted model's output method and return the Property object."""
    output_desc = MODEL_REGISTRY[record["model_key"]]["outputs"][record["output_key"]]
    method_name = output_desc.get("method", record["output_key"])
    return getattr(record["model_obj"], method_name)()


# --------------------------------------------------------------------------- #
# Inputs rendering
# --------------------------------------------------------------------------- #


def rebuild_inputs(metric_key: str) -> None:
    """Rebuild the inputs container for *metric_key* and register widgets.

    Clears ``INPUTS_CONTAINER`` and ``state.input_widgets``, then renders one
    widget per entry in the descriptor's ``inputs`` dict. Shows the "no fitted
    models" banner in ``STATE_VIEW`` when a model-output input is required but
    no eligible outputs are available.

    Parameters
    ----------
    metric_key : str
        Key into ``METRICS_REGISTRY``.

    Notes
    -----
    Side-effects: deletes and recreates children of ``INPUTS_CONTAINER``;
    writes ``state.input_widgets``; may show/hide ``STATE_VIEW``.
    """
    state.input_widgets = {}
    if dpg.does_item_exist(INPUTS_CONTAINER):
        dpg.delete_item(INPUTS_CONTAINER, children_only=True)

    descriptor = METRICS_REGISTRY[metric_key]
    inputs = descriptor.get("inputs", {})
    records = available_outputs()
    output_labels = [r["label"] for r in records]
    needs_model_output = any(d.get("type") in _MODEL_OUTPUT_TYPES for d in inputs.values())

    if needs_model_output and not output_labels:
        render_empty(
            STATE_VIEW,
            "No fitted models — fit a model in the Models tab first.",
        )
        dpg.configure_item(STATE_VIEW, show=True)
    else:
        _clear_state_view()

    if not inputs:
        dpg.add_text("No inputs required.", parent=INPUTS_CONTAINER, color=INFO)
        return

    for input_name, input_desc in inputs.items():
        itype = input_desc.get("type")
        if itype == "XY":
            dpg.add_text(
                f"{input_name}: driven by the Step 1 period/team selection.",
                parent=INPUTS_CONTAINER,
                color=INFO,
            )
            state.input_widgets[input_name] = {"type": "XY"}
        elif itype in _MODEL_OUTPUT_TYPES:
            _render_model_output_input(input_name, itype, output_labels)
        elif itype == "ndarray":
            tag = f"metrics_input__{input_name}"
            dpg.add_text(f"{input_name} (array):", parent=INPUTS_CONTAINER)
            dpg.add_input_text(
                tag=tag, parent=INPUTS_CONTAINER, width=300, hint="(x, y), (x, y), ..."
            )
            state.input_widgets[input_name] = {"type": "ndarray", "tag": tag}
        else:
            tag = f"metrics_input__{input_name}"
            dpg.add_text(f"{input_name}:", parent=INPUTS_CONTAINER)
            dpg.add_input_text(tag=tag, parent=INPUTS_CONTAINER, width=300)
            state.input_widgets[input_name] = {"type": itype, "tag": tag}


def _render_model_output_input(input_name: str, itype: str, output_labels: list[str]) -> None:
    """Render source combo (and column combo for sig type) for a model-output input.

    Registers the created widget tags in ``state.input_widgets[input_name]``.
    For ``sig`` inputs, immediately populates the column combo via
    ``_refresh_columns``.
    """
    source_tag = f"metrics_input__{input_name}__source"
    dpg.add_text(f"{input_name} (model output):", parent=INPUTS_CONTAINER)
    dpg.add_combo(
        items=output_labels,
        default_value=output_labels[0] if output_labels else "",
        tag=source_tag,
        parent=INPUTS_CONTAINER,
        width=320,
        callback=_on_source_change if itype == "sig" else None,
        user_data=input_name,
    )
    column_tag = None
    if itype == "sig":
        column_tag = f"metrics_input__{input_name}__column"
        dpg.add_text("  column:", parent=INPUTS_CONTAINER)
        dpg.add_combo(items=[], tag=column_tag, parent=INPUTS_CONTAINER, width=200)
    state.input_widgets[input_name] = {
        "type": itype,
        "source_combo": source_tag,
        "column_combo": column_tag,
    }
    if itype == "sig":
        _refresh_columns(input_name)


def _on_source_change(sender, app_data, user_data) -> None:  # noqa: ARG001 -- DPG cb
    """Refresh the column combo when the source model-output combo changes (DPG callback)."""
    try:
        _refresh_columns(user_data)
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("metrics: column refresh failed for input %s", user_data)


def _refresh_columns(input_name: str) -> None:
    """Repopulate the per-input column combo from the picked output's shape."""
    widget = state.input_widgets.get(input_name, {})
    source_tag = widget.get("source_combo")
    column_tag = widget.get("column_combo")
    if not (source_tag and column_tag and dpg.does_item_exist(column_tag)):
        return
    label = dpg.get_value(source_tag) if dpg.does_item_exist(source_tag) else ""
    record = next((r for r in available_outputs() if r["label"] == label), None)
    columns: list[str] = []
    if record is not None:
        try:
            prop = _resolve_output_property(record)
            arr = np.asarray(prop.property)
            n = arr.shape[1] if arr.ndim >= 2 else 1
            columns = [f"P{i}" for i in range(n)]
        except Exception:  # noqa: BLE001 -- defensive: bad output shape
            logger.exception("metrics: failed to derive columns for %s", input_name)
    dpg.configure_item(column_tag, items=columns, default_value=columns[0] if columns else "")


# --------------------------------------------------------------------------- #
# Params rendering
# --------------------------------------------------------------------------- #


def rebuild_params(metric_key: str) -> None:
    """Rebuild the params container for *metric_key*.

    Clears ``PARAMS_CONTAINER`` and renders one widget per entry in the
    descriptor's ``params`` dict. Self-rendered param types (zone bounds, arrays,
    label lists) use ``_render_freeform_param``; all others delegate to
    ``build_param_widget``.

    Parameters
    ----------
    metric_key : str
        Key into ``METRICS_REGISTRY``.

    Notes
    -----
    Side-effect: deletes and recreates children of ``PARAMS_CONTAINER``.
    """
    if dpg.does_item_exist(PARAMS_CONTAINER):
        dpg.delete_item(PARAMS_CONTAINER, children_only=True)

    descriptor = METRICS_REGISTRY[metric_key]
    params = descriptor.get("params", {})
    upstream = _resolve_callable(descriptor["function_path"])

    if not params:
        dpg.add_text("No configurable parameters.", parent=PARAMS_CONTAINER, color=INFO)
        return

    for pname, pdesc in params.items():
        if pdesc.get("type") in _SELF_RENDERED_PARAM_TYPES:
            _render_freeform_param(pname, pdesc)
        else:
            build_param_widget(
                pname,
                pdesc,
                PARAMS_CONTAINER,
                upstream_callable=upstream,
                tag=f"metrics_param__{pname}",
                param_container="params",
            )


def _render_freeform_param(pname: str, pdesc: dict) -> None:
    """Render a free-text input for zone bounds, ndarray, or label-list params.

    The descriptor ``example`` is shown as the input hint; no separate
    description line is added because the hint already conveys the expected
    format.
    """
    tag = f"metrics_param__{pname}"
    example = pdesc.get("example", "")
    dpg.add_text(f"{pname}:", parent=PARAMS_CONTAINER)
    dpg.add_input_text(
        tag=tag, parent=PARAMS_CONTAINER, width=320, hint=example or "comma/space-separated"
    )


# --------------------------------------------------------------------------- #
# Collect -- read inputs + params back into a kwargs dict for the upstream call
# --------------------------------------------------------------------------- #


def collect_kwargs(metric_key: str, *, period_internal: str | None, team: str | None) -> dict:
    """Read every input and param widget into a kwargs dict for the metric call.

    Parameters
    ----------
    metric_key : str
        Key into ``METRICS_REGISTRY``.
    period_internal : str or None
        Internal period string from Step 1; resolves the ``XY`` input. The
        broadcast loop passes the per-leaf period/team here.
    team : str or None
        Team string from Step 1; resolves the ``XY`` input.

    Returns
    -------
    dict
        Kwargs ready to unpack into the upstream floodlight metric function.
        None-valued optional params are omitted unless the descriptor marks them
        required.
    """
    descriptor = METRICS_REGISTRY[metric_key]
    kwargs: dict = {}

    for input_name, input_desc in descriptor.get("inputs", {}).items():
        kwargs[input_name] = _collect_input(input_name, input_desc, period_internal, team)

    for pname, pdesc in descriptor.get("params", {}).items():
        value = _collect_param(pname, pdesc)
        if value is not None or pdesc.get("required"):
            kwargs[pname] = value

    return kwargs


def _collect_input(input_name: str, input_desc: dict, period_internal, team):
    """Read one input widget and return the resolved value for the metric call.

    For ``XY`` inputs, delegates to ``get_xy_for_period_team``. For model-output
    inputs (``sig`` / ``PlayerProperty|TeamProperty``), resolves the selected
    fitted model's output. For ``sig``, the picked column is extracted as a 1-D
    array and passed to the upstream metric unaltered: floodlight's metric
    functions own NaN handling and raise descriptive errors when the signal is
    invalid (e.g. "Signal cannot contain np.nan."). The GUI surfaces that error
    via the compute error boundary so the user can address it deliberately.

    Parameters
    ----------
    input_name : str
        Descriptor input key.
    input_desc : dict
        Descriptor entry for this input (contains ``type``).
    period_internal : str or None
        Resolved period for XY lookup.
    team : str or None
        Resolved team for XY lookup.

    Returns
    -------
    object
        The resolved input value (XY, Property, ndarray, or str).

    Raises
    ------
    ValueError
        If a model-output combo has no selection or no matching output record.
    """
    itype = input_desc.get("type")
    if itype == "XY":
        from floodlight_gui.core.xy_access import get_xy_for_period_team

        return get_xy_for_period_team(state.app_instance, period_internal, team)

    widget = state.input_widgets.get(input_name, {})
    if itype in _MODEL_OUTPUT_TYPES:
        label = dpg.get_value(widget["source_combo"]) if widget.get("source_combo") else ""
        record = next((r for r in available_outputs() if r["label"] == label), None)
        if record is None:
            raise ValueError(f"No model output selected for input '{input_name}'.")
        prop = _resolve_output_property(record)
        if itype == "sig":
            col_label = dpg.get_value(widget["column_combo"]) if widget.get("column_combo") else ""
            col_idx = int(str(col_label).lstrip("P") or "0")
            arr = np.asarray(prop.property)
            return arr[:, col_idx] if arr.ndim >= 2 else arr
        return prop

    # ndarray / string free-text inputs.
    raw = dpg.get_value(widget["tag"]) if widget.get("tag") else ""
    if itype == "ndarray":
        return _parse_xy_tuples(raw)
    return raw


def _collect_param(pname: str, pdesc: dict):
    """Read one param widget and return the typed value for the metric call.

    Returns the descriptor default when the widget tag does not exist (e.g. the
    params container has not been rendered yet).
    """
    ptype = pdesc.get("type")
    tag = f"metrics_param__{pname}"
    if not dpg.does_item_exist(tag):
        return pdesc.get("default")

    raw = dpg.get_value(tag)

    if ptype in ("list[tuple[float, float]]", "zone_list"):
        # Empty -> None so the upstream auto-generates zone names.
        return _parse_zone_bounds(raw) if str(raw).strip() else None
    if ptype == "ndarray":
        return _parse_xy_tuples(raw)
    if ptype == "list[str]":
        parts = [s.strip() for s in str(raw).split(",") if s.strip()]
        return parts or None
    if ptype == "list[int]":
        return [int(s) for s in str(raw).replace(",", " ").split() if s.strip()] or None
    if ptype == "int":
        return int(raw)
    if ptype == "float":
        return float(raw)
    if ptype == "bool":
        return bool(raw)
    if ptype == "enum":
        return None if raw == "None" else raw
    return raw


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #


def _parse_zone_bounds(raw: str):
    """Parse '(lo, hi), (lo, hi), ...' (or bracketed) into a list of float tuples."""
    cleaned = str(raw).strip().strip("[]")
    tuples: list[tuple[float, float]] = []
    for chunk in cleaned.split(")"):
        nums = [n for n in chunk.replace("(", "").replace(",", " ").split() if n]
        if len(nums) >= 2:
            tuples.append((float(nums[0]), float(nums[1])))
    return tuples or None


def _parse_xy_tuples(raw: str):
    """Parse '(x, y), (x, y), ...' into an (M, 2) float ndarray (or None)."""
    pairs = _parse_zone_bounds(raw)
    return np.asarray(pairs, dtype=float) if pairs else None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _resolve_callable(function_path: str):
    """Import and return the callable at *function_path* (dotted module.attr string)."""
    module_path, _, attr = function_path.rpartition(".")
    return getattr(importlib.import_module(module_path), attr)


def _clear_state_view() -> None:
    """Clear and hide the STATE_VIEW container."""
    if dpg.does_item_exist(STATE_VIEW):
        dpg.delete_item(STATE_VIEW, children_only=True)
        dpg.configure_item(STATE_VIEW, show=False)
