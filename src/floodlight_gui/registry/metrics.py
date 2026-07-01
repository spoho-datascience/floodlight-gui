"""Single source of truth for all metric descriptors exposed by the GUI.

Each entry in ``METRICS_REGISTRY`` maps a registry key to a descriptor dict that
drives UI generation in the Metrics tab. The executor lives in
``tabs/metrics/execute.py``; this module is DPG-free at module scope.

Descriptor schema (validated by ``registry/_validators._validate_metric_descriptor``):

    Required keys:
        function_path   -- dotted import path of the upstream floodlight function
        category        -- top-level grouping key (matches upstream module name)
        display_name    -- human-readable label for the UI
        description     -- short description shown in the metrics picker
        inputs          -- dict of named data inputs the function requires
        params          -- dict of tuneable call-kwargs with type/default metadata
        returns         -- return type tag ("float", "DataFrame", etc.)

    Input ``type`` values:
        "PlayerProperty|TeamProperty"  -- a property output from a fitted model
        "XY"                           -- raw tracking data from loaded position data
        "ndarray"                      -- a numpy array (e.g. a formation template)
        "sig"                          -- a 1-D numpy array from a property column

    Params schema notes:
        A param with ``required: True`` and no ``default`` is valid: it signals
        that the user must supply a value at the UI boundary before the function
        can be called. The validator does not require a default on required params.

        Each param/input dict may carry an optional ``tooltip`` key (str, default
        None) for a short hover hint in the UI. When absent the UI falls back to
        the upstream callable's first docstring line, then "No description
        available".

    Error format on shape violation (locked):
        ``f"METRICS_REGISTRY descriptor for '{key}': {reason}"``
"""

from __future__ import annotations

from floodlight_gui.core.event_bus import Events, bus

METRICS_REGISTRY = {
    # ------------------------------------------------------------------ #
    # Entropy (floodlight.metrics.entropy)
    # ------------------------------------------------------------------ #
    "approx_entropy": {
        "function_path": "floodlight.metrics.entropy.approx_entropy",
        "category": "entropy",
        "display_name": "Approximate Entropy",
        "description": (
            "Approximate entropy (ApEn) measuring time series complexity. "
            "Operates on a single 1-D signal (one player column from a "
            "PlayerProperty, or a TeamProperty)."
        ),
        "inputs": {
            "sig": {
                "type": "sig",
                "required": True,
                "description": ("1-D time series -- select a model output and a player"),
            },
        },
        "params": {
            "m": {
                "type": "int",
                "default": 2,
                "min": 1,
                "max": 10,
                "description": "Length of compared runs of data",
            },
            "r": {
                "type": "float",
                "default": 0.5,
                "min": 0.01,
                "max": 5.0,
                "description": "Tolerance for accepting matches",
            },
        },
        "returns": "float",
    },
    # ------------------------------------------------------------------ #
    # Zone Aggregation (floodlight.metrics.zone_aggregation)
    # ------------------------------------------------------------------ #
    "aggregate_property_by_zones": {
        "function_path": "floodlight.metrics.zone_aggregation.aggregate_property_by_zones",
        "category": "zone_aggregation",
        "display_name": "Zone Aggregation",
        "description": (
            "Aggregate a property by threshold-based zones. Requires two "
            "properties: one to aggregate (e.g. time spent) and one for "
            "binning (e.g. velocity values that define zone membership)."
        ),
        "inputs": {
            "property_to_aggregate": {
                "type": "PlayerProperty|TeamProperty",
                "required": True,
                "description": "Values to sum/count/mean within each zone",
            },
            "binning_property": {
                "type": "PlayerProperty|TeamProperty",
                "required": True,
                "description": (
                    "Property whose values define zone membership (e.g. velocity for speed zones)"
                ),
            },
        },
        "params": {
            "zones": {
                # Type "list[tuple[float, float]]" matches the upstream
                # signature ``zones: list[tuple[Union[int, float, np.number], ...]]``.
                # The metrics tab parses this via _collect_param_value's
                # "list[tuple[float, float]]" branch; shorthand "(lo,hi),(lo,hi),..."
                # or "[(lo,hi),(lo,hi),...]" parses to the upstream tuple list.
                "type": "list[tuple[float, float]]",
                "required": True,
                "example": "(0.0, 2.0), (2.0, 4.0)",
                "description": (
                    "Zone boundaries as (lo, hi) tuples. Format: "
                    "'(lo1, hi1), (lo2, hi2), ...' -- example: "
                    "'(0.0, 2.0), (2.0, 4.0)'."
                ),
            },
            "aggregation": {
                "type": "enum",
                "options": ["sum", "count", "mean", "min", "max"],
                "default": "sum",
            },
            "zone_names": {
                "type": "list[str]",
                "default": None,
                "example": "slow, medium, fast",
                "description": (
                    "Optional comma-separated zone labels. Leave blank to auto-generate "
                    "names from boundaries (e.g. '0 to 2', '2 to 4')."
                ),
            },
        },
        "returns": "DataFrame",
    },
    # ------------------------------------------------------------------ #
    # Formation Similarity (floodlight.metrics.trajectory_clustering)
    # ------------------------------------------------------------------ #
    "formation_similarity": {
        "function_path": "floodlight.metrics.trajectory_clustering.formation_similarity",
        "category": "trajectory_clustering",
        "display_name": "Formation Similarity",
        "description": (
            "Formation similarity (FSIM) via template matching. "
            "Compares tracking data against an idealized formation template."
        ),
        "inputs": {
            "xy": {
                "type": "XY",
                "required": True,
            },
        },
        "params": {
            "template": {
                # 'template' is in 'params', not 'inputs', because the metrics tab
                # renders 'inputs' as dropdowns sourced from loaded data. A formation
                # template has no upstream data source and must be entered as free text,
                # so it belongs alongside other tuneable kwargs.
                "type": "ndarray",
                "required": True,
                "description": (
                    "Idealized formation positions as Mx2 array. "
                    "Enter as bracketed (x, y) tuples separated by commas, "
                    "e.g. '(0, 0), (10, 5), (10, -5), (20, 0)'."
                ),
                "example": "(0, 0), (10, 5), (10, -5), (20, 0)",
            },
            "exclude_xIDs": {
                "type": "list[int]",
                "default": None,
                "description": "Player indices to exclude (e.g. goalkeeper)",
            },
            "role_assignment": {
                "type": "bool",
                "default": True,
                "description": "Use Hungarian algorithm for optimal role assignment",
            },
            "n_iter": {
                "type": "int",
                "default": 1,
                "min": 1,
                "max": 10,
                "description": "Number of role assignment iterations",
            },
            "delta": {
                # ``1/3`` matches the upstream default exactly
                # (``delta: float = 0.3333333333333333``) without a
                # hardcoded truncation that could drift on an upstream bump.
                "type": "float",
                "default": 1 / 3,
                "min": 0.01,
                "max": 1.0,
                "description": "Similarity decay parameter",
            },
        },
        "returns": "float",
    },
}


def register_metric(key: str, descriptor: dict) -> None:
    """Validate *descriptor* and insert it into ``METRICS_REGISTRY`` at *key*.

    Execution order is locked: validate, check for duplicate, mutate, then emit.
    Subscribers that read ``METRICS_REGISTRY`` inside their callback are guaranteed
    to see the new entry because the mutation happens before the event fires.

    Parameters
    ----------
    key : str
        Registry key for the new metric (must not already be present).
    descriptor : dict
        Metric descriptor conforming to the schema documented in this module's
        header. Validated by ``registry._validators._validate_metric_descriptor``.

    Raises
    ------
    ValueError
        If the descriptor fails shape validation. Error format (locked):
        ``f"METRICS_REGISTRY descriptor for '{key}': {reason}"``.
    ValueError
        If *key* is already registered. Error format (locked):
        ``f"key '{key}' already registered in METRICS_REGISTRY; unregister first"``.

    Notes
    -----
    Emits ``Events.METRICS_REGISTRY_CHANGED`` with ``key`` and ``descriptor``
    as payload after the registry is updated.
    """
    from floodlight_gui.registry._validators import _validate_metric_descriptor

    _validate_metric_descriptor(key, descriptor)

    if key in METRICS_REGISTRY:
        raise ValueError(f"key '{key}' already registered in METRICS_REGISTRY; unregister first")

    METRICS_REGISTRY[key] = descriptor
    bus.emit(Events.METRICS_REGISTRY_CHANGED, key=key, descriptor=descriptor)
