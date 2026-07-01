"""Behavioral contracts for ``tabs/_shared/tab_bar``.

``resolve_active_category`` maps a DPG ``tab_bar`` active value to a
category key. The load-bearing contract is that DPG returns the active
child tab's TAG (either a str tag or an int alias-id), never the label;
the resolver parses the ``{prefix}_category_{cat}_tab`` pattern out of the
TAG. DPG is the seam for the int alias-id branch (``get_alias_id``) and
the ``_from_bar`` wrapper (``does_item_exist`` / ``get_value``).

Behavioral contracts guarded here
---------------------------------
resolve_active_category
  C1  A str TAG matching ``{prefix}_category_{cat}_tab`` whose category is
      valid resolves to that category key.
  C2  An int alias-id resolves by matching ``get_alias_id`` of each valid
      category's tag (the alias-id, never the label, is the input).
  C3  Returns None when the parsed category is not in ``valid_categories``,
      when a str does not match the tag pattern, or when no int alias
      matches.

resolve_active_category_from_bar
  C4  Reads the bar's active value and delegates, returning the resolved
      category for an existing bar.
  C5  Returns None without reading the value when the bar does not exist.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs._shared.tab_bar as tb
from floodlight_gui.tabs._shared.tab_bar import (
    resolve_active_category,
    resolve_active_category_from_bar,
)
from tests._dpg_stub import make_dpg_stub

_VALID = {"filter", "interpolation", "spatial"}


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install the shared fake-DPG recorder as the module's ``dpg`` binding.

    The recorder's ``get_alias_id`` is deterministic (``hash(tag) &
    0xFFFFFFFF``), so the int-alias branch can be driven by feeding the
    same value back in as the active alias-id.
    """
    stub = make_dpg_stub()
    monkeypatch.setattr(tb, "dpg", stub)
    return stub


def test_str_tag_resolves_to_category(dpg_stub):
    """C1: a matching str TAG resolves to its category key."""
    result = resolve_active_category(
        "transforms_category_filter_tab", prefix="transforms", valid_categories=_VALID
    )
    assert result == "filter"


def test_int_alias_resolves_to_category(dpg_stub):
    """C2: an int alias-id resolves by matching get_alias_id of a valid tag."""
    alias = dpg_stub.get_alias_id("transforms_category_spatial_tab")
    result = resolve_active_category(alias, prefix="transforms", valid_categories=_VALID)
    assert result == "spatial"


@pytest.mark.parametrize(
    "active",
    [
        "transforms_category_bogus_tab",  # parses but category not valid
        "some_unrelated_widget_tag",  # str does not match the pattern
        999_999,  # int alias matching no valid category
    ],
)
def test_unrecognized_active_returns_none(dpg_stub, active):
    """C3: a non-matching tag, invalid category, or unmatched alias yields None."""
    result = resolve_active_category(active, prefix="transforms", valid_categories=_VALID)
    assert result is None


def test_from_bar_reads_and_delegates(dpg_stub):
    """C4: the wrapper reads the bar value and resolves it for an existing bar."""
    dpg_stub.existing_items.add("bar")
    dpg_stub.values["bar"] = "transforms_category_interpolation_tab"
    result = resolve_active_category_from_bar("bar", prefix="transforms", valid_categories=_VALID)
    assert result == "interpolation"


def test_from_bar_missing_bar_returns_none_without_read(dpg_stub):
    """C5: a missing bar returns None and never reads a value."""
    result = resolve_active_category_from_bar(
        "absent_bar", prefix="transforms", valid_categories=_VALID
    )
    assert result is None
    assert dpg_stub.calls_of("get_value") == []
