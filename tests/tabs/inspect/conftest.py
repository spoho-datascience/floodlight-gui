"""Local fixtures for the inspect-tab suite.

The DPG toolkit is the seam for the inspect tab's DPG-aware modules
(``leaves``, ``engine``, ``controls``). The shared ``make_dpg_stub`` does not
model the item-children / item-type / item-label queries that ``controls``
needs, nor the tag-tree that ``engine`` builds. This module provides:

- ``fake_dpg``: a fake-DPG namespace that records widget creation into a
  parent-keyed tree and answers ``get_item_children`` / ``get_item_type`` /
  ``get_item_label`` / ``get_value`` / ``set_value`` against that tree. It is
  monkeypatched in by the per-module tests onto the ``dpg`` reference of the
  module under test.
- ``app_double``: a lightweight fake exposing the read accessors the
  collectors call (``event_data``, ``position_data``, ``teamsheet``,
  ``get_temporal_divisions``, ``get_team_names``, etc.).

No fixture here imports dearpygui.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
import pytest


class _FakeDpg:
    """Record-and-query fake of the DPG surface the inspect tab calls.

    Items are stored in ``items`` keyed by tag, each a dict with ``type``,
    ``label``, ``value``, ``children`` (list of child tags), ``parent``, and
    ``config`` (kwargs from ``configure_item``). Container context managers and
    add-* widget builders register items and append them to the active
    parent's child list. ``get_item_children`` returns the slot-1 child list,
    matching the DPG convention the inspect controls rely on.
    """

    mvTable_SizingFixedFit = 8192

    def __init__(self) -> None:
        self.items: dict = {}
        self._stack: list[str] = []
        self.calls: list[tuple] = []

    # -- registration helpers ------------------------------------------- #

    def _register(self, tag, item_type, *, label=None, value=None, parent=None):
        if tag is None:
            tag = f"__auto_{len(self.items)}"
        parent = parent or (self._stack[-1] if self._stack else None)
        self.items[tag] = {
            "type": item_type,
            "label": label,
            "value": value,
            "children": [],
            "parent": parent,
            "config": {},
        }
        if parent is not None and parent in self.items:
            self.items[parent]["children"].append(tag)
        return tag

    @contextmanager
    def _container(self, item_type, *args, **kwargs):
        tag = self._register(
            kwargs.get("tag"),
            item_type,
            label=kwargs.get("label"),
            parent=kwargs.get("parent"),
        )
        self.calls.append((item_type, args, kwargs))
        self._stack.append(tag)
        try:
            yield tag
        finally:
            self._stack.pop()

    # -- container context managers ------------------------------------- #

    def tab(self, *a, **kw):
        return self._container("mvAppItemType::mvTab", *a, **kw)

    def table(self, *a, **kw):
        return self._container("mvAppItemType::mvTable", *a, **kw)

    def table_row(self, *a, **kw):
        return self._container("mvAppItemType::mvTableRow", *a, **kw)

    def group(self, *a, **kw):
        return self._container("mvAppItemType::mvGroup", *a, **kw)

    def collapsing_header(self, *a, **kw):
        return self._container("mvAppItemType::mvCollapsingHeader", *a, **kw)

    def child_window(self, *a, **kw):
        return self._container("mvAppItemType::mvChildWindow", *a, **kw)

    # -- add-widget builders -------------------------------------------- #

    def add_text(self, text="", **kwargs):
        return self._register(
            kwargs.get("tag"),
            "mvAppItemType::mvText",
            value=text,
            parent=kwargs.get("parent"),
        )

    def add_tab_bar(self, **kwargs):
        return self._register(
            kwargs.get("tag"),
            "mvAppItemType::mvTabBar",
            parent=kwargs.get("parent"),
        )

    def add_group(self, **kwargs):
        return self._register(
            kwargs.get("tag"),
            "mvAppItemType::mvGroup",
            parent=kwargs.get("parent"),
        )

    def add_checkbox(self, **kwargs):
        return self._register(
            kwargs.get("tag"),
            "mvAppItemType::mvCheckbox",
            label=kwargs.get("label"),
            value=kwargs.get("default_value", False),
            parent=kwargs.get("parent"),
        )

    def add_combo(self, **kwargs):
        return self._register(
            kwargs.get("tag"),
            "mvAppItemType::mvCombo",
            label=kwargs.get("label"),
            value=kwargs.get("default_value"),
            parent=kwargs.get("parent"),
        )

    def add_button(self, **kwargs):
        return self._register(
            kwargs.get("tag"),
            "mvAppItemType::mvButton",
            label=kwargs.get("label"),
            parent=kwargs.get("parent"),
        )

    def add_table_column(self, **kwargs):
        return self._register(
            kwargs.get("tag"),
            "mvAppItemType::mvTableColumn",
            label=kwargs.get("label"),
            parent=kwargs.get("parent") or (self._stack[-1] if self._stack else None),
        )

    # -- queries / mutations -------------------------------------------- #

    def does_item_exist(self, tag):
        return tag in self.items

    def get_item_children(self, tag, slot=1):
        item = self.items.get(tag)
        return list(item["children"]) if item else []

    def get_item_type(self, tag):
        item = self.items.get(tag)
        return item["type"] if item else None

    def get_item_label(self, tag):
        item = self.items.get(tag)
        return item["label"] if item else None

    def get_value(self, tag):
        item = self.items.get(tag)
        return item["value"] if item else None

    def set_value(self, tag, value):
        if tag in self.items:
            self.items[tag]["value"] = value

    def configure_item(self, tag, **kwargs):
        if tag in self.items:
            self.items[tag]["config"].update(kwargs)

    def delete_item(self, tag, children_only=False):
        item = self.items.get(tag)
        if item is None:
            return
        for child in list(item["children"]):
            self.delete_item(child, children_only=False)
        item["children"] = []
        if not children_only:
            self.items.pop(tag, None)

    # -- test conveniences ---------------------------------------------- #

    def texts_under(self, tag) -> list[str]:
        """Return the value of every descendant text item under ``tag``."""
        out: list[str] = []
        item = self.items.get(tag)
        if item is None:
            return out
        for child in item["children"]:
            citem = self.items[child]
            if citem["type"] == "mvAppItemType::mvText":
                out.append(citem["value"])
            out.extend(self.texts_under(child))
        return out

    def add_root(self, tag, item_type="mvAppItemType::mvChildWindow"):
        """Seed a pre-existing root container so children can attach to it."""
        self.items[tag] = {
            "type": item_type,
            "label": None,
            "value": None,
            "children": [],
            "parent": None,
            "config": {},
        }
        return tag


@pytest.fixture
def fake_dpg():
    """Return a fresh record-and-query fake DPG namespace.

    Tests monkeypatch this onto the ``dpg`` attribute of the module under
    test so DPG calls are recorded instead of hitting a real context.
    """
    return _FakeDpg()


class _AppDouble:
    """Fake app exposing the read accessors the inspect collectors call.

    Only the accessors used by ``collect`` are implemented. Each attribute is
    supplied verbatim by the test so collectors see realistic shapes (the real
    nested grouped event/position trees, flat teamsheet dicts, Code dicts under
    internal period keys, and a Pitch object).
    """

    def __init__(
        self,
        *,
        event_data=None,
        position_data=None,
        position_structure=None,
        teamsheet=None,
        possession_data=None,
        ball_status=None,
        pitch=None,
        divisions=None,
        teams=None,
        data_format="test_format",
        active_xy=None,
    ):
        self.event_data = event_data
        self.position_data = position_data
        self._position_structure = position_structure
        self.teamsheet = teamsheet
        self.possession_data = possession_data
        self.ball_status = ball_status
        self.pitch = pitch
        self._divisions = divisions or []
        self._teams = teams or []
        self._data_format = data_format
        self._active_xy = active_xy or {}

    def get_temporal_divisions(self):
        return list(self._divisions)

    def get_team_names(self):
        return list(self._teams)

    def get_position_data_structure(self):
        return self._position_structure

    def get_data_format(self):
        return self._data_format

    def get_active_xy(self, period_internal, entity):
        return self._active_xy.get((period_internal, entity))


@pytest.fixture
def app_double():
    """Factory building an ``_AppDouble`` with the supplied accessors."""

    def _build(**kwargs):
        return _AppDouble(**kwargs)

    return _build


def make_events_obj(df: pd.DataFrame):
    """Wrap a DataFrame as a floodlight-Events-like object (``.events``)."""
    return SimpleNamespace(events=df)
