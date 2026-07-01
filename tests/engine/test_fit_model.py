"""Behavioral contracts for ``floodlight_gui.engine.fit_model``.

This executor resolves a MODEL_REGISTRY descriptor to a model class,
filters init/fit params against the upstream constructor, resolves teams
for multi-team models, fetches XY via the active-XY resolver, and fits.
The XY resolver and the upstream model classes are the seams; both are
stubbed so the tests assert only this module's own decisions.

Behavioral contracts guarded here
---------------------------------
is_multi_team
  C1  Returns True when the descriptor declares more than one input,
      False when it declares exactly one.
  C2  Raises KeyError for a model_key absent from MODEL_REGISTRY.

_select_teams_for_inputs (the role-matching team resolver)
  C3  ``home``/``away`` input hints resolve by exact case-insensitive
      team-name match.
  C4  ``home``/``away`` hints fall back to a substring match when no exact
      match exists.
  C5  Inputs carrying no role hint (the real ``xy1``/``xy2`` shape) resolve
      positionally to the 1st/2nd unused non-Ball team in ``teams`` order.
  C6  Ball is excluded from every candidate slot (realistic ``xy_home``/
      ``xy_away`` and ``xy1``/``xy2`` input keys).
  C7  Raises ValueError when fewer non-Ball teams exist than inputs.

fit_model
  C8  Resolves the model class from the descriptor ``class_path`` and
      returns the fitted instance.
  C9  Init params are filtered against the constructor signature and
      sourced ui_params > Pitch-type > descriptor default.
  C10 A descriptor init_param the constructor does not accept raises
      TypeError before any fit.
  C11 Fit params skip XY-typed entries and source ui_params > default.
  C12 ``fit_param_coerce`` is applied to the fit kwargs before dispatch on
      both single- and multi-team paths.
  C13 Multi-team: explicit ``team_names`` are honored verbatim (resolver
      bypassed).
  C14 Multi-team: ``team_names`` whose length differs from the input count
      raises ValueError.
  C15 Multi-team without ``team_names`` resolves teams via the role
      resolver and fits with one XY per input.
  C16 A resolved team/period with no XY raises ValueError (single- and
      multi-team paths).
  C17 Single-team path fetches one XY for ``team_name`` and fits it
      positionally.
"""

from __future__ import annotations

import pytest

import floodlight_gui.engine.fit_model as fm
from floodlight_gui.engine.fit_model import (
    _select_teams_for_inputs as select_teams,
)
from floodlight_gui.engine.fit_model import (
    fit_model,
    is_multi_team,
)

# --------------------------------------------------------------------------- #
# Test doubles                                                                  #
# --------------------------------------------------------------------------- #


class _FakeModel:
    """Minimal stand-in for a floodlight model class.

    Records the positional XY args and keyword args ``fit`` was called with
    so tests can assert the executor's dispatch decisions without invoking
    any real analytics.
    """

    def __init__(self, **init_kw):
        self.init_kw = init_kw
        self.fit_args = None
        self.fit_kw = None

    def fit(self, *xy_args, **fit_kw):
        self.fit_args = xy_args
        self.fit_kw = fit_kw
        return self


class _PitchOnlyModel:
    """Model whose constructor accepts a single ``pitch`` keyword."""

    def __init__(self, pitch=None):
        self.pitch = pitch
        self.fit_args = None
        self.fit_kw = None

    def fit(self, *xy_args, **fit_kw):
        self.fit_args = xy_args
        self.fit_kw = fit_kw
        return self


class _FakeApp:
    """App double exposing ``pitch`` for Pitch-typed init params."""

    def __init__(self, pitch="PITCH_SENTINEL", teams=None):
        self.pitch = pitch
        self._teams = teams or []

    def get_team_names(self):
        return list(self._teams)


@pytest.fixture
def install_descriptor(monkeypatch):
    """Install a single descriptor under a key and bind its model class.

    Returns a callable ``(key, descriptor, model_cls)`` that patches both
    ``MODEL_REGISTRY`` (so only this entry exists) and ``_import_class``
    (so ``class_path`` resolves to the supplied test double). Isolates the
    executor from the real registry and from real floodlight classes.
    """

    def _install(key, descriptor, model_cls=_FakeModel):
        monkeypatch.setattr(fm, "MODEL_REGISTRY", {key: descriptor})
        monkeypatch.setattr(fm, "_import_class", lambda _path: model_cls)
        return key

    return _install


@pytest.fixture
def stub_xy(monkeypatch):
    """Replace the XY resolver with a controllable double.

    Returns a callable ``(mapping)`` where ``mapping`` maps ``(period,
    team)`` to the XY object to return (or ``None``). The default returns a
    unique sentinel per call so XY identity is assertable.
    """

    def _install(mapping=None):
        calls = []

        def _resolver(app, period, team):
            calls.append((period, team))
            if mapping is not None:
                return mapping.get((period, team))
            return f"XY::{period}::{team}"

        _resolver.calls = calls
        monkeypatch.setattr(fm, "get_xy_for_period_team", _resolver)
        return _resolver

    return _install


def _single_desc(class_path="x.Y", init_params=None, fit_params=None, coerce=None):
    """Build a one-input descriptor with the given init/fit params."""
    desc = {
        "class_path": class_path,
        "inputs": {"xy": {"type": "XY", "required": True}},
        "init_params": init_params or {},
        "fit_params": fit_params or {},
    }
    if coerce is not None:
        desc["fit_param_coerce"] = coerce
    return desc


def _multi_desc(class_path="x.Y", inputs=None, fit_params=None):
    """Build a two-input descriptor (multi-team)."""
    return {
        "class_path": class_path,
        "inputs": inputs
        or {
            "xy_home": {"type": "XY", "required": True},
            "xy_away": {"type": "XY", "required": True},
        },
        "init_params": {},
        "fit_params": fit_params or {},
    }


# --------------------------------------------------------------------------- #
# is_multi_team                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "inputs, expected",
    [
        ({"xy": {}}, False),
        ({"xy_home": {}, "xy_away": {}}, True),
        ({"a": {}, "b": {}, "c": {}}, True),
    ],
)
def test_is_multi_team_reflects_input_count(monkeypatch, inputs, expected):
    """C1: is_multi_team is True iff the descriptor declares >1 input."""
    monkeypatch.setattr(fm, "MODEL_REGISTRY", {"k": {"inputs": inputs}})
    assert is_multi_team("k") is expected


def test_is_multi_team_unknown_key_raises(monkeypatch):
    """C2: an unknown model_key raises KeyError."""
    monkeypatch.setattr(fm, "MODEL_REGISTRY", {})
    with pytest.raises(KeyError):
        is_multi_team("absent")


# --------------------------------------------------------------------------- #
# _select_teams_for_inputs                                                      #
# --------------------------------------------------------------------------- #


def test_select_teams_exact_match_by_role():
    """C3: home/away hints resolve by exact case-insensitive name match."""
    inputs = {"xy_home": {}, "xy_away": {}}
    resolved = select_teams(inputs, ["Away", "Home"])
    assert resolved == ["Home", "Away"]


def test_select_teams_substring_fallback():
    """C4: home/away hints fall back to a substring match when no exact hit."""
    inputs = {"xy_home": {}, "xy_away": {}}
    resolved = select_teams(inputs, ["HomeTeamFC", "AwayTeamFC"])
    assert resolved == ["HomeTeamFC", "AwayTeamFC"]


def test_select_teams_no_role_hint_resolves_positionally():
    """C5: no-role-hint inputs (real ``xy1``/``xy2``) resolve positionally.

    ``xy1``/``xy2`` are the actual NearestOpponentModel input keys; they
    contain none of the ``home``/``away``/``team1``/``team2`` role markers,
    so each slot falls through to the first/second unused non-Ball team in
    ``teams`` order.
    """
    inputs = {"xy1": {}, "xy2": {}}
    resolved = select_teams(inputs, ["Alpha", "Beta"])
    assert resolved == ["Alpha", "Beta"]


@pytest.mark.parametrize(
    "inputs",
    [
        {"xy_home": {}, "xy_away": {}},  # discrete_voronoi input keys
        {"xy1": {}, "xy2": {}},  # nearest_opponent input keys (no role hint)
    ],
)
def test_select_teams_excludes_ball(inputs):
    """C6: Ball never fills a resolved slot even when listed first."""
    resolved = select_teams(inputs, ["Ball", "Home", "Away"])
    assert "Ball" not in resolved
    assert len(resolved) == 2


def test_select_teams_too_few_teams_raises():
    """C7: fewer non-Ball teams than inputs raises ValueError."""
    inputs = {"xy_home": {}, "xy_away": {}}
    with pytest.raises(ValueError):
        select_teams(inputs, ["Ball", "Home"])


# --------------------------------------------------------------------------- #
# fit_model                                                                     #
# --------------------------------------------------------------------------- #


def test_fit_model_resolves_class_and_returns_fitted(install_descriptor, stub_xy):
    """C8: fit_model fits and returns the resolved model instance."""
    install_descriptor("m", _single_desc())
    stub_xy()
    result = fit_model(_FakeApp(), "m", "firstHalf", "Home")
    assert isinstance(result, _FakeModel)
    assert result.fit_args is not None


@pytest.mark.parametrize(
    "init_params, ui_params, expected_value",
    [
        # ui_params win over the descriptor default
        ({"pitch": {"type": "Pitch"}}, {"pitch": "UI_PITCH"}, "UI_PITCH"),
        # Pitch-typed param with no ui override sources app.pitch
        ({"pitch": {"type": "Pitch"}}, {}, "PITCH_SENTINEL"),
        # plain default used when neither ui nor Pitch applies
        ({"pitch": {"type": "int", "default": 7}}, {}, 7),
    ],
)
def test_fit_model_init_param_sourcing(
    install_descriptor, stub_xy, init_params, ui_params, expected_value
):
    """C9: init params source ui_params > Pitch-type > descriptor default."""
    install_descriptor("m", _single_desc(init_params=init_params), _PitchOnlyModel)
    stub_xy()
    result = fit_model(_FakeApp(), "m", "firstHalf", "Home", ui_params=ui_params)
    assert result.pitch == expected_value


def test_fit_model_unknown_init_param_raises(install_descriptor, stub_xy):
    """C10: an init_param the constructor rejects raises TypeError before fit."""
    desc = _single_desc(init_params={"bogus": {"type": "int", "default": 1}})
    install_descriptor("m", desc, _FakeModel)
    stub_xy()
    with pytest.raises(TypeError):
        fit_model(_FakeApp(), "m", "firstHalf", "Home")


def test_fit_model_fit_param_skips_xy_and_sources_values(install_descriptor, stub_xy):
    """C11: fit params skip XY-typed entries and source ui_params > default."""
    fit_params = {
        "xy2": {"type": "XY"},  # must be skipped (never a kwarg)
        "difference": {"type": "enum", "default": "central"},
        "axis": {"type": "enum", "default": None},
    }
    install_descriptor("m", _single_desc(fit_params=fit_params))
    stub_xy()
    result = fit_model(_FakeApp(), "m", "firstHalf", "Home", ui_params={"difference": "backward"})
    assert "xy2" not in result.fit_kw
    assert result.fit_kw["difference"] == "backward"
    assert result.fit_kw["axis"] is None


def test_fit_model_applies_coerce_before_dispatch(install_descriptor, stub_xy):
    """C12: fit_param_coerce rewrites the fit kwargs before fit is called."""
    fit_params = {"exclude_xIDs": {"type": "list[int]", "default": [1, 2]}}

    def _coerce(fit_kw):
        fit_kw["exclude_xIDs"] = [fit_kw["exclude_xIDs"]]
        return fit_kw

    install_descriptor("m", _single_desc(fit_params=fit_params, coerce=_coerce))
    stub_xy()
    result = fit_model(_FakeApp(), "m", "firstHalf", "Home")
    assert result.fit_kw["exclude_xIDs"] == [[1, 2]]


def test_fit_model_multi_team_honors_explicit_team_names(install_descriptor, stub_xy):
    """C13: explicit team_names are used verbatim, bypassing the resolver."""
    install_descriptor("m", _multi_desc())
    resolver = stub_xy()
    # App returns an order that the resolver would reshuffle; team_names wins.
    fit_model(
        _FakeApp(teams=["Away", "Home"]),
        "m",
        "firstHalf",
        "ignored",
        team_names=["Home", "Away"],
    )
    assert resolver.calls == [("firstHalf", "Home"), ("firstHalf", "Away")]


def test_fit_model_multi_team_team_names_arity_mismatch_raises(install_descriptor, stub_xy):
    """C14: team_names whose length differs from the input count raises ValueError."""
    install_descriptor("m", _multi_desc())
    stub_xy()
    with pytest.raises(ValueError):
        fit_model(_FakeApp(), "m", "firstHalf", "x", team_names=["OnlyOne"])


def test_fit_model_multi_team_resolves_via_role_resolver(install_descriptor, stub_xy):
    """C15: without team_names, teams come from the role resolver, one XY per input."""
    install_descriptor("m", _multi_desc())
    resolver = stub_xy()
    result = fit_model(_FakeApp(teams=["Away", "Home"]), "m", "firstHalf", "x")
    # xy_home -> Home, xy_away -> Away regardless of app ordering.
    assert resolver.calls == [("firstHalf", "Home"), ("firstHalf", "Away")]
    assert len(result.fit_args) == 2


@pytest.mark.parametrize(
    "desc_factory, team_names",
    [
        (lambda: _single_desc(), None),
        (lambda: _multi_desc(), ["Home", "Away"]),
    ],
)
def test_fit_model_missing_xy_raises(install_descriptor, stub_xy, desc_factory, team_names):
    """C16: a resolved team/period with no XY raises ValueError on both paths."""
    install_descriptor("m", desc_factory())
    stub_xy(mapping={})  # every lookup returns None
    with pytest.raises(ValueError):
        fit_model(_FakeApp(teams=["Home", "Away"]), "m", "firstHalf", "Home", team_names=team_names)


def test_fit_model_single_team_fits_xy_positionally(install_descriptor, stub_xy):
    """C17: single-team path fetches one XY for team_name and fits it positionally."""
    install_descriptor("m", _single_desc())
    resolver = stub_xy()
    result = fit_model(_FakeApp(), "m", "firstHalf", "Home")
    assert resolver.calls == [("firstHalf", "Home")]
    assert result.fit_args == ("XY::firstHalf::Home",)
