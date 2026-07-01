"""Behavioral contracts for ``floodlight_gui.tabs.metrics.execute``.

This is the compute producer behind the Compute button and the metrics tab's
silent-corrupting core: flow selection (single vs. broadcast), cache-key
derivation, and the transactional commit-or-rollback of the broadcast loop. The
pure computation (``calculate_metric``), the kwargs collection (``params``), and
the results panel (``results``) are collaborators owned elsewhere and are
stubbed; the DPG combos are the seam and use the shared fake-DPG stub. Tests
assert which leaves this module stages, commits, and rolls back, never any
computed metric numbers.

Not guarded (loud or defensive, caught in seconds of use): the no-metric no-op
and the multi-XY rejection (both defensive guards, and no shipped metric
declares multi-XY) and the cosmetic status-string assertions.

Behavioral contracts guarded here
---------------------------------
_should_broadcast
  C1  Broadcasts only when a Step-1 combo holds "All" and the metric has an XY
      or a model-output input; a pure-param metric or a fully-specified scope
      never broadcasts.

on_compute (boundary transaction)
  C2  Catches any compute exception at the callback boundary and leaves
      ``state.results`` unchanged (no partial write leaks).

_compute_single (cache-key derivation)
  C3  An XY metric stores one leaf keyed by (metric, period_internal, team) from
      the Step-1 combos and forwards the collected kwargs to ``calculate_metric``.
  C4  A non-XY metric keys its leaf by the source metadata derived from the
      picked model output, not by the "All" combo.

_compute_broadcast (transactional commit / rollback)
  C5  On full success, every staged leaf is committed to ``state.results`` and
      the saved combo values are restored.
  C6  When a leaf raises, ``state.results`` is left untouched (staged dict
      discarded) and the original exception propagates.
  C7  With no leaves in scope, nothing is committed.

_broadcast_combos / _derive_source_key (leaf enumeration + cache key)
  C8  An XY metric expands to the period x team cross-product, iterating a
      concrete axis value as a singleton and "All" as every division/team.
  C9  A non-XY metric derives a (period, composite source-label) cache key from
      the picked model outputs so distinct sources stay in distinct leaves.

flow (end-to-end select -> params -> execute -> cache)
  C10 Driving the public ``on_compute`` for a real XY metric with a concrete
      period/team collects kwargs, calls the (stubbed) ``calculate_metric``
      exactly once with them, and caches the result under the correct
      (metric, period_internal, team) key.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import floodlight_gui.tabs.metrics.execute as execute
from floodlight_gui.tabs.metrics import state
from tests._dpg_stub import make_dpg_stub

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """Reset the shared metrics state to a clean slate for each test."""
    monkeypatch.setattr(state, "results", {})
    monkeypatch.setattr(state, "selected_metric_key", None)
    monkeypatch.setattr(state, "input_widgets", {})
    monkeypatch.setattr(state, "app_instance", None)
    monkeypatch.setattr(state, "panel", None)


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install the fake-DPG stub on the execute module.

    Returns a callable ``(period, team) -> stub`` that seeds the two Step-1
    combos with the given values (both tags always exist).
    """

    def _install(period="All", team="All"):
        stub = make_dpg_stub(
            values={"metrics_period_combo": period, "metrics_team_combo": team},
            existing_items={"metrics_period_combo", "metrics_team_combo"},
        )
        monkeypatch.setattr(execute, "dpg", stub)
        return stub

    return _install


@pytest.fixture
def stub_calc(monkeypatch):
    """Replace ``calculate_metric`` with a recording double.

    Returns the recorder; ``recorder.calls`` lists the ``(descriptor, kwargs)``
    tuples and each call returns a unique sentinel dict so cache values are
    assertable. Set ``recorder.raise_on`` to a kwargs-predicate to make a
    specific leaf fail.
    """

    def _recorder(descriptor, kwargs):
        _recorder.calls.append((descriptor, dict(kwargs)))
        if _recorder.raise_on is not None and _recorder.raise_on(kwargs):
            raise ValueError("boom")
        return {"value": float(len(_recorder.calls))}

    _recorder.calls = []
    _recorder.raise_on = None
    monkeypatch.setattr(execute, "calculate_metric", _recorder)
    return _recorder


@pytest.fixture
def stub_collaborators(monkeypatch):
    """Stub ``params.collect_kwargs`` and the ``results`` panel calls.

    ``collect_kwargs`` echoes its period/team so the compute leaf and its kwargs
    are traceable; the results panel calls become no-ops.
    """
    monkeypatch.setattr(
        execute.params,
        "collect_kwargs",
        lambda key, *, period_internal, team: {"period": period_internal, "team": team},
    )
    monkeypatch.setattr(execute.results, "refresh_leaf", lambda *a, **k: None)
    monkeypatch.setattr(execute.results, "rebuild", lambda *a, **k: None)


def _xy_descriptor():
    """Return a minimal XY-input metric descriptor."""
    return {"display_name": "Formation", "inputs": {"xy": {"type": "XY"}}, "params": {}}


def _param_only_descriptor():
    """Return a metric descriptor with no XY or model-output input."""
    return {"display_name": "Pure", "inputs": {}, "params": {}}


# --------------------------------------------------------------------------- #
# _should_broadcast
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "period, team, descriptor, expected",
    [
        # "All" on an axis + XY input -> broadcast.
        ("All", "Home", _xy_descriptor(), True),
        ("First Half", "All", _xy_descriptor(), True),
        # No "All" anywhere -> single even with an XY input.
        ("First Half", "Home", _xy_descriptor(), False),
        # "All" present but pure-param metric -> never broadcast.
        ("All", "All", _param_only_descriptor(), False),
    ],
)
def test_should_broadcast(dpg_stub, period, team, descriptor, expected):
    """C1: broadcast iff a combo is "All" and the metric has a broadcastable input."""
    dpg_stub(period, team)
    assert execute._should_broadcast(descriptor) is expected


# --------------------------------------------------------------------------- #
# on_compute boundary transaction
# --------------------------------------------------------------------------- #


def test_on_compute_catches_compute_error(monkeypatch, dpg_stub):
    """C2: an exception during compute is caught and results left unchanged."""
    dpg_stub("First Half", "Home")
    desc = _xy_descriptor()
    monkeypatch.setattr(execute, "METRICS_REGISTRY", {"m": desc})

    def _boom(metric_key, descriptor):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(execute, "_compute_single", _boom)
    monkeypatch.setattr(execute, "show_error_modal", lambda *a, **k: None)
    monkeypatch.setattr(execute, "render_error", lambda *a, **k: None)
    state.selected_metric_key = "m"

    execute.on_compute()  # must not raise
    assert state.results == {}


# --------------------------------------------------------------------------- #
# _compute_single (cache-key derivation)
# --------------------------------------------------------------------------- #


def test_compute_single_xy_keys_by_step1_combos(dpg_stub, stub_calc, stub_collaborators):
    """C3: an XY metric stores one leaf keyed by the Step-1 (period, team)."""
    dpg_stub("First Half", "Home")
    desc = _xy_descriptor()
    execute._compute_single("m", desc)

    assert list(state.results.keys()) == [("m", "firstHalf", "Home")]
    # The collected kwargs (period/team echoed by the stub) reach calculate_metric.
    assert stub_calc.calls[0][1] == {"period": "firstHalf", "team": "Home"}


def test_compute_single_non_xy_keys_by_source_metadata(
    monkeypatch, dpg_stub, stub_calc, stub_collaborators
):
    """C4: a non-XY metric keys its leaf by the derived source, not the combo."""
    dpg_stub("All", "All")
    desc = {"display_name": "Entropy", "inputs": {"sig": {"type": "sig"}}, "params": {}}
    monkeypatch.setattr(execute, "_derive_source_key", lambda d: ("firstHalf", "Home:velocity"))
    execute._compute_single("m", desc)
    assert list(state.results.keys()) == [("m", "firstHalf", "Home:velocity")]


# --------------------------------------------------------------------------- #
# _compute_broadcast (transactional commit / rollback)
# --------------------------------------------------------------------------- #


def test_compute_broadcast_commits_all_and_restores_combos(
    monkeypatch, dpg_stub, stub_calc, stub_collaborators
):
    """C5: a successful broadcast commits every leaf and restores the combos."""
    stub = dpg_stub("All", "All")
    desc = _xy_descriptor()
    combos = [
        ("firstHalf", "Home", "First Half"),
        ("firstHalf", "Away", "First Half"),
    ]
    monkeypatch.setattr(execute, "_broadcast_combos", lambda d: combos)

    execute._compute_broadcast("m", desc)

    assert set(state.results.keys()) == {
        ("m", "firstHalf", "Home"),
        ("m", "firstHalf", "Away"),
    }
    # Combos restored to their saved "All" values after iteration.
    assert stub.values["metrics_period_combo"] == "All"
    assert stub.values["metrics_team_combo"] == "All"


def test_compute_broadcast_rolls_back_on_failure(
    monkeypatch, dpg_stub, stub_calc, stub_collaborators
):
    """C6: a failing leaf leaves state.results untouched and re-raises."""
    dpg_stub("All", "All")
    desc = _xy_descriptor()
    combos = [
        ("firstHalf", "Home", "First Half"),
        ("firstHalf", "Away", "First Half"),
    ]
    monkeypatch.setattr(execute, "_broadcast_combos", lambda d: combos)
    # Fail on the Away leaf; the Home leaf was already staged but must be dropped.
    stub_calc.raise_on = lambda kw: kw["team"] == "Away"

    with pytest.raises(ValueError, match="boom"):
        execute._compute_broadcast("m", desc)
    assert state.results == {}


def test_compute_broadcast_empty_scope_commits_nothing(
    monkeypatch, dpg_stub, stub_calc, stub_collaborators
):
    """C7: no leaves in scope commits nothing (no partial / placeholder write)."""
    dpg_stub("All", "All")
    desc = _xy_descriptor()
    monkeypatch.setattr(execute, "_broadcast_combos", lambda d: [])

    execute._compute_broadcast("m", desc)
    assert state.results == {}
    assert stub_calc.calls == []


# --------------------------------------------------------------------------- #
# _broadcast_combos / _derive_source_key (leaf enumeration + cache key)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "period, team, expected",
    [
        # Both "All": full period x team cross-product.
        (
            "All",
            "All",
            [
                ("firstHalf", "Home", "First Half"),
                ("firstHalf", "Away", "First Half"),
                ("secondHalf", "Home", "Second Half"),
                ("secondHalf", "Away", "Second Half"),
            ],
        ),
        # Concrete period axis: iterated as a singleton, not expanded.
        (
            "First Half",
            "All",
            [
                ("firstHalf", "Home", "First Half"),
                ("firstHalf", "Away", "First Half"),
            ],
        ),
    ],
    ids=["cross-product", "specified-axis"],
)
def test_broadcast_combos_xy_enumeration(monkeypatch, dpg_stub, period, team, expected):
    """C8: an XY metric expands "All" axes and keeps a concrete axis as a singleton.

    Each combo carries (period_internal, team, period_display) so the loop can
    drive the Step-1 combo per iteration.
    """
    dpg_stub(period, team)
    app = SimpleNamespace(
        get_temporal_divisions=lambda: ["firstHalf", "secondHalf"],
        get_team_names=lambda: ["Home", "Away"],
    )
    monkeypatch.setattr(state, "app_instance", app)

    assert execute._broadcast_combos(_xy_descriptor()) == expected


def test_derive_source_key_builds_composite_label(monkeypatch, dpg_stub):
    """C9: a non-XY metric derives (period, "team:output + ...") from picked outputs.

    Distinct sources land in distinct leaves rather than collapsing under "All".
    """
    dpg_stub("All", "All")
    desc = {"inputs": {"a": {"type": "sig"}, "b": {"type": "PlayerProperty|TeamProperty"}}}
    records = [
        {"label": "LblA", "period": "firstHalf", "team": "Home", "output_key": "velocity"},
        {"label": "LblB", "period": "firstHalf", "team": "Away", "output_key": "accel"},
    ]
    monkeypatch.setattr(execute.params, "available_outputs", lambda: records)
    monkeypatch.setattr(
        state,
        "input_widgets",
        {"a": {"source_combo": "sa"}, "b": {"source_combo": "sb"}},
    )
    execute.dpg.values["sa"] = "LblA"
    execute.dpg.values["sb"] = "LblB"

    period, label = execute._derive_source_key(desc)
    assert period == "firstHalf"
    assert label == "Home:velocity + Away:accel"


# --------------------------------------------------------------------------- #
# End-to-end flow: select -> params -> execute -> cache
# --------------------------------------------------------------------------- #


def test_compute_flow_xy_metric_routes_collected_kwargs_to_cache(monkeypatch, dpg_stub, stub_calc):
    """C10: drive the public on_compute for an XY metric end to end.

    Step 1 picks a concrete period + team; the XY metric's real ``collect_kwargs``
    runs against a stubbed XY resolver to seed the input kwarg. Invoking
    ``on_compute`` must take the single-compute flow, call the stubbed
    ``calculate_metric`` exactly once with the collected kwargs, and cache the
    returned result under the correct (metric, period_internal, team) key. The
    executor (``calculate_metric``) stays stubbed so this guards the tab's own
    select -> params -> execute -> cache wiring, not the computation.
    """
    stub = dpg_stub("First Half", "Home")
    # The real params.collect_kwargs reads dpg through the params module too.
    monkeypatch.setattr(execute.params, "dpg", stub)
    monkeypatch.setattr(execute.results, "refresh_leaf", lambda *a, **k: None)

    desc = _xy_descriptor()
    monkeypatch.setattr(execute, "METRICS_REGISTRY", {"formation": desc})
    monkeypatch.setattr(execute.params, "METRICS_REGISTRY", {"formation": desc})

    # XY input resolves through get_xy_for_period_team(app, period, team).
    resolver_calls = []

    def _resolver(app, period, team):
        resolver_calls.append((period, team))
        return "XY_OBJ"

    monkeypatch.setattr("floodlight_gui.core.xy_access.get_xy_for_period_team", _resolver)
    state.input_widgets = {"xy": {"type": "XY"}}
    state.selected_metric_key = "formation"
    state.app_instance = object()

    execute.on_compute()

    # calculate_metric called exactly once, with the collected XY kwarg.
    assert len(stub_calc.calls) == 1
    _desc, kwargs = stub_calc.calls[0]
    assert kwargs == {"xy": "XY_OBJ"}
    assert resolver_calls == [("firstHalf", "Home")]
    # Result cached under the correct (metric, period_internal, team) key.
    assert list(state.results.keys()) == [("formation", "firstHalf", "Home")]
    assert state.results[("formation", "firstHalf", "Home")] == {"value": 1.0}
