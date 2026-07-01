"""Metrics tab layout builder and EventBus wiring.

Responsibility: build the 5-step DPG scaffold and subscribe to events; every
heavier concern delegates to ``select`` / ``params`` / ``execute`` / ``results``.

Layering: DPG-aware (imports ``dearpygui`` at module scope); must not be imported
by backend or test modules.
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.core.periods import period_internal_to_display
from floodlight_gui.registry.metrics import METRICS_REGISTRY
from floodlight_gui.status_bar import FOOTER_HEIGHT_PX
from floodlight_gui.tabs._shared.broadcast import ALL_SENTINEL
from floodlight_gui.tabs._shared.export_action import render_export_action
from floodlight_gui.tabs._shared.help_popup import render_help_button
from floodlight_gui.tabs._shared.selectors import period_team_selector
from floodlight_gui.tabs._shared.tab_header import add_tab_header
from floodlight_gui.tabs.metrics import execute, params, results, select, state
from floodlight_gui.theme import INFO

logger = logging.getLogger(__name__)

# Step labels must match exactly.
STEP_LABELS = (
    "Step 1: Select Data",
    "Step 2: Select Metric",
    "Step 3: Configure Parameters",
    "Step 4: Results",
    "Step 5: Export Results",
)

TYPE_COMBO = "metrics_type_selection"
HELP_GROUP = "metrics_combo_help_group"
SELECTION_SUMMARY = "metrics_selection_summary"
STATE_VIEW = "metrics_state_view_container"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def create_metrics_tab() -> None:
    """Build the Metrics tab into the current DPG container.

    Returns
    -------
    None

    Notes
    -----
    Side-effects:

    - Creates DPG widgets under the tag ``"metrics_tab"`` (tab item), plus child
      tags ``"metrics_content_wrap"``, ``"metrics_selector_container"``,
      ``TYPE_COMBO`` (``"metrics_type_selection"``), ``HELP_GROUP``,
      ``STATE_VIEW``, ``"metrics_inputs_container"``,
      ``"metrics_params_container"``, ``"metrics_compute_btn"``,
      ``"metrics_compute_status"``,
      ``"metrics_results_placeholder"``, ``"metrics_results_info"``,
      ``"metrics_results_outer_tab_bar"``, and the export-action widgets from
      ``render_export_action``.
    - Subscribes to ``Events.APP_INITIALIZED``, ``Events.DATA_LOADED``,
      ``Events.DATA_CLEARED``, ``Events.MODEL_FITTED``, and
      ``Events.MODEL_OUTPUTS_CHANGED`` on the global ``bus`` (priority 10 each).
    - Fires ``_on_metric_change`` once (cold start) to populate Step 3 with the
      default metric.
    """
    state.panel = results.make_panel()

    with dpg.tab(label="Metrics", tag="metrics_tab"):
        add_tab_header("metrics")  # OUTSIDE the scroll wrap
        with dpg.child_window(tag="metrics_content_wrap", height=-FOOTER_HEIGHT_PX):
            dpg.add_text(
                "Compute a floodlight metric: pick data and a metric, "
                "configure it, compute, and export.",
                color=INFO,
            )
            # Hidden state-view slot for empty/error banners.
            with dpg.group(tag=STATE_VIEW, show=False):
                pass

            _build_step1()
            _build_step2()
            _build_step3()
            _build_step4()
            _build_step5()

    _subscribe_events()
    _cold_start()


# --------------------------------------------------------------------------- #
# Step builders
# --------------------------------------------------------------------------- #


def _build_step1() -> None:
    """Render the collapsing Step 1 header with the period/team selector."""
    with (  # noqa: SIM117 -- DPG nested with preserves parent-child registration order
        dpg.collapsing_header(label=STEP_LABELS[0], default_open=True, closable=False),
        dpg.group(tag="metrics_selector_container"),
    ):
        period_team_selector(
            "metrics_selector_container",
            _on_selection_change,
            _on_selection_change,
            tag_prefix="metrics",
        )


def _build_step2() -> None:
    """Render the collapsing Step 2 header with the metric combo and help button group."""
    # noqa: SIM117 below -- DPG nested with preserves parent-child registration order
    with dpg.collapsing_header(label=STEP_LABELS[1], default_open=True, closable=False):  # noqa: SIM117
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=select.metric_display_names(),
                default_value=select.metric_display_names()[0] if METRICS_REGISTRY else "",
                tag=TYPE_COMBO,
                width=260,
                callback=_on_metric_change,
            )
            with dpg.group(tag=HELP_GROUP, horizontal=True):
                pass


def _build_step3() -> None:
    """Render the collapsing Step 3 header with input, params, and the Compute button."""
    with dpg.collapsing_header(label=STEP_LABELS[2], default_open=True, closable=False):
        dpg.add_text(
            "Select model outputs / arrays for this metric's inputs, then "
            "configure its parameters.",
            color=INFO,
        )
        with dpg.group(tag="metrics_inputs_container"):
            pass
        dpg.add_separator()
        with dpg.group(tag="metrics_params_container"):
            pass
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Compute Metric",
                tag="metrics_compute_btn",
                callback=execute.on_compute,
            )
            dpg.add_text("Ready", tag="metrics_compute_status")


def _build_step4() -> None:
    """Render the collapsing Step 4 header with the results panel and outer tab bar."""
    with dpg.collapsing_header(label=STEP_LABELS[3], default_open=True, closable=False):
        dpg.add_text(
            "Computed metrics appear here. Compute a metric to populate this panel.",
            tag="metrics_results_placeholder",
        )
        dpg.add_text("", tag="metrics_results_info", color=INFO, show=False)
        dpg.add_tab_bar(tag="metrics_results_outer_tab_bar")


def _build_step5() -> None:
    """Render the collapsing Step 5 header with the CSV export-action widget bundle."""
    with dpg.collapsing_header(
        label=STEP_LABELS[4], default_open=True, closable=False, tag="metrics_step5_header"
    ):
        render_export_action(
            "metrics_step5_header",
            tab_name="metrics",
            artifact_name="metric",
            mode="all",
            kind="metric",
            payload=results.single_payload,
            label="Export Results",
            secondary_button={
                "mode": "all",
                "kind": "metric_all",
                "payload": results.broadcast_payload,
                "label": "Export all",
            },
            status_tag="metrics_export_status",
            filename_input_tag="metrics_export_filename",
            app=lambda: state.app_instance,
        )


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #


def _on_selection_change(sender=None, app_data=None, user_data=None) -> None:  # noqa: ARG001
    """Update the selection summary and refilter model-output inputs on period/team change."""
    try:
        period = _combo("metrics_period_combo")
        team = _combo("metrics_team_combo")
        if dpg.does_item_exist(SELECTION_SUMMARY):
            dpg.set_value(SELECTION_SUMMARY, f"Selected: {period} / {team}")
        # Refilter the model-output inputs so they reflect the new period/team selection.
        if state.selected_metric_key is not None:
            params.rebuild_inputs(state.selected_metric_key)
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("metrics: selection-change failed")


def _on_metric_change(sender=None, app_data=None, user_data=None) -> None:  # noqa: ARG001
    """Resolve the selected metric key and rebuild the help button, inputs, and params."""
    try:
        display = _combo(TYPE_COMBO) or app_data
        state.selected_metric_key = select.key_for_display(display)
        if state.selected_metric_key is None:
            return
        _rebuild_help()
        params.rebuild_inputs(state.selected_metric_key)
        params.rebuild_params(state.selected_metric_key)
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("metrics: metric-change failed")


def _rebuild_help() -> None:
    """Replace the help button in HELP_GROUP to match the currently selected metric."""
    if not dpg.does_item_exist(HELP_GROUP):
        return
    with contextlib.suppress(SystemError):
        dpg.delete_item(HELP_GROUP, children_only=True)
    key = state.selected_metric_key
    if key is None:
        return
    # parent= targets the help group directly; avoids the container-stack dance.
    with contextlib.suppress(SystemError):
        render_help_button(
            key, METRICS_REGISTRY[key], "METRICS", tag_prefix="metrics", parent=HELP_GROUP
        )


# --------------------------------------------------------------------------- #
# Event wiring (exactly one subscription per event)
# --------------------------------------------------------------------------- #


def _subscribe_events() -> None:
    """Register all EventBus subscribers for the Metrics tab (one per event)."""
    bus.subscribe(Events.APP_INITIALIZED, _on_app_initialized, priority=10)
    bus.subscribe(Events.DATA_LOADED, _on_data_loaded, priority=10)
    bus.subscribe(Events.DATA_CLEARED, _on_data_cleared, priority=10)
    bus.subscribe(Events.MODEL_FITTED, _on_models_changed, priority=10)
    bus.subscribe(Events.MODEL_OUTPUTS_CHANGED, _on_models_changed, priority=10)


def _on_app_initialized(**_payload) -> None:
    """Cache the app reference from the APP_INITIALIZED payload into ``state.app_instance``."""
    app = _payload.get("app")
    state.app_instance = app


def _on_data_loaded(**_payload) -> None:
    """Refresh period/team selectors and rebuild metric inputs after data is loaded."""
    try:
        _refresh_selectors()
        if state.selected_metric_key is not None:
            params.rebuild_inputs(state.selected_metric_key)
            params.rebuild_params(state.selected_metric_key)
    except Exception:  # noqa: BLE001 -- event boundary
        logger.exception("metrics: DATA_LOADED refresh failed")


def _on_data_cleared(**_payload) -> None:
    """Clear the results panel and reset the state-view banner when data is unloaded."""
    try:
        results.clear()
        if dpg.does_item_exist(STATE_VIEW):
            dpg.delete_item(STATE_VIEW, children_only=True)
            dpg.configure_item(STATE_VIEW, show=False)
    except Exception:  # noqa: BLE001 -- event boundary
        logger.exception("metrics: DATA_CLEARED failed")


def _on_models_changed(**_payload) -> None:
    """Available model outputs changed -> refresh the input dropdowns."""
    try:
        if state.selected_metric_key is not None:
            params.rebuild_inputs(state.selected_metric_key)
    except Exception:  # noqa: BLE001 -- event boundary
        logger.exception("metrics: model-change refresh failed")


# --------------------------------------------------------------------------- #
# Selector population + cold start
# --------------------------------------------------------------------------- #


def _refresh_selectors() -> None:
    """Repopulate the period and team combos from the live app, then notify dependents."""
    app = state.app_instance
    if app is None:
        return
    divisions = list(app.get_temporal_divisions() or [])
    period_items = [ALL_SENTINEL] + [period_internal_to_display(p) for p in divisions]
    team_items = [ALL_SENTINEL] + list(app.get_team_names() or [])

    if dpg.does_item_exist("metrics_period_combo"):
        dpg.configure_item("metrics_period_combo", items=period_items, default_value=ALL_SENTINEL)
    if dpg.does_item_exist("metrics_team_combo"):
        dpg.configure_item("metrics_team_combo", items=team_items, default_value=ALL_SENTINEL)
    _on_selection_change()


def _cold_start() -> None:
    """Fire the metric-changed logic once so Step 3 reflects the default metric."""
    if METRICS_REGISTRY:
        _on_metric_change()


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _combo(tag: str) -> str:
    """Return the current value of a combo widget, or an empty string if the tag is absent."""
    return dpg.get_value(tag) if dpg.does_item_exist(tag) else ""
