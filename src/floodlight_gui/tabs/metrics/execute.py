"""Metrics tab compute producer: the ``metrics_compute_btn`` DPG callback.

Resolves the active metric, collects inputs and params into kwargs, delegates
pure computation to ``calculate_metric``, caches the result in ``state.results``,
and refreshes the Results panel. DPG-aware layer; BLE001 at the callback boundary.

Two compute flows:

Single: one (period, team) leaf. For non-XY metrics the period and team come
from the picked model-output metadata rather than the Step-1 combos.

Broadcast: activated when a Step-1 combo holds "All" and the metric has an XY
or model-output input. XY metrics iterate the period x team cross-product;
model-output metrics iterate the fitted leaves. Combos are overridden per
iteration (restored in a ``finally``) so collectors resolve the right scope.
Stages all results and commits atomically on full success; any exception discards
the staged dict, leaving ``state.results`` unchanged.
"""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.periods import period_internal_to_display
from floodlight_gui.engine.calculate_metric import calculate_metric
from floodlight_gui.registry.metrics import METRICS_REGISTRY
from floodlight_gui.tabs._shared.broadcast import ALL_SENTINEL, bridge_period_to_internal
from floodlight_gui.tabs._shared.error_helpers import friendly_error_message, show_error_modal
from floodlight_gui.tabs._shared.state_views import render_error
from floodlight_gui.tabs.metrics import params, results, state

logger = logging.getLogger(__name__)

PERIOD_COMBO = "metrics_period_combo"
TEAM_COMBO = "metrics_team_combo"
STATUS = "metrics_compute_status"
STATE_VIEW = "metrics_state_view_container"


def on_compute(sender=None, app_data=None, user_data=None) -> None:  # noqa: ARG001 -- DPG cb
    """Compute button callback: resolve the active metric and run single or broadcast flow.

    Selects the flow (single vs. broadcast) based on the Step-1 combo values
    and the descriptor's input class. All exceptions are caught at this boundary
    so a compute failure cannot crash the DPG render loop.

    Notes
    -----
    Side-effects: writes ``state.results`` (via ``_compute_single`` or
    ``_compute_broadcast``), sets the ``STATUS`` widget, and updates the
    Results panel via ``results.refresh_leaf`` / ``results.rebuild``.
    Multi-XY metrics are rejected here before any compute is attempted.
    """
    dpg.set_value(STATUS, "Computing...")
    try:
        metric_key = state.selected_metric_key
        if not metric_key:
            return
        descriptor = METRICS_REGISTRY[metric_key]

        if int(descriptor.get("fit_xy_arity", 1)) > 1:
            show_error_modal(
                STATE_VIEW,
                ValueError(
                    f"{descriptor.get('display_name', metric_key)} needs multiple "
                    f"XY inputs, which the single-team metrics flow does not support."
                ),
                context="Multi-XY metric",
            )
            dpg.set_value(STATUS, "Not supported: multi-XY metric.")
            return

        if _should_broadcast(descriptor):
            _compute_broadcast(metric_key, descriptor)
        else:
            _compute_single(metric_key, descriptor)
    except Exception as exc:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("metrics: compute failed")
        dpg.set_value(STATUS, friendly_error_message(exc, context="Compute failed."))
        if dpg.does_item_exist(STATE_VIEW):
            render_error(STATE_VIEW, exc)
            dpg.configure_item(STATE_VIEW, show=True)
        show_error_modal(STATE_VIEW, exc, context="Compute failed.")


# --------------------------------------------------------------------------- #
# Flow selection
# --------------------------------------------------------------------------- #


def _has_xy_input(descriptor: dict) -> bool:
    """Return True if any input in the descriptor has type "XY"."""
    return any(d.get("type") == "XY" for d in descriptor.get("inputs", {}).values())


def _has_model_output_input(descriptor: dict) -> bool:
    """Return True if any input in the descriptor is a model-output type."""
    return any(
        d.get("type") in params._MODEL_OUTPUT_TYPES for d in descriptor.get("inputs", {}).values()
    )


def _should_broadcast(descriptor: dict) -> bool:
    """Return True when the combo selection is "All" and the metric has a broadcastable input.

    XY metrics broadcast over the period x team cross-product. Metrics that
    consume a model output broadcast over the fitted leaves of that output.
    Pure-param metrics (no XY or model-output input) never broadcast.
    """
    if ALL_SENTINEL not in (_combo(PERIOD_COMBO), _combo(TEAM_COMBO)):
        return False
    return _has_xy_input(descriptor) or _has_model_output_input(descriptor)


# --------------------------------------------------------------------------- #
# Single compute
# --------------------------------------------------------------------------- #


def _compute_single(metric_key: str, descriptor: dict) -> None:
    """Run one compute leaf and store the result in ``state.results``.

    Notes
    -----
    Side-effects: writes ``state.results[(metric_key, period, team)]``,
    calls ``results.refresh_leaf``, and sets the ``STATUS`` widget.
    """
    if _has_xy_input(descriptor):
        period_internal = bridge_period_to_internal(_combo(PERIOD_COMBO))
        team = _combo(TEAM_COMBO)
        cache_period, cache_team = period_internal, team
    else:
        # Non-XY: derive period+team from the picked model output's metadata so
        # each distinct source accumulates its own leaf (never collapses to "All").
        period_internal = None
        team = None
        cache_period, cache_team = _derive_source_key(descriptor)

    kwargs = params.collect_kwargs(metric_key, period_internal=period_internal, team=team)
    state.results[(metric_key, cache_period, cache_team)] = calculate_metric(descriptor, kwargs)

    results.refresh_leaf(metric_key, cache_period, cache_team)
    dpg.set_value(STATUS, f"Computed {descriptor['display_name']} for {cache_team}")


def _derive_source_key(descriptor: dict) -> tuple[str, str]:
    """Derive a (period, source-label) cache key from the picked model-output inputs.

    For non-XY metrics the cache leaf is keyed by the period and a composite
    label combining every picked output (team + output key). This keeps distinct
    sources in distinct leaves rather than collapsing them under "All".
    """
    period = "all"
    source_parts: list[str] = []
    records = params.available_outputs()
    for input_name, input_desc in descriptor.get("inputs", {}).items():
        if input_desc.get("type") not in params._MODEL_OUTPUT_TYPES:
            continue
        widget = state.input_widgets.get(input_name, {})
        label = dpg.get_value(widget["source_combo"]) if widget.get("source_combo") else ""
        record = next((r for r in records if r["label"] == label), None)
        if record is None:
            continue
        period = record["period"]
        source_parts.append(f"{record['team']}:{record['output_key']}")
    source_label = " + ".join(source_parts) if source_parts else "result"
    return period, source_label


# --------------------------------------------------------------------------- #
# Broadcast compute (transactional)
# --------------------------------------------------------------------------- #


def _broadcast_combos(descriptor: dict) -> list[tuple[str, str, str]]:
    """Return (period_internal, team, period_display) tuples to iterate in broadcast mode.

    XY metrics expand to the full period x team cross-product. Metrics that read
    a model output expand to that output's fitted leaves. The period_display value
    is used to drive the Step-1 combo per iteration so collectors resolve the
    correct scope.
    """
    if _has_xy_input(descriptor):
        return [
            (bridge_period_to_internal(pd), team, pd)
            for pd in _expand_periods()
            for team in _expand_teams()
        ]
    return [
        (period_internal, team, period_internal_to_display(period_internal))
        for (period_internal, team) in params.scoped_output_leaves(descriptor)
    ]


def _compute_broadcast(metric_key: str, descriptor: dict) -> None:
    """Run all broadcast leaves and commit atomically to ``state.results``.

    Stages results into a local dict. Any exception during iteration leaves
    ``state.results`` untouched (the staged dict is discarded). On full success
    all staged entries are committed in one ``update`` call.

    Notes
    -----
    Side-effects: writes ``state.results``, calls ``results.rebuild`` and
    ``results.refresh_leaf``, and sets the ``STATUS`` widget.
    """
    combos = _broadcast_combos(descriptor)
    if not combos:
        dpg.set_value(
            STATUS,
            "Nothing to compute for 'All' — no fitted model outputs in scope.",
        )
        return

    staged: dict[tuple[str, str, str], dict] = {}
    saved_period = _combo(PERIOD_COMBO)
    saved_team = _combo(TEAM_COMBO)
    try:
        for period_internal, team, period_display in combos:
            _set_combo(PERIOD_COMBO, period_display)
            _set_combo(TEAM_COMBO, team)
            kwargs = params.collect_kwargs(metric_key, period_internal=period_internal, team=team)
            staged[(metric_key, period_internal, team)] = calculate_metric(descriptor, kwargs)
    finally:
        _set_combo(PERIOD_COMBO, saved_period)
        _set_combo(TEAM_COMBO, saved_team)

    # Commit all on full success; any exception above already aborted, leaving
    # state.results untouched (the staged dict is discarded).
    state.results.update(staged)
    results.rebuild()
    # Land on the last computed leaf.
    if staged:
        last = next(reversed(staged))
        results.refresh_leaf(*last)
    dpg.set_value(STATUS, f"Computed {descriptor['display_name']} for {len(staged)} combos")


def _expand_periods() -> list[str]:
    """Return the display-form period labels to iterate: the selected value, or all divisions."""
    val = _combo(PERIOD_COMBO)
    if val != ALL_SENTINEL:
        return [val]
    divisions = state.app_instance.get_temporal_divisions() or []
    return [period_internal_to_display(p) for p in divisions]


def _expand_teams() -> list[str]:
    """Return the team names to iterate: the selected value, or all teams."""
    val = _combo(TEAM_COMBO)
    if val != ALL_SENTINEL:
        return [val]
    return list(state.app_instance.get_team_names() or [])


# --------------------------------------------------------------------------- #
# Combo helpers
# --------------------------------------------------------------------------- #


def _combo(tag: str) -> str:
    """Return the current string value of a DPG combo, or "" when the tag does not exist."""
    return dpg.get_value(tag) if dpg.does_item_exist(tag) else ""


def _set_combo(tag: str, value: str) -> None:
    """Set a DPG combo value; no-op when the tag does not exist."""
    if dpg.does_item_exist(tag):
        dpg.set_value(tag, value)
