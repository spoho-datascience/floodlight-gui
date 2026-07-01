"""Behavioral contracts for ``floodlight_gui.tabs.load.file_section``.

This section wires the local-file providers: a provider combo whose change
resets the per-provider session state, file-picker callbacks that stash chosen
paths, and a Load button that validates required inputs before dispatching to
``app.load_provider_data``. The DPG toolkit, the IO registry, and the app
accessor are the seams; all are stubbed so the tests assert only this section's
validation and dispatch decisions, never the loader's behavior.

Value-triage gate: every contract here is a silent-corrupting seam on the load
path -- a wrong key/path resolution, a skipped required-file guard, or an
app-not-ready guard. A bad dispatch silently loads nothing or the wrong data
with no crash. The file-provider load is fully synchronous (``_on_load`` calls
``app.load_provider_data`` and returns), so ``test_load_flow_dispatches_real_provider``
drives the real provider-combo -> picker -> Load chain end to end against a
recording app double.

Behavioral contracts guarded here
---------------------------------
_on_combo
  C1  Resolves the selected display label to its registry key, resets the
      per-provider session state, and renders no form for a cleared selection.

_make_picker_result
  C2  Stores the chosen path under its param, preferring an explicit selection
      and falling back to the dialog's file_path_name; an empty result is a
      no-op.

_on_load
  C3  Does nothing when no provider is selected.
  C4  Blocks the load and reports the missing labels when a required file
      input is unchosen.
  C5  With all required inputs chosen, dispatches the key, file paths, and
      extra params to the loader and reports the outcome.
  C6  Reports "App not ready." and dispatches nothing when the app is absent.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.load.file_section as fs

from .conftest import make_load_dpg


@pytest.fixture
def reset_state():
    """Snapshot and restore the module-level session dict around each test."""
    snapshot = dict(fs._S)
    yield
    fs._S.clear()
    fs._S.update(snapshot)


@pytest.fixture
def stub_dpg(monkeypatch):
    """Install the Load-tab DPG recorder as the module's ``dpg`` and return it."""
    stub = make_load_dpg()
    monkeypatch.setattr(fs, "dpg", stub)
    return stub


def test_on_combo_resolves_key_and_resets_state(monkeypatch, stub_dpg, reset_state):
    """C1: a known label resolves to its key and clears prior session state."""
    rendered = []
    monkeypatch.setattr(fs, "render_provider_help", lambda *a: None)
    monkeypatch.setattr(fs, "_render_form", lambda key: rendered.append(key))

    fs._S["reverse"] = {"DFL Tracking": "dfl"}
    fs._S["file_paths"] = {"old": "/stale"}
    fs._S["extra_widgets"] = {"old": "tag"}

    fs._on_combo(None, "DFL Tracking", None)
    assert fs._S["key"] == "dfl"
    assert fs._S["file_paths"] == {}
    assert fs._S["extra_widgets"] == {}
    assert rendered == ["dfl"]


def test_on_combo_cleared_selection_renders_no_form(monkeypatch, stub_dpg, reset_state):
    """C1: an unrecognized/empty label leaves key None and renders no form."""
    rendered = []
    monkeypatch.setattr(fs, "_render_form", lambda key: rendered.append(key))
    fs._S["reverse"] = {"DFL Tracking": "dfl"}

    fs._on_combo(None, "", None)
    assert fs._S["key"] is None
    assert rendered == []


@pytest.mark.parametrize(
    "app_data, expected",
    [
        ({"selections": {"f": "/picked/file.xml"}}, "/picked/file.xml"),
        ({"selections": {}, "file_path_name": "/typed/name.xml"}, "/typed/name.xml"),
    ],
)
def test_picker_result_stores_chosen_path(stub_dpg, reset_state, app_data, expected):
    """C2: the picker stores the chosen path, preferring an explicit selection."""
    fs._S["file_paths"] = {}
    fs._make_picker_result("filepath_pos")(None, app_data, None)
    assert fs._S["file_paths"]["filepath_pos"] == expected


def test_picker_result_empty_is_noop(stub_dpg, reset_state):
    """C2: an empty dialog result stores nothing."""
    fs._S["file_paths"] = {}
    fs._make_picker_result("filepath_pos")(None, {"selections": {}, "file_path_name": ""}, None)
    assert fs._S["file_paths"] == {}


def test_on_load_without_provider_does_nothing(monkeypatch, stub_dpg, reset_state):
    """C3: no selected provider means no load and no status write."""
    called = []
    monkeypatch.setattr(fs, "get_app", lambda: _RecordingApp(called))
    fs._S["key"] = None
    fs._on_load()
    assert called == []
    assert not stub_dpg.calls_of("set_value")


def test_on_load_missing_required_blocks_and_reports(monkeypatch, stub_dpg, reset_state):
    """C4: a missing required input blocks the load and names the gap."""
    called = []
    monkeypatch.setattr(fs, "get_app", lambda: _RecordingApp(called))
    monkeypatch.setattr(
        fs,
        "IO_REGISTRY",
        {"dfl": {"display_name": "DFL", "file_inputs": {"filepath_pos": {"required": True}}}},
    )
    fs._S["key"] = "dfl"
    fs._S["file_paths"] = {}
    fs._S["extra_widgets"] = {}

    fs._on_load()
    assert called == []
    status = stub_dpg.values[fs._STATUS_TAG]
    assert status.startswith("Missing required file(s):")


@pytest.mark.parametrize(
    "load_ok, expected_fragment",
    [(True, "Loaded DFL."), (False, "Load failed (see log).")],
)
def test_on_load_dispatches_and_reports_outcome(
    monkeypatch, stub_dpg, reset_state, load_ok, expected_fragment
):
    """C5: a complete form dispatches to the loader and reports its outcome."""
    called = []
    monkeypatch.setattr(fs, "get_app", lambda: _RecordingApp(called, returns=load_ok))
    monkeypatch.setattr(
        fs,
        "IO_REGISTRY",
        {"dfl": {"display_name": "DFL", "file_inputs": {"filepath_pos": {"required": True}}}},
    )
    fs._S["key"] = "dfl"
    fs._S["file_paths"] = {"filepath_pos": "/picked.xml"}
    fs._S["extra_widgets"] = {}

    fs._on_load()
    assert called == [("dfl", {"filepath_pos": "/picked.xml"}, {})]
    assert stub_dpg.values[fs._STATUS_TAG] == expected_fragment


def test_on_load_app_not_ready_blocks_dispatch(monkeypatch, stub_dpg, reset_state):
    """C6: an absent app reports readiness and dispatches nothing."""
    monkeypatch.setattr(fs, "get_app", lambda: None)
    monkeypatch.setattr(
        fs,
        "IO_REGISTRY",
        {"dfl": {"display_name": "DFL", "file_inputs": {"filepath_pos": {"required": True}}}},
    )
    fs._S["key"] = "dfl"
    fs._S["file_paths"] = {"filepath_pos": "/picked.xml"}
    fs._S["extra_widgets"] = {}

    fs._on_load()
    assert stub_dpg.values[fs._STATUS_TAG] == "App not ready."


def test_load_flow_dispatches_real_provider(monkeypatch, stub_dpg, reset_state):
    """Flow: select a real file provider -> seed its file widget -> Load -> dispatch.

    Drives the real synchronous file-load chain end to end against the live
    IO_REGISTRY (no descriptor monkeypatch): the provider combo resolves
    ``tracab`` and renders its form through ``_render_form``; the file-dialog
    result callback seeds the required ``filepath_dat`` path; and the public
    ``_on_load`` handler dispatches to the loader exactly once carrying the
    provider key and the chosen file path. The loaded outcome routes back to
    the status text on the same call (the file path is synchronous, so there is
    no worker hop to drive).
    """
    called = []
    monkeypatch.setattr(fs, "get_app", lambda: _RecordingApp(called, returns=True))
    # The help/form renderers reach real DPG via section_helpers; no-op them so the
    # flow exercises only the key-resolution -> picker -> load decisions.
    monkeypatch.setattr(fs, "render_provider_help", lambda *a: None)
    monkeypatch.setattr(fs, "_render_form", lambda key: None)

    # Build the live display->key reverse map and select the real tracab provider.
    keys = fs.file_provider_keys()
    _items, reverse = fs.combo_items(keys)
    fs._S["reverse"] = reverse
    tracab_label = next(lbl for lbl, k in reverse.items() if k == "tracab")
    fs._on_combo(None, tracab_label, None)
    assert fs._S["key"] == "tracab"

    # Seed the required file input through the real file-dialog result callback.
    fs._make_picker_result("filepath_dat")(None, {"selections": {"f": "/data/match.dat"}}, None)

    fs._on_load()

    assert called == [("tracab", {"filepath_dat": "/data/match.dat"}, {})]
    assert stub_dpg.values[fs._STATUS_TAG].startswith("Loaded")


class _RecordingApp:
    """App double recording ``load_provider_data`` calls and returning a flag."""

    def __init__(self, sink, returns=True):
        self._sink = sink
        self._returns = returns

    def load_provider_data(self, key, file_paths, **extra):
        self._sink.append((key, file_paths, extra))
        return self._returns
