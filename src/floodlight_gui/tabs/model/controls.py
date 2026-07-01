"""Model tab layout builder and EventBus wiring (DPG-aware).

Orchestrates the 5-step Models tab: builds the DPG scaffold, subscribes to
exactly one handler per EventBus event, and routes UI callbacks to the
sub-modules (select / params / execute / results). Heavier logic lives there.

DPG carve-out: this module imports ``dearpygui`` at module scope because it
lives under ``tabs/`` (the DPG-aware layer); backend modules must not.
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.status_bar import FOOTER_HEIGHT_PX
from floodlight_gui.tabs._shared.export_action import render_export_action
from floodlight_gui.tabs._shared.tab_header import add_tab_header
from floodlight_gui.tabs.model import execute, labels, params, results, select, state
from floodlight_gui.theme import INFO

logger = logging.getLogger(__name__)

# Step labels must match exactly.
STEP_LABELS = (
    "Step 1: Select Data",
    "Step 2: Select Model",
    "Step 3: Configure Parameters",
    "Step 4: Results",
    "Step 5: Export Results",
)

CATEGORY_BAR = "models_category_tab_bar"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def create_model_tab() -> None:
    """Build the Models tab into the current DPG container and wire EventBus.

    Constructs the 5-step collapsing layout inside a ``dpg.tab`` tagged
    ``"model_tab"``, subscribes EventBus handlers (``_subscribe_events``), and
    fires an initial ``_cold_start`` to populate pickers before any data loads.

    Notes
    -----
    DPG side-effects:
        Creates DPG items: ``model_tab``, ``model_content_wrap``,
        ``models_category_tab_bar``, ``model_fit_btn``, ``model_fit_status``,
        ``model_results_outer_tab_bar``,
        ``model_step3_header``, ``model_export_status``,
        ``model_export_filename`` (plus tags delegated to select/params/results).

    EventBus side-effects:
        Subscribes ``_on_app_initialized`` to ``Events.APP_INITIALIZED``,
        ``_on_data_loaded`` to ``Events.DATA_LOADED``,
        ``_on_data_cleared`` to ``Events.DATA_CLEARED``, and
        ``_on_model_fitted`` to ``Events.MODEL_FITTED``.

    Writes ``state.panel`` with the results panel handle returned by
    ``results.make_panel()``.
    """
    state.panel = results.make_panel()

    with dpg.tab(label="Models", tag="model_tab"):
        add_tab_header("model")  # OUTSIDE the scroll wrap
        with dpg.child_window(tag="model_content_wrap", height=-FOOTER_HEIGHT_PX):
            dpg.add_text(
                "Fit a floodlight model: pick data and a model, configure it, "
                "fit, inspect results, and export.",
                color=INFO,
            )
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
    """Render the Step 1 collapsing header with the data-selection selector."""
    with (  # noqa: SIM117 -- DPG nested with preserves registration order
        dpg.collapsing_header(label=STEP_LABELS[0], default_open=True, closable=False),
        dpg.group(tag=select.SELECTOR_PARENT),
    ):
        select.mount(_on_selection_change)


def _build_step2() -> None:
    """Render the Step 2 collapsing header with the category tab bar and model combo."""
    with dpg.collapsing_header(label=STEP_LABELS[1], default_open=True, closable=False):
        dpg.add_text(
            "Choose a model by category, then pick which outputs to compute and export.", color=INFO
        )
        with dpg.tab_bar(tag=CATEGORY_BAR, callback=_on_category_change):
            for cat in labels.CATEGORY_ORDER:
                with dpg.tab(label=cat.title(), tag=labels.category_tab_tag(cat)):  # noqa: SIM117 -- DPG nested with preserves registration order
                    with dpg.group(horizontal=True):
                        names = labels.display_names_in_category(cat)
                        dpg.add_combo(
                            items=names,
                            default_value=names[0] if names else "",
                            tag=labels.model_combo_tag(cat),
                            width=260,
                            callback=_on_model_change,
                        )
                        with dpg.group(tag=labels.help_group_tag(cat), horizontal=True):
                            pass
        dpg.add_separator()
        dpg.add_text("Outputs to compute & export:")
        with dpg.group(tag=select.OUTPUTS_CONTAINER):
            pass


def _build_step3() -> None:
    """Render the Step 3 collapsing header with parameter widgets and the Fit button."""
    with dpg.collapsing_header(label=STEP_LABELS[2], default_open=True, closable=False):
        with dpg.group(tag=params.PARAMS_CONTAINER):
            pass
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="Fit Model", tag="model_fit_btn", callback=execute.on_fit)
            dpg.add_text("Ready", tag="model_fit_status")


def _build_step4() -> None:
    """Render the Step 4 collapsing header with the results panel placeholder."""
    with dpg.collapsing_header(label=STEP_LABELS[3], default_open=True, closable=False):
        dpg.add_text(
            "Fitted models appear here. Fit a model to populate this panel.",
            tag="model_results_placeholder",
        )
        dpg.add_text("", tag="model_results_info", color=INFO, show=False)
        dpg.add_tab_bar(tag="model_results_outer_tab_bar")


def _build_step5() -> None:
    """Render the Step 5 collapsing header with player selection and the export panel."""
    # Tag model_step3_header is a locked cross-tab reference consumed by
    # render_export_action as the parent container; do not rename it.
    with dpg.collapsing_header(
        label=STEP_LABELS[4], default_open=False, closable=False, tag="model_step3_header"
    ):
        dpg.add_text("Select Players to Export:")
        with dpg.group(horizontal=True):
            dpg.add_button(label="All Players", callback=lambda: select.set_all_players(True))
            dpg.add_button(label="Clear Selection", callback=lambda: select.set_all_players(False))
        # Per-team player selection: one tab per team, built on DATA_LOADED.
        with dpg.child_window(tag=select.PLAYER_SCROLL, height=160):
            dpg.add_tab_bar(tag=select.PLAYER_TAB_BAR)
        render_export_action(
            "model_step3_header",
            tab_name="models",
            artifact_name="model",
            mode="all",
            kind="model_single",
            payload=results.single_payload,
            label="Export Results",
            secondary_button={
                "mode": "all",
                "kind": "model_all",
                "payload": results.broadcast_payload,
                "label": "Export all",
            },
            status_tag="model_export_status",
            filename_input_tag="model_export_filename",
            app=lambda: state.app_instance,
        )


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #


def _on_selection_change(sender=None, app_data=None, user_data=None) -> None:  # noqa: ARG001
    """DPG callback: forward a selector change to select.on_selection_change."""
    try:
        select.on_selection_change()
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("model: selection change failed")


def _on_category_change(sender=None, app_data=None, user_data=None) -> None:  # noqa: ARG001
    """DPG callback: a category tab switch is equivalent to a model change."""
    _model_changed()


def _on_model_change(sender=None, app_data=None, user_data=None) -> None:  # noqa: ARG001
    """DPG callback: forward a model combo change to the shared model-changed handler."""
    _model_changed()


def _model_changed() -> None:
    """Sync the model selection (help/outputs/params) then the arity-aware selector."""
    try:
        select.on_model_change()
        model_key = select.active_model_key()
        if model_key is not None:
            select.ensure_arity(model_key, _on_selection_change)
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("model: model change failed")


# --------------------------------------------------------------------------- #
# Event wiring (exactly one subscription per event)
# --------------------------------------------------------------------------- #


def _subscribe_events() -> None:
    """Register all EventBus subscriptions for the Models tab."""
    bus.subscribe(Events.APP_INITIALIZED, _on_app_initialized, priority=0)
    bus.subscribe(Events.DATA_LOADED, _on_data_loaded, priority=10)
    bus.subscribe(Events.DATA_CLEARED, _on_data_cleared, priority=10)
    bus.subscribe(Events.MODEL_FITTED, _on_model_fitted, priority=5)


def _on_app_initialized(**payload) -> None:
    """Store the app reference from the APP_INITIALIZED payload into state."""
    state.app_instance = payload.get("app")


def _on_data_loaded(**_payload) -> None:
    """Refresh all model pickers and player checkboxes when data loads."""
    try:
        # Selector refresh must precede the per-category param re-trigger.
        select.refresh(_on_selection_change)
        for cat in labels.CATEGORY_ORDER:
            select.on_model_change(cat)
        # End in sync with the active (default) category.
        select.on_model_change(labels.DEFAULT_CATEGORY)
        model_key = select.active_model_key()
        if model_key is not None:
            select.ensure_arity(model_key, _on_selection_change)
        # Build Step 5 per-team player selection tabs now that data exists.
        select.rebuild_player_checkboxes()
    except Exception:  # noqa: BLE001 -- event boundary
        logger.exception("model: DATA_LOADED refresh failed")


def _on_data_cleared(**_payload) -> None:
    """Clear all fitted-model state and reset the results panel when data is removed."""
    try:
        state.fitted_models.clear()
        state.output_checked.clear()
        state.output_results.clear()
        results.clear()
        select.rebuild_player_checkboxes()  # clears the per-team tabs (no data)
    except Exception:  # noqa: BLE001 -- event boundary
        logger.exception("model: DATA_CLEARED failed")


def _on_model_fitted(**payload) -> None:
    """Surgically refresh the results panel for the fitted model."""
    try:
        if state.panel is None:
            return
        model_key = payload.get("model_key")
        half = payload.get("half_name")
        team = payload.get("team_name")
        if model_key is None:
            results.rebuild()
        elif half and team:
            results.refresh_leaf(model_key, half, team)
        elif half is None and team is None:
            results.refresh_model_leaves(model_key)
        else:
            results.rebuild()
    except Exception:  # noqa: BLE001 -- event boundary
        logger.exception("model: MODEL_FITTED refresh failed")


# --------------------------------------------------------------------------- #
# Cold start
# --------------------------------------------------------------------------- #


def _cold_start() -> None:
    """Fire model-changed once per category, then once for the default category."""
    with contextlib.suppress(Exception):
        for cat in labels.CATEGORY_ORDER:
            select.on_model_change(cat)
        select.on_model_change(labels.DEFAULT_CATEGORY)
