"""Behavioral contracts for ``floodlight_gui.tabs.metrics.results``.

The results module projects cached compute results into export payloads. The
silent-corrupting core kept here is the value-normalization in ``_project``
(a wrong projection silently mislabels the exported data) and the cache-key
routing of ``broadcast_payload`` (filtering by the active metric key) plus the
``clear`` state reset. The DPG-drawing leaf renderer, the export tuple's
cosmetic shape (length / display-name slot), and the empty-chrome guards are
dropped: those are visible on the path the user clicks every export.

Behavioral contracts guarded here
---------------------------------
_project (cache dict -> export payload object)
  C1  None -> empty DataFrame; a scalar result -> ``{"value": ...}``; an ndarray
      result -> a DataFrame; a DataFrame result passes through. A wrong branch
      here silently mislabels the exported value.

broadcast_payload (cache-key routing)
  C2  Returns one entry per cached result sharing the active leaf's metric key
      and drops entries under other keys; routing a foreign key into the export
      would silently corrupt the exported set.

clear
  C3  Empties ``state.results`` (state reset).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import floodlight_gui.tabs.metrics.results as results
from floodlight_gui.tabs.metrics import state


class _PanelDouble:
    """Stand-in for ResultsPanel exposing only the active-leaf walk.

    ``active`` is the ``(key, period, team)`` tuple the user is "viewing", or
    None. ``cleared`` records whether ``clear`` was delegated.
    """

    def __init__(self, active=None):
        self._active = active
        self.cleared = False

    def active_leaf(self):
        return self._active

    def clear(self):
        self.cleared = True


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """Reset the metrics result cache and panel for each test."""
    monkeypatch.setattr(state, "results", {})
    monkeypatch.setattr(state, "panel", None)


# --------------------------------------------------------------------------- #
# _project (value normalization -- wrong branch silently mislabels export)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "result, check",
    [
        # None -> empty DataFrame.
        (None, lambda out: isinstance(out, pd.DataFrame) and out.empty),
        # Scalar -> {"value": ...} dict (not wrapped in a frame).
        ({"value": 3.5}, lambda out: out == {"value": 3.5}),
        # ndarray -> DataFrame wrapping the array, shape preserved.
        (
            {"dataframe": np.array([[1.0, 2.0], [3.0, 4.0]])},
            lambda out: isinstance(out, pd.DataFrame) and out.shape == (2, 2),
        ),
    ],
    ids=["none", "scalar", "ndarray"],
)
def test_project_normalizes_by_result_shape(result, check):
    """C1: each cached-result shape projects to its documented export payload."""
    assert check(results._project(result))


def test_project_dataframe_passes_through():
    """C1: a DataFrame result is returned by identity (no copy / reshape)."""
    frame = pd.DataFrame({"a": [1, 2]})
    assert results._project({"dataframe": frame}) is frame


# --------------------------------------------------------------------------- #
# broadcast_payload (cache-key routing)
# --------------------------------------------------------------------------- #


def test_broadcast_payload_routes_only_active_metric_key():
    """C2: only cached entries under the active leaf's metric key are exported.

    Entries under a different metric key must not leak into the broadcast export
    (a silent corruption of the exported set).
    """
    state.panel = _PanelDouble(active=("approx_entropy", "firstHalf", "Home"))
    state.results = {
        ("approx_entropy", "firstHalf", "Home"): {"value": 1.0},
        ("approx_entropy", "secondHalf", "Away"): {"value": 2.0},
        ("formation_similarity", "firstHalf", "Home"): {"value": 9.0},
    }
    keys = {(t[4], t[0], t[1]) for t in results.broadcast_payload()}
    assert keys == {
        ("approx_entropy", "firstHalf", "Home"),
        ("approx_entropy", "secondHalf", "Away"),
    }


def test_broadcast_payload_empty_when_no_active_leaf():
    """C2: broadcast_payload is [] when no leaf is active (nothing to route)."""
    state.panel = _PanelDouble(active=None)
    state.results = {("approx_entropy", "firstHalf", "Home"): {"value": 1.0}}
    assert results.broadcast_payload() == []


# --------------------------------------------------------------------------- #
# clear (state reset)
# --------------------------------------------------------------------------- #


def test_clear_empties_result_cache():
    """C3: clear resets state.results and delegates to the panel."""
    panel = _PanelDouble()
    state.panel = panel
    state.results = {("approx_entropy", "firstHalf", "Home"): {"value": 1.0}}
    results.clear()
    assert state.results == {}
    assert panel.cleared is True
