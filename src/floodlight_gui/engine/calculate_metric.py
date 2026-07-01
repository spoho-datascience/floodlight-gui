"""Generic metric executor driven by METRICS_REGISTRY.

Resolves a descriptor's ``function_path`` to the upstream callable, calls it
with the already-collected kwargs, and normalizes the result into a
cache-friendly dict.

DPG-free invariant: this module never imports dearpygui. Input collection,
caching, and rendering stay in ``tabs/metrics/execute.py``; this module is the
pure compute layer that the callback delegates to.
"""

from __future__ import annotations

import importlib

import pandas as pd

__all__ = ["calculate_metric"]


def calculate_metric(descriptor: dict, kwargs: dict) -> dict:
    """Resolve a METRICS_REGISTRY descriptor's callable, call it, and normalize the result.

    Parameters
    ----------
    descriptor : dict
        A METRICS_REGISTRY entry. Must contain ``"function_path"``: a dotted
        import path of the form ``"module.submodule.function_name"``.
    kwargs : dict
        Keyword arguments collected by the metrics tab and forwarded verbatim
        to the upstream callable.

    Returns
    -------
    dict
        Normalized result with one of the following shapes:

        ``{"dataframe": pd.DataFrame}``
            When the upstream returns a ``DataFrame`` or a floodlight
            ``Property`` object (``PlayerProperty``, ``TeamProperty``,
            ``DyadicProperty``).

        ``{"value": float}``
            When the upstream returns a scalar ``int`` or ``float``.

        ``{"value": <raw>}``
            Fallback for any other return type.

        For the ``zone_aggregation`` category the wrapped DataFrame gains a
        leading ``xID`` column so each player row is identifiable (see
        ``_label_player_rows``).

    Raises
    ------
    KeyError
        If ``descriptor`` does not contain ``"function_path"``.
    ModuleNotFoundError
        If the module portion of ``"function_path"`` cannot be imported.
    AttributeError
        If the function name does not exist on the resolved module.
    """
    return _label_player_rows(descriptor, _wrap_result(_call_upstream(descriptor, kwargs)))


def _call_upstream(descriptor: dict, kwargs: dict):
    """Import and call the function named by ``descriptor['function_path']`` with *kwargs*."""
    module_path, _, attr = descriptor["function_path"].rpartition(".")
    func = getattr(importlib.import_module(module_path), attr)
    return func(**kwargs)


def _wrap_result(raw) -> dict:
    """Normalize an upstream result into a cache-friendly dict."""
    if isinstance(raw, pd.DataFrame):
        return {"dataframe": raw}
    if isinstance(raw, (int, float)):
        return {"value": float(raw)}
    # Duck-type floodlight Property objects (PlayerProperty / TeamProperty /
    # DyadicProperty) by their `.property` ndarray -- matches the pre-rebuild
    # behavior without a hard floodlight import.
    if hasattr(raw, "property"):
        return {"dataframe": pd.DataFrame(raw.property)}
    return {"value": raw}


def _label_player_rows(descriptor: dict, result: dict) -> dict:
    """Surface positional player rows as an explicit ``xID`` column.

    Zone aggregation returns a DataFrame with one row per player (positional
    index 0..N-1) and one column per zone, so the player identity is invisible
    in a values-only table and CSV. Insert the row index as a leading ``xID``
    column. Other metric categories pass through unchanged.
    """
    if descriptor.get("category") != "zone_aggregation":
        return result
    frame = result.get("dataframe")
    if not isinstance(frame, pd.DataFrame) or "xID" in frame.columns:
        return result
    labeled = frame.copy()
    labeled.insert(0, "xID", labeled.index)
    return {**result, "dataframe": labeled}
