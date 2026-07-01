"""Behavioral contracts for the model results projector.

``_project`` turns a fitted-model output value into a ``(frame, columns)`` pair
for the array view. floodlight Property objects have no ``to_dataframe``, so it
reads ``.property``: a 1-D team-level array (for example convex hull area)
becomes one named column; a 2-D per-player or per-team array renders as-is. A
value it cannot project yields ``(None, None)`` so the caller shows a type label.

Behavioral contracts guarded here
---------------------------------
C1  A 1-D ``.property`` (team-level, e.g. convex hull area) projects to a (T, 1)
    frame with a column named from the property, so the result renders instead
    of showing nothing.
C2  A 2-D ``.property`` (per player or team) is returned as-is with P{i} columns.
C3  A DataFrame projects via ``reset_index``; a non-array value is unprojectable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from floodlight_gui.tabs.model.results import _project


class _Prop:
    """Minimal floodlight-Property stand-in: a ``.property`` ndarray plus a ``.name``."""

    def __init__(self, arr, name=None):
        self.property = arr
        self.name = name


def test_one_d_property_becomes_a_single_named_column():
    """C1: a 1-D team property (e.g. convex_hull_area) renders as one named column.

    floodlight's ``convex_hull_area()`` returns a TeamProperty whose ``.property``
    is a 1-D (T,) array; without this the results panel rendered nothing.
    """
    frame, columns = _project(_Prop(np.array([50.0, 12.5, 30.0]), name="convex_hull_area"))
    assert isinstance(frame, np.ndarray) and frame.shape == (3, 1)
    assert columns == ["Frame", "convex_hull_area"]


def test_one_d_property_without_name_falls_back_to_value():
    """C1: a nameless 1-D property still renders, labeled "Value"."""
    frame, columns = _project(_Prop(np.arange(4.0)))
    assert frame.shape == (4, 1)
    assert columns == ["Frame", "Value"]


def test_two_d_property_renders_with_positional_columns():
    """C2: a 2-D property (per player or team) is returned as-is with P{i} columns."""
    arr = np.arange(6.0).reshape(3, 2)
    frame, columns = _project(_Prop(arr))
    assert frame is arr
    assert columns == ["Frame", "P0", "P1"]


def test_dataframe_resets_index_and_non_array_is_unprojectable():
    """C3: a DataFrame projects via reset_index; a plain scalar yields (None, None)."""
    frame, columns = _project(pd.DataFrame({"a": [1, 2]}))
    assert isinstance(frame, pd.DataFrame) and columns is None
    assert list(frame.columns) == ["index", "a"]
    assert _project(3.14) == (None, None)
