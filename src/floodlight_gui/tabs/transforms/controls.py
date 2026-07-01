"""Transforms tab layout builder and EventBus wiring (DPG-aware).

Builds the 4-section stepped layout (Select Data / Select Transform /
Configure Parameters / Results), populates the 5-category tab_bar
(Filter | Interpolation | Spatial | Temporal | Permutation) from
TRANSFORM_REGISTRY, and subscribes to APP_INITIALIZED / DATA_LOADED /
XY_STACK_CHANGED. The executor for all ops lives in execute.py.
"""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.registry.transforms import TRANSFORM_REGISTRY
from floodlight_gui.status_bar import FOOTER_HEIGHT_PX
from floodlight_gui.tabs._shared.broadcast import bridge_period_to_internal
from floodlight_gui.tabs._shared.selectors import period_team_selector
from floodlight_gui.tabs._shared.tab_header import add_tab_header
from floodlight_gui.tabs.transforms import state
from floodlight_gui.tabs.transforms.execute import (
    _apply_clicked,
    _reset_all_clicked,
    _reset_target_clicked,
    _undo_clicked,
)
from floodlight_gui.tabs.transforms.params import _on_op_changed
from floodlight_gui.tabs.transforms.select import _on_category_tab_changed
from floodlight_gui.theme import DISABLED, INFO

logger = logging.getLogger(__name__)

__all__ = ["create_transforms_tab"]

# ---------------------------------------------------------------------------
# Op-key constants (define the tab's operational domain)
# ---------------------------------------------------------------------------

# min_max_normalize is intentionally absent: its descriptor expects
# `positions: ndarray`, not an XY object, so the apply path cannot dispatch it.
# The descriptor remains in TRANSFORM_REGISTRY for scan_floodlight.py parity.
TRANSFORMS_OP_KEYS = (
    # filter (5)
    "butterworth_lowpass",
    "savgol_lowpass",
    "wiener",
    "fir_lowpass",
    "kalman",
    # interpolation (3)
    "interpolate_linear",
    "interpolate_polynomial",
    "interpolate_spline",
    # spatial (5 - min_max_normalize excluded; see note above)
    "subtract_centroid",
    "translate",
    "scale",
    "reflect",
    "rotate",
    # temporal (2)
    "resample",
    "slice",
    # permutation (1)
    "assign_roles",
)

# Upstream-verbatim category order for the Step 2 picker.
# First entry is the cold-start default (DPG selects the first tab automatically).
_CATEGORIES: tuple[str, ...] = ("filter", "interpolation", "spatial", "temporal", "permutation")

_op_keys = [k for k in TRANSFORMS_OP_KEYS if k in TRANSFORM_REGISTRY]

# Per-category op_keys lookup; drives the Step 2 dropdown population.
_cat_ops: dict[str, list[str]] = {
    cat: [k for k in TRANSFORMS_OP_KEYS if TRANSFORM_REGISTRY.get(k, {}).get("category") == cat]
    for cat in _CATEGORIES
}

# Flat display-name to key lookup; display names must be unique across all ops.
_display_to_key: dict[str, str] = {
    TRANSFORM_REGISTRY[k]["display_name"]: k for k in TRANSFORMS_OP_KEYS if k in TRANSFORM_REGISTRY
}

# Guard: if any two ops share a display_name the flat lookup silently overwrites.
# Fail at import time so the collision is caught before any UI is built.
assert len(_display_to_key) == sum(1 for k in TRANSFORMS_OP_KEYS if k in TRANSFORM_REGISTRY), (
    "TRANSFORM_REGISTRY display_name collision detected. "
    "Refactor _display_to_key to be per-category before adding the colliding op."
)

# Per-category current op_key; seeded from each category's first registered op.
_current_op_key: dict[str, str | None] = {
    cat: (_cat_ops[cat][0] if _cat_ops[cat] else None) for cat in _CATEGORIES
}


# ---------------------------------------------------------------------------
# EventBus handlers
# ---------------------------------------------------------------------------


def _on_app_initialized(app, **_):
    """Capture the app reference from the APP_INITIALIZED event."""
    state.app_instance = app


def _on_data_loaded(**_):
    """Refresh the period/team selectors and applied-stack display after data loads."""
    from floodlight_gui.tabs.transforms import select as _sel

    _sel.refresh_transforms_display()


def _on_transforms_period_changed(sender, app_data):
    """Sync internal period state and refresh the target summary on period combo change.

    bridge_period_to_internal returns None when app_data is "All", signalling
    broadcast mode to downstream consumers.
    """
    state._transforms_selected_period_internal = bridge_period_to_internal(app_data)
    from floodlight_gui.tabs.transforms import select as _sel

    _sel._update_target_summary()
    from floodlight_gui.tabs.transforms import results as _results

    _results._refresh_stack_display()


def _on_transforms_team_changed(sender, app_data):
    """Refresh the target summary when the team combo changes."""
    from floodlight_gui.tabs.transforms import select as _sel

    _sel._update_target_summary()
    from floodlight_gui.tabs.transforms import results as _results

    _results._refresh_stack_display()


def _on_xy_stack_changed(app=None, **_data):
    """Reflow the Applied Stack when the XY op stack changes.

    Guards on transforms_stack_broadcast_tab_bar (the surviving container)
    before touching the DPG tree.
    """
    if not dpg.does_item_exist("transforms_stack_broadcast_tab_bar"):
        return
    from floodlight_gui.tabs.transforms import select as _sel

    _sel._update_target_summary()
    from floodlight_gui.tabs.transforms import results as _results

    _results._refresh_stack_display()


def create_transforms_tab():
    """Build the Transforms tab inside a DPG tab_bar context.

    Creates a 4-section stepped layout: (1) period/team selector,
    (2) 5-category tab_bar op picker, (3) per-op parameter widgets +
    action buttons, (4) Applied Stack results view. Subscribes to
    APP_INITIALIZED, DATA_LOADED, and XY_STACK_CHANGED on the global bus.

    Notes
    -----
    DPG tags owned: ``transforms_tab``, ``transforms_content_wrap``,
    ``transforms_state_view_container``, ``transforms_selector_container``,
    ``transforms_target_summary``, ``transforms_category_tab_bar``,
    ``transforms_category_{cat}_tab``, ``transforms_op_combo_{cat}``,
    ``transforms_combo_help_group_{cat}``, ``transforms_params_container``,
    ``transforms_apply_btn``, ``transforms_undo_btn``,
    ``transforms_reset_target_btn``, ``transforms_reset_all_btn``,
    ``transforms_status``, ``transforms_step4_header``,
    ``transforms_stack_window``, ``transforms_stack_broadcast_tab_bar``.

    EventBus subscriptions (all registered once per app session):
    APP_INITIALIZED (priority 0), DATA_LOADED (priority 10),
    XY_STACK_CHANGED (priority 10).
    """
    with dpg.tab(label="Transforms", tag="transforms_tab"):
        add_tab_header("transforms")
        # Per-tab content scroll surface.
        with dpg.child_window(
            tag="transforms_content_wrap",
            height=-FOOTER_HEIGHT_PX,
            border=False,
            autosize_x=True,
        ):
            dpg.add_text(
                "Apply floodlight.transforms.* ops"
                " (filter / interpolation / spatial / temporal / permutation).\n"
                "Ops stack per (period, team); the derived XY is what downstream tabs read.",
                color=INFO,
            )
            dpg.add_separator()

            with dpg.child_window(
                tag="transforms_state_view_container",
                autosize_x=True,
                height=70,
                border=False,
                show=False,
            ):
                pass

            # --- Step 1: Select Data ---
            # period_team_selector owns the transforms_period_combo and
            # transforms_team_combo tags; _get_target() in select.py reads them.
            with dpg.collapsing_header(
                label="Step 1: Select Data", default_open=True, closable=False
            ):  # noqa: E501 - single-line form required by structural test needle
                with dpg.group(tag="transforms_selector_container"):
                    period_team_selector(
                        parent_tag="transforms_selector_container",
                        period_callback=_on_transforms_period_changed,
                        team_callback=_on_transforms_team_changed,
                        tag_prefix="transforms",
                    )
                dpg.add_spacer(height=4)
                dpg.add_text("Target: -", tag="transforms_target_summary", color=INFO)

            dpg.add_spacer(height=10)

            # --- Step 2: Select Transform ---
            # 5-category tab_bar in upstream-verbatim order. Filter is the
            # cold-start default because _CATEGORIES[0] == "filter" and DPG
            # selects the first tab automatically.
            # tab_bar children must be dpg.tab nodes only; info text goes above.
            with dpg.collapsing_header(
                label="Step 2: Select Transform", default_open=True, closable=False
            ):  # noqa: E501 - single-line form required by structural test needle
                dpg.add_text("Select transform category and op:", color=INFO)
                with dpg.tab_bar(
                    tag="transforms_category_tab_bar",
                    callback=_on_category_tab_changed,
                ):
                    for category in _CATEGORIES:
                        cat_label = category.capitalize()
                        cat_ops_for = _cat_ops[category]
                        display_names = [
                            TRANSFORM_REGISTRY[k]["display_name"]
                            for k in cat_ops_for
                            if k in TRANSFORM_REGISTRY
                        ]
                        with (
                            dpg.tab(label=cat_label, tag=f"transforms_category_{category}_tab"),
                            dpg.group(horizontal=True),
                        ):
                            dpg.add_combo(
                                items=display_names,
                                default_value=display_names[0] if display_names else "",
                                callback=_on_op_changed,
                                width=250,
                                tag=f"transforms_op_combo_{category}",
                            )
                            # Per-category help button container (populated by _on_op_changed).
                            dpg.add_group(tag=f"transforms_combo_help_group_{category}")

            dpg.add_spacer(height=10)

            # --- Step 3: Configure Parameters ---
            # _build_params_ui rebuilds transforms_params_container on every
            # _on_op_changed call so the widgets stay in sync with the active
            # category's selected op.
            with dpg.collapsing_header(
                label="Step 3: Configure Parameters", default_open=True, closable=False
            ):  # noqa: E501 - single-line form required by structural test needle
                dpg.add_group(tag="transforms_params_container")

                dpg.add_spacer(height=8)
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="Apply Op",
                        callback=_apply_clicked,
                        width=140,
                        height=32,
                        tag="transforms_apply_btn",
                    )
                    dpg.add_button(
                        label="Undo Last",
                        callback=_undo_clicked,
                        width=120,
                        height=32,
                        tag="transforms_undo_btn",
                    )
                    dpg.add_button(
                        label="Reset Target",
                        callback=_reset_target_clicked,
                        width=140,
                        height=32,
                        tag="transforms_reset_target_btn",
                    )
                    dpg.add_button(
                        label="Reset All",
                        callback=_reset_all_clicked,
                        width=120,
                        height=32,
                        tag="transforms_reset_all_btn",
                    )
                dpg.add_text("Ready", tag="transforms_status", color=DISABLED)

            dpg.add_spacer(height=10)

            # --- Step 4: Results ---
            # Applied Stack only; no action buttons in this section.
            # The broadcast tab_bar is always the render target: specific
            # (period, team) picks land as a 1x1 cross-product (single period
            # tab + single team tab). _refresh_stack_display() rebuilds it on
            # every selector change or XY_STACK_CHANGED event.
            # Never add_text with parent=<tab_bar tag>; leaf text is written
            # via render_stack_leaf into the per-leaf tab, not the bar.
            with (  # noqa: E501 - collapsing_header single-line form required by structural test needle
                dpg.collapsing_header(
                    label="Step 4: Results",
                    default_open=True,
                    tag="transforms_step4_header",
                    closable=False,
                ),
                dpg.child_window(height=220, tag="transforms_stack_window", border=True),
            ):
                dpg.add_tab_bar(tag="transforms_stack_broadcast_tab_bar", show=True)

            dpg.add_spacer(height=10)

    # Bootstrap: call _on_op_changed for each category's default op so per-combo
    # help groups are pre-populated before the user interacts. The final call
    # re-runs the default-active category (Filter, _CATEGORIES[0]) so Step 3
    # ends in sync with the visually active Step 2 tab; without it Step 3 would
    # show the last-iterated category's params (Permutation) on cold start.
    for cat in _CATEGORIES:
        _on_op_changed(f"transforms_op_combo_{cat}", None)
    _on_op_changed(f"transforms_op_combo_{_CATEGORIES[0]}", None)

    # EventBus subscriptions: exactly one per event per session.
    bus.subscribe(Events.APP_INITIALIZED, _on_app_initialized, priority=0)
    bus.subscribe(Events.DATA_LOADED, _on_data_loaded, priority=10)
    bus.subscribe(Events.XY_STACK_CHANGED, _on_xy_stack_changed, priority=10)
