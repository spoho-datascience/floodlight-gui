"""Behavioral contracts for ``tabs/_shared/export_action``.

The export helper writes CSV / binary files and emits
``Events.EXPORT_REQUESTED``. The EventBus and DPG toolkit are the seams: the bus
is replaced with a recorder so tests assert the helper's own emission decisions
(not the bus internals), and DPG is the shared fake recorder. CSV / binary
writes use ``tmp_path`` through the session-scoped export folder.

Only the silent-and-corrupting behaviors are guarded: the broadcast loop must
write exactly one CSV per leaf under a per-leaf filename (a shared name would
silently overwrite one export with another), it must emit exactly once even when
a leaf write fails (a double / dropped emit silently de-syncs subscribers), and
the binary path must emit before invoking the writer and refuse an unknown
format before emitting (a spurious emit on a failed export is silent). The
slugify / filename-resolver cosmetics, the folder-picker payload parsing, and
the render-entry-point button chrome are loud-or-cosmetic on a path the user
drives by hand every export, so they are not guarded.

Behavioral contracts guarded here
---------------------------------
_resolve_filename (CSV)
  C2  A broadcast filename carries the per-leaf {artifact}_{period}_{team}
      infix so two leaves never resolve to the same path and silently overwrite.

_do_broadcast_export
  C5  Writes one CSV per DataFrame leaf and emits exactly one EXPORT_REQUESTED
      carrying the export ``kind`` after all writes.
  C6  A per-leaf write error is contained (partial export) and still emits
      exactly one event; the status text reports the first error.
  C7  An empty payload writes nothing and still emits exactly one event.

_do_binary_export
  C8  Resolves the format and emits EXPORT_REQUESTED before invoking the writer
      with the resolved filepath.
  C9  An unknown format (no writer) raises before any emission.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pandas as pd
import pytest

import floodlight_gui.tabs._shared.export_action as ea
from floodlight_gui.core.event_bus import Events
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def bus_recorder(monkeypatch):
    """Replace the module bus with a recorder capturing ``emit`` calls."""
    emitted: list = []
    recorder = SimpleNamespace(emit=lambda event, **kw: emitted.append((event, kw)))
    monkeypatch.setattr(ea, "bus", recorder)
    recorder.emitted = emitted
    return recorder


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install the shared fake-DPG recorder as the module's ``dpg`` binding."""
    stub = make_dpg_stub()
    monkeypatch.setattr(ea, "dpg", stub)
    return stub


@pytest.fixture(autouse=True)
def reset_export_dir():
    """Capture and restore the session-scoped export folder around each test."""
    original = ea.get_export_dir()
    yield
    ea.set_export_dir(original)


# --------------------------------------------------------------------------- #
# C2: per-leaf filename infix prevents silent overwrite
# --------------------------------------------------------------------------- #


def test_broadcast_filename_carries_per_leaf_infix():
    """C2: a broadcast filename embeds the leaf so two leaves never collide."""
    home = ea._resolve_filename(
        user_typed="myfile",
        tab_name="models",
        artifact_name="velocity",
        period="firstHalf",
        team="Home",
        broadcast=True,
    )
    away = ea._resolve_filename(
        user_typed="myfile",
        tab_name="models",
        artifact_name="velocity",
        period="firstHalf",
        team="Away",
        broadcast=True,
    )
    assert home != away
    assert home == "myfile_velocity_firsthalf_home.csv"


# --------------------------------------------------------------------------- #
# C5 / C6 / C7: _do_broadcast_export
# --------------------------------------------------------------------------- #


def _df_leaf(period, team, df, display="velocity"):
    """Build a canonical leaf tuple carrying a DataFrame payload."""
    return (period, team, df, None, "", None, display)


def test_broadcast_writes_one_csv_per_leaf_and_emits_once(tmp_path, bus_recorder, dpg_stub):
    """C5: one CSV per DataFrame leaf is written and a single event is emitted."""
    ea.set_export_dir(str(tmp_path))
    leaves = [
        _df_leaf("firstHalf", "Home", pd.DataFrame({"a": [1, 2]})),
        _df_leaf("firstHalf", "Away", pd.DataFrame({"a": [3, 4]})),
    ]
    ctx = {
        "tab_name": "models",
        "artifact_name": "velocity",
        "kind": "model_all",
        "payload": lambda: leaves,
        "filename_input_tag": "models_export_filename",
    }
    ea._do_broadcast_export(ctx, "models_export_status")
    written = [f for f in os.listdir(tmp_path) if f.endswith(".csv")]
    assert len(written) == 2
    export_events = [e for e in bus_recorder.emitted if e[0] is Events.EXPORT_REQUESTED]
    assert len(export_events) == 1
    assert export_events[0][1]["kind"] == "model_all"


def test_broadcast_partial_failure_still_emits_and_reports(tmp_path, bus_recorder, dpg_stub):
    """C6: a per-leaf error is contained; one event still fires and status reports it."""
    ea.set_export_dir(str(tmp_path))

    class _Boom(pd.DataFrame):
        """A DataFrame whose ``to_csv`` always raises, to force a leaf error."""

        @property
        def _constructor(self):
            return _Boom

        def to_csv(self, *a, **kw):  # noqa: ARG002
            raise OSError("disk full")

    leaves = [
        _df_leaf("firstHalf", "Home", _Boom({"a": [1]})),
        _df_leaf("firstHalf", "Away", pd.DataFrame({"a": [2]})),
    ]
    ctx = {
        "tab_name": "models",
        "artifact_name": "velocity",
        "kind": "model_all",
        "payload": lambda: leaves,
        "filename_input_tag": "models_export_filename",
    }
    ea._do_broadcast_export(ctx, "models_export_status")
    export_events = [e for e in bus_recorder.emitted if e[0] is Events.EXPORT_REQUESTED]
    assert len(export_events) == 1
    status = dpg_stub.values.get("models_export_status", "")
    assert "first error" in status


def test_broadcast_empty_payload_still_emits_once(tmp_path, bus_recorder, dpg_stub):
    """C7: an empty payload writes nothing and still emits exactly one event."""
    ea.set_export_dir(str(tmp_path))
    ctx = {
        "tab_name": "models",
        "artifact_name": "velocity",
        "kind": "model_all",
        "payload": lambda: [],
        "filename_input_tag": "models_export_filename",
    }
    ea._do_broadcast_export(ctx, "models_export_status")
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".csv")]
    export_events = [e for e in bus_recorder.emitted if e[0] is Events.EXPORT_REQUESTED]
    assert len(export_events) == 1


# --------------------------------------------------------------------------- #
# C8 / C9: _do_binary_export
# --------------------------------------------------------------------------- #


def test_binary_export_emits_before_writer(tmp_path, bus_recorder, dpg_stub):
    """C8: binary export emits EXPORT_REQUESTED before calling the writer."""
    ea.set_export_dir(str(tmp_path))
    writer_calls: list = []
    order: list = []
    bus_recorder.emit = lambda event, **kw: (
        order.append("emit"),
        bus_recorder.emitted.append((event, kw)),
    )

    def _writer(filepath):
        order.append("write")
        writer_calls.append(filepath)

    ctx = {
        "formats": ["PNG"],
        "format_tag": "",
        "writer_for_format": {"PNG": _writer},
        "kind": "visualization_image",
        "viz_mode": "frame",
        "n": 7,
        "filename_input_tag": "",
    }
    ea._do_binary_export(ctx, "viz_status")
    assert order == ["emit", "write"]
    assert writer_calls and writer_calls[0].endswith("frame_7.png")


def test_binary_export_unknown_format_raises_before_emit(tmp_path, bus_recorder, dpg_stub):
    """C9: a format with no registered writer raises before any emission."""
    ea.set_export_dir(str(tmp_path))
    ctx = {
        "formats": ["PNG"],
        "format_tag": "",
        "writer_for_format": {},  # no writer for PNG
        "kind": "visualization_image",
        "viz_mode": "frame",
        "n": 1,
        "filename_input_tag": "",
    }
    with pytest.raises(ValueError):
        ea._do_binary_export(ctx, "viz_status")
    assert not [e for e in bus_recorder.emitted if e[0] is Events.EXPORT_REQUESTED]
