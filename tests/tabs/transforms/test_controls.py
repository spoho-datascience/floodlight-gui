"""Behavioral contracts for ``tabs.transforms.controls``.

``controls`` is mostly the DPG layout builder plus a few EventBus handlers that
delegate to the select/results refreshers. The one piece of tab-owned state
logic worth guarding is the period-combo handler: it bridges the raw combo
value into ``state._transforms_selected_period_internal``, mapping the "All"
sentinel to None (broadcast) and a real period to its internal key. The refresh
collaborators it also calls are stubbed so the test asserts only the bridging.

Behavioral contracts guarded here
---------------------------------
_on_transforms_period_changed
  C1  Bridges a real period display value to its internal key in state.
  C2  Bridges the "All" sentinel to None (broadcast signal).
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.transforms.controls as controls
import floodlight_gui.tabs.transforms.results as results
import floodlight_gui.tabs.transforms.select as select
import floodlight_gui.tabs.transforms.state as state


@pytest.fixture(autouse=True)
def _stub_refreshers(monkeypatch):
    """Neutralize the DPG-touching refreshers the period handler calls.

    ``_on_transforms_period_changed`` calls ``select._update_target_summary``
    and ``results._refresh_stack_display`` after updating state; both touch the
    DPG tree. Replacing them with no-ops keeps the test DPG-free and focused on
    the bridging decision.
    """
    monkeypatch.setattr(select, "_update_target_summary", lambda: None)
    monkeypatch.setattr(results, "_refresh_stack_display", lambda: None)


@pytest.mark.parametrize(
    "app_data, expected",
    [
        ("First Half", "firstHalf"),
        ("All", None),
    ],
)
def test_period_changed_bridges_into_state(monkeypatch, app_data, expected):
    """C1/C2: the period handler bridges the combo value into internal state.

    A real period maps to its internal key; the "All" sentinel maps to None to
    signal broadcast scope to downstream consumers.
    """
    monkeypatch.setattr(state, "_transforms_selected_period_internal", "stale")
    controls._on_transforms_period_changed(sender=None, app_data=app_data)
    assert state._transforms_selected_period_internal == expected
