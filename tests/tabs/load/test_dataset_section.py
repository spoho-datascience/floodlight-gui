"""Behavioral contracts for ``floodlight_gui.tabs.load.dataset_section``.

This section wires the public-dataset providers (EIGD-H, IDSSE): a dataset
combo, a match selector populated from the registry catalogue, and an Import
button that resolves the match id and hands off to the background worker. The
match catalogue (``available_matches``), the worker launcher, the app accessor,
and the DPG toolkit are the seams; all are stubbed so the tests assert only this
section's selector population, match-id resolution, and dispatch decisions.

Value-triage gate: the silent-corrupting seams are the match-label-to-id map
(``_render_form`` builds it; ``_resolve_match_id`` reads it; a wrong map
silently imports the wrong match), the combo key resolution + match-map reset
in ``_on_combo``, and the ``_on_import`` guards + dispatch. The combo's visible
``items``/``default_value`` (a list the user sees and clicks every session) is
loud-or-cosmetic, so ``_render_form`` is asserted only on the map it builds.

Behavioral contracts guarded here
---------------------------------
_render_form
  C1  Builds the match label-to-id map from the provider catalogue (the map
      ``_resolve_match_id`` later reads to translate a selection into a load).

_resolve_match_id
  C2  The combo selection resolves through the label-to-id map; an empty or
      absent selection yields None.

_on_combo
  C3  Resolves the selected label to its key, resets the match map, and renders
      no form for a cleared selection.

_on_import
  C4  No provider is a no-op; an absent app reports readiness; a ready app
      resolves the match id and starts the background download.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.load.dataset_section as ds

from .conftest import make_load_dpg


@pytest.fixture
def reset_state():
    """Snapshot and restore the module-level session dict around each test."""
    snapshot = dict(ds._S)
    yield
    ds._S.clear()
    ds._S.update(snapshot)


@pytest.fixture
def stub_dpg(monkeypatch):
    """Install the Load-tab DPG recorder as the module's ``dpg`` and return it."""
    stub = make_load_dpg()
    monkeypatch.setattr(ds, "dpg", stub)
    return stub


def test_render_form_builds_match_label_to_id_map(monkeypatch, stub_dpg, reset_state):
    """C1: the catalogue is collapsed into the label->id map _resolve_match_id reads."""
    matches = [
        {"id": "J03WMX", "label": "Koeln vs Bayern"},
        {"id": "J03WN1", "label": "Bochum vs Leverkusen"},
    ]
    monkeypatch.setattr(ds, "available_matches", lambda key: matches)
    monkeypatch.setattr(ds, "prime_button", lambda tag: None)

    ds._render_form("idsse")
    assert ds._S["match_reverse"] == {
        "Koeln vs Bayern": "J03WMX",
        "Bochum vs Leverkusen": "J03WN1",
    }


def test_resolve_match_id_uses_combo_label_to_id_map(stub_dpg, reset_state):
    """C2: the combo selection resolves through the label-to-id mapping."""
    stub_dpg.existing_items.add(ds._MATCH_COMBO_TAG)
    stub_dpg.values[ds._MATCH_COMBO_TAG] = "Koeln vs Bayern"
    ds._S["match_reverse"] = {"Koeln vs Bayern": "J03WMX"}
    assert ds._resolve_match_id() == "J03WMX"


def test_resolve_match_id_none_when_no_widgets(stub_dpg, reset_state):
    """C2: with no match widgets present the resolution is None."""
    ds._S["match_reverse"] = {}
    assert ds._resolve_match_id() is None


def test_on_combo_resolves_key_and_resets_match_map(monkeypatch, stub_dpg, reset_state):
    """C3: a known label resolves to its key and clears the prior match map."""
    rendered = []
    monkeypatch.setattr(ds, "render_provider_help", lambda *a: None)
    monkeypatch.setattr(ds, "_render_form", lambda key: rendered.append(key))
    ds._S["reverse"] = {"EIGD-H": "eigd_h"}
    ds._S["match_reverse"] = {"stale": "x"}

    ds._on_combo(None, "EIGD-H", None)
    assert ds._S["key"] == "eigd_h"
    assert ds._S["match_reverse"] == {}
    assert rendered == ["eigd_h"]


def test_on_combo_cleared_selection_renders_no_form(monkeypatch, stub_dpg, reset_state):
    """C3: an unrecognized label leaves key None and renders no form."""
    rendered = []
    monkeypatch.setattr(ds, "_render_form", lambda key: rendered.append(key))
    ds._S["reverse"] = {"EIGD-H": "eigd_h"}

    ds._on_combo(None, "", None)
    assert ds._S["key"] is None
    assert rendered == []


def test_on_import_without_provider_is_noop(monkeypatch, stub_dpg, reset_state):
    """C4: no selected provider starts no download."""
    started = []
    monkeypatch.setattr(ds, "start_dataset_download", lambda *a: started.append(a))
    monkeypatch.setattr(ds, "get_app", lambda: object())
    ds._S["key"] = None
    ds._on_import()
    assert started == []


def test_on_import_app_not_ready_blocks(monkeypatch, stub_dpg, reset_state):
    """C4: an absent app reports readiness and starts no download."""
    started = []
    monkeypatch.setattr(ds, "start_dataset_download", lambda *a: started.append(a))
    monkeypatch.setattr(ds, "get_app", lambda: None)
    ds._S["key"] = "eigd_h"
    ds._on_import()
    assert started == []
    assert stub_dpg.values[ds._STATUS_TAG] == "App not ready."


def test_on_import_starts_download_with_resolved_match(monkeypatch, stub_dpg, reset_state):
    """C4: a ready app resolves the match id and starts the background download."""
    started = []
    app = object()
    monkeypatch.setattr(ds, "start_dataset_download", lambda *a: started.append(a))
    monkeypatch.setattr(ds, "get_app", lambda: app)
    monkeypatch.setattr(ds, "_resolve_match_id", lambda: "J03WMX")
    ds._S["key"] = "idsse"

    ds._on_import()
    assert started == [(app, "idsse", "J03WMX", ds._STATUS_TAG)]
