"""Dispatch fitted-model overlays to live render adapters in the visualization tab.

Reads MODEL_REGISTRY and OVERLAY_ADAPTER_REGISTRY to resolve which adapter class
handles each fitted model, then constructs or rebinds DPG adapter widgets. Team
colors are resolved by non-ball-team index (not by name) so arbitrary team names
map correctly.

DPG-aware: imports ``dearpygui`` at module scope (tabs/ layer).

``_build_overlay_specs_for_export`` reads ``viz_state`` through a lazy facade
import so that test monkeypatching of the facade module's ``viz_state`` attribute
is intercepted at call time. All other functions read state via
``state.viz_state`` directly.
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus  # noqa: F401 -- re-exported via __all__
from floodlight_gui.registry.models import MODEL_REGISTRY
from floodlight_gui.rendering.adapters import OVERLAY_ADAPTER_REGISTRY
from floodlight_gui.tabs.visualization import colors as _colors
from floodlight_gui.tabs.visualization import state

logger = logging.getLogger(__name__)

__all__ = [
    "_on_overlay_toggle",
    "_on_model_fitted",
    "_on_voronoi_alpha",
    "_bind_adapter_from_fitted_models",
    "_build_overlay_specs_for_export",
    "_update_overlay_status",
    "_color_for_token",
    "_is_ball_team",
    "_resolve_team_color_by_index",
    "_BAND_COLORS",
    "_TEAM_COLORS",
    "_COLOR_CYCLE",
]

# ---------------------------------------------------------------------------
# Color constants -- single source of truth is colors.py. Aliased here so
# importers (render_loop, timeline, export_writers) and the test suite can
# import from this module without pulling in colors.py directly.
# ---------------------------------------------------------------------------
_BAND_COLORS = _colors.BAND_COLORS
_TEAM_COLORS = _colors.TEAM_COLORS
_DEFAULT_OVERLAY_ALPHA = _colors.DEFAULT_OVERLAY_ALPHA
_COLOR_CYCLE = _colors.COLOR_CYCLE
_BALL_TOKENS = _colors.BALL_TOKENS

# ---------------------------------------------------------------------------
# Drawlist / layer tag constants needed by overlay binding.
# ---------------------------------------------------------------------------
_OVERLAY_LAYER_TAG = "__overlay_layer"
_DRAWLIST_TAG = "viz_drawlist"


# ---------------------------------------------------------------------------
# Team-color helpers -- aliased from colors.py so callers can import from
# this module and automatically honor the name-keyed resolution and override
# hook defined there.
# ---------------------------------------------------------------------------
_color_for_token = _colors.color_for_token
_is_ball_team = _colors.is_ball_team
_resolve_team_color_by_index = _colors.resolve_team_color_by_index


# ---------------------------------------------------------------------------
# Export spec builder
# ---------------------------------------------------------------------------


def _build_overlay_specs_for_export() -> list[dict]:
    """Snapshot live adapter state into spec dicts for the export pipeline.

    Reads ``viz_state["active_adapters"]`` and ``model_tab.fitted_models``
    at call time (no EventBus subscription -- this is a one-shot
    user-initiated operation, not a reactive handler).

    Visibility filter: adapters with visibility OFF are excluded. The
    attribute name differs between adapters (VoronoiAdapter uses
    ``_visible``; HullAdapter uses ``_hull_visible``); the cascading
    ``getattr`` below handles both. Adapters that omit both attributes
    default to visible for forward compatibility with future adapter types.

    When an active adapter has no matching fitted model the slot is
    silently skipped and the export proceeds with the existing pitch and
    players path.

    Returns
    -------
    list[dict]
        One spec dict per visible, model-backed adapter. Keys: ``key``,
        ``model``, ``alpha``, ``team1_color``, ``team2_color``, ``n_team1``.

    Notes
    -----
    Reads ``viz_state`` through the facade module so that test
    monkeypatching of the facade's ``viz_state`` attribute is intercepted
    at call time rather than at import time.
    """
    # Defer model.state import to handler time to avoid circular import at
    # module load. Same pattern as `_bind_adapter_from_fitted_models`.
    from floodlight_gui.tabs.model import state as _model_state
    from floodlight_gui.tabs.visualization import state as _state

    specs: list[dict] = []
    active = _state.viz_state.get("active_adapters") or {}
    fitted = _model_state.fitted_models or {}
    selected_half = _state.viz_state.get("selected_half")

    for adapter_key, adapter in active.items():
        # Visibility cascade: VoronoiAdapter._visible vs HullAdapter._hull_visible.
        visible = getattr(adapter, "_visible", getattr(adapter, "_hull_visible", True))
        if not visible:
            continue

        # Find the first fitted model whose descriptor resolves to this adapter_key.
        model = None
        for (half, _team, model_key), (m, _params) in fitted.items():
            desc = MODEL_REGISTRY.get(model_key) or {}
            if desc.get("overlay_adapter") != adapter_key:
                continue
            if half and selected_half and half != selected_half:
                continue
            model = m
            break

        if model is None:
            # No fitted model for this adapter: skip silently.
            continue

        # Alpha cascade: VoronoiAdapter exposes ``_alpha``; HullAdapter does not
        # (uses a module-scope constant). The fallback keeps Hull working without
        # requiring a UI alpha control on that adapter.
        alpha = float(getattr(adapter, "_alpha", _DEFAULT_OVERLAY_ALPHA))

        # n_team1 is used by Voronoi for cell classification; Hull ignores it.
        n_team1 = int(getattr(adapter, "_n_team1", 0))

        specs.append(
            {
                "key": adapter_key,
                "model": model,
                "alpha": alpha,
                "team1_color": _resolve_team_color_by_index(
                    0, _state.viz_state.get("cached_team_names")
                ),
                "team2_color": _resolve_team_color_by_index(
                    1, _state.viz_state.get("cached_team_names")
                ),
                "n_team1": n_team1,
            }
        )

    return specs


# ---------------------------------------------------------------------------
# Overlay dispatch callbacks
# ---------------------------------------------------------------------------


def _on_overlay_toggle(sender, app_data):
    """Handle an overlay visibility checkbox toggle (DPG callback).

    On check: look up the latest fits in ``model_tab.fitted_models`` for
    ``adapter_key`` and bind the adapter via
    ``_bind_adapter_from_fitted_models``. On uncheck: hide the adapter via
    ``set_visible(False)`` (state is preserved so re-checking is fast).

    The checkbox is the canonical adapter-binding signal: adapter binding
    is intentionally deferred until the user opts in by ticking the box,
    so fits performed before viz init are not silently dropped.
    """
    adapter_key = dpg.get_item_user_data(sender)
    if app_data:
        _bind_adapter_from_fitted_models(adapter_key)
    else:
        adapter = state.viz_state.get("active_adapters", {}).get(adapter_key)
        if adapter is not None:
            adapter.set_visible(False)
    from floodlight_gui.tabs.visualization import render_loop as _rl

    _rl._render_current_frame()


def _bind_adapter_from_fitted_models(adapter_key: str) -> None:
    """Bind ``adapter_key`` from the current ``model_tab.fitted_models`` entries.

    Pulls models from the cache rather than a MODEL_FITTED payload so
    checkbox-toggle works regardless of fit order.

    For ``hull``: binds every ``(half, team, "convex_hull")`` entry whose
    half matches ``state.viz_state["selected_half"]``. One HullAdapter holds
    all teams via ``add_team``.

    For ``voronoi``: binds the single ``(selected_half, "BothTeams",
    "discrete_voronoi")`` entry. VoronoiAdapter.init calls ``clear()``
    first so re-init is idempotent.

    Team colors are resolved by non-ball-team index (not by name) so
    arbitrary team names (club names, "teamA/teamB", etc.) map to the
    correct palette slot.

    Parameters
    ----------
    adapter_key : str
        Key into ``OVERLAY_ADAPTER_REGISTRY`` (e.g. "hull", "voronoi").

    Notes
    -----
    Writes into ``state.viz_state["active_adapters"]``, creating the entry
    if absent. Calls ``adapter.set_visible(True)`` and
    ``_update_overlay_status()`` on success.
    """
    mapper = state.viz_state.get("mapper")
    if mapper is None:
        logger.warning(
            "Cannot bind %r adapter: viz tab not initialised "
            "(mapper is None). Auto-init runs on DATA_LOADED -- "
            "load a match first.",
            adapter_key,
        )
        # Auto-uncheck so the user sees the action didn't take effect.
        checkbox_tag = f"viz_overlay_{adapter_key}"
        if dpg.does_item_exist(checkbox_tag):
            with contextlib.suppress(SystemError):
                dpg.set_value(checkbox_tag, False)
        return

    from floodlight_gui.tabs.model import state as _model_state_bind

    fitted_models = _model_state_bind.fitted_models

    selected_half = state.viz_state.get("selected_half")

    # Collect all fitted_models entries whose model_key resolves to adapter_key.
    matches = []
    for (half, team, model_key), (model, _params) in fitted_models.items():
        desc = MODEL_REGISTRY.get(model_key, {})
        if desc.get("overlay_adapter") != adapter_key:
            continue
        if half and selected_half and half != selected_half:
            continue
        matches.append((half, team, model_key, model))

    if not matches:
        logger.info(
            "No fitted %r models for half=%r -- nothing to bind",
            adapter_key,
            selected_half,
        )
        return

    adapter_class = OVERLAY_ADAPTER_REGISTRY[adapter_key]
    active = state.viz_state.setdefault("active_adapters", {})
    adapter = active.get(adapter_key)
    if adapter is None:
        adapter = adapter_class(_DRAWLIST_TAG, mapper)
        active[adapter_key] = adapter

    cached_names = state.viz_state.get("cached_team_names")
    non_ball = [t for t in (cached_names or []) if not _is_ball_team(t)]
    for half, team, _model_key, model in matches:
        # Resolve colors by non-ball-team index so arbitrary team names land
        # on the correct palette slot. ``color`` is used by the Hull adapter
        # per team; ``team1_color``/``team2_color`` are used by Voronoi.
        team_idx = non_ball.index(team) if team in non_ball else 0
        payload = {
            "color": _resolve_team_color_by_index(team_idx, cached_names),
            "team1_color": _resolve_team_color_by_index(0, cached_names),
            "team2_color": _resolve_team_color_by_index(1, cached_names),
            "fit_half": half,
        }
        kwargs = adapter_class.build_init_kwargs(
            model=model,
            team_name=team,
            payload=payload,
            viz_state=state.viz_state,
        )
        # HullAdapter supports incremental add_team after first bind;
        # VoronoiAdapter does not. Dispatch by capability (hasattr), not by
        # adapter_key string, so the dispatcher stays generic over the registry.
        if hasattr(adapter, "add_team") and adapter._parent_layer is not None:
            adapter.add_team(team, model, kwargs["color"])
        else:
            adapter.init(parent_layer_tag=_OVERLAY_LAYER_TAG, **kwargs)

    # set_visible(True) ensures update_frame draws. The user's check implies "show".
    adapter.set_visible(True)
    _update_overlay_status()


def _on_voronoi_alpha(sender, app_data):
    """Handle a Voronoi opacity slider change (DPG callback).

    Persists the new alpha in ``state.viz_state`` so the value survives
    re-fits, then forwards it to the live adapter and triggers a redraw.
    """
    state.viz_state["voronoi_alpha_value"] = float(app_data)
    voronoi_adapter = state.viz_state.get("active_adapters", {}).get("voronoi")
    if voronoi_adapter is not None:
        voronoi_adapter.set_alpha(app_data)
        from floodlight_gui.tabs.visualization import render_loop as _rl

        _rl._render_current_frame()


def _update_overlay_status():
    """Refresh the overlay status text widget with the current active adapter keys."""
    active = state.viz_state.get("active_adapters", {})
    status = "No models fitted" if not active else ", ".join(sorted(active.keys()))
    if dpg.does_item_exist("viz_overlay_status"):
        with contextlib.suppress(SystemError):
            dpg.configure_item("viz_overlay_status", default_value=status)


def _on_model_fitted(**data):
    """Handle the MODEL_FITTED event: reveal the overlay checkbox for the fitted model.

    The fit itself does not bind the adapter. The user opts in explicitly
    by ticking the checkbox; ``_on_overlay_toggle`` then pulls the model
    from ``model_tab.fitted_models`` and binds via
    ``_bind_adapter_from_fitted_models``.

    If the checkbox is already ticked when a new fit arrives (for example,
    the user is iterating fits or adding a second team), the adapter is
    rebound immediately so the new model is reflected without requiring a
    manual toggle.

    Parameters
    ----------
    **data : dict
        EventBus payload. Must include ``model_key`` (str); all other
        keys are ignored.

    Notes
    -----
    Shows/hides DPG widgets: the overlay checkbox
    (``viz_overlay_{adapter_key}``), companion widgets declared on the
    adapter class via ``ui_widget_tags()``, and the
    ``viz_overlay_placeholder`` text.
    """
    model_key = data.get("model_key")
    if not model_key:
        return
    desc = MODEL_REGISTRY.get(model_key)
    if not desc:
        return
    adapter_key = desc.get("overlay_adapter")
    if not adapter_key:
        return  # model has no overlay (e.g. centroid, kinematics)

    if OVERLAY_ADAPTER_REGISTRY.get(adapter_key) is None:
        logger.warning(
            "MODEL_REGISTRY[%r]['overlay_adapter']=%r not in OVERLAY_ADAPTER_REGISTRY",
            model_key,
            adapter_key,
        )
        return

    # Reveal the checkbox and any adapter-declared companion widgets.
    checkbox_tag = f"viz_overlay_{adapter_key}"
    if dpg.does_item_exist(checkbox_tag):
        with contextlib.suppress(SystemError):
            dpg.configure_item(checkbox_tag, show=True)
    # Companion widgets (e.g. Voronoi alpha slider) are declared on the adapter
    # class via ``ui_widget_tags()``. Iterating them here keeps this handler
    # generic: adding a new adapter with its own widgets requires no change here.
    for tag in OVERLAY_ADAPTER_REGISTRY[adapter_key].ui_widget_tags():
        if dpg.does_item_exist(tag):
            with contextlib.suppress(SystemError):
                dpg.configure_item(tag, show=True)

    # Hide the placeholder once any checkbox is shown.
    if dpg.does_item_exist("viz_overlay_placeholder"):
        with contextlib.suppress(SystemError):
            dpg.configure_item("viz_overlay_placeholder", show=False)

    # If the checkbox is already ticked, rebind so the new fit is reflected immediately.
    if dpg.does_item_exist(checkbox_tag) and dpg.get_value(checkbox_tag):
        _bind_adapter_from_fitted_models(adapter_key)
        from floodlight_gui.tabs.visualization import render_loop as _rl

        _rl._render_current_frame()

    _update_overlay_status()
