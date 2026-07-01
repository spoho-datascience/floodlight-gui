"""Shared fake-DPG recorder for wiring tests.

The seam: ``make_dpg_stub`` returns a ``SimpleNamespace`` standing in for the
``dearpygui.dearpygui`` module. Tests patch their tab module's ``dpg`` with it
and assert against the recorded call log instead of driving a live GUI. The
stub never imports dearpygui at any scope, keeping the test layer DPG-free.

Modelled fidelity that tests depend on:
  - container builders behave as context managers (record enter/exit);
  - widgets with a ``tag=`` auto-register so later ``does_item_exist`` is true;
  - ``get_value`` on a tab_bar tag returns the active child tab's TAG, not its
    label, matching the real DPG behavior;
  - ``delete_item`` with ``children_only=True`` keeps the container itself.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace


def make_dpg_stub(
    calls: list | None = None,
    existing_items: set | None = None,
    values: dict | None = None,
) -> SimpleNamespace:
    """Return a fake-DPG SimpleNamespace that records every call to .calls.

    Parameters
    ----------
    calls:
        Shared call log (created as an empty list if None). Every recorded call
        appends a ``(name, args, kwargs)`` tuple.
    existing_items:
        Set backing ``does_item_exist``. Add tags here to simulate pre-existing
        DPG items. Widgets with a ``tag=`` kwarg auto-register on creation.
    values:
        Dict backing ``get_value``. Seed ``{"<tab_bar_tag>": "<child_tab_tag>"}``
        to model the tab_bar contract: ``get_value`` on a ``tab_bar`` tag
        returns the active child tab's TAG string, not its label.

    Returns
    -------
    SimpleNamespace with attributes:
        .calls          — list of (name, args, kwargs) for every recorded call
        .calls_of(name) — helper: filter .calls by function name
        .existing_items — the set backing does_item_exist
        .values         — the dict backing get_value

        Container context managers (each records ``{kind}_enter`` / ``{kind}_exit``):
            group, tooltip, child_window, window, table, table_row,
            collapsing_header, tab, tab_bar, handler_registry

        Add-widget recorders (lambda, append (name, args, kwargs) + return synthetic tag):
            add_text, add_button, add_combo, add_checkbox, add_input_int,
            add_input_float, add_input_text, add_separator, add_spacer,
            add_table_column, add_viewport_drawlist, add_drawlist,
            add_slider_int, add_slider_float, draw_text

        Mutation / query:
            set_value, configure_item, delete_item (children_only semantics),
            does_item_exist, get_value, get_alias_id, is_item_focused, get_frame_rate

        Constants (plain ints):
            mvKey_Spacebar, mvKey_Left, mvKey_Right, mvKey_Home, mvKey_End,
            mvTable_SizingFixedFit
    """
    if calls is None:
        calls = []
    if existing_items is None:
        existing_items = set()
    if values is None:
        values = {}

    # ---------------------------------------------------------------------- #
    # Unified container context-manager factory                              #
    # ---------------------------------------------------------------------- #

    @contextmanager
    def _ctx(kind, *args, **kwargs):
        # Auto-register the tag if present so subsequent does_item_exist works
        tag = kwargs.get("tag")
        if tag:
            existing_items.add(tag)
        calls.append((f"{kind}_enter", args, kwargs))
        try:
            yield None
        finally:
            calls.append((f"{kind}_exit", args, kwargs))

    # ---------------------------------------------------------------------- #
    # Add-widget factory; returns a synthetic tag so callers can chain IDs.   #
    # ---------------------------------------------------------------------- #

    def _record(kind):
        def _fn(*args, **kwargs):
            # Auto-register tag in existing_items and seed default value
            tag = kwargs.get("tag")
            if tag:
                existing_items.add(tag)
                default = kwargs.get("default_value")
                if default is not None and tag not in values:
                    values[tag] = default
            calls.append((kind, args, kwargs))
            return f"{kind}_{len(calls)}"  # synthetic tag

        return _fn

    # ---------------------------------------------------------------------- #
    # Mutation / query                                                        #
    # ---------------------------------------------------------------------- #

    def _set_value(tag, value):
        values[tag] = value
        calls.append(("set_value", (tag, value), {}))

    def _configure_item(tag, **kwargs):
        calls.append(("configure_item", (tag,), kwargs))

    def _delete_item(tag, **kwargs):
        """Record deletion, honouring children_only semantics.

        With ``children_only=True`` the item itself survives (DPG container
        clear); otherwise it is also removed from ``existing_items``.
        """
        calls.append(("delete_item", (tag,), kwargs))
        if not kwargs.get("children_only"):
            existing_items.discard(tag)

    def _does_item_exist(tag):
        calls.append(("does_item_exist", (tag,), {}))
        return tag in existing_items

    def _get_value(tag):
        """Return value for tag. For tab_bar tags, returns child TAG (not label)."""
        calls.append(("get_value", (tag,), {}))
        return values.get(tag)

    def _get_alias_id(tag):
        calls.append(("get_alias_id", (tag,), {}))
        return hash(tag) & 0xFFFFFFFF

    def _is_item_focused(tag):
        calls.append(("is_item_focused", (tag,), {}))
        return False

    def _get_frame_rate():
        calls.append(("get_frame_rate", (), {}))
        return 60.0

    # ---------------------------------------------------------------------- #
    # Assemble namespace                                                       #
    # ---------------------------------------------------------------------- #

    stub = SimpleNamespace(
        # Container CMs
        group=lambda *a, **kw: _ctx("group", *a, **kw),
        tooltip=lambda *a, **kw: _ctx("tooltip", *a, **kw),
        child_window=lambda *a, **kw: _ctx("child_window", *a, **kw),
        window=lambda *a, **kw: _ctx("window", *a, **kw),
        table=lambda *a, **kw: _ctx("table", *a, **kw),
        table_row=lambda *a, **kw: _ctx("table_row", *a, **kw),
        collapsing_header=lambda *a, **kw: _ctx("collapsing_header", *a, **kw),
        tab=lambda *a, **kw: _ctx("tab", *a, **kw),
        tab_bar=lambda *a, **kw: _ctx("tab_bar", *a, **kw),
        handler_registry=lambda *a, **kw: _ctx("handler_registry", *a, **kw),
        # Add-widget recorders
        add_text=_record("add_text"),
        add_button=_record("add_button"),
        add_group=_record("add_group"),
        add_tab_bar=_record("add_tab_bar"),
        add_combo=_record("add_combo"),
        add_checkbox=_record("add_checkbox"),
        add_input_int=_record("add_input_int"),
        add_input_float=_record("add_input_float"),
        add_input_text=_record("add_input_text"),
        add_separator=_record("add_separator"),
        add_spacer=_record("add_spacer"),
        add_table_column=_record("add_table_column"),
        add_viewport_drawlist=_record("add_viewport_drawlist"),
        add_drawlist=_record("add_drawlist"),
        add_slider_int=_record("add_slider_int"),
        add_slider_float=_record("add_slider_float"),
        draw_text=_record("draw_text"),
        add_loading_indicator=_record("add_loading_indicator"),
        # Mutation / query
        set_value=_set_value,
        configure_item=_configure_item,
        delete_item=_delete_item,
        does_item_exist=_does_item_exist,
        get_value=_get_value,
        get_alias_id=_get_alias_id,
        is_item_focused=_is_item_focused,
        get_frame_rate=_get_frame_rate,
        # Integer constants (DPG key codes + table sizing flags)
        mvKey_Spacebar=32,
        mvKey_Left=263,
        mvKey_Right=262,
        mvKey_Home=268,
        mvKey_End=269,
        mvTable_SizingFixedFit=8192,
        # Internal state access (for tests that need to inspect post-call)
        existing_items=existing_items,
        values=values,
    )

    # Attach shared call log and helper
    stub.calls = calls
    stub.calls_of = lambda name: [c for c in calls if c[0] == name]

    return stub
