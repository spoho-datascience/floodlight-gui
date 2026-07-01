"""Behavioral contracts for ``tabs/_shared/results_panel``.

``ResultsPanel`` owns a four-level DPG tab hierarchy (key -> period -> team ->
leaf) and exposes rebuild / refresh_leaf / clear / active_leaf. The DPG toolkit
is the seam and is replaced with the shared fake recorder (extended with
``add_tab`` / ``get_item_user_data``). Only the routing decisions are guarded
here: which leaf body is delegated (and in what de-duplicated order), which bar
each refresh selects, where a refreshed leaf's counts are sourced from, and the
active-leaf walk. A wrong answer on any of these silently renders or routes the
wrong result, with no visible error. The build / empty-state / summary-text /
tag-sanitizing chrome is loud-or-cosmetic and seen on every render, so it is not
guarded.

Behavioral contracts guarded here
---------------------------------
ResultsPanel
  C2  rebuild delegates exactly one leaf body per de-duplicated (key, period,
      team) triple, preserving first-seen order. A wrong order / missed dedup
      silently renders the wrong leaf set.
  C5  refresh_leaf renders the requested leaf and selects it in each bar (outer
      -> period -> team) so focus lands on the new result, not a stale one.
  C6  refresh_leaf with no explicit counts sources them from count_provider; the
      wrong source silently mislabels the summary.
  C8  active_leaf returns the (key, period, team) read from each bar's active
      child user_data, and None when any level is missing -- a wrong read routes
      a downstream action to the wrong leaf.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs._shared.results_panel as rp
from floodlight_gui.tabs._shared.results_panel import ResultsPanel
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install an extended fake-DPG recorder as the module's ``dpg`` binding.

    ResultsPanel uses ``add_tab`` (leaf creation) and ``get_item_user_data``
    (active-leaf walk), which the shared recorder does not ship; both are added
    here with the same record-and-register semantics, plus a ``user_data`` store
    so the active-leaf walk can read back what a tab was created with.
    """
    stub = make_dpg_stub()
    user_data_store: dict = {}

    def _add_tab(*a, **kw):
        tag = kw.get("tag")
        if tag:
            stub.existing_items.add(tag)
            user_data_store[tag] = kw.get("user_data")
        stub.calls.append(("add_tab", a, kw))
        return tag

    stub.add_tab = _add_tab

    # The container ``tab`` CM also carries user_data; capture it too.
    original_tab = stub.tab

    def _tab(*a, **kw):
        tag = kw.get("tag")
        if tag:
            user_data_store[tag] = kw.get("user_data")
        return original_tab(*a, **kw)

    stub.tab = _tab

    def _get_item_user_data(tag):
        stub.calls.append(("get_item_user_data", (tag,), {}))
        return user_data_store.get(tag)

    stub.get_item_user_data = _get_item_user_data
    stub.user_data_store = user_data_store
    monkeypatch.setattr(rp, "dpg", stub)
    return stub


@pytest.fixture
def captured_leaves():
    """Return a list capturing each ``render_leaf`` call's arguments."""
    return []


@pytest.fixture
def make_panel(captured_leaves):
    """Factory building a ResultsPanel whose render_leaf records its arguments."""

    def _build(**overrides):
        def _render(key, period, team, leaf_tag):
            captured_leaves.append((key, period, team, leaf_tag))

        kwargs = {
            "prefix": "model",
            "display_name": lambda k: f"DN:{k}",
            "render_leaf": _render,
            "noun": "model",
        }
        kwargs.update(overrides)
        return ResultsPanel(**kwargs)

    return _build


def _seed_outer_bar(stub, prefix="model"):
    """Register the three owner-created widget tags so the panel is not a no-op."""
    stub.existing_items.add(f"{prefix}_results_outer_tab_bar")
    stub.existing_items.add(f"{prefix}_results_placeholder")
    stub.existing_items.add(f"{prefix}_results_info")


# --------------------------------------------------------------------------- #
# C2: rebuild delegates one leaf per unique triple, in order
# --------------------------------------------------------------------------- #


def test_rebuild_delegates_one_leaf_per_unique_triple(dpg_stub, make_panel, captured_leaves):
    """C2: rebuild renders one leaf per de-duplicated triple in first-seen order."""
    _seed_outer_bar(dpg_stub)
    panel = make_panel()
    entries = [
        ("centroid", "firstHalf", "Home"),
        ("centroid", "firstHalf", "Away"),
        ("centroid", "firstHalf", "Home"),  # duplicate, ignored
        ("velocity", "firstHalf", "Home"),
    ]
    panel.rebuild(entries)
    rendered = [(k, p, t) for (k, p, t, _tag) in captured_leaves]
    assert rendered == [
        ("centroid", "firstHalf", "Home"),
        ("centroid", "firstHalf", "Away"),
        ("velocity", "firstHalf", "Home"),
    ]


# --------------------------------------------------------------------------- #
# C5: refresh_leaf renders the leaf and selects it in every bar
# --------------------------------------------------------------------------- #


def test_refresh_leaf_renders_and_selects_each_bar(dpg_stub, make_panel, captured_leaves):
    """C5: refresh_leaf renders the leaf and selects it in the outer/period/team bars."""
    _seed_outer_bar(dpg_stub)
    panel = make_panel()
    panel.refresh_leaf("centroid", "firstHalf", "Home", entries_count=1, key_count=1)
    assert ("centroid", "firstHalf", "Home") in [(k, p, t) for (k, p, t, _tag) in captured_leaves]
    # Each bar is set to its active child so focus lands on the new leaf.
    selected_bars = {c[1][0] for c in dpg_stub.calls_of("set_value")}
    assert panel.outer_bar_tag in selected_bars
    assert panel._period_bar_tag("centroid") in selected_bars
    assert panel._team_bar_tag("centroid", "firstHalf") in selected_bars


# --------------------------------------------------------------------------- #
# C6: refresh_leaf sources counts from the provider when none are passed
# --------------------------------------------------------------------------- #


def test_refresh_leaf_uses_count_provider_when_no_explicit_counts(dpg_stub, make_panel):
    """C6: refresh_leaf pulls counts from count_provider when none are passed."""
    _seed_outer_bar(dpg_stub)
    calls = []

    def _provider():
        calls.append(True)
        return (7, 3)

    panel = make_panel(count_provider=_provider)
    panel.refresh_leaf("centroid", "firstHalf", "Home")
    assert calls  # provider consulted
    info_sets = [c for c in dpg_stub.calls_of("set_value") if c[1][0] == "model_results_info"]
    assert info_sets[-1][1][1].startswith("7 result(s) across 3 model(s)")


# --------------------------------------------------------------------------- #
# C8: active_leaf walk
# --------------------------------------------------------------------------- #


def test_active_leaf_reads_selected_user_data(dpg_stub, make_panel):
    """C8: active_leaf returns the (key, period, team) from each bar's active child."""
    _seed_outer_bar(dpg_stub)
    panel = make_panel()
    panel.rebuild([("centroid", "firstHalf", "Home")])
    # Point each bar at the leaf's chain of tabs (get_value returns the child tag).
    dpg_stub.values[panel.outer_bar_tag] = panel._key_tab_tag("centroid")
    dpg_stub.values[panel._period_bar_tag("centroid")] = panel._period_tab_tag(
        "centroid", "firstHalf"
    )
    dpg_stub.values[panel._team_bar_tag("centroid", "firstHalf")] = panel._leaf_tab_tag(
        "centroid", "firstHalf", "Home"
    )
    assert panel.active_leaf() == ("centroid", "firstHalf", "Home")


def test_active_leaf_none_when_level_unselected(dpg_stub, make_panel):
    """C8: active_leaf returns None when a bar has no active selection."""
    _seed_outer_bar(dpg_stub)
    panel = make_panel()
    panel.rebuild([("centroid", "firstHalf", "Home")])
    # Outer bar has no selection seeded -> get_value returns None.
    assert panel.active_leaf() is None
