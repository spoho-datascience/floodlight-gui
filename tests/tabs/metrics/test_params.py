"""Behavioral contracts for ``floodlight_gui.tabs.metrics.params``.

This layer discovers fitted model outputs eligible as metric inputs, scopes them
by the Step-1 period/team selection, and collects every input and param widget
back into a kwargs dict for the upstream floodlight call. Its collaborators are
the Models tab state (a plain module of dicts), the active-XY resolver, and the
DPG toolkit; all three are the seam and are stubbed. The metric descriptors and
the type-coercion rules are this module's own job and are asserted exactly.

Behavioral contracts guarded here
---------------------------------
available_outputs (model-output discovery + Step-1 scope filter)
  C1  Emits one record per checked, Property-returning fitted leaf, with the
      documented shape (label, value_key, period, team, keys, model_obj).
  C2  Drops a leaf whose ``returns`` is not a floodlight Property type.
  C3  Drops a leaf whose (model_key, output_key) is unchecked.
  C4  Applies the Step-1 scope: a concrete period/team filters leaves; "All"
      on an axis drops that axis's filter; a BothTeams slot survives the team
      filter.
  C5  Dedupes records by label (first wins) so an "All" collapse never yields
      duplicate combo items.

scoped_output_leaves (broadcast leaf expansion for non-XY metrics)
  C7  Returns the (period_internal, team) leaves of the picked model output
      under the current scope; [] when the metric has no model-output input or
      nothing matches the picked label.

collect_kwargs (read inputs + params into the upstream call kwargs)
  C8  An XY input is resolved through ``get_xy_for_period_team`` with the passed
      period/team.
  C9  A model-output ``sig`` input extracts the picked column as a 1-D array;
      a Property input passes the resolved Property object through.
  C10 None-valued optional params are omitted; a required param is kept even
      when None.

_collect_param (per-type widget coercion)
  C11 Each param type coerces its raw widget string to the typed value, and a
      missing widget tag falls back to the descriptor default.

_collect_input (model-output guard)
  C12 A model-output input whose combo selects a label with no matching record
      raises ValueError.

parsers
  C13 Zone-bound and xy-tuple free text parse to the documented float
      tuple-list / (M, 2) ndarray, and empty text yields None.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

import floodlight_gui.tabs.metrics.params as params
from floodlight_gui.tabs.metrics import state
from tests._dpg_stub import make_dpg_stub

# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


class _FakeProp:
    """Minimal stand-in for a floodlight Property: exposes a ``.property`` array."""

    def __init__(self, array):
        self.property = np.asarray(array, dtype=float)


class _FakeModelObj:
    """Fitted-model double whose output methods return canned Property objects.

    Maps a method name to the Property it returns so ``_resolve_output_property``
    can dispatch by ``MODEL_REGISTRY[...]['outputs'][...]['method']``.
    """

    def __init__(self, methods):
        for name, prop in methods.items():
            setattr(self, name, (lambda p=prop: p))


@pytest.fixture
def model_state(monkeypatch):
    """Install a fake Models-tab state module visible to ``params._model_state``.

    Returns a callable ``(fitted, checked) -> module`` that seeds the
    ``fitted_models`` and ``output_checked`` dicts and registers the module so
    ``from floodlight_gui.tabs.model import state`` resolves to it.

    Returns
    -------
    callable
        ``install(fitted, checked) -> SimpleNamespace``.
    """

    def _install(fitted, checked):
        mod = SimpleNamespace(fitted_models=fitted, output_checked=checked)
        monkeypatch.setattr(params, "_model_state", lambda: mod)
        return mod

    return _install


@pytest.fixture
def model_registry(monkeypatch):
    """Patch ``params.MODEL_REGISTRY`` to a controllable descriptor map.

    Returns a callable ``(mapping) -> mapping`` so a test declares exactly the
    model descriptors (display_name + outputs) its fitted leaves reference.
    """

    def _install(mapping):
        monkeypatch.setattr(params, "MODEL_REGISTRY", mapping)
        return mapping

    return _install


@pytest.fixture
def patch_step1(monkeypatch):
    """Override ``params._step1_scope`` with a fixed 4-tuple.

    Returns a callable ``(filter_period, period_internal, filter_team, raw_team)``
    so scope-filter tests do not need live DPG combos.
    """

    def _install(scope):
        monkeypatch.setattr(params, "_step1_scope", lambda: scope)
        return scope

    return _install


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install the shared fake-DPG stub on the params module.

    Returns the stub so tests can seed widget values via ``stub.values`` /
    ``stub.existing_items``.
    """
    stub = make_dpg_stub()
    monkeypatch.setattr(params, "dpg", stub)
    return stub


def _velocity_model_registry():
    """Return a one-entry MODEL_REGISTRY with a Property and a non-Property output."""
    return {
        "velocity": {
            "display_name": "Velocity",
            "outputs": {
                "velocity": {
                    "method": "velocity",
                    "returns": "PlayerProperty",
                    "label": "Velocity",
                },
                "centroid": {
                    "method": "centroid",
                    "returns": "XY",  # not a Property -> never eligible
                    "label": "Centroid position",
                },
            },
        }
    }


# --------------------------------------------------------------------------- #
# available_outputs
# --------------------------------------------------------------------------- #


def test_available_outputs_record_shape(model_state, model_registry, patch_step1):
    """C1: each checked Property leaf yields a record with the documented shape."""
    model_registry(_velocity_model_registry())
    model_state(
        fitted={("firstHalf", "Home", "velocity"): "MODEL"},
        checked={("velocity", "velocity"): True},
    )
    patch_step1((False, None, False, "All"))

    records = params.available_outputs()
    assert len(records) == 1
    rec = records[0]
    assert rec["period"] == "firstHalf"
    assert rec["team"] == "Home"
    assert rec["model_key"] == "velocity"
    assert rec["output_key"] == "velocity"
    assert rec["model_obj"] == "MODEL"
    assert rec["label"] == "Velocity -> Velocity"
    assert rec["value_key"] == "firstHalf|Home|velocity|velocity"


def test_available_outputs_excludes_non_property_returns(model_state, model_registry, patch_step1):
    """C2: only Property-returning outputs are eligible (XY-returning ones drop)."""
    model_registry(_velocity_model_registry())
    model_state(
        fitted={("firstHalf", "Home", "velocity"): "MODEL"},
        checked={("velocity", "velocity"): True, ("velocity", "centroid"): True},
    )
    patch_step1((False, None, False, "All"))

    labels = [r["label"] for r in params.available_outputs()]
    assert labels == ["Velocity -> Velocity"]


def test_available_outputs_excludes_unchecked(model_state, model_registry, patch_step1):
    """C3: an output whose (model_key, output_key) is unchecked is dropped."""
    model_registry(_velocity_model_registry())
    model_state(
        fitted={("firstHalf", "Home", "velocity"): "MODEL"},
        checked={("velocity", "velocity"): False},
    )
    patch_step1((False, None, False, "All"))
    assert params.available_outputs() == []


def _per_leaf_registry():
    """Return four single-output models so each fitted leaf carries a distinct label.

    Sharing one model+output collapses every leaf under one label (the dedup
    contract, C5). To isolate the Step-1 scope filter (C4) we give each leaf its
    own model key so the surviving teams are observable without dedup masking them.
    """
    return {
        mk: {
            "display_name": mk,
            "outputs": {
                "velocity": {"method": "velocity", "returns": "PlayerProperty", "label": "V"}
            },
        }
        for mk in ("m_fh_home", "m_fh_away", "m_fh_both", "m_sh_home")
    }


@pytest.mark.parametrize(
    "scope, expected_teams",
    [
        # No filter ("All"/"All"): every leaf survives.
        ((False, None, False, "All"), {"Home", "Away", "BothTeams"}),
        # Concrete period firstHalf: drops the secondHalf leaf.
        ((True, "firstHalf", False, "All"), {"Home", "Away", "BothTeams"}),
        # Concrete team Home: Away drops, BothTeams survives the team filter.
        ((False, None, True, "Home"), {"Home", "BothTeams"}),
    ],
)
def test_available_outputs_applies_step1_scope(
    model_state, model_registry, patch_step1, scope, expected_teams
):
    """C4: the Step-1 scope filters leaves; BothTeams survives a concrete team."""
    model_registry(_per_leaf_registry())
    model_state(
        fitted={
            ("firstHalf", "Home", "m_fh_home"): "M1",
            ("firstHalf", "Away", "m_fh_away"): "M2",
            ("firstHalf", "BothTeams", "m_fh_both"): "M3",
            ("secondHalf", "Home", "m_sh_home"): "M4",
        },
        checked={(mk, "velocity"): True for mk in _per_leaf_registry()},
    )
    patch_step1(scope)
    teams = {r["team"] for r in params.available_outputs()}
    assert teams == expected_teams


def test_available_outputs_dedupes_by_label(model_state, model_registry, patch_step1):
    """C5: leaves collapsing to one label under "All" yield a single record."""
    model_registry(_velocity_model_registry())
    model_state(
        fitted={
            ("firstHalf", "Home", "velocity"): "M1",
            ("secondHalf", "Home", "velocity"): "M2",
        },
        checked={("velocity", "velocity"): True},
    )
    patch_step1((False, None, True, "Home"))
    # Both leaves share label "Velocity -> Velocity"; only the first survives.
    assert len(params.available_outputs()) == 1


# --------------------------------------------------------------------------- #
# scoped_output_leaves
# --------------------------------------------------------------------------- #


def test_scoped_output_leaves_returns_matched_leaves(
    model_state, model_registry, patch_step1, dpg_stub
):
    """C7: returns the picked output's fitted leaves under the current scope."""
    model_registry(_velocity_model_registry())
    model_state(
        fitted={
            ("firstHalf", "Home", "velocity"): "M1",
            ("secondHalf", "Home", "velocity"): "M2",
        },
        checked={("velocity", "velocity"): True},
    )
    patch_step1((False, None, False, "All"))
    # The metric has one model-output input wired to a source combo whose value
    # selects the (deduped) "Velocity -> Velocity" label.
    state.input_widgets = {"prop": {"type": "PlayerProperty|TeamProperty", "source_combo": "src"}}
    dpg_stub.values["src"] = "Velocity -> Velocity"

    descriptor = {"inputs": {"prop": {"type": "PlayerProperty|TeamProperty"}}}
    leaves = params.scoped_output_leaves(descriptor)
    assert leaves == [("firstHalf", "Home"), ("secondHalf", "Home")]


def test_scoped_output_leaves_no_model_output_input(model_state, model_registry, patch_step1):
    """C7: a metric with no model-output input expands to no leaves."""
    model_registry(_velocity_model_registry())
    model_state(fitted={}, checked={})
    patch_step1((False, None, False, "All"))
    descriptor = {"inputs": {"xy": {"type": "XY"}}}
    assert params.scoped_output_leaves(descriptor) == []


# --------------------------------------------------------------------------- #
# collect_kwargs
# --------------------------------------------------------------------------- #


def test_collect_kwargs_xy_input_uses_resolver(monkeypatch, dpg_stub):
    """C8: an XY input is resolved through get_xy_for_period_team(period, team)."""
    calls = []

    def _resolver(app, period, team):
        calls.append((period, team))
        return "XY_OBJ"

    # The collector imports the resolver lazily from core.xy_access.
    monkeypatch.setitem(
        sys.modules,
        "floodlight_gui.core.xy_access",
        SimpleNamespace(get_xy_for_period_team=_resolver),
    )
    descriptor = {"inputs": {"xy": {"type": "XY"}}, "params": {}}
    monkeypatch.setattr(params, "METRICS_REGISTRY", {"m": descriptor})
    state.input_widgets = {"xy": {"type": "XY"}}

    kwargs = params.collect_kwargs("m", period_internal="firstHalf", team="Home")
    assert kwargs == {"xy": "XY_OBJ"}
    assert calls == [("firstHalf", "Home")]


def test_collect_kwargs_sig_input_extracts_column(
    monkeypatch, model_state, model_registry, patch_step1, dpg_stub
):
    """C9: a ``sig`` model-output input extracts the picked column as a 1-D array."""
    model_registry(_velocity_model_registry())
    model_state(
        fitted={
            ("firstHalf", "Home", "velocity"): _FakeModelObj(
                {"velocity": _FakeProp([[10.0, 20.0], [11.0, 21.0]])}
            )
        },
        checked={("velocity", "velocity"): True},
    )
    patch_step1((False, None, False, "All"))

    descriptor = {"inputs": {"sig": {"type": "sig"}}, "params": {}}
    monkeypatch.setattr(params, "METRICS_REGISTRY", {"m": descriptor})
    state.input_widgets = {"sig": {"type": "sig", "source_combo": "src", "column_combo": "col"}}
    dpg_stub.values["src"] = "Velocity -> Velocity"
    dpg_stub.values["col"] = "P1"

    kwargs = params.collect_kwargs("m", period_internal=None, team=None)
    assert np.array_equal(kwargs["sig"], np.array([20.0, 21.0]))


def test_collect_kwargs_property_input_passes_property_through(
    monkeypatch, model_state, model_registry, patch_step1, dpg_stub
):
    """C9: a Property input forwards the resolved Property object verbatim."""
    prop = _FakeProp([[1.0, 2.0]])
    model_registry(_velocity_model_registry())
    model_state(
        fitted={("firstHalf", "Home", "velocity"): _FakeModelObj({"velocity": prop})},
        checked={("velocity", "velocity"): True},
    )
    patch_step1((False, None, False, "All"))

    descriptor = {"inputs": {"prop": {"type": "PlayerProperty|TeamProperty"}}, "params": {}}
    monkeypatch.setattr(params, "METRICS_REGISTRY", {"m": descriptor})
    state.input_widgets = {"prop": {"type": "PlayerProperty|TeamProperty", "source_combo": "src"}}
    dpg_stub.values["src"] = "Velocity -> Velocity"

    kwargs = params.collect_kwargs("m", period_internal=None, team=None)
    assert kwargs["prop"] is prop


def test_collect_kwargs_omits_none_optional_keeps_required(monkeypatch, dpg_stub):
    """C10: a None optional param is omitted; a required param is kept even when None."""
    descriptor = {
        "inputs": {},
        "params": {
            "opt": {"type": "list[str]", "default": None},
            "req": {"type": "list[str]", "default": None, "required": True},
        },
    }
    monkeypatch.setattr(params, "METRICS_REGISTRY", {"m": descriptor})
    state.input_widgets = {}
    # No widget tags exist -> _collect_param returns each descriptor default (None).
    kwargs = params.collect_kwargs("m", period_internal=None, team=None)
    assert "opt" not in kwargs
    assert kwargs["req"] is None


# --------------------------------------------------------------------------- #
# _collect_param (per-type coercion)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ptype, raw, expected",
    [
        ("int", "7", 7),
        ("float", "0.25", 0.25),
        ("bool", True, True),
        ("enum", "None", None),
        ("enum", "sum", "sum"),
        ("list[str]", "slow, medium, fast", ["slow", "medium", "fast"]),
        ("list[str]", "  ", None),
        ("list[int]", "1, 2, 3", [1, 2, 3]),
        ("list[int]", "", None),
        ("zone_list", "", None),
    ],
)
def test_collect_param_coerces_by_type(dpg_stub, ptype, raw, expected):
    """C11: each param type coerces its raw widget value to the typed result."""
    tag = "metrics_param__p"
    dpg_stub.existing_items.add(tag)
    dpg_stub.values[tag] = raw
    assert params._collect_param("p", {"type": ptype}) == expected


def test_collect_param_missing_widget_returns_default(dpg_stub):
    """C11: with no widget rendered, the descriptor default is returned."""
    assert params._collect_param("p", {"type": "int", "default": 5}) == 5


# --------------------------------------------------------------------------- #
# _collect_input guard
# --------------------------------------------------------------------------- #


def test_collect_input_no_matching_record_raises(
    model_state, model_registry, patch_step1, dpg_stub
):
    """C12: a model-output combo selecting an unknown label raises ValueError."""
    model_registry(_velocity_model_registry())
    model_state(fitted={}, checked={})  # no records at all
    patch_step1((False, None, False, "All"))
    state.input_widgets = {"prop": {"type": "PlayerProperty|TeamProperty", "source_combo": "src"}}
    dpg_stub.values["src"] = "Stale Label"
    with pytest.raises(ValueError, match="No model output selected"):
        params._collect_input("prop", {"type": "PlayerProperty|TeamProperty"}, None, None)


# --------------------------------------------------------------------------- #
# parsers
# --------------------------------------------------------------------------- #


def test_parse_zone_bounds_and_xy_tuples():
    """C13: free text parses to float tuple-list / (M, 2) ndarray; empty -> None."""
    assert params._parse_zone_bounds("(0.0, 2.0), (2.0, 4.0)") == [(0.0, 2.0), (2.0, 4.0)]
    assert params._parse_zone_bounds("   ") is None

    arr = params._parse_xy_tuples("(0, 0), (10, 5)")
    assert isinstance(arr, np.ndarray)
    assert arr.shape == (2, 2)
    assert np.array_equal(arr, np.array([[0.0, 0.0], [10.0, 5.0]]))
    assert params._parse_xy_tuples("") is None
