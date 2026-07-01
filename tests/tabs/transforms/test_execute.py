"""Behavioral contracts for ``tabs.transforms.execute``.

The producer layer wires the four action buttons to the right backend call.
Its tab-owned job is routing: a single (period, team) pick must go through the
app wrapper (which emits one stack-changed event), while an "All" pick must go
through the matching broadcast helper exactly once (which also emits once).
The broadcast helpers, the app wrapper, and DPG are the seams; the helpers are
patched to recorders and the app is the recording double, so the tests assert
which collaborator was called with what, never the mutation result itself.

Behavioral contracts guarded here
---------------------------------
_apply_clicked
  C1  Single scope routes to ``app.apply_xy_op`` for the one bridged leaf and
      not to the broadcast helper.
  C2  Broadcast scope routes to ``broadcast_apply_xy_op`` exactly once with the
      store, op key, and the expanded period/team lists; the app wrapper is not
      called per leaf.
  C3  With no app or no loaded data, apply makes no backend call.
  C4  Single scope with no pristine XY for the leaf makes no apply call.

_undo_clicked
  C5  Single scope with a non-empty stack routes to ``app.undo_xy_op``; an empty
      stack makes no call.
  C6  Broadcast scope routes to ``broadcast_undo_xy_op`` exactly once.

_reset_target_clicked
  C7  Single scope routes to ``app.reset_xy_ops`` for the one leaf.
  C8  Broadcast scope routes to ``broadcast_reset_xy_op`` exactly once.

_reset_all_clicked
  C9  Routes to ``app.reset_xy_ops`` with no leaf arguments (clear-everything).
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.transforms.execute as execute
import floodlight_gui.tabs.transforms.params as params
import floodlight_gui.tabs.transforms.results as results
import floodlight_gui.tabs.transforms.select as select
import floodlight_gui.tabs.transforms.state as state
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install one shared fake DPG into every transforms module on the apply path.

    ``execute`` drives status text; ``select._resolve_scope`` and
    ``select._get_active_op_key`` read the combos and tab_bar;
    ``params._collect_params`` reads the param widgets; ``results`` and
    ``select`` refreshers run in the post-action refresh. All must see the same
    recorder so no real ``dearpygui`` call is reached.

    Returns
    -------
    SimpleNamespace
        The shared recorder.
    """
    stub = make_dpg_stub()
    monkeypatch.setattr(execute, "dpg", stub)
    monkeypatch.setattr(select, "dpg", stub)
    monkeypatch.setattr(params, "dpg", stub)
    monkeypatch.setattr(results, "dpg", stub)
    return stub


@pytest.fixture
def bound_app(monkeypatch, app_double):
    """Bind the recording app double onto the transforms tab state."""
    monkeypatch.setattr(state, "app_instance", app_double)
    return app_double


@pytest.fixture
def spy_broadcast(monkeypatch):
    """Replace the three broadcast helpers with call recorders.

    Returns
    -------
    dict
        Maps ``"apply"``/``"undo"``/``"reset"`` to a list of recorded
        ``(args, kwargs)`` tuples.
    """
    log: dict[str, list] = {"apply": [], "undo": [], "reset": []}

    def _rec(key):
        def _fn(*args, **kwargs):
            log[key].append((args, kwargs))

        return _fn

    monkeypatch.setattr(execute, "broadcast_apply_xy_op", _rec("apply"))
    monkeypatch.setattr(execute, "broadcast_undo_xy_op", _rec("undo"))
    monkeypatch.setattr(execute, "broadcast_reset_xy_op", _rec("reset"))
    return log


def _seed_combos(stub, period, team):
    """Seed both selector combos and the active category tab_bar."""
    stub.existing_items.update(
        {"transforms_period_combo", "transforms_team_combo", "transforms_category_tab_bar"}
    )
    stub.values["transforms_period_combo"] = period
    stub.values["transforms_team_combo"] = team
    # Filter category is the cold-start default; its op is butterworth_lowpass.
    stub.values["transforms_category_tab_bar"] = "transforms_category_filter_tab"


def _names(app):
    """Return the recorded backend method names on the app double."""
    return [c[0] for c in app.calls]


# --------------------------------------------------------------------------- #
# _apply_clicked                                                               #
# --------------------------------------------------------------------------- #


def test_apply_single_routes_to_app(dpg_stub, bound_app, spy_broadcast):
    """C1: a specific period/team applies via the app wrapper, not broadcast."""
    _seed_combos(dpg_stub, "First Half", "Home")
    execute._apply_clicked(None, None)
    assert ("apply_xy_op", "firstHalf", "Home", "butterworth_lowpass", {}) in [
        (c[0], c[1], c[2], c[3], c[4]) for c in bound_app.calls if c[0] == "apply_xy_op"
    ]
    assert spy_broadcast["apply"] == []


def test_apply_broadcast_routes_to_helper_once(dpg_stub, bound_app, spy_broadcast):
    """C2: an "All" pick applies via the broadcast helper exactly once."""
    _seed_combos(dpg_stub, "All", "All")
    execute._apply_clicked(None, None)
    assert len(spy_broadcast["apply"]) == 1
    _args, kwargs = spy_broadcast["apply"][0]
    assert kwargs["op_key"] == "butterworth_lowpass"
    assert kwargs["periods"] == bound_app.get_temporal_divisions()
    assert kwargs["teams"] == bound_app.get_team_names()
    assert "apply_xy_op" not in _names(bound_app)


def test_apply_no_data_makes_no_call(dpg_stub, bound_app, spy_broadcast):
    """C3: apply is a no-op (no backend call) when no data is loaded."""
    bound_app.loaded_data = None
    _seed_combos(dpg_stub, "First Half", "Home")
    execute._apply_clicked(None, None)
    assert bound_app.calls == []
    assert spy_broadcast["apply"] == []


def test_apply_single_missing_pristine_xy_skips(dpg_stub, bound_app, spy_broadcast):
    """C4: single scope with no pristine XY for the leaf makes no apply call."""
    _seed_combos(dpg_stub, "First Half", "Home")
    bound_app._pristine = None
    execute._apply_clicked(None, None)
    assert "apply_xy_op" not in _names(bound_app)


# --------------------------------------------------------------------------- #
# _undo_clicked                                                                #
# --------------------------------------------------------------------------- #


def test_undo_single_routes_to_app_when_stack_nonempty(dpg_stub, bound_app, spy_broadcast):
    """C5: undo routes to the app wrapper only when the leaf stack is non-empty."""
    _seed_combos(dpg_stub, "First Half", "Home")
    # Empty stack: nothing to undo.
    execute._undo_clicked(None, None)
    assert "undo_xy_op" not in _names(bound_app)
    # Non-empty stack: routes to the app wrapper.
    bound_app._stacks[("firstHalf", "Home")] = [("butterworth_lowpass", {})]
    execute._undo_clicked(None, None)
    assert ("undo_xy_op", "firstHalf", "Home") in bound_app.calls


def test_undo_broadcast_routes_to_helper_once(dpg_stub, bound_app, spy_broadcast):
    """C6: an "All" pick undoes via the broadcast helper exactly once."""
    _seed_combos(dpg_stub, "All", "All")
    execute._undo_clicked(None, None)
    assert len(spy_broadcast["undo"]) == 1
    assert "undo_xy_op" not in _names(bound_app)


# --------------------------------------------------------------------------- #
# _reset_target_clicked                                                        #
# --------------------------------------------------------------------------- #


def test_reset_target_single_routes_to_app(dpg_stub, bound_app, spy_broadcast):
    """C7: a specific pick resets the single leaf via the app wrapper."""
    _seed_combos(dpg_stub, "First Half", "Home")
    execute._reset_target_clicked(None, None)
    assert ("reset_xy_ops", "firstHalf", "Home") in bound_app.calls
    assert spy_broadcast["reset"] == []


def test_reset_target_broadcast_routes_to_helper_once(dpg_stub, bound_app, spy_broadcast):
    """C8: an "All" pick resets via the broadcast helper exactly once."""
    _seed_combos(dpg_stub, "All", "All")
    execute._reset_target_clicked(None, None)
    assert len(spy_broadcast["reset"]) == 1


# --------------------------------------------------------------------------- #
# _reset_all_clicked                                                           #
# --------------------------------------------------------------------------- #


def test_reset_all_clears_everything(dpg_stub, bound_app, spy_broadcast):
    """C9: reset-all routes to the app wrapper with no leaf arguments."""
    execute._reset_all_clicked(None, None)
    assert ("reset_xy_ops", None, None) in bound_app.calls


# --------------------------------------------------------------------------- #
# End-to-end producer flow: select -> params -> execute                        #
# --------------------------------------------------------------------------- #


def test_apply_flow_routes_selected_op_and_collected_kwargs(dpg_stub, bound_app, spy_broadcast):
    """Drive select -> params -> execute as one chain through the public Apply handler.

    Exercises the real producer wiring end to end: the active category tab_bar
    is set to ``filter`` with ``butterworth_lowpass`` selected (its cold-start
    op), its param widgets are seeded on the stub, and a specific period + team
    are picked. Invoking ``_apply_clicked`` must route the single leaf to
    ``app.apply_xy_op`` exactly once, carrying the bridged internal period, the
    raw team, the resolved op key, and the type-coerced kwargs collected from
    the param widgets (``order`` int, ``Wn`` float).
    """
    # Step 1: specific period + team; filter category active (butterworth_lowpass).
    _seed_combos(dpg_stub, "First Half", "Home")

    # Step 2/3: seed butterworth_lowpass param widgets so _collect_params reads
    # them through the live descriptor (order: int, Wn: float, remove_short_seqs: bool).
    dpg_stub.existing_items.update(
        {
            "transforms_param_butterworth_lowpass_order",
            "transforms_param_butterworth_lowpass_Wn",
            "transforms_param_butterworth_lowpass_remove_short_seqs",
        }
    )
    dpg_stub.values["transforms_param_butterworth_lowpass_order"] = "3"
    dpg_stub.values["transforms_param_butterworth_lowpass_Wn"] = "1.0"
    dpg_stub.values["transforms_param_butterworth_lowpass_remove_short_seqs"] = False

    execute._apply_clicked(None, None)

    apply_calls = [c for c in bound_app.calls if c[0] == "apply_xy_op"]
    assert len(apply_calls) == 1
    _name, period_internal, team, op_key, kwargs = apply_calls[0]
    assert period_internal == "firstHalf"
    assert team == "Home"
    assert op_key == "butterworth_lowpass"
    assert kwargs == {"order": 3, "Wn": 1.0, "remove_short_seqs": False}
    assert spy_broadcast["apply"] == []
