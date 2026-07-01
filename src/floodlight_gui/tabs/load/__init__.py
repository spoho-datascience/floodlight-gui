"""Load Data tab package entry point: re-exports ``create_load_tab`` and wires module-level
EventBus subscriptions.

Orchestrates the tab layout by delegating widget construction to three section modules:
``file_section`` (local-file providers, sync load), ``dataset_section`` (public datasets,
async threaded download), and ``pitch_section`` (collapsible Pitch builder).

DPG-aware layer: imports ``dearpygui`` at module scope. The ``core/`` backend modules must
not import from this package.

EventBus subscriptions registered at import time (priority 20):
- ``Events.DATA_LOADED``: hides the first-run empty-state guidance.
- ``Events.DATA_CLEARED``: restores the first-run empty-state guidance.

Layout (DPG tag "load_tab"):
    tab "Load Data" (tag "load_tab")
      add_tab_header("load")
      child_window(height=-FOOTER_HEIGHT_PX, tag="load_content")
        file_section.build("load_content")
        dataset_section.build("load_content")
        data_info text (tag "data_info")
        pitch_section.build("load_content")
        load_empty_state group (tag "load_empty_state")
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.status_bar import FOOTER_HEIGHT_PX
from floodlight_gui.tabs._shared.state_views import render_empty
from floodlight_gui.tabs._shared.tab_header import add_tab_header
from floodlight_gui.theme import INFO

from . import dataset_section, file_section, pitch_section

__all__ = ["create_load_tab"]


def _set_empty_state(*, show: bool) -> None:
    """Show or hide the first-run empty-state group (tag "load_empty_state") if it exists."""
    if dpg.does_item_exist("load_empty_state"):
        dpg.configure_item("load_empty_state", show=show)


# Hide the empty-state guidance once data is loaded; show it again when cleared.
bus.subscribe(Events.DATA_LOADED, lambda **_: _set_empty_state(show=False), priority=20)
bus.subscribe(Events.DATA_CLEARED, lambda **_: _set_empty_state(show=True), priority=20)


def create_load_tab() -> None:
    """Build the Load Data tab inside the active DPG tab bar.

    Called once during UI construction (``app.create_ui``). Delegates layout to
    ``file_section``, ``dataset_section``, and ``pitch_section``.

    Notes
    -----
    Side-effects:
    - Creates DPG items rooted at tag "load_tab" (tab) and "load_content" (child window).
    - Creates tag "data_info" (text widget), updated by ``app.update_data_info`` after each
      successful load.
    - Creates tag "load_empty_state" (group), toggled by the module-level EventBus
      subscriptions on DATA_LOADED / DATA_CLEARED.
    - Calls ``file_section.build``, ``dataset_section.build``, ``pitch_section.build``,
      which register their own DPG items and EventBus subscriptions.
    """
    with dpg.tab(label="Load Data", tag="load_tab"):
        add_tab_header("load")
        with dpg.child_window(
            height=-FOOTER_HEIGHT_PX, border=False, autosize_x=True, tag="load_content"
        ):
            file_section.build("load_content")
            dataset_section.build("load_content")

            # Data summary, populated by app.update_data_info on each load.
            dpg.add_separator()
            dpg.add_text("Data Information:", color=INFO)
            dpg.add_text("No data loaded", tag="data_info", wrap=600)

            pitch_section.build("load_content")

            # First-run guidance; hidden once data is loaded (see subscriptions).
            dpg.add_separator()
            dpg.add_group(tag="load_empty_state")
            render_empty(
                "load_empty_state",
                "No data loaded yet - pick a provider or import a dataset above.",
            )
