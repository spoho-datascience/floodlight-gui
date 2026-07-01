"""Behavioral contracts for ``floodlight_gui.tabs.load.pitch_section``.

This section is the collapsible Pitch builder. It reads an editable form back
into ``Pitch(**kwargs)`` and replaces the active pitch through the app's single
producer path. The DPG toolkit and the app accessor are the seams; the form
state map (``_S["widgets"]``) is seeded directly so the tests assert this
section's own kwarg collection and dispatch decisions.

Value-triage gate: the silent-corrupting seams are ``_collect_kwargs`` (a wrong
kwarg -- a sentinel kept, a tuple half-read -- silently feeds a malformed pitch
into the coordinate system that drives every later visualization) and the
``_on_create`` guard + dispatch (replacing the pitch through ``app.replace_pitch``,
or correctly refusing to when no data is loaded). Dropped from this file:
``discover_templates`` (the template name list is visible in the combo every
session) and ``_fill_form`` (form pre-fill display the user sees and can correct
on screen) -- both loud-or-cosmetic.

Behavioral contracts guarded here
---------------------------------
_collect_kwargs
  C1  Reads the form into Pitch kwargs: tuple params come from the lo/hi pair;
      blank optional-floats and the "None"/empty sentinels are omitted; present
      values are kept (optional-float coerced to float).

_on_create
  C2  With no loaded data, reports the guidance and does not replace the pitch;
      with loaded data, builds a Pitch and replaces it through the app.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.load.pitch_section as ps

from .conftest import make_load_dpg


@pytest.fixture
def reset_state():
    """Snapshot and restore the module-level widgets dict around each test."""
    snapshot = dict(ps._S)
    yield
    ps._S.clear()
    ps._S.update(snapshot)


@pytest.fixture
def stub_dpg(monkeypatch):
    """Install the Load-tab DPG recorder as the module's ``dpg`` and return it."""
    stub = make_load_dpg()
    monkeypatch.setattr(ps, "dpg", stub)
    return stub


def test_collect_kwargs_reads_form_and_omits_sentinels(stub_dpg, reset_state):
    """C1: tuples come from lo/hi, sentinels are dropped, present values kept."""
    ps._S["widgets"] = {
        "xlim": ("xlim_tag", "tuple[float, float]"),
        "length": ("length_tag", "string"),
        "width": ("width_tag", "string"),
        "sport": ("sport_tag", "enum"),
        "unit": ("unit_tag", "enum"),
    }
    stub_dpg.values.update(
        {
            "xlim_tag__lo": -52.5,
            "xlim_tag__hi": 52.5,
            "length_tag": "105",  # optional float, present
            "width_tag": "  ",  # optional float, blank -> omitted
            "sport_tag": "None",  # sentinel -> omitted
            "unit_tag": "m",  # plain enum string, kept
        }
    )

    kwargs = ps._collect_kwargs()
    assert kwargs["xlim"] == (-52.5, 52.5)
    assert kwargs["length"] == 105.0
    assert "width" not in kwargs
    assert "sport" not in kwargs
    assert kwargs["unit"] == "m"


def test_collect_kwargs_omits_empty_string(stub_dpg, reset_state):
    """C1: an empty string param falls through to the floodlight default."""
    ps._S["widgets"] = {"sport": ("sport_tag", "string")}
    stub_dpg.values["sport_tag"] = ""
    assert "sport" not in ps._collect_kwargs()


def test_on_create_without_loaded_data_reports_guidance(monkeypatch, stub_dpg, reset_state):
    """C2: missing loaded data blocks the replace and reports guidance."""
    replaced = []

    class _App:
        store = None

        def replace_pitch(self, pitch):
            replaced.append(pitch)

    monkeypatch.setattr(ps, "get_app", lambda: _App())
    ps._on_create()
    assert replaced == []
    assert "Load data first" in stub_dpg.values[ps._STATUS_TAG]


def test_on_create_builds_and_replaces_pitch(monkeypatch, stub_dpg, reset_state):
    """C2: with loaded data a Pitch is built and replaced through the app."""
    replaced = []

    class _Store:
        loaded_data = ("pitch", None, None, None)

    class _App:
        store = _Store()

        def replace_pitch(self, pitch):
            replaced.append(pitch)

    monkeypatch.setattr(ps, "get_app", lambda: _App())
    # Status string reads pitch.xlim/ylim; give the built pitch those attributes.
    sentinel = type("P", (), {"xlim": (-52.5, 52.5), "ylim": (-34.0, 34.0)})()
    monkeypatch.setattr(ps, "_build_pitch", lambda: sentinel)

    ps._on_create()
    assert replaced == [sentinel]
    assert "Pitch created" in stub_dpg.values[ps._STATUS_TAG]
