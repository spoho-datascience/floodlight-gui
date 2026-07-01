"""Single source of truth for TRANSFORM_REGISTRY: descriptors for every floodlight
transform and XY-method the GUI exposes.

DPG-free: this module imports no dearpygui; it belongs to the backend layer and
must remain importable without a display context.

Executor: ``registry/dispatch.py`` (``apply_xy_op``) dispatches ``source: "xy.method"``
entries; ``engine/apply_transforms.py`` dispatches ``function_path`` entries.

Descriptor schema (validated by ``registry/_validators._validate_transform_descriptor``):
    Required keys:
        function_path, category, display_name, description, inputs, params, returns
    XY-method sentinel entries also carry:
        source="xy.method", method, in_place, build_args
    Optional per param/input:
        tooltip (str, default None) - short hover hint; UI falls back to the
        upstream callable's docstring first line if absent.
    Permissive: ``inputs.<k>.type == "ndarray"`` is accepted (used by
    ``min_max_normalize.positions``); the validator does not whitelist input-type
    strings because descriptors document what is there, not an idealized shape.
    ValueError format on shape violation (locked):
        f"TRANSFORM_REGISTRY descriptor for '{key}': {reason}"

``PARAM_LABEL_MAP`` and ``format_stack_param_value`` are also defined here and
imported by consumer tabs (transforms, model, metrics).
"""  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping

from __future__ import annotations

from typing import Any

from floodlight_gui.core.event_bus import Events, bus

TRANSFORM_REGISTRY = {
    # ------------------------------------------------------------------ #
    # Filters (floodlight.transforms.filter)
    # ------------------------------------------------------------------ #
    "butterworth_lowpass": {
        "function_path": "floodlight.transforms.filter.butterworth_lowpass",
        "category": "filter",
        "display_name": "Butterworth Low-Pass",
        "description": "Apply a Butterworth low-pass filter for signal smoothing",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "order": {
                "type": "int",
                "default": 3,
                "min": 1,
                "max": 10,
                "description": "Butterworth filter order (1-10).",
            },
            "Wn": {
                # floodlight applies this as Wn / (0.5 * framerate) internally; upstream default=1.
                "type": "float",
                "default": 1.0,
                "min": 0.01,
                "max": 100.0,
                "description": "Critical lowpass frequency in Hz.",
            },
            "remove_short_seqs": {
                "type": "bool",
                "default": False,
                "description": "Drop short runs of valid samples that cannot be filtered.",
            },
        },
        "returns": "XY",
    },
    "savgol_lowpass": {
        "function_path": "floodlight.transforms.filter.savgol_lowpass",
        "category": "filter",
        "display_name": "Savitzky-Golay Low-Pass",
        "description": "Apply a Savitzky-Golay low-pass filter for signal smoothing",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "window_length": {
                "type": "int",
                "default": 5,
                "min": 3,
                "max": 101,
                "description": "Filter window length (odd integer >= 3, must be > poly_order).",
            },
            "poly_order": {
                "type": "int",
                "default": 3,
                "min": 1,
                "max": 10,
                "description": "Polynomial order (< window_length).",
            },
            "remove_short_seqs": {
                "type": "bool",
                "default": False,
                "description": "Drop short runs of valid samples that cannot be filtered.",
            },
        },
        "returns": "XY",
    },
    "wiener": {
        "function_path": "floodlight.transforms.filter.wiener",
        "category": "filter",
        "display_name": "Wiener Filter",
        "description": "Wiener-filter smoothing (scipy.signal.wiener) applied per non-NaN sequence.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "window_size": {
                "type": "int",
                "default": 5,
                "min": 3,
                "max": 101,
                "description": "Local window for noise estimation (mysize in scipy.signal.wiener).",
            },
            "noise": {
                "type": "float",
                "default": None,
                "min": 0.0,
                "max": 1000.0,
                "description": "Leave blank to estimate locally (None upstream).",
            },
            "remove_short_seqs": {
                "type": "bool",
                "default": False,
                "description": "Drop sequences shorter than window_size; otherwise pass through unfiltered.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
        },
        "returns": "XY",
    },
    "fir_lowpass": {
        "function_path": "floodlight.transforms.filter.fir_lowpass",
        "category": "filter",
        "display_name": "FIR Low-Pass",
        "description": "FIR low-pass filter (scipy.signal.firwin + filtfilt). **kwargs forwarded to filtfilt are not exposed in the GUI.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "numtaps": {
                "type": "int",
                "default": 21,
                "min": 3,
                "max": 501,
                "description": "Filter length. Odd values recommended for Type I filter.",
            },
            "cutoff": {
                "type": "float",
                "default": 1.0,
                "min": 0.01,
                "max": 100.0,
                "description": "Cutoff frequency in Hz.",
            },
            "window": {
                "type": "enum",
                "options": ["hamming", "hann", "blackman", "bartlett", "boxcar"],
                "default": "hamming",
                "description": "scipy.signal window name.",
            },
            "remove_short_seqs": {
                "type": "bool",
                "default": False,
                "description": (
                    "Drop sequences shorter than 3*numtaps; otherwise pass through unfiltered."
                ),
            },
        },
        "returns": "XY",
    },
    "kalman": {
        "function_path": "floodlight.transforms.filter.kalman",
        "category": "filter",
        "display_name": "Kalman Filter",
        "description": "Forward Kalman filter with constant-velocity motion model. Requires xy.framerate to be set.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "process_noise": {
                "type": "float",
                "default": 1.0,
                "min": 0.01,
                "max": 100.0,
                "description": (
                    "Process noise intensity (m^2/s^4) -- larger = trust observation more."
                ),
            },
            "measurement_noise": {
                "type": "float",
                "default": 0.04,
                "min": 0.0001,
                "max": 100.0,
                "description": "Measurement noise variance (m^2) -- default 0.04 = 0.20 m RMSE.",
            },
        },
        "returns": "XY",
    },
    # ------------------------------------------------------------------ #
    # Interpolation (floodlight.transforms.interpolation)
    # ------------------------------------------------------------------ #
    "interpolate_linear": {
        "function_path": "floodlight.transforms.interpolation.interpolate_linear",
        "category": "interpolation",
        "display_name": "Linear Interpolation",
        "description": "Fill bounded NaN gaps via linear interpolation along the temporal axis. Leading/trailing NaNs are preserved.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "xIDs": {
                "type": "list[int]",
                "default": None,
                "description": "Column indices (xIDs) to interpolate. None = all players.",
            },
            "max_gap": {
                "type": "int",
                "default": None,
                "min": 1,
                "description": "Largest consecutive NaN gap to fill. None = unlimited.",
            },
        },
        "returns": "XY",
    },
    "interpolate_polynomial": {
        "function_path": "floodlight.transforms.interpolation.interpolate_polynomial",
        "category": "interpolation",
        "display_name": "Polynomial Interpolation",
        "description": "Fill bounded NaN gaps via polynomial fit (degree = `order`).",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "order": {
                "type": "int",
                "default": 3,
                "min": 1,
                "max": 7,
                "description": "Polynomial order (cubic = 3 is upstream default).",
            },
            "xIDs": {
                "type": "list[int]",
                "default": None,
                "description": "Column indices (xIDs) to interpolate. None = all players.",
            },
            "max_gap": {
                "type": "int",
                "default": None,
                "min": 1,
                "description": "Largest consecutive NaN gap to fill. None = unlimited.",
            },
        },
        "returns": "XY",
    },
    "interpolate_spline": {
        "function_path": "floodlight.transforms.interpolation.interpolate_spline",
        "category": "interpolation",
        "display_name": "Spline Interpolation",
        "description": "Fill bounded NaN gaps via cubic spline (degree = `k`).",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "k": {
                "type": "int",
                "default": 3,
                "min": 1,
                "max": 7,
                "description": "Spline degree (cubic = 3 is upstream default).",
            },
            "xIDs": {
                "type": "list[int]",
                "default": None,
                "description": "Column indices (xIDs) to interpolate. None = all players.",
            },
            "max_gap": {
                "type": "int",
                "default": None,
                "min": 1,
                "description": "Largest consecutive NaN gap to fill. None = unlimited.",
            },
        },
        "returns": "XY",
    },
    # ------------------------------------------------------------------ #
    # Temporal (floodlight.transforms.temporal)
    # ------------------------------------------------------------------ #
    "resample": {
        "function_path": "floodlight.transforms.temporal.resample",
        "category": "temporal",
        "display_name": "Resample",
        "description": "Resample XY to a new framerate. Upsample uses interp_method (or NaN fill); downsample uses nearest-source-index integer math (no anti-aliasing).",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "target_framerate": {
                "type": "int",
                "default": 25,
                "min": 1,
                "max": 120,
                "description": "Target framerate in Hz (positive integer).",
            },
            "interp_method": {
                "type": "enum",
                "options": ["none", "linear", "polynomial", "spline", "nearest"],
                "default": "none",
                "description": (
                    "Upsample interpolation method. 'none' leaves new rows as NaN."
                    " Ignored on downsample."
                ),
            },
            "order": {
                "type": "int",
                "default": 3,
                "min": 1,
                "max": 7,
                "description": "Polynomial order (only used when interp_method='polynomial').",
            },
            "k": {
                "type": "int",
                "default": 3,
                "min": 1,
                "max": 7,
                "description": "Spline degree (only used when interp_method='spline').",
            },
        },
        "returns": "XY",
    },
    # ------------------------------------------------------------------ #
    # Spatial (floodlight.transforms.spatial)
    # ------------------------------------------------------------------ #
    "subtract_centroid": {
        "function_path": "floodlight.transforms.spatial.subtract_centroid",
        "category": "spatial",
        "display_name": "Subtract Centroid",
        "description": "Center positions relative to the team centroid",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "exclude_xIDs": {
                "type": "list[int]",
                "default": None,
                "description": "Column indices to exclude from centroid computation",
            },
        },
        "returns": "XY",
    },
    # Upstream ``min_max_normalize`` operates on a single (N, 2) formation snapshot,
    # not a temporal XY stream. The input is named ``positions`` and typed ``ndarray``
    # so XY-stream consumers skip this entry naturally. Kept here so the
    # scan_floodlight.py alignment check stays clean.
    "min_max_normalize": {
        "function_path": "floodlight.transforms.spatial.min_max_normalize",
        "category": "spatial",
        "display_name": "Min-Max Normalize Formation",
        "description": (
            "Min-max normalize an (N, 2) formation snapshot to [0, 1] per axis. "
            "Operates on a single-frame ndarray, not an XY tracking stream."
        ),
        "inputs": {
            "positions": {
                "type": "ndarray",
                "required": True,
                "description": "Single-frame formation snapshot, not a tracking stream.",
            },
        },
        "params": {},
        "returns": "ndarray",
    },
    # ------------------------------------------------------------------ #
    # Permutation / Role Assignment (floodlight.transforms.permutation)
    # ------------------------------------------------------------------ #
    "assign_roles": {
        "function_path": "floodlight.transforms.permutation.assign_roles",
        "category": "permutation",
        "display_name": "Assign Roles",
        "description": "Consistent role assignment using the Hungarian algorithm",
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "n_iter": {
                "type": "int",
                "default": 1,
                "min": 1,
                "max": 100,
                "description": "Number of role-assignment iterations.",
            },
            "reference": {
                "type": "ndarray",
                "default": None,
                "description": "Reference layout (numpy ndarray). None = first frame is used.",
            },
        },
        "returns": "XY",
    },
    # ------------------------------------------------------------------ #
    # XY core methods (floodlight.core.xy.XY)
    # Dispatched via ``source: "xy.method"`` sentinel; ``function_path`` is
    # retained for docstring resolution in ``resolve.py`` (walks the dotted
    # path through the XY class). ``build_args`` coerces GUI params into the
    # upstream method signature.
    # ------------------------------------------------------------------ #
    "translate": {
        "source": "xy.method",
        "method": "translate",
        "function_path": "floodlight.core.xy.XY.translate",
        "category": "spatial",
        "display_name": "Translate",
        "description": (
            "Shift every point by a fixed (dx, dy) vector. Upstream "
            "XY.translate takes a `shift` 2-vector; the GUI exposes two "
            "scalar widgets for usability."
        ),
        "in_place": True,
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "dx_meters": {
                "type": "float",
                "default": 0.0,
                "description": "Shift along the x-axis (pitch units, meters).",
            },
            "dy_meters": {
                "type": "float",
                "default": 0.0,
                "description": "Shift along the y-axis (pitch units, meters).",
            },
        },
        "build_args": lambda p: {
            "shift": (
                float(p.get("dx_meters", 0.0)),
                float(p.get("dy_meters", 0.0)),
            ),
        },
        "returns": "XY",
    },
    "scale": {
        "source": "xy.method",
        "method": "scale",
        "function_path": "floodlight.core.xy.XY.scale",
        "category": "spatial",
        "display_name": "Scale",
        "description": "Multiply coordinates by a factor, optionally along one axis only.",
        "in_place": True,
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "factor": {
                "type": "float",
                "default": 1.0,
                "description": "Multiplicative factor (1.0 = no change).",
            },
            "axis": {
                "type": "enum",
                "options": ["both", "x", "y"],
                "default": "both",
                "description": "Axis to scale. 'both' scales x and y uniformly.",
            },
        },
        "build_args": lambda p: {
            "factor": float(p.get("factor", 1.0)),
            "axis": None if p.get("axis", "both") == "both" else p["axis"],
        },
        "returns": "XY",
    },
    "reflect": {
        "source": "xy.method",
        "method": "reflect",
        "function_path": "floodlight.core.xy.XY.reflect",
        "category": "spatial",
        "display_name": "Reflect",
        "description": "Mirror coordinates across the given axis.",
        "in_place": True,
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "axis": {
                "type": "enum",
                "options": ["x", "y"],
                "default": "x",
                "description": "Axis to reflect across.",
            },
        },
        "build_args": lambda p: {"axis": p.get("axis", "x")},
        "returns": "XY",
    },
    "rotate": {
        "source": "xy.method",
        "method": "rotate",
        "function_path": "floodlight.core.xy.XY.rotate",
        "category": "spatial",
        "display_name": "Rotate",
        "description": "Rotate all points about the origin by alpha degrees.",
        "in_place": True,
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "alpha": {
                "type": "float",
                "default": 0.0,
                "min": -360.0,
                "max": 360.0,
                "description": "Rotation angle in degrees, -360..360.",
            },
        },
        "build_args": lambda p: {"alpha": float(p.get("alpha", 0.0))},
        "returns": "XY",
    },
    "slice": {
        "source": "xy.method",
        "method": "slice",
        "function_path": "floodlight.core.xy.XY.slice",
        "category": "temporal",
        "display_name": "Slice",
        "description": "Keep only frames in [start, end). Changes frame count.",
        "in_place": False,
        "inputs": {
            "xy": {"type": "XY", "required": True},
        },
        "params": {
            "startframe": {
                "type": "int",
                "default": 0,
                "min": 0,
                "description": "First frame to keep (inclusive). 0 = from beginning.",
            },
            "endframe": {
                "type": "int",
                "default": 0,
                "min": 0,
                "description": (
                    "Last frame, exclusive. Leave blank for 'until end'; "
                    "a literal 0 is passed through and will yield an empty slice."
                ),
            },
        },
        # Treat "field not present / blank" as None, but preserve a
        # literal 0 from the UI. Python's falsy check collapsed endframe=0
        # to "until end" silently, which is both incorrect (0 is an invalid
        # end bound) and confusing for the user.
        "build_args": lambda p: {
            "startframe": (int(p["startframe"]) if p.get("startframe") not in (None, "") else None),
            "endframe": (int(p["endframe"]) if p.get("endframe") not in (None, "") else None),
            "inplace": False,
        },
        "returns": "XY",
    },
}


# ---------------------------------------------------------------------------- #
# PARAM_LABEL_MAP -- single source of truth for friendly widget labels
# ---------------------------------------------------------------------------- #
# Widget labels derive from the param's argument name by default.
# Add an entry here ONLY when the argument name is genuinely user-hostile
# (technical abbreviation, signal-processing jargon, or filesystem
# slug). Single edit point covers all 4 registries because
# ``build_param_widget`` reads from here.

PARAM_LABEL_MAP: dict[str, str] = {
    # Player-ID technical keys
    "xIDs": "Player IDs",
    "exclude_xIDs": "Exclude player IDs",
    # Filter signal-processing jargon
    "Wn": "Normalized cutoff frequency",
    "remove_short_seqs": "Remove short sequences",
    "poly_order": "Polynomial order",
    "window_length": "Window length",
    "numtaps": "Number of taps",
    "process_noise": "Process noise",
    "measurement_noise": "Measurement noise",
    "target_framerate": "Target framerate (Hz)",
    "interp_method": "Interpolation method",
    "max_gap": "Max gap (frames)",
    # IO file_input keys
    "filepath_mat": "Match info XML",
    "filepath_dat": "Tracking data file",
    "filepath_metadata": "Metadata file",
    "filepath_xml": "Events XML",
    # Model param keys
    "max_acceleration": "Max acceleration (m/s^2)",
    "vmax": "Terminal velocity (m/s)",
    "xpoints": "X-axis mesh points",
    "motion_model": "Motion model",
    # Metric param keys
    "zone_names": "Zone names",
    # Metric input keys
    "sig": "Signal (1D)",
    "property_to_aggregate": "Property to aggregate",
    "binning_property": "Binning property",
    "xy": "XY positions",
    "template": "Template (Mx2 array)",
    # Pitch constructor params (locked)
    "xlim": "X-axis limits (m)",
    "ylim": "Y-axis limits (m)",
}


def format_stack_param_value(value: Any) -> str:
    """Display-format a single op-stack param value for the transforms results panel.

    Floats are rounded to 3 decimal places to suppress floating-point noise
    that surfaces after in-place XY math (e.g. ``XY.translate`` can store
    ``1.0000000000000119`` instead of ``1.0``). Other types fall through to
    ``str()``.

    Parameters
    ----------
    value : Any
        A param value from the op stack entry.

    Returns
    -------
    str
        Human-readable representation of the value.
    """
    if isinstance(value, float):
        return f"{round(value, 3)}"
    return str(value)


def register_transform(key: str, descriptor: dict) -> None:
    """Validate a descriptor, insert it into TRANSFORM_REGISTRY, and emit
    TRANSFORM_REGISTRY_CHANGED.

    Ordering: validate, check for duplicate key, mutate TRANSFORM_REGISTRY, then emit.
    Subscribers reading TRANSFORM_REGISTRY inside their callback will see the new entry
    because the mutation happens before the emission.

    Parameters
    ----------
    key : str
        Registry key for the new transform (must not already exist).
    descriptor : dict
        Descriptor dict conforming to the TRANSFORM_REGISTRY schema.

    Raises
    ------
    ValueError
        If the descriptor fails shape validation. Message format (locked):
        ``f"TRANSFORM_REGISTRY descriptor for '{key}': {reason}"``.
    ValueError
        If ``key`` is already registered. Message format (locked):
        ``f"key '{key}' already registered in TRANSFORM_REGISTRY; unregister first"``.
    """
    from floodlight_gui.registry._validators import _validate_transform_descriptor

    _validate_transform_descriptor(key, descriptor)

    if key in TRANSFORM_REGISTRY:
        raise ValueError(f"key '{key}' already registered in TRANSFORM_REGISTRY; unregister first")

    TRANSFORM_REGISTRY[key] = descriptor
    bus.emit(Events.TRANSFORM_REGISTRY_CHANGED, key=key, descriptor=descriptor)
