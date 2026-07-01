"""Single source of truth for all floodlight model descriptors exposed in the GUI.

Each entry in ``MODEL_REGISTRY`` fully describes one model class: its import
path, UI category, fit/init parameters, required XY inputs, and queryable
outputs. Tabs read these descriptors to generate their UI; no hardcoded model
lists exist in the tab layer. Adding a new descriptor surfaces it in the UI
automatically.

Descriptor schema (validated by ``registry/_validators._validate_model_descriptor``):

Required keys:
    class_path (str): dotted import path to the floodlight model class.
    category (str): grouping label used by the UI tab bar.
    display_name (str): human-readable name shown in the picker.
    description (str): one-line summary shown beneath the picker.
    inputs (dict): XY input slots, each with ``type`` and ``required``.
    init_params (dict): constructor kwargs, each with ``type`` and ``default``.
    fit_params (dict): fit() kwargs, each with ``type`` and ``default``.
    outputs (dict): named result accessors (method, returns, label).

Optional keys:
    fit_xy_arity (int): number of XY positional args for fit(); absent means 1.
    overlay_adapter (str or None): key into OVERLAY_ADAPTER_REGISTRY; None means
        no pitch overlay is available for this model.
    fit_param_coerce (callable): kwarg-shape adapter called before dispatch; see
        ``_coerce_convex_hull_exclude_xids`` for the contract.
    tooltip (str): per-param hover hint; falls back to the upstream docstring's
        first line, then "No description available".

ValueError format on shape violation:
    f"MODEL_REGISTRY descriptor for '{key}': {reason}"

DPG-free: this module never imports dearpygui. It must import cleanly in any
context, including tests that import the registry without a display.
"""

from __future__ import annotations

from floodlight_gui.core.event_bus import Events, bus

# ---------------------------------------------------------------------------
# fit_param_coerce hook for convex_hull.
# ConvexHullModel.fit expects list[list[int]] for the multi-XY EAP case.
# The single-XY UI path collects a flat list[int], so this adapter wraps it
# into [list[int]]. Empty list is normalized to None (meaning "no exclusion").
# ---------------------------------------------------------------------------


def _coerce_convex_hull_exclude_xids(fit_kw: dict) -> dict:
    """Adapt the GUI's flat list[int] into the shape ConvexHullModel.fit expects.

    Mutates and returns ``fit_kw``. An empty list is normalized to None.

    Parameters
    ----------
    fit_kw : dict
        Collected fit kwargs (modified in place).

    Returns
    -------
    dict
        The same dict with ``exclude_xIDs`` coerced to ``[list[int]]`` or None.
    """
    val = fit_kw.get("exclude_xIDs")
    if val in (None, []):
        fit_kw["exclude_xIDs"] = None
    else:
        fit_kw["exclude_xIDs"] = [val]
    return fit_kw


MODEL_REGISTRY = {
    # ------------------------------------------------------------------ #
    # Kinematics
    # ------------------------------------------------------------------ #
    "velocity": {
        "class_path": "floodlight.models.kinematics.VelocityModel",
        "category": "kinematics",
        "display_name": "Velocity",
        # Only the `velocity` output is exposed: upstream VelocityModel exposes
        # only that method; a stale "cumulative displacement" copy was removed.
        "description": "Frame-wise velocity for each player",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "init_params": {},
        "fit_params": {
            "difference": {
                "type": "enum",
                "options": ["central", "backward"],
                "default": "central",
            },
            "axis": {
                "type": "enum",
                "options": [None, "x", "y"],
                "default": None,
                "description": "Restrict computation to a single spatial axis",
            },
        },
        "outputs": {
            "velocity": {
                "method": "velocity",
                "returns": "PlayerProperty",
                "label": "Velocity",
            },
        },
        # overlay_adapter: None means no pitch overlay is available for this model.
        "overlay_adapter": None,
    },
    "acceleration": {
        "class_path": "floodlight.models.kinematics.AccelerationModel",
        "category": "kinematics",
        "display_name": "Acceleration",
        "description": "Frame-wise acceleration for each player",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "init_params": {},
        "fit_params": {
            "difference": {
                "type": "enum",
                "options": ["central", "backward"],
                "default": "central",
            },
            "axis": {
                "type": "enum",
                "options": [None, "x", "y"],
                "default": None,
                "description": "Restrict computation to a single spatial axis",
            },
        },
        "outputs": {
            "acceleration": {
                "method": "acceleration",
                "returns": "PlayerProperty",
                "label": "Acceleration",
            },
        },
        # overlay_adapter: None means no pitch overlay is available for this model.
        "overlay_adapter": None,
    },
    "distance": {
        "class_path": "floodlight.models.kinematics.DistanceModel",
        "category": "kinematics",
        "display_name": "Distance",
        "description": "Euclidean distances covered by each player",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "init_params": {},
        "fit_params": {
            "difference": {
                "type": "enum",
                "options": ["central", "backward"],
                "default": "central",
            },
            "axis": {
                "type": "enum",
                "options": [None, "x", "y"],
                "default": None,
                "description": "Restrict computation to a single spatial axis",
            },
        },
        "outputs": {
            "distance_covered": {
                "method": "distance_covered",
                "returns": "PlayerProperty",
                "label": "Distance covered",
            },
            "cumulative_distance_covered": {
                "method": "cumulative_distance_covered",
                "returns": "PlayerProperty",
                "label": "Cumulative distance covered",
            },
        },
        # overlay_adapter: None means no pitch overlay is available for this model.
        "overlay_adapter": None,
    },
    # ------------------------------------------------------------------ #
    # Geometry
    # ------------------------------------------------------------------ #
    "centroid": {
        "class_path": "floodlight.models.geometry.CentroidModel",
        "category": "geometry",
        "display_name": "Centroid",
        "description": "Team centroid position, player-to-centroid distance, and stretch index",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "init_params": {},
        "fit_params": {
            "exclude_xIDs": {
                "type": "list[int]",
                "default": None,
                "description": "Column indices (xIDs) to exclude, e.g. goalkeeper",
            },
        },
        "outputs": {
            "centroid": {
                "method": "centroid",
                "returns": "XY",
                "label": "Centroid position",
            },
            "centroid_distance": {
                "method": "centroid_distance",
                "returns": "PlayerProperty",
                "label": "Distance to centroid",
            },
            "stretch_index": {
                "method": "stretch_index",
                "returns": "TeamProperty",
                "label": "Stretch index",
            },
        },
        # overlay_adapter: CentroidModel has no .plot in floodlight 1.2.0; kept None.
        "overlay_adapter": None,
    },
    "nearest_mate": {
        "class_path": "floodlight.models.geometry.NearestMateModel",
        "category": "geometry",
        "display_name": "Nearest Mate",
        "description": "Distance to nearest teammate for each player",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "init_params": {},
        "fit_params": {},
        "outputs": {
            "distance_to_nearest_mate": {
                "method": "distance_to_nearest_mate",
                "returns": "PlayerProperty",
                "label": "Distance to nearest mate",
            },
            "team_spread": {
                "method": "team_spread",
                "returns": "TeamProperty",
                "label": "Team spread",
            },
        },
        # overlay_adapter: None means no pitch overlay is available for this model.
        "overlay_adapter": None,
    },
    "nearest_opponent": {
        "class_path": "floodlight.models.geometry.NearestOpponentModel",
        "category": "geometry",
        "display_name": "Nearest Opponent",
        "description": "Distance to nearest opponent for each player",
        # fit_xy_arity=2 signals that NearestOpponentModel.fit(xy1, xy2)
        # takes both teams. Consumer tabs read this field and call
        # period_team_selector(team_count=2) to render Team A / Team B pickers.
        "fit_xy_arity": 2,
        "inputs": {
            "xy1": {
                "type": "XY",
                "required": True,
            },
            "xy2": {
                "type": "XY",
                "required": True,
            },
        },
        "init_params": {},
        "fit_params": {},
        "outputs": {
            "distance_to_nearest_opponent": {
                "method": "distance_to_nearest_opponent",
                "returns": "Tuple[PlayerProperty, PlayerProperty]",
                "label": "Distance to nearest opponent",
            },
        },
        # overlay_adapter: None means no pitch overlay is available for this model.
        "overlay_adapter": None,
    },
    "convex_hull": {
        "class_path": "floodlight.models.geometry.ConvexHullModel",
        "category": "geometry",
        "display_name": "Convex Hull",
        "description": "Convex hull area of a team over time",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "init_params": {},
        "fit_params": {
            "exclude_xIDs": {
                "type": "list[int]",
                "default": None,
                "description": "Column indices (xIDs) to exclude (e.g. goalkeeper). None = include all.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
        },
        "outputs": {
            "convex_hull_area": {
                "method": "convex_hull_area",
                "returns": "TeamProperty",
                "label": "Convex hull area",
            },
        },
        # ConvexHullModel has .plot() in floodlight 1.2.0; HullAdapter wires it.
        "overlay_adapter": "hull",
        # fit_param_coerce: adapts the GUI's flat list[int] to the [list[int]]
        # shape ConvexHullModel.fit requires for the multi-XY EAP signature.
        "fit_param_coerce": _coerce_convex_hull_exclude_xids,
    },
    # ------------------------------------------------------------------ #
    # Kinetics
    # ------------------------------------------------------------------ #
    "metabolic_power": {
        "class_path": "floodlight.models.kinetics.MetabolicPowerModel",
        "category": "kinetics",
        "display_name": "Metabolic Power",
        "description": "Metabolic power, equivalent distance, and cumulative variants",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        # MetabolicPowerModel.__init__(self) takes no kwargs. init_params
        # stays empty by design. The regression guard in
        # tests/test_metabolic_power_widget_coverage.py asserts that
        # inspect.signature(MetabolicPowerModel.__init__).parameters - {'self'}
        # remains the empty set; if upstream ever grows an __init__ kwarg the
        # test fails and this dict must be populated.
        "init_params": {},
        "fit_params": {
            "difference": {
                "type": "enum",
                # MetabolicPowerModel.fit documents {'central', 'forward'}, not
                # {'central', 'backward'} used by the kinematics models above.
                # Honor upstream verbatim (thin-frontend principle).
                "options": ["central", "forward"],
                "default": "central",
            },
            "axis": {
                "type": "enum",
                "options": [None, "x", "y"],
                "default": None,
                "description": "Restrict computation to a single spatial axis",
            },
            "eccr": {
                "type": "float",
                "default": 3.6,
                "min": 0.1,
                "max": 100.0,
                # This description is informational for the min/max clamp rationale
                # only; the widget tooltip resolves from the upstream docstring.
                "description": "Energy cost of constant running (J/(kg.m))",
            },
        },
        "outputs": {
            "metabolic_power": {
                "method": "metabolic_power",
                "returns": "PlayerProperty",
                "label": "Metabolic power",
            },
            "cumulative_metabolic_power": {
                "method": "cumulative_metabolic_power",
                "returns": "PlayerProperty",
                "label": "Cumulative metabolic power",
            },
            "equivalent_distance": {
                "method": "equivalent_distance",
                "returns": "PlayerProperty",
                "label": "Equivalent distance",
            },
            "cumulative_equivalent_distance": {
                "method": "cumulative_equivalent_distance",
                "returns": "PlayerProperty",
                "label": "Cumulative equivalent distance",
            },
        },
        # overlay_adapter: None means no pitch overlay is available for this model.
        "overlay_adapter": None,
    },
    # ------------------------------------------------------------------ #
    # Space
    # ------------------------------------------------------------------ #
    "discrete_voronoi": {
        "class_path": "floodlight.models.space.DiscreteVoronoiModel",
        "category": "space",
        "display_name": "Discrete Voronoi",
        "description": "Discretized Voronoi tessellation for space control analysis",
        # fit_xy_arity=2 signals that DiscreteVoronoiModel.fit(xy1, xy2)
        # takes both teams. Consumer tabs read this field and call
        # period_team_selector(team_count=2) to render Team A / Team B pickers.
        "fit_xy_arity": 2,
        "inputs": {
            "xy_home": {
                "type": "XY",
                "required": True,
            },
            "xy_away": {
                "type": "XY",
                "required": True,
            },
        },
        "init_params": {
            # Tooltips resolve from DiscreteVoronoiModel's class docstring
            # (not __init__) because floodlight documents the constructor on
            # the class; __init__ carries only object.__init__ boilerplate.
            "pitch": {
                "type": "Pitch",
                "required": True,
            },
            "mesh": {
                "type": "enum",
                "options": ["square", "hexagonal"],
                "default": "square",
            },
            "xpoints": {
                "type": "int",
                "default": 100,
                "min": 10,
                "max": 1000,
                "description": (
                    "Mesh density along the pitch's long axis. Higher values give "
                    "sharper Voronoi cell boundaries at roughly linear per-frame cost "
                    "(xpoints=200 ~ 4x texture upload bytes vs default). Default 100 "
                    "matches floodlight upstream. Y-axis points are auto-inferred from "
                    "pitch shape upstream (no ypoints param). A higher default is "
                    "avoided because the added per-frame cost can drop playback below "
                    "smooth framerates; raise this slider explicitly for sharper cells "
                    "and accept the tradeoff."
                ),
            },
            # motion_model added for floodlight 1.2 motion-based space-control:
            "motion_model": {
                "type": "enum",
                "options": ["euclidean", "taki_hasegawa", "fujimura_sugihara"],
                "default": "euclidean",
                "description": (
                    "Space-control assignment rule. 'euclidean' (default, fast) = nearest by distance. "  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
                    "'taki_hasegawa' = arrival-time under max acceleration constraint (slow on dense meshes). "  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
                    "'fujimura_sugihara' = exponential-decay velocity model (slow on dense meshes)."
                ),
            },
            "max_acceleration": {
                "type": "float",
                "default": 4.2,
                "min": 1.0,
                "max": 10.0,
                "description": "Only used by 'taki_hasegawa'. Default 4.2 per Brefeld et al. (2019).",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
            "vmax": {
                "type": "float",
                "default": 7.8,
                "min": 1.0,
                "max": 15.0,
                "description": "Only used by 'fujimura_sugihara'. Default 7.8 per Fujimura & Sugihara (2005).",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
            "alpha": {
                "type": "float",
                "default": 1.3,
                "min": 0.1,
                "max": 10.0,
                "description": "Only used by 'fujimura_sugihara'. Default 1.3 per Fujimura & Sugihara (2005).",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
        },
        "fit_params": {},
        "outputs": {
            "player_controls": {
                "method": "player_controls",
                "returns": "Tuple[PlayerProperty, PlayerProperty]",
                "label": "Player space control",
                "description": "Per-player control percentages for each team",
            },
            "team_controls": {
                "method": "team_controls",
                "returns": "Tuple[TeamProperty, TeamProperty]",
                "label": "Team space control",
                "description": "Aggregate control percentages for each team",
            },
        },
        # DiscreteVoronoiModel has .plot() in floodlight 1.2.0; VoronoiAdapter wires it.
        "overlay_adapter": "voronoi",
    },
}


def register_model(key: str, descriptor: dict) -> None:
    """Validate a descriptor, insert it into MODEL_REGISTRY, and emit MODEL_REGISTRY_CHANGED.

    Ordering contract (mutate-then-emit): MODEL_REGISTRY is updated before the
    event is emitted so subscribers reading MODEL_REGISTRY inside their callback
    see the new entry.

    1. Validate descriptor shape via ``_validate_model_descriptor`` (raises on violation).
    2. Reject duplicate keys (raises on collision).
    3. Insert ``MODEL_REGISTRY[key] = descriptor``.
    4. Emit ``Events.MODEL_REGISTRY_CHANGED`` with ``key`` and ``descriptor`` payload.

    Parameters
    ----------
    key : str
        Registry key for the new model (must be unique).
    descriptor : dict
        Model descriptor satisfying the schema documented in this module's header.

    Raises
    ------
    ValueError
        On shape violation, with the locked format:
        ``f"MODEL_REGISTRY descriptor for '{key}': {reason}"``.
    ValueError
        On duplicate key, with the locked format:
        ``f"key '{key}' already registered in MODEL_REGISTRY; unregister first"``.
    """
    from floodlight_gui.registry._validators import _validate_model_descriptor

    _validate_model_descriptor(key, descriptor)

    if key in MODEL_REGISTRY:
        raise ValueError(f"key '{key}' already registered in MODEL_REGISTRY; unregister first")

    MODEL_REGISTRY[key] = descriptor
    bus.emit(Events.MODEL_REGISTRY_CHANGED, key=key, descriptor=descriptor)
