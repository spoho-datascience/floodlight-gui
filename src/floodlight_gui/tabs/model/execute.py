"""Model-tab producer: the Fit-Model button callback and broadcast routing.

Handles the multi-XY team gate, broadcast (period/team "All") fitting with
all-or-nothing rollback, and single fitting (incl. arity>1 explicit Team A/B).
Inputs are passed to ``fit_model`` VERBATIM (thin frontend -- no cleaning).

Layering: DPG-aware tab module. Writes ``state.fitted_models`` (cross-tab
contract read by metrics + viz) and emits ``Events.MODEL_FITTED`` on success.

Caching contract: fitted entries land in ``state.fitted_models`` under
key ``(period_internal, team_or_"BothTeams", model_key)`` with value
``(model_obj, fit_params)``. Multi-team fits store ``"BothTeams"`` and a
``_team_names`` tuple inside fit_params.
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.core.periods import period_internal_to_display
from floodlight_gui.engine.fit_model import fit_model, is_multi_team
from floodlight_gui.registry.models import MODEL_REGISTRY
from floodlight_gui.tabs._shared.broadcast import (
    ALL_SENTINEL,
    bridge_period_to_internal,
    check_multi_xy_model_team_gate,
)
from floodlight_gui.tabs._shared.error_helpers import friendly_error_message, show_error_modal
from floodlight_gui.tabs._shared.state_views import render_loading
from floodlight_gui.tabs.model import params, results, select, state

logger = logging.getLogger(__name__)

FIT_STATUS = "model_fit_status"
PARAMS_CONTAINER = "models_params_container"
STEP5_HEADER = "model_step3_header"  # Legacy id (Export step header); do not rename.


def on_fit(sender=None, app_data=None, user_data=None) -> None:  # noqa: ARG001 -- DPG cb
    """DPG callback for the Fit Model button. Delegates to ``_fit`` and absorbs all errors.

    Notes
    -----
    Broad ``except Exception`` is required at DPG callback boundaries so a fit error
    cannot crash the render loop (BLE001).
    """
    try:
        _fit()
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("model: fit callback crashed")


def _fit() -> None:
    """Resolve active model and combo selections, then dispatch to broadcast or single fit.

    Guards: requires a loaded data session and a resolved model key. Runs the
    multi-XY team gate before dispatching. Sets a status message and returns early
    on any guard failure.
    """
    app = state.app_instance
    if app is None or not getattr(app, "loaded_data", None):
        _status("No data loaded")
        return

    model_key = select.active_model_key()
    if model_key is None:
        return
    desc = MODEL_REGISTRY[model_key]

    # Multi-XY gate (fires only on the single team combo holding "All").
    try:
        check_multi_xy_model_team_gate(descriptor=desc, team_combo_value=_combo("model_team_combo"))
    except ValueError as exc:
        _status(f"Error: {friendly_error_message(exc)}")
        show_error_modal(PARAMS_CONTAINER, exc)
        return

    ui_params = params.collect_ui_params(model_key)
    arity = int(desc.get("fit_xy_arity", 1))
    multi = is_multi_team(model_key)

    period_raw = _combo("model_period_combo")
    team_raw = _combo("model_team_combo")
    is_broadcast = period_raw == ALL_SENTINEL or (team_raw == ALL_SENTINEL and arity == 1)

    if is_broadcast:
        _fit_broadcast(model_key, desc, ui_params, period_raw, team_raw, arity, multi)
    else:
        _fit_single(model_key, desc, ui_params, period_raw, arity, multi)


# --------------------------------------------------------------------------- #
# Broadcast (period/team "All") with all-or-nothing rollback
# --------------------------------------------------------------------------- #


def _fit_broadcast(model_key, desc, ui_params, period_raw, team_raw, arity, multi) -> None:
    """Fit a model across all selected (period, team) combinations with rollback on error.

    Iterates over every (period, team) combo derived from the "All" sentinel
    selections and calls ``fit_model`` for each. On any per-leaf error the
    accumulated fits are discarded (``state.fitted_models`` is not mutated).
    On full success all entries are committed at once, output caches are
    invalidated, the results view is rebuilt, and ``Events.MODEL_FITTED`` is
    emitted once.

    Notes
    -----
    Writes ``state.fitted_models`` (cross-tab contract).
    Emits ``Events.MODEL_FITTED`` once after all combos succeed.
    """
    app = state.app_instance
    periods = (
        list(app.get_temporal_divisions() or [])
        if period_raw == ALL_SENTINEL
        else [bridge_period_to_internal(period_raw)]
    )

    # Team axis: arity>=2 collapses to one "BothTeams" entry from explicit A/B
    # picks; arity==1 expands to all teams (when "All") else the single team.
    if arity >= 2:
        team_names = _explicit_team_names(arity)
        if team_names is None:
            return  # _explicit_team_names already set a friendly status
        combos = [(p, "BothTeams") for p in periods]
    elif team_raw == ALL_SENTINEL:
        team_names = None
        combos = [(p, t) for p in periods for t in (app.get_team_names() or [])]
    else:
        team_names = None
        combos = [(p, team_raw) for p in periods]

    _status("Fitting...")
    accumulated: dict = {}
    for period, team in combos:
        try:
            model = fit_model(
                app,
                model_key,
                period,
                None if team == "BothTeams" else team,
                ui_params,
                team_names=team_names,
            )
        except Exception as exc:  # noqa: BLE001 -- producer boundary
            logger.exception("model: broadcast fit failed at (%s, %s)", period, team)
            _status(
                f"Error: {friendly_error_message(exc)}. Rolled back {len(accumulated)} prior fits."
            )
            show_error_modal(PARAMS_CONTAINER, exc)
            return  # accumulated discarded; fitted_models untouched
        fp = dict(ui_params)
        if team_names is not None:
            fp["_team_names"] = tuple(team_names)
        accumulated[(period, team, model_key)] = (model, fp)

    # Full success: commit every entry, drop stale per-leaf output caches
    # (a re-fit reuses the same key), refresh, emit once.
    state.fitted_models.update(accumulated)
    for p, t, mk in accumulated:
        _invalidate_outputs(mk, p, t)
    results.rebuild()
    with contextlib.suppress(Exception):
        bus.emit(
            Events.MODEL_FITTED, model_key=model_key, model=None, half_name=None, team_name=None
        )
    _status(f"Fitted {desc['display_name']} for {len(accumulated)} combos")
    _open_export()


# --------------------------------------------------------------------------- #
# Single fit
# --------------------------------------------------------------------------- #


def _fit_single(model_key, desc, ui_params, period_raw, arity, multi) -> None:
    """Fit a model for one explicit (period, team) selection.

    Commits the result to ``state.fitted_models``, invalidates the per-leaf
    output cache, refreshes the results leaf, and emits ``Events.MODEL_FITTED``.
    For ``discrete_voronoi`` a loading placeholder is shown before the fit and
    the params widgets are restored afterwards.

    Notes
    -----
    Writes ``state.fitted_models`` (cross-tab contract).
    Emits ``Events.MODEL_FITTED`` on success.
    """
    app = state.app_instance
    period = bridge_period_to_internal(period_raw)
    team_raw = _combo("model_team_combo")

    if arity >= 2:
        team_names = _explicit_team_names(arity)
        if team_names is None:
            return
    else:
        team_names = None

    _status("Fitting...")
    if model_key == "discrete_voronoi":
        with contextlib.suppress(Exception):
            render_loading(PARAMS_CONTAINER, "Fitting Discrete Voronoi (full match)...")
    try:
        model = fit_model(
            app,
            model_key,
            period,
            None if multi else team_raw,
            ui_params,
            team_names=team_names,
        )
        store_team = "BothTeams" if multi else team_raw
        fp = dict(ui_params)
        if team_names is not None:
            fp["_team_names"] = tuple(team_names)
        state.fitted_models[(period, store_team, model_key)] = (model, fp)
        _invalidate_outputs(model_key, period, store_team)

        label = "both teams" if multi else team_raw
        results.refresh_leaf(model_key, period, store_team)
        emit_kwargs = dict(
            model_key=model_key,
            model=model,
            half_name=period,
            team_name=store_team,
        )
        if model_key == "discrete_voronoi":
            with contextlib.suppress(Exception):
                emit_kwargs["n_team1"] = _n_team1(model)
        with contextlib.suppress(Exception):
            bus.emit(Events.MODEL_FITTED, **emit_kwargs)
        _status(f"Fitted {desc['display_name']} for {label}, {period_internal_to_display(period)}")
        _open_export()
        # Restore the params widgets after a voronoi loading view.
        if model_key == "discrete_voronoi":
            params.rebuild_params(model_key)
    except Exception as exc:  # noqa: BLE001 -- producer boundary
        logger.exception("model: single fit failed")
        _status(f"Error: {exc}")
        show_error_modal(PARAMS_CONTAINER, exc)
        with contextlib.suppress(Exception):
            from floodlight_gui.tabs._shared.state_views import render_error

            render_error(PARAMS_CONTAINER, exc)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _invalidate_outputs(model_key: str, period: str, team: str) -> None:
    """Drop cached output results for a (re)fit leaf.

    Output results live in ``state.output_results`` keyed
    ``(model_key, period, team, output_key)`` and are computed lazily. A re-fit
    reuses the same ``fitted_models`` key, so without this the leaf would keep
    showing the previous fit's outputs (stale results after changing params or,
    for multi-XY models, swapping Team A / Team B).
    """
    stale = [
        k for k in state.output_results if k[0] == model_key and k[1] == period and k[2] == team
    ]
    for k in stale:
        del state.output_results[k]


def _explicit_team_names(arity: int) -> list[str] | None:
    """Read the explicit Team A/B/... combos; validate each slot is non-empty."""
    names: list[str] = []
    for i in range(arity):
        tag = f"model_team_combo_{chr(ord('a') + i)}"
        val = _combo(tag)
        if not val or val == ALL_SENTINEL:
            _status(f"Error: pick a team for Team {chr(ord('A') + i)}.")
            return None
        names.append(val)
    return names


def _n_team1(model) -> int | None:
    """Return the team-1 player count from a fitted model, or None if unavailable.

    floodlight's DiscreteVoronoiModel exposes the team-1 player count as
    ``_N1_`` (not ``n_team1``); the old attr name always returned None.
    """
    with contextlib.suppress(Exception):
        return getattr(model, "_N1_", None)
    return None


def _open_export() -> None:
    """Expand the Export step collapsible header if it exists."""
    if dpg.does_item_exist(STEP5_HEADER):
        with contextlib.suppress(SystemError):
            dpg.set_value(STEP5_HEADER, True)


def _status(text: str) -> None:
    """Write *text* to the fit-status DPG widget."""
    if dpg.does_item_exist(FIT_STATUS):
        with contextlib.suppress(SystemError):
            dpg.set_value(FIT_STATUS, text)


def _combo(tag: str) -> str:
    """Return the current value of a DPG combo/input widget, or empty string if absent."""
    return dpg.get_value(tag) if dpg.does_item_exist(tag) else ""
