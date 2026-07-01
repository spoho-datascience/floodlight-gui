"""Behavioral contracts for ``floodlight_gui.engine.calculate_metric``.

``calculate_metric`` resolves a METRICS_REGISTRY descriptor's
``function_path`` to the upstream callable, calls it with the collected
kwargs, and normalizes the return into a cache-friendly dict. The upstream
metric function (reached via ``importlib.import_module`` + ``getattr``) is the
seam; it is stubbed to return a chosen object so the tests assert only this
module's two decisions: how it dispatches and how it wraps the result.

Behavioral contracts guarded here
---------------------------------
C1  A descriptor missing ``function_path`` raises ``KeyError``.
C2  The dotted ``function_path`` is split into module/attr; the module is
    imported and the attribute called with the kwargs forwarded verbatim.
C3  A ``pandas.DataFrame`` return is wrapped as ``{"dataframe": <df>}`` (the
    same object, not a copy).
C4  A scalar ``int``/``float`` return is wrapped as ``{"value": float(raw)}``.
C5  A floodlight Property-like return (duck-typed by a ``.property``
    attribute) is wrapped as ``{"dataframe": DataFrame(raw.property)}``.
C6  Any other return type falls back to ``{"value": <raw>}`` unchanged.
C7  For a ``zone_aggregation`` descriptor the wrapped DataFrame gains a leading
    ``xID`` column from the row index, so each player row is identifiable;
    other categories are left untouched.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

from floodlight_gui.engine.calculate_metric import calculate_metric

# --------------------------------------------------------------------------- #
# Seam fixture                                                                  #
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_metric(monkeypatch):
    """Install a fake importable metric module with one recording function.

    Returns a callable ``(module_path, fn_name, returns)`` -> the recorder.
    The recorder captures the kwargs it was called with and returns the
    supplied object, isolating ``calculate_metric`` from real floodlight
    analytics. A descriptor pointing at ``module_path.fn_name`` is what the
    test then dispatches.
    """

    def _install(module_path, fn_name, returns):
        recorder = types.SimpleNamespace(kwargs=None)

        def _fn(**kwargs):
            recorder.kwargs = kwargs
            return returns

        mod = types.ModuleType(module_path)
        setattr(mod, fn_name, _fn)
        monkeypatch.setitem(sys.modules, module_path, mod)
        return recorder

    return _install


# --------------------------------------------------------------------------- #
# C1: missing function_path                                                     #
# --------------------------------------------------------------------------- #


def test_missing_function_path_raises_keyerror():
    """C1: a descriptor without function_path raises KeyError."""
    with pytest.raises(KeyError):
        calculate_metric({"display_name": "x"}, {})


# --------------------------------------------------------------------------- #
# C2: importlib dispatch forwards kwargs verbatim                               #
# --------------------------------------------------------------------------- #


def test_dispatch_imports_and_forwards_kwargs(fake_metric):
    """C2: the function is resolved from the dotted path and called with kwargs."""
    recorder = fake_metric("fake_metrics.entropy", "approx_entropy", returns=0.0)
    kwargs = {"sig": np.zeros(3), "m": 2, "r": 0.5}
    calculate_metric({"function_path": "fake_metrics.entropy.approx_entropy"}, kwargs)
    assert recorder.kwargs == kwargs


# --------------------------------------------------------------------------- #
# C3 / C5: DataFrame and Property results -> {"dataframe": ...}                  #
# --------------------------------------------------------------------------- #


def test_dataframe_result_wrapped_unchanged(fake_metric):
    """C3: a DataFrame return is wrapped as {"dataframe": <same object>}."""
    df = pd.DataFrame({"a": [1, 2]})
    fake_metric("fake_metrics.zones", "agg", returns=df)
    result = calculate_metric({"function_path": "fake_metrics.zones.agg"}, {})
    assert result == {"dataframe": df}
    assert result["dataframe"] is df


def test_property_duck_type_wrapped_as_dataframe(fake_metric):
    """C5: a Property-like object (.property ndarray) becomes a DataFrame wrap."""

    class _Prop:
        def __init__(self, arr):
            self.property = arr

    arr = np.arange(6).reshape(3, 2)
    fake_metric("fake_metrics.prop", "compute", returns=_Prop(arr))
    result = calculate_metric({"function_path": "fake_metrics.prop.compute"}, {})
    assert set(result) == {"dataframe"}
    assert isinstance(result["dataframe"], pd.DataFrame)
    assert result["dataframe"].shape == (3, 2)


# --------------------------------------------------------------------------- #
# C4: scalar coercion                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw", [3, 3.5, np.float64(2.0)])
def test_scalar_result_coerced_to_float_value(fake_metric, raw):
    """C4: an int/float scalar is wrapped as {"value": float(raw)}."""
    fake_metric("fake_metrics.fsim", "formation_similarity", returns=raw)
    result = calculate_metric({"function_path": "fake_metrics.fsim.formation_similarity"}, {})
    assert result == {"value": float(raw)}
    assert isinstance(result["value"], float)


# --------------------------------------------------------------------------- #
# C6: fallback                                                                  #
# --------------------------------------------------------------------------- #


def test_other_result_falls_back_to_value(fake_metric):
    """C6: a non-DataFrame, non-scalar, non-Property return is passed through."""
    payload = {"nested": "result"}
    fake_metric("fake_metrics.misc", "f", returns=payload)
    result = calculate_metric({"function_path": "fake_metrics.misc.f"}, {})
    assert result == {"value": payload}
    assert result["value"] is payload


# --------------------------------------------------------------------------- #
# C7: zone_aggregation gains an xID column                                      #
# --------------------------------------------------------------------------- #


def test_zone_aggregation_gets_leading_xid_column(fake_metric):
    """C7: a zone_aggregation result gains a leading xID column from the row index.

    Without it the player-per-row table shows only zone values, so each row is
    unidentifiable in both the results view and the CSV export.
    """
    df = pd.DataFrame({"Low": [1.0, 2.0], "High": [3.0, 4.0]})
    fake_metric("fake_metrics.zones", "agg", returns=df)
    result = calculate_metric(
        {"function_path": "fake_metrics.zones.agg", "category": "zone_aggregation"}, {}
    )
    out = result["dataframe"]
    assert list(out.columns) == ["xID", "Low", "High"]
    assert list(out["xID"]) == [0, 1]


def test_non_zone_dataframe_keeps_columns_unchanged(fake_metric):
    """C7: a DataFrame from a non-zone metric is not given an xID column."""
    df = pd.DataFrame({"a": [1, 2]})
    fake_metric("fake_metrics.other", "f", returns=df)
    result = calculate_metric(
        {"function_path": "fake_metrics.other.f", "category": "complexity"}, {}
    )
    assert list(result["dataframe"].columns) == ["a"]
