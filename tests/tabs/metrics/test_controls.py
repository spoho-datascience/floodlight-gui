"""Behavioral contracts for ``floodlight_gui.tabs.metrics.controls``.

The controls module is mostly the DPG layout builder (pure drawing, dropped)
plus EventBus wiring. Two callbacks carry silent-corrupting logic and are kept:
caching the app reference on APP_INITIALIZED (state wiring read by every
collector), and resolving the Step-2 metric selection into
``state.selected_metric_key`` -- the output selection that feeds compute. A
wrong key silently runs the wrong metric; an unresolvable display must NOT leave
a stale key live. ``select`` / ``params`` are collaborators owned elsewhere and
are stubbed; the DPG combo is the seam.

Behavioral contracts guarded here
---------------------------------
_on_app_initialized
  C1  Caches the ``app`` from the event payload into ``state.app_instance``.

_on_metric_change (output selection feeding compute)
  C2  Resolves the combo's display name to a registry key via
      ``select.key_for_display`` and stores it; an unresolvable display stores
      None and skips the input/param rebuilds (no stale metric runs).
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.metrics.controls as controls
from floodlight_gui.tabs.metrics import state
from tests._dpg_stub import make_dpg_stub


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """Reset the metrics state for each test."""
    monkeypatch.setattr(state, "app_instance", None)
    monkeypatch.setattr(state, "selected_metric_key", None)


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install the fake-DPG stub on the controls module and seed the metric combo.

    Returns a callable ``(display) -> stub`` that registers the Step-2 type combo
    with the given display value.
    """

    def _install(display):
        stub = make_dpg_stub(
            values={controls.TYPE_COMBO: display},
            existing_items={controls.TYPE_COMBO},
        )
        monkeypatch.setattr(controls, "dpg", stub)
        return stub

    return _install


def test_on_app_initialized_caches_app():
    """C1: the app from the APP_INITIALIZED payload is cached into state."""
    sentinel = object()
    controls._on_app_initialized(app=sentinel)
    assert state.app_instance is sentinel


def test_on_metric_change_resolves_and_stores_key(monkeypatch, dpg_stub):
    """C2: a resolvable display name is stored as the selected metric key."""
    dpg_stub("Approximate Entropy")
    monkeypatch.setattr(controls.select, "key_for_display", lambda d: "approx_entropy")
    monkeypatch.setattr(controls, "_rebuild_help", lambda: None)
    monkeypatch.setattr(controls.params, "rebuild_inputs", lambda k: None)
    monkeypatch.setattr(controls.params, "rebuild_params", lambda k: None)

    controls._on_metric_change()
    assert state.selected_metric_key == "approx_entropy"


def test_on_metric_change_unresolvable_keeps_no_stale_key(monkeypatch, dpg_stub):
    """C2: an unresolvable display stores None and skips the rebuilds.

    Guards the silent-corrupting path: a stale ``selected_metric_key`` would let
    compute run the previously-picked metric against the new selection.
    """
    state.selected_metric_key = "prior"
    dpg_stub("Mystery Metric")
    monkeypatch.setattr(controls.select, "key_for_display", lambda d: None)

    rebuilt = []
    monkeypatch.setattr(controls.params, "rebuild_inputs", lambda k: rebuilt.append(k))
    monkeypatch.setattr(controls.params, "rebuild_params", lambda k: rebuilt.append(k))

    controls._on_metric_change()
    assert state.selected_metric_key is None
    assert rebuilt == []
