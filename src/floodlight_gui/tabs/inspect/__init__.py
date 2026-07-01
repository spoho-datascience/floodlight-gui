"""Inspect tab package: read-only SECTIONS-driven viewer for all loaded floodlight data.

Every inspectable data kind is one entry in ``SECTIONS`` (a ``_Section``
descriptor). The generic renderer in ``render.py`` walks the section's shape
(grouped period/entity, period-only, flat entity-only, or single) and
dispatches each leaf to a render strategy (event table, array view,
teamsheet table, Code summary, key-value text).

Internal submodule layout:
  - ``state``    : shared ``app_instance`` global and read-only constants.
  - ``controls`` : event-filter and position-source widgets plus their readers.
  - ``collect``  : per-section collectors that normalize raw app data.
  - ``leaves``   : per-payload leaf renderers.
  - ``sections`` : ``_Section`` descriptors and the ``SECTIONS`` list.
  - ``engine``   : generic render engine and refresh entry points.

DPG-aware: ``dearpygui`` is imported at module scope. This package must stay
under ``tabs/`` and must not be imported from backend or test modules.

EventBus subscriptions at the bottom of this file wire the tab to
``APP_INITIALIZED``, ``DATA_LOADED``, and ``XY_STACK_CHANGED`` on import.
"""

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.status_bar import FOOTER_HEIGHT_PX
from floodlight_gui.tabs._shared.tab_header import add_tab_header
from floodlight_gui.tabs.inspect import state
from floodlight_gui.tabs.inspect.render import refresh_data_display
from floodlight_gui.tabs.inspect.sections import SECTIONS
from floodlight_gui.tabs.inspect.state import SINGLE
from floodlight_gui.theme import INFO

__all__ = ["create_inspect_tab", "refresh_data_display"]


def create_inspect_tab():
    """Build the Inspect tab DPG container and its SECTIONS-driven scaffold.

    Renders the outer tab item (label "Inspect Data"), a scrollable child
    window, a placeholder text shown before any data is loaded, a manual
    Refresh button, and one ``collapsing_header`` per ``SECTIONS`` entry.
    Each header contains a status text, an optional controls row (from
    ``sec.controls``), and either a ``dpg.group`` (single-shape sections) or
    a ``dpg.tab_bar`` (all other shapes) tagged ``{sec.key}_bar``.

    Notes
    -----
    DPG widget tags owned by this function:
      - ``inspect_tab`` : the tab item.
      - ``inspect_content_wrap`` : outer scrollable child window.
      - ``inspect_placeholder`` : pre-load placeholder text.
      - ``inspect_content_area`` : inner content child window (hidden until load).
      - ``inspect_status`` : status text updated by the refresh engine.
      - ``{sec.key}_header``, ``{sec.key}_info``, ``{sec.key}_bar`` per section.

    EventBus subscriptions (``APP_INITIALIZED``, ``DATA_LOADED``,
    ``XY_STACK_CHANGED``) are wired at package import, not inside this
    function.
    """
    with dpg.tab(label="Inspect Data", tag="inspect_tab"):
        add_tab_header("inspect")
        # Per-tab scroll surface; height reserves space for the pinned status bar.
        with dpg.child_window(
            tag="inspect_content_wrap", height=-FOOTER_HEIGHT_PX, border=False, autosize_x=True
        ):
            dpg.add_text("No data loaded. Load data first.", tag="inspect_placeholder", color=INFO)
            with dpg.child_window(
                tag="inspect_content_area", autosize_x=True, height=60, border=False, show=False
            ):
                pass
            with dpg.group(horizontal=True):
                dpg.add_button(label="Refresh Data", callback=refresh_data_display, width=120)
                dpg.add_text("Status: Ready", tag="inspect_status", color=INFO)

            for sec in SECTIONS:
                with dpg.collapsing_header(
                    label=sec.label, tag=f"{sec.key}_header", show=False, closable=False
                ):
                    dpg.add_text(sec.empty, tag=f"{sec.key}_info", color=INFO)
                    if sec.controls is not None:
                        sec.controls()
                    if sec.shape == SINGLE:
                        dpg.add_group(tag=f"{sec.key}_bar")
                    else:
                        dpg.add_tab_bar(tag=f"{sec.key}_bar")


# --------------------------------------------------------------------------- #
# EventBus wiring
# --------------------------------------------------------------------------- #


def _on_app_initialized(app, **_):
    """Store the app reference received from ``Events.APP_INITIALIZED``."""
    state.app_instance = app


def _on_data_loaded(app=None, **_):
    """Update the app reference and trigger a full tab refresh on ``Events.DATA_LOADED``."""
    if app is not None:
        state.app_instance = app
    refresh_data_display()


def _on_xy_stack_changed_inspect(app=None, **_):
    """Re-render the Position section on ``Events.XY_STACK_CHANGED``.

    The pristine-source view is invariant under ops apply/undo, so only
    the "Post-stack" source selection needs to re-render. The early-return
    guard prevents needless rebuilds when another source is active.
    """
    if not dpg.does_item_exist("inspect_position_source_combo"):
        return
    try:
        if dpg.get_value("inspect_position_source_combo") != "Post-stack":
            return
    except SystemError:
        return
    from floodlight_gui.tabs.inspect.render import _render_one

    _render_one("position")


bus.subscribe(Events.APP_INITIALIZED, _on_app_initialized, priority=0)
bus.subscribe(Events.DATA_LOADED, _on_data_loaded, priority=10)
bus.subscribe(Events.XY_STACK_CHANGED, _on_xy_stack_changed_inspect, priority=10)
