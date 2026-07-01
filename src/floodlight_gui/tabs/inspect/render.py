"""Rendering engine for the Inspect tab's SECTIONS viewer.

``_render_section`` walks a section's shape (grouped / period / flat / single)
and dispatches each leaf to its render strategy. ``refresh_data_display`` runs
every section (the Refresh button + DATA_LOADED); ``_render_one`` re-renders a
single section in place (filter / source-toggle callbacks). Both read the live
``state.app_instance`` via qualified access: never import it by value.

DPG carve-out: this module imports ``dearpygui`` at module scope because it
lives under ``tabs/`` (the DPG-aware layer); backend modules must not.

BLE001 convention: every DPG callback wraps its body in
``try / except Exception: logger.exception(...)`` with a ``# noqa: BLE001``
marker so a callback error can never crash the render loop.
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.tabs._shared.error_helpers import friendly_error_message, show_error_modal
from floodlight_gui.tabs._shared.state_views import render_empty, render_error
from floodlight_gui.tabs.inspect import state
from floodlight_gui.tabs.inspect.sections import _SECTION_BY_KEY, SECTIONS, _Section
from floodlight_gui.tabs.inspect.state import FLAT, GROUPED, PERIOD, SINGLE, _slug

logger = logging.getLogger(__name__)


def _render_section(sec: _Section, app) -> None:
    """Render one SECTIONS entry into its pre-existing DPG tab-bar container.

    Calls ``sec.collect(app)`` to get the data tree, clears the bar's children,
    then dispatches by ``sec.shape``: SINGLE renders one leaf directly; PERIOD
    and FLAT each get one DPG tab per key; GROUPED nests period tabs with an
    inner entity tab-bar. If the tree is empty the bar is cleared and the
    header visibility is set by ``sec.hide_when_empty``.

    Parameters
    ----------
    sec : _Section
        The section descriptor (from ``SECTIONS``).
    app
        Live app instance; passed through to ``sec.collect`` and ``sec.info``.

    Notes
    -----
    Writes DPG children under ``{sec.key}_bar`` and updates
    ``{sec.key}_header`` and ``{sec.key}_info`` tags.
    """
    bar = f"{sec.key}_bar"
    # Scaffold guard: the tab UI may not be built in the current DPG context
    # (e.g. a headless test fires DATA_LOADED on the bus). Never render into a
    # container that does not exist - creating a child of a missing parent is a
    # native DPG crash, not a catchable SystemError.
    if not dpg.does_item_exist(bar):
        return
    tree = sec.collect(app)

    if not tree:
        dpg.set_value(f"{sec.key}_info", sec.empty)
        if dpg.does_item_exist(f"{sec.key}_header"):
            dpg.configure_item(f"{sec.key}_header", show=not sec.hide_when_empty)
        if dpg.does_item_exist(bar):
            dpg.delete_item(bar, children_only=True)
        return

    dpg.configure_item(f"{sec.key}_header", show=True)
    if dpg.does_item_exist(bar):
        dpg.delete_item(bar, children_only=True)

    if sec.shape == SINGLE:
        sec.leaf(bar, tree)
    elif sec.shape in (PERIOD, FLAT):
        for label, payload in tree.items():
            leaf_tag = f"{sec.key}_{_slug(label)}"
            with dpg.tab(label=label, tag=leaf_tag, parent=bar):
                sec.leaf(leaf_tag, payload)
    elif sec.shape == GROUPED:
        for period, entities in tree.items():
            pslug = _slug(period)
            with dpg.tab(label=period, tag=f"{sec.key}_p_{pslug}", parent=bar):
                inner = f"{sec.key}_pb_{pslug}"
                dpg.add_tab_bar(tag=inner)
                for entity, payload in entities.items():
                    leaf_tag = f"{sec.key}_{pslug}_{_slug(entity)}"
                    with dpg.tab(label=entity, tag=leaf_tag, parent=inner):
                        sec.leaf(leaf_tag, payload)

    if sec.info is not None:
        dpg.set_value(f"{sec.key}_info", sec.info(app, tree))


def _render_one(key: str) -> None:
    """Re-render a single section in place (filter / source-toggle callbacks)."""
    if not state.app_instance or not state.app_instance.loaded_data:
        return
    sec = _SECTION_BY_KEY.get(key)
    if sec is None:
        return
    try:
        _render_section(sec, state.app_instance)
    except Exception:  # noqa: BLE001 - DPG callback boundary; must not crash render loop
        logger.exception("inspect: re-render of section %s failed", key)


def refresh_data_display() -> None:
    """Refresh every SECTIONS entry from the current app data.

    Called by the Refresh button callback and by the ``DATA_LOADED`` EventBus
    subscriber. Iterates over every entry in ``SECTIONS``, fires each section's
    ``on_load`` hook if present, and delegates rendering to ``_render_section``.
    On success the placeholder is hidden and the status bar is updated. On error
    the status bar shows a short message and a modal is shown.

    Notes
    -----
    Reads ``state.app_instance`` (qualified, never imported by value).
    Writes the following DPG tags: ``inspect_status``, ``inspect_placeholder``,
    ``inspect_content_area``. No-ops when ``inspect_content_wrap`` does not exist
    (headless context; the tab UI has not been built).
    """
    if not dpg.does_item_exist("inspect_content_wrap"):
        return  # tab UI not built in this DPG context (e.g. headless data-load event)
    if not state.app_instance or not state.app_instance.loaded_data:
        dpg.set_value("inspect_status", "Status: No data available")
        _set_empty_view("No data loaded -- open the Load tab and pick a file or sample dataset.")
        return
    try:
        for sec in SECTIONS:
            if sec.on_load is not None:
                sec.on_load(state.app_instance)
            _render_section(sec, state.app_instance)
        dpg.configure_item("inspect_placeholder", show=False)
        _clear_empty_view()
        dpg.set_value("inspect_status", "Status: Data refreshed")
    except Exception as e:  # noqa: BLE001 - top-level UI callback; must not crash
        logger.exception("Error refreshing data display: %s", e)
        dpg.set_value("inspect_status", f"Status: Error - {friendly_error_message(e)}")
        if dpg.does_item_exist("inspect_content_area"):
            with contextlib.suppress(SystemError):
                dpg.configure_item("inspect_content_area", show=True)
                render_error("inspect_content_area", e)
        with contextlib.suppress(SystemError):
            show_error_modal(
                "inspect_tab",
                e,
                context="Inspect tab refresh failed.",
                suggested_fix="Try re-loading the data from the Load tab.",
            )


def _set_empty_view(message: str) -> None:
    """Show the empty-state placeholder inside ``inspect_content_area``."""
    if dpg.does_item_exist("inspect_content_area"):
        with contextlib.suppress(SystemError):
            dpg.configure_item("inspect_content_area", show=True)
            render_empty("inspect_content_area", message)


def _clear_empty_view() -> None:
    """Clear and hide the empty-state placeholder inside ``inspect_content_area``."""
    if dpg.does_item_exist("inspect_content_area"):
        with contextlib.suppress(SystemError):
            dpg.delete_item("inspect_content_area", children_only=True)
            dpg.configure_item("inspect_content_area", show=False)
