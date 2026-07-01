"""Event-filter and position-source controls for the inspect tab (DPG-aware layer).

Provides two readers consumed by the collectors (``_selected_event_types`` /
``_position_source``), two (re)build helpers called on data-load, and two widget
builders called by the section ``controls`` builders. Re-render callbacks lazily
import ``_render_one`` from ``.engine`` to break the controls->engine->sections->controls
import cycle.
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg


def _selected_event_types() -> list[str]:
    """Return the labels of all checked event-type filter checkboxes.

    Returns
    -------
    list[str]
        eID strings whose checkbox is currently checked. Empty when the
        ``event_type_filters`` group does not exist or no box is checked.
    """
    out: list[str] = []
    if dpg.does_item_exist("event_type_filters"):
        for child in dpg.get_item_children("event_type_filters", slot=1) or []:
            if dpg.get_item_type(child) == "mvAppItemType::mvCheckbox" and dpg.get_value(child):
                out.append(dpg.get_item_label(child))
    return out


def _rebuild_event_filters(app) -> None:
    """(Re)build filter checkboxes from the loaded event types.

    Deletes all children of the ``event_type_filters`` group and recreates one
    checkbox per unique eID found in ``app.event_data``. Called on data-load only,
    not on every render.

    Parameters
    ----------
    app
        FloodlightApp instance; provides ``event_data``, ``get_temporal_divisions``,
        and ``get_team_names``.

    Notes
    -----
    Side-effects: mutates DPG children of tag ``event_type_filters``. Each
    checkbox carries a ``_render_one("event")`` callback so toggling it
    re-renders the event section.
    """
    from floodlight_gui.tabs.inspect.render import _render_one

    if not dpg.does_item_exist("event_type_filters"):
        return
    dpg.delete_item("event_type_filters", children_only=True)
    types: set = set()
    event_data = app.event_data
    if isinstance(event_data, tuple) and event_data:
        events = event_data[0]
        for period in app.get_temporal_divisions():
            if period in events:
                for team in app.get_team_names():
                    team_events = events[period].get(team)
                    if (
                        team_events is not None
                        and hasattr(team_events, "events")
                        and "eID" in team_events.events.columns
                    ):
                        types.update(team_events.events["eID"].dropna().unique())
    for event_type in sorted(types):
        dpg.add_checkbox(
            label=str(event_type),
            tag=f"filter_{event_type}",
            default_value=True,
            parent="event_type_filters",
            callback=lambda *_: _render_one("event"),
        )
    if not types:
        dpg.add_text("No event types found", parent="event_type_filters")


def _set_event_filters(mode: str) -> None:
    """Set all event-type checkboxes to a preset and re-render the event section.

    Parameters
    ----------
    mode : {"all", "none", "passes"}
        "all" checks every checkbox; "none" unchecks all; "passes" checks only
        those whose label contains "pass" (case-insensitive).
    """
    from floodlight_gui.tabs.inspect.render import _render_one

    if not dpg.does_item_exist("event_type_filters"):
        return
    for child in dpg.get_item_children("event_type_filters", slot=1) or []:
        if dpg.get_item_type(child) != "mvAppItemType::mvCheckbox":
            continue
        if mode == "all":
            dpg.set_value(child, True)
        elif mode == "none":
            dpg.set_value(child, False)
        elif mode == "passes":
            dpg.set_value(child, "pass" in dpg.get_item_label(child).lower())
    _render_one("event")


def _position_source() -> str:
    """Return the active position-source key from the combo widget.

    Returns
    -------
    str
        ``"post_stack"`` when the combo shows "Post-stack", otherwise
        ``"pristine"``. Falls back to ``"pristine"`` when the combo tag does
        not exist or DPG raises ``SystemError`` (widget not yet rendered).
    """
    if dpg.does_item_exist("inspect_position_source_combo"):
        try:
            value = dpg.get_value("inspect_position_source_combo")
        except SystemError:
            return "pristine"
        return "post_stack" if value == "Post-stack" else "pristine"
    return "pristine"


def _controls_event() -> None:
    """Build the collapsing event-type filter panel (DPG widgets).

    Notes
    -----
    Side-effects: creates DPG tags ``event_filter_window`` and
    ``event_type_filters``. Checkboxes are populated later by
    ``_rebuild_event_filters`` on data-load.
    """
    with dpg.collapsing_header(label="Event Type Filters", default_open=False):
        with dpg.group(horizontal=True):
            dpg.add_button(label="All", callback=lambda: _set_event_filters("all"), width=60)
            dpg.add_button(label="None", callback=lambda: _set_event_filters("none"), width=60)
            dpg.add_button(label="Passes", callback=lambda: _set_event_filters("passes"), width=60)
        with dpg.child_window(height=150, tag="event_filter_window"):
            dpg.add_group(tag="event_type_filters")


def _controls_position() -> None:
    """Build the position-source combo (Pristine / Post-stack) and wire its callback.

    Notes
    -----
    Side-effects: creates DPG tag ``inspect_position_source_combo``. On change
    the combo calls ``_render_one("position")`` via ``.engine``.
    """
    from floodlight_gui.tabs.inspect.render import _render_one

    dpg.add_combo(
        items=["Pristine", "Post-stack"],
        default_value="Pristine",
        tag="inspect_position_source_combo",
        label="Source",
        width=140,
        callback=lambda *_: _render_one("position"),
    )
