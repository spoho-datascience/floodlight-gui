"""Behavioral contracts for ``floodlight_gui.tabs.model.execute``.

This module is the model-tab's fit producer: the Fit-Model button callback. Its
own job is to guard the session, run the multi-XY team gate, decide between a
single fit and an "All"-broadcast fit, drive the broadcast loop with
all-or-nothing rollback into ``state.fitted_models``, store fits under the
cache key (with "BothTeams" + ``_team_names`` for multi-team), invalidate
stale output caches, refresh the results view, and emit ``MODEL_FITTED``. The
fit engine (``fit_model`` / ``is_multi_team``), the gate, the param collector,
the results feeders, and the error modal are collaborators; all are stubbed so
the tests assert only this module's dispatch and caching decisions. The DPG
toolkit is the seam.

Behavioral contracts guarded here
---------------------------------
_fit dispatch
  C1  No-ops with a status message when no data session is loaded, and returns
      silently when no model key is resolved.
  C2  A failing multi-XY team gate aborts the fit (no fit_model call) and
      surfaces the error.
  C3  Routes to broadcast when period is "All", or when team is "All" and the
      model is single-XY; otherwise routes to a single fit.

_fit_broadcast
  C4  Builds the (period, team) combo set from the "All" selections: arity>=2
      collapses to one "BothTeams" entry per period; single-XY "All" team
      expands to every team.
  C5  All-or-nothing rollback: a mid-loop failure leaves ``state.fitted_models``
      untouched; full success commits every fitted entry and emits MODEL_FITTED
      once.

_fit_single
  C6  Commits one entry under ``(period, team_or_BothTeams, model_key)`` with
      ``_team_names`` recorded for multi-team, invalidates its output cache,
      refreshes the leaf, and emits MODEL_FITTED.

helpers
  C7  ``_explicit_team_names`` returns the per-slot picks, or None (with a
      status) when any slot is empty or holds "All".
  C8  ``_invalidate_outputs`` drops only the cached outputs of the matching
      (model_key, period, team) leaf.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.model.execute as execute_mod
from floodlight_gui.core.event_bus import Events
from floodlight_gui.tabs._shared.broadcast import ALL_SENTINEL
from floodlight_gui.tabs.model import execute, state
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def harness(monkeypatch):
    """Wire execute's collaborators to controllable doubles and return the control surface.

    Returns a namespace with: ``stub`` (fake DPG), ``combos`` (combo-tag ->
    value seed), ``fits`` (recorded fit_model calls), ``emitted`` (MODEL_FITTED
    payloads), ``set_fit`` (install a fit_model behaviour). The active model key,
    arity, and multi-team flag are configured per test via ``configure``.
    """
    stub = make_dpg_stub()
    monkeypatch.setattr(execute_mod, "dpg", stub)

    fits: list = []
    emitted: list = []
    state.app_instance = type(
        "_App",
        (),
        {
            "loaded_data": object(),
            "get_temporal_divisions": lambda self: ["firstHalf", "secondHalf"],
            "get_team_names": lambda self: ["Home", "Away"],
        },
    )()

    # Default collaborators: gate passes, params empty, fit succeeds, results no-op.
    # The error modal and friendly-error helpers touch real DPG, so stub them.
    # Capture the genuine param collector first so the flow tests can restore it.
    live_collect_ui_params = execute_mod.params.collect_ui_params
    monkeypatch.setattr(execute_mod, "check_multi_xy_model_team_gate", lambda **k: None)
    monkeypatch.setattr(execute_mod.params, "collect_ui_params", lambda mk: {})
    monkeypatch.setattr(execute_mod.results, "rebuild", lambda: None)
    monkeypatch.setattr(execute_mod.results, "refresh_leaf", lambda *a: None)
    monkeypatch.setattr(execute_mod, "show_error_modal", lambda *a, **k: None)
    monkeypatch.setattr(execute_mod, "friendly_error_message", lambda exc: str(exc))

    def _fit_model(app, model_key, period, team, ui_params, team_names=None):
        fits.append(
            {"model_key": model_key, "period": period, "team": team, "team_names": team_names}
        )
        return f"MODEL::{period}::{team}"

    monkeypatch.setattr(execute_mod, "fit_model", _fit_model)
    monkeypatch.setattr(execute_mod.bus, "emit", lambda ev, **kw: emitted.append((ev, kw)))

    ns = type("H", (), {})()
    ns.stub = stub
    ns.fits = fits
    ns.emitted = emitted
    ns.live_collect_ui_params = live_collect_ui_params

    def configure(*, model_key="velocity", arity=1, multi=False, desc=None, combos=None):
        d = desc or {"display_name": "Velocity", "fit_xy_arity": arity}
        monkeypatch.setattr(execute_mod, "MODEL_REGISTRY", {model_key: d})
        monkeypatch.setattr(execute_mod.select, "active_model_key", lambda: model_key)
        monkeypatch.setattr(execute_mod, "is_multi_team", lambda mk: multi)
        for tag, val in (combos or {}).items():
            stub.existing_items.add(tag)
            stub.values[tag] = val

    def set_fit(fn):
        monkeypatch.setattr(execute_mod, "fit_model", fn)

    ns.configure = configure
    ns.set_fit = set_fit
    return ns


# --------------------------------------------------------------------------- #
# _fit dispatch
# --------------------------------------------------------------------------- #


def test_fit_guards_no_data_and_no_model(harness, monkeypatch):
    """C1: no loaded data sets a status and skips fitting; no model key returns silently."""
    harness.stub.existing_items.add(execute_mod.FIT_STATUS)
    state.app_instance = None
    execute._fit()
    assert harness.fits == []
    assert any("No data" in str(c[1][1]) for c in harness.stub.calls_of("set_value"))

    # Data present but no model key resolved.
    state.app_instance = type("_App", (), {"loaded_data": object()})()
    monkeypatch.setattr(execute_mod.select, "active_model_key", lambda: None)
    monkeypatch.setattr(execute_mod, "MODEL_REGISTRY", {})
    execute._fit()
    assert harness.fits == []


def test_fit_aborts_on_gate_failure(harness, monkeypatch):
    """C2: a failing multi-XY gate surfaces the error and never calls fit_model."""
    harness.configure(
        model_key="nearest_opponent", arity=2, combos={"model_team_combo": ALL_SENTINEL}
    )
    seen: list = []
    monkeypatch.setattr(execute_mod, "show_error_modal", lambda *a: seen.append(a))

    def _gate(**_k):
        raise ValueError("needs two teams")

    monkeypatch.setattr(execute_mod, "check_multi_xy_model_team_gate", _gate)
    execute._fit()
    assert harness.fits == []
    assert seen  # error modal shown


@pytest.mark.parametrize(
    "period, team, arity, multi, broadcast",
    [
        (ALL_SENTINEL, "Home", 1, False, True),  # period All -> broadcast
        ("First Half", ALL_SENTINEL, 1, False, True),  # team All + single-XY -> broadcast
        ("First Half", "Home", 1, False, False),  # explicit -> single
        ("First Half", ALL_SENTINEL, 2, True, False),  # team All but arity 2 -> single path
    ],
)
def test_fit_routes_broadcast_vs_single(
    harness, monkeypatch, period, team, arity, multi, broadcast
):
    """C3: routing follows the period/team "All" sentinels and the model arity."""
    routed: list = []
    harness.configure(
        model_key="velocity",
        arity=arity,
        multi=multi,
        combos={"model_period_combo": period, "model_team_combo": team},
    )
    monkeypatch.setattr(execute_mod, "_fit_broadcast", lambda *a: routed.append("b"))
    monkeypatch.setattr(execute_mod, "_fit_single", lambda *a: routed.append("s"))
    execute._fit()
    assert routed == (["b"] if broadcast else ["s"])


# --------------------------------------------------------------------------- #
# _fit_broadcast
# --------------------------------------------------------------------------- #


def test_broadcast_single_xy_all_teams_commits_every_combo(harness):
    """C4/C5: single-XY "All" team broadcast fits every period x team and commits all."""
    harness.configure(
        model_key="velocity",
        arity=1,
        multi=False,
        combos={"model_period_combo": ALL_SENTINEL, "model_team_combo": ALL_SENTINEL},
    )
    execute._fit()
    # 2 periods x 2 teams = 4 fits, all committed.
    assert len(harness.fits) == 4
    assert len(state.fitted_models) == 4
    assert sum(1 for (ev, _) in harness.emitted if ev is Events.MODEL_FITTED) == 1


def test_broadcast_multi_team_collapses_to_bothteams(harness):
    """C4: an arity>=2 broadcast collapses each period to a single BothTeams entry."""
    harness.configure(
        model_key="discrete_voronoi",
        arity=2,
        multi=True,
        desc={"display_name": "Discrete Voronoi", "fit_xy_arity": 2},
        combos={
            "model_period_combo": ALL_SENTINEL,
            "model_team_combo_a": "Home",
            "model_team_combo_b": "Away",
        },
    )
    execute._fit()
    keys = set(state.fitted_models)
    assert keys == {
        ("firstHalf", "BothTeams", "discrete_voronoi"),
        ("secondHalf", "BothTeams", "discrete_voronoi"),
    }
    # Multi-team fits record the explicit team names internally.
    for _model, fp in state.fitted_models.values():
        assert fp["_team_names"] == ("Home", "Away")


def test_broadcast_rollback_leaves_state_untouched(harness):
    """C5: a mid-loop failure discards all accumulated fits (fitted_models stays empty)."""
    harness.configure(
        model_key="velocity",
        arity=1,
        multi=False,
        combos={"model_period_combo": ALL_SENTINEL, "model_team_combo": ALL_SENTINEL},
    )

    calls = {"n": 0}

    def _flaky(app, model_key, period, team, ui_params, team_names=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom on second combo")
        return "ok"

    harness.set_fit(_flaky)
    execute._fit()
    assert state.fitted_models == {}
    assert not any(ev is Events.MODEL_FITTED for (ev, _) in harness.emitted)


# --------------------------------------------------------------------------- #
# _fit_single
# --------------------------------------------------------------------------- #


def test_single_fit_commits_invalidates_refreshes_emits(harness, monkeypatch):
    """C6: a single fit commits one entry, refreshes its leaf, and emits MODEL_FITTED."""
    refreshed: list = []
    monkeypatch.setattr(execute_mod.results, "refresh_leaf", lambda *a: refreshed.append(a))
    # Seed a stale output cache for the target leaf to prove invalidation.
    state.output_results[("velocity", "firstHalf", "Home", "velocity")] = "stale"
    harness.configure(
        model_key="velocity",
        arity=1,
        multi=False,
        combos={"model_period_combo": "First Half", "model_team_combo": "Home"},
    )
    execute._fit()
    assert ("firstHalf", "Home", "velocity") in state.fitted_models
    assert ("velocity", "firstHalf", "Home", "velocity") not in state.output_results
    assert refreshed == [("velocity", "firstHalf", "Home")]
    assert any(ev is Events.MODEL_FITTED for (ev, _) in harness.emitted)


def test_single_fit_multi_team_stores_bothteams_and_names(harness):
    """C6: a multi-team single fit stores BothTeams and the explicit _team_names tuple."""
    # nearest_opponent is multi-team (arity 2) but not voronoi, so the single-fit
    # path runs without the voronoi-only loading/rebuild branches (real DPG).
    harness.configure(
        model_key="nearest_opponent",
        arity=2,
        multi=True,
        desc={"display_name": "Nearest Opponent", "fit_xy_arity": 2},
        combos={
            "model_period_combo": "First Half",
            "model_team_combo_a": "Home",
            "model_team_combo_b": "Away",
        },
    )
    execute._fit()
    entry = state.fitted_models[("firstHalf", "BothTeams", "nearest_opponent")]
    assert entry[1]["_team_names"] == ("Home", "Away")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def test_explicit_team_names_reads_or_rejects(harness):
    """C7: explicit Team A/B picks are returned; an empty or "All" slot yields None."""
    harness.stub.existing_items.update({"model_team_combo_a", "model_team_combo_b"})
    harness.stub.values["model_team_combo_a"] = "Home"
    harness.stub.values["model_team_combo_b"] = "Away"
    assert execute._explicit_team_names(2) == ["Home", "Away"]

    harness.stub.values["model_team_combo_b"] = ALL_SENTINEL
    assert execute._explicit_team_names(2) is None


def test_invalidate_outputs_drops_only_matching_leaf(harness):
    """C8: _invalidate_outputs removes only the matching leaf's cached outputs."""
    state.output_results[("velocity", "firstHalf", "Home", "velocity")] = 1
    state.output_results[("velocity", "secondHalf", "Home", "velocity")] = 2
    state.output_results[("distance", "firstHalf", "Home", "distance_covered")] = 3
    execute._invalidate_outputs("velocity", "firstHalf", "Home")
    assert ("velocity", "firstHalf", "Home", "velocity") not in state.output_results
    assert ("velocity", "secondHalf", "Home", "velocity") in state.output_results
    assert ("distance", "firstHalf", "Home", "distance_covered") in state.output_results


# --------------------------------------------------------------------------- #
# End-to-end fit flow: select -> params -> execute through the public handler   #
# --------------------------------------------------------------------------- #


def _record_full_fit(harness):
    """Install a fit_model recorder that also captures the coerced ui_params dict.

    The harness default recorder drops ``ui_params``; the flow tests need to
    assert the coerced kwargs that reached the engine, so this records the full
    call (including ``ui_params``) into ``harness.fits``.
    """

    def _fit(app, model_key, period, team, ui_params, team_names=None):
        harness.fits.append(
            {
                "model_key": model_key,
                "period": period,
                "team": team,
                "ui_params": ui_params,
                "team_names": team_names,
            }
        )
        return f"MODEL::{period}::{team}"

    harness.set_fit(_fit)


def test_fit_flow_single_routes_selected_model_and_coerced_params(harness, monkeypatch):
    """Drive the real select -> params -> execute chain through ``on_fit``.

    Unlike the dispatch tests this uses the LIVE registry and the LIVE
    ``params.collect_ui_params``, so the velocity model's enum fit-params are read
    off seeded widgets and coerced (the ``axis`` "None" sentinel -> Python None)
    on the way to the (stubbed) ``fit_model``. A specific period + team are
    picked. The single leaf must reach ``fit_model`` exactly once carrying the
    bridged internal period, the raw team, and the coerced kwargs; the fitted
    result must be stored under ``(period, team, model_key)``; and MODEL_FITTED
    must be emitted once.
    """
    import floodlight_gui.tabs.model.params as params_mod
    from floodlight_gui.registry.models import MODEL_REGISTRY as REAL_REGISTRY

    # Use the live registry + live param collector for velocity (no stubs here).
    monkeypatch.setattr(execute_mod, "MODEL_REGISTRY", REAL_REGISTRY)
    monkeypatch.setattr(execute_mod.select, "active_model_key", lambda: "velocity")
    monkeypatch.setattr(execute_mod, "is_multi_team", lambda mk: False)
    # Restore the LIVE param collector (the harness fixture stubs it to {}); it
    # reads through params' own dpg, so point that at the harness stub too.
    monkeypatch.setattr(execute_mod.params, "collect_ui_params", harness.live_collect_ui_params)
    monkeypatch.setattr(params_mod, "dpg", harness.stub)
    _record_full_fit(harness)

    # Specific period + team.
    for tag, val in (("model_period_combo", "First Half"), ("model_team_combo", "Home")):
        harness.stub.existing_items.add(tag)
        harness.stub.values[tag] = val
    # Seed velocity's enum fit-param widgets so the live collector reads them.
    harness.stub.existing_items.update(
        {"model_param_velocity_difference", "model_param_velocity_axis"}
    )
    harness.stub.values["model_param_velocity_difference"] = "central"
    harness.stub.values["model_param_velocity_axis"] = "None"

    execute.on_fit()

    assert len(harness.fits) == 1
    call = harness.fits[0]
    assert call["model_key"] == "velocity"
    assert call["period"] == "firstHalf"  # bridged internal period
    assert call["team"] == "Home"  # raw team fed to the model
    assert call["team_names"] is None
    # enum "None" sentinel coerced to Python None before reaching the engine.
    assert call["ui_params"] == {"difference": "central", "axis": None}

    # Fitted result stored under the cache key, emitted once.
    assert ("firstHalf", "Home", "velocity") in state.fitted_models
    stored_model, _fp = state.fitted_models[("firstHalf", "Home", "velocity")]
    assert stored_model == "MODEL::firstHalf::Home"
    assert sum(1 for (ev, _) in harness.emitted if ev is Events.MODEL_FITTED) == 1


def test_fit_flow_multi_team_passes_both_resolved_teams(harness, monkeypatch):
    """Multi-team single fit feeds BOTH resolved teams to the model (silent-corrupting).

    For an arity-2 model the explicit Team A / Team B picks must both reach
    ``fit_model`` via ``team_names``; a wrong or dropped team would silently fit
    the wrong pairing. The leaf is stored under "BothTeams" with the resolved
    pair recorded in ``_team_names``.
    """
    from floodlight_gui.registry.models import MODEL_REGISTRY as REAL_REGISTRY

    monkeypatch.setattr(execute_mod, "MODEL_REGISTRY", REAL_REGISTRY)
    monkeypatch.setattr(execute_mod.select, "active_model_key", lambda: "nearest_opponent")
    monkeypatch.setattr(execute_mod, "is_multi_team", lambda mk: True)
    monkeypatch.setattr(execute_mod.params, "dpg", harness.stub)
    _record_full_fit(harness)

    for tag, val in (
        ("model_period_combo", "First Half"),
        ("model_team_combo_a", "Home"),
        ("model_team_combo_b", "Away"),
    ):
        harness.stub.existing_items.add(tag)
        harness.stub.values[tag] = val

    execute.on_fit()

    assert len(harness.fits) == 1
    call = harness.fits[0]
    assert call["model_key"] == "nearest_opponent"
    assert call["period"] == "firstHalf"
    assert call["team"] is None  # multi-team passes None for the single-team slot
    assert call["team_names"] == ["Home", "Away"]  # both resolved teams fed

    entry = state.fitted_models[("firstHalf", "BothTeams", "nearest_opponent")]
    assert entry[1]["_team_names"] == ("Home", "Away")
    assert sum(1 for (ev, _) in harness.emitted if ev is Events.MODEL_FITTED) == 1
