"""CSV export for fitted floodlight models and metrics.

Projects fitted-model method results (PlayerProperty, TeamProperty, XY, etc.)
to labeled DataFrames and writes them to CSV. DPG-free at module scope: this
module contains no dearpygui imports and must remain importable in the
DPG-free backend layer.
"""

from __future__ import annotations

import inspect
import logging
import os

import pandas as pd
from floodlight.core.property import DyadicProperty, PlayerProperty, TeamProperty
from floodlight.core.xy import XY as FlXY

from floodlight_gui.core.xy_access import get_xy_for_period_team

logger = logging.getLogger(__name__)


def _property_to_dataframe(
    method_result,
    slots,
    *,
    name: str,
    team_name: str,
    teams_in_data: list,
    selected_players: list,
    slots_resolver,
    resolve_slot,
    fit_params=None,
) -> pd.DataFrame:
    """Convert a fitted-model method result to a labeled DataFrame (pure projector).

    No I/O, no DPG, no file operations: only data shaping. Used by
    ``export_model_metric_directly`` to produce a consistently labeled DataFrame.

    Parameters
    ----------
    method_result :
        Raw return value from the fitted model method. Dispatched via isinstance
        across 6 branches (Tuple, TeamProperty, DyadicProperty, FlXY,
        PlayerProperty, fallback).
    slots :
        Primary team's PlayerSlot list (used by FlXY generic branch + PlayerProperty).
    name : str
        Method name / metric name; drives column suffixes.
    team_name : str
        Primary team label (e.g. "Home").
    teams_in_data : list
        All team labels in the loaded dataset, used by the Tuple branch to label
        team columns (e.g. ["Home", "Away"]). Falls back to ["Home", "Away"] if
        fewer than 2 entries.
    selected_players : list
        Player identifiers to include in PlayerProperty columns. Empty list returns
        an empty DataFrame.
    slots_resolver : callable
        ``slots_resolver(team_label) -> list[PlayerSlot]``, called by the Tuple
        branch to obtain per-team slot lists for multi-team exports.
    resolve_slot : callable
        ``resolve_slot(player_id) -> PlayerSlot | None``, called by the
        PlayerProperty branch to map a player identifier to a slot.
    fit_params : dict, optional
        Fit-time parameters. ``fit_params["axis"]`` drives the column suffix for
        VelocityModel / AccelerationModel:

          - ``None``  -> ``_{name}_magnitude``
          - ``"x"``   -> ``_{name}_x``
          - ``"y"``   -> ``_{name}_y``

        For non-kinematic methods the suffix is simply ``_{name}`` (no axis tag).

    Returns
    -------
    pd.DataFrame
        Labeled DataFrame. May be empty (e.g. no selected_players for
        PlayerProperty). Callers are responsible for checking ``df.empty`` before
        writing to disk.

    Raises
    ------
    NotImplementedError
        For DyadicProperty inputs (deferred; not yet supported).
    ValueError
        For unknown result types with no ``.property`` attribute.
    """
    df = pd.DataFrame()

    # ----- Tuple[PlayerProperty | TeamProperty, ...] - two-team outputs -----
    if isinstance(method_result, tuple) and len(method_result) == 2:
        team_a_result, team_b_result = method_result
        # Ensure we have at least two team labels.
        effective_teams = list(teams_in_data[:2]) if len(teams_in_data) >= 2 else ["Home", "Away"]

        for team_label, team_result in zip(
            effective_teams, (team_a_result, team_b_result), strict=False
        ):  # noqa: E501
            if isinstance(team_result, PlayerProperty):
                # (T, N) - per-player columns; obtain slots for this team via resolver.
                team_slots = slots_resolver(team_label)
                for slot in team_slots:
                    if slot.col_index < team_result.property.shape[1]:
                        col = f"{team_label}_{_slot_label(slot)}_{name}"
                        df[col] = team_result.property[:, slot.col_index]
            elif isinstance(team_result, TeamProperty):
                col = f"{team_label}_{name}"
                df[col] = team_result.property

    # ----- TeamProperty (T,) -----
    elif isinstance(method_result, TeamProperty):
        col = f"{team_name}_{name}"
        df[col] = method_result.property

    # ----- DyadicProperty (T, N1, N2) - not yet supported -----
    elif isinstance(method_result, DyadicProperty):
        shape = method_result.property.shape
        raise NotImplementedError(
            f"DyadicProperty export is not supported yet "
            f"(T={shape[0]}, N1={shape[1]}, N2={shape[2]})."
        )

    # ----- XY return (e.g. CentroidModel.centroid() -> XY(T, 2)) -----
    elif isinstance(method_result, FlXY):
        xy_arr = method_result.xy
        if xy_arr.ndim == 2 and xy_arr.shape[1] == 2:
            # Centroid-like: exactly 2 columns (x, y)
            df[f"{team_name}_{name}_x"] = xy_arr[:, 0]
            df[f"{team_name}_{name}_y"] = xy_arr[:, 1]
        else:
            # Generic per-player x/y columns for (T, 2N) tracking XY
            for slot in slots:
                if 2 * slot.col_index + 1 < xy_arr.shape[1]:
                    label = _slot_label(slot)
                    df[f"{label}_{name}_x"] = xy_arr[:, 2 * slot.col_index]
                    df[f"{label}_{name}_y"] = xy_arr[:, 2 * slot.col_index + 1]

    # ----- PlayerProperty (T, N) - per-player columns -----
    elif isinstance(method_result, PlayerProperty):
        prop_arr = method_result.property  # shape (T, N)

        # Derive column suffix from fit_params['axis'] for kinematic methods.
        axis = (fit_params or {}).get("axis")
        if name in ("velocity", "acceleration"):
            if axis is None:
                suffix = "magnitude"
            elif axis in ("x", "y"):
                suffix = axis
            else:
                suffix = str(axis)
            col_suffix = f"_{name}_{suffix}"
        else:
            col_suffix = f"_{name}"

        for player_id in selected_players:
            slot = resolve_slot(player_id)
            if slot is None:
                logger.debug("Player id %r not resolved to a slot; skipping", player_id)
                continue
            if slot.col_index >= prop_arr.shape[1]:
                logger.debug(
                    "Player slot col_index %d out of range for property shape %s; skipping",
                    slot.col_index,
                    prop_arr.shape,
                )
                continue
            col = f"{_slot_label(slot)}{col_suffix}"
            df[col] = prop_arr[:, slot.col_index]

    # ----- Fallback: unknown type - try .property attribute -----
    else:
        if hasattr(method_result, "property"):
            arr = method_result.property
            if arr.ndim == 1:
                df[f"{team_name}_{name}"] = arr
            else:
                for i in range(arr.shape[1] if arr.ndim >= 2 else 1):
                    df[f"col_{i}_{name}"] = arr[:, i] if arr.ndim >= 2 else arr
        else:
            raise ValueError(
                f"Cannot export {type(method_result).__name__} — "
                f"unknown return type for method {name!r}"
            )

    # Prepend a `frame` column so concatenated or downstream-loaded CSVs
    # stay frame-aligned regardless of read order. Only when at least one
    # data column was added (empty DataFrame is returned as-is).
    if not df.empty:
        df.insert(0, "frame", range(len(df)))

    return df


def _slot_label(slot) -> str:
    """Return a unique, concise per-slot label for projector column names.

    The column identifier is xID alone: all other per-player context (name,
    jersey, team) belongs in the filename or in the upstream teamsheet, not
    the CSV header. Falls back to ``col_{N}`` when xid is None so
    teamsheet-less data still gets unique columns (col_index is always set).
    """
    if slot.xid is not None:
        return str(slot.xid)
    return f"col_{slot.col_index}"


def _build_resolve_slot(slots):
    """Build a ``resolve_slot`` callable from a list of PlayerSlot objects.

    Supports four lookup strategies: pid exact match, ``"col_<N>"`` for
    teamsheet-less data, ``"x<N>"`` xid prefix form, and bare int/str xid
    coercion. Returns None when no strategy matches.
    """
    slot_by_pid: dict = {s.pid: s for s in slots if s.pid is not None}
    slot_by_xid: dict = {s.xid: s for s in slots if s.xid is not None}
    slot_by_col: dict = {s.col_index: s for s in slots}

    def _resolve_slot(player_id):
        """Look up a PlayerSlot by player_id using all four strategies."""
        if player_id in slot_by_pid:
            return slot_by_pid[player_id]
        if isinstance(player_id, str) and player_id.startswith("col_"):
            try:
                col_int = int(player_id[4:])
            except (TypeError, ValueError):
                col_int = None
            if col_int is not None and col_int in slot_by_col:
                return slot_by_col[col_int]
        if isinstance(player_id, str) and player_id.startswith("x"):
            try:
                xid_int = int(player_id[1:])
            except (TypeError, ValueError):
                xid_int = None
            if xid_int is not None and xid_int in slot_by_xid:
                return slot_by_xid[xid_int]
        try:
            xid_int = int(player_id)
        except (TypeError, ValueError):
            xid_int = None
        if xid_int is not None and xid_int in slot_by_xid:
            return slot_by_xid[xid_int]
        return None

    return _resolve_slot


def export_model_metric_directly(
    app_instance,
    fitted_model,
    selected_players,
    filename,
    half_name,
    team_name,
    model_type,
    method_name,
    fit_params=None,
    *,
    output_dir: str = "exports",
):
    """Export a specific metric from a fitted model to CSV.

    Resolves app-level context (slots, XY accessor, team labels), calls
    :func:`_property_to_dataframe` to project the method result to a labeled
    DataFrame, then writes to CSV.

    Dispatch on floodlight Property types produces correct CSV column layouts:

    - PlayerProperty (T, N): per-player columns (one per selected player)
    - TeamProperty (T,): single team column
    - Tuple[PlayerProperty, PlayerProperty]: both teams' columns side by side
    - Tuple[TeamProperty, TeamProperty]: both teams' single columns
    - XY (T, 2): two columns (_x, _y) for centroid-like outputs
    - DyadicProperty: not yet supported; returns (False, error_message)

    Parameters
    ----------
    app_instance :
        The main app instance.
    fitted_model :
        The fitted floodlight model.
    selected_players :
        List of selected player identifiers.
    filename :
        Base filename for export (without .csv extension).
    half_name :
        Temporal division name.
    team_name :
        Team name.
    model_type :
        Display label for the model; used in error messages.
    method_name :
        Method name to call on fitted_model.
    fit_params : dict, optional
        Fit-time parameters. For VelocityModel / AccelerationModel the
        ``'axis'`` entry drives the exported column suffix:
        ``None`` -> ``'magnitude'``, ``'x'`` -> ``'x'``, ``'y'`` -> ``'y'``.
    output_dir : str, optional
        Destination folder for the CSV file. Defaults to ``"exports"``.
        Auto-created if it does not exist. Call sites should supply
        ``get_export_dir()`` from ``tabs/_shared/export_action.py`` to honour
        the session-scoped folder chosen by the user.

    Returns
    -------
    tuple
        ``(success: bool, filepath_or_error: str)``
    """
    try:
        logger.debug("Exporting %s for %s", method_name, model_type)
        logger.debug("Selected players: %s", selected_players)
        logger.debug("Half: %s, Team: %s", half_name, team_name)

        # Resolve player_id -> col_index via PlayerSlot map.
        slots = (
            app_instance.get_player_slots(team_name)
            if hasattr(app_instance, "get_player_slots")
            else []
        )
        if not slots:
            raise ValueError(f"No player slots found for {team_name}")

        # Build resolve_slot callable using the shared helper.
        resolve_slot = _build_resolve_slot(slots)

        def slots_resolver(team_label):
            """Return the PlayerSlot list for team_label, or [] when unavailable."""
            if hasattr(app_instance, "get_player_slots"):
                return app_instance.get_player_slots(team_label)
            return []

        # Fetch the XY for this period/team; required by methods that take XY as input.
        team_data = get_xy_for_period_team(app_instance, half_name, team_name)
        if team_data is None:
            raise ValueError(f"Could not find data for {team_name} in {half_name}")

        # ------------------------------------------------------------------ #
        # Resolve method and call it.
        # Some output methods (e.g. CentroidModel.stretch_index / centroid_distance)
        # require the XY as their first argument (post-fit queries, not no-arg
        # accessors). Detect via inspect and pass team_data in that case.
        # ------------------------------------------------------------------ #
        if not hasattr(fitted_model, method_name):
            return False, (
                f"Method '{method_name}' not available on {model_type} model — "
                f"available: {[m for m in dir(fitted_model) if not m.startswith('_')]}"
            )

        try:
            bound_method = getattr(fitted_model, method_name)
            sig = inspect.signature(bound_method)
            # Collect required positional params (excluding 'self', which is already
            # bound, and params with defaults).
            required_params = [
                p
                for p in sig.parameters.values()
                if p.default is inspect.Parameter.empty
                and p.kind
                not in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                )
            ]
            if required_params:
                # Method needs arguments; pass team_data as the XY input.
                # Covers CentroidModel.stretch_index(xy) and .centroid_distance(xy).
                # Forward the axis kwarg from fit_params where accepted.
                kwargs = {}
                param_names = list(sig.parameters.keys())
                if "axis" in param_names:
                    axis = (fit_params or {}).get("axis")
                    kwargs["axis"] = axis
                method_result = bound_method(team_data, **kwargs)
            else:
                method_result = bound_method()
        except Exception as e:  # noqa: BLE001
            logger.exception("Method call failed: %s.%s", model_type, method_name)
            return False, f"Method {method_name}() failed: {e}"

        logger.debug("Got method result: %s", type(method_result).__name__)

        # Derive team labels for the Tuple branch.
        teams_in_data: list = []
        if hasattr(app_instance, "data_metadata") and app_instance.data_metadata:
            teams_in_data = app_instance.data_metadata.get("teams", [])
        if len(teams_in_data) < 2:
            teams_in_data = ["Home", "Away"]

        # ------------------------------------------------------------------ #
        # Project method result to DataFrame.
        # DyadicProperty raises NotImplementedError; unknown types raise ValueError.
        # ------------------------------------------------------------------ #
        try:
            df = _property_to_dataframe(
                method_result,
                slots,
                name=method_name,
                team_name=team_name,
                teams_in_data=teams_in_data,
                selected_players=selected_players,
                slots_resolver=slots_resolver,
                resolve_slot=resolve_slot,
                fit_params=fit_params,
            )
        except NotImplementedError as nie:
            # DyadicProperty: surface as a soft failure without crashing.
            return False, str(nie)
        except ValueError as ve:
            return False, str(ve)

        if df.empty:
            return False, (
                f"No data exported — possibly no matching player slots "
                f"for selected_players={selected_players}"
            )

        # Write to CSV in the requested output directory.
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, f"{filename}.csv")
        df.to_csv(filepath, index=False)
        logger.info(
            "Exported %s.%s to %s (%d cols)", model_type, method_name, filepath, len(df.columns)
        )
        return True, filepath

    except Exception as e:  # noqa: BLE001 - export can fail for many reasons; surface to caller
        logger.exception("Error in export_model_metric_directly: %s", e)
        return False, str(e)
