# Registry descriptor reference

This is the schema for the registry descriptors that drive the GUI. Adding a
feature is adding one of these dicts to the matching file in
`src/floodlight_gui/registry/`. The validator in
`src/floodlight_gui/registry/_validators.py` is the source of truth for which
keys are required and optional; `python -c "from floodlight_gui.registry import
validate_all; validate_all()"` checks a descriptor against it.

## Parameter and input fields (shared by every registry)

`params`, `init_params`, `fit_params`, `inputs`, `file_inputs`, and
`extra_params` are all dicts keyed by the parameter name, where each value is a
field dict. The supported field keys:

| Key | Applies to | Meaning |
|---|---|---|
| `type` | all | Widget / value type. For params: `"int"`, `"float"`, `"enum"`, `"string"`, `"bool"`. For inputs: a data type such as `"XY"` or a model-output type. |
| `default` | params | Initial value. |
| `min`, `max` | numeric params | Bounds for `int` / `float` widgets. |
| `options` | enum params | The list of allowed choices. |
| `required` | inputs, file_inputs, some params | Whether the user must supply it. |
| `extensions` | file_inputs | Allowed file extensions, e.g. `[".xml"]`. |
| `label` | extra_params, model outputs | Display label when it differs from the key. |
| `example` | metric params | An example value shown in the UI. |
| `description` | field-level | Informational only; not surfaced in the UI (see "The description key"). |
| `tooltip` | all | Explicit hover text. Optional; see below. |

### Where hover tooltips come from

A parameter's hover tooltip is resolved at widget-build time by
`resolve_tooltip` (`tabs/_shared/descriptor_widgets.py`) through a 4-tier chain:

1. the field's `tooltip` value, if present (explicit override);
2. the parameter's description parsed from the **upstream floodlight docstring**
   (the default for almost every field);
3. the first line of the upstream docstring (legacy fallback);
4. `"No description available"`.

So you usually write no tooltip at all and the text comes from floodlight. The
field's `description` key is informational only and is **never** used as a
tooltip (that keeps the hover text from drifting away from upstream). To force a
specific tooltip, add a `tooltip` key (spelled exactly `tooltip`, no trailing
colon).

### Where the parameter label comes from

The label shown next to a parameter widget is not taken from the descriptor. It
comes from `PARAM_LABEL_MAP` in `registry/transforms.py`, a shared key-to-label
map applied to every param widget in every tab (for example
`"Wn": "Normalized cutoff frequency"`). A param key with no entry in the map is
shown verbatim. Per-param `label` fields are deliberately unsupported for params
(the map is the single source of truth); to give a param a friendly name, add it
to `PARAM_LABEL_MAP`.

### The description key

`description` means different things at two levels:

- **Top-level** (on the descriptor itself): the GUI-side summary line shown at
  the top of the `?` help modal, above the upstream docstring body.
- **Field-level** (on a param or input field): informational only. It is not
  surfaced anywhere in the UI; the param shows its `PARAM_LABEL_MAP` label and a
  docstring-derived tooltip. It survives as inline documentation in the registry
  source for maintainers.

## IO_REGISTRY (`registry/io.py`)

**Required:** `module`, `display_name`, `sport`, `file_inputs`,
`loader_functions`, `outputs`.
**Optional:** `extra_params`, `is_dataset`, `dataset_class`, `disabled`,
`list_matches`, `load_match`.

A file-based provider:

```python
"dfl": {
    "module": "floodlight.io.dfl",          # upstream floodlight io module
    "display_name": "DFL / STS",
    "sport": "football",                     # "football" | "handball"
    "file_inputs": {                         # one file picker per entry
        "filepath_mat": {
            "extensions": [".xml"],
            "required": True,
            "tooltip": "Match-info XML (teams, players, pitch, periods).",
        },
        "filepath_pos": {"extensions": [".xml"], "required": True},
        "filepath_ev": {"extensions": [".xml"], "required": False},
    },
    "loader_functions": {                    # which upstream loaders to call
        "positions": {
            "function": "read_position_data_xml",   # name in `module`
            "args": ["filepath_pos", "filepath_mat"],  # file_inputs keys, in order
            "description": "Load XY position data from DFL XML",
        },
        # ...one per data kind: events, teamsheets, pitch
    },
    "outputs": ["xy", "teamsheets", "pitch", "events", "codes"],  # data kinds produced
    # Optional:
    # "extra_params": {"<name>": {"type": "enum", "default": "...", "label": "..."}},
}
```

A public-dataset provider sets `is_dataset` and provides callable hooks instead
of file inputs:

```python
"idsse": {
    "module": "floodlight.io.datasets",
    "display_name": "IDSSE (Public Dataset)",
    "sport": "football",
    "is_dataset": True,
    "dataset_class": "IDSSEDataset",         # class in floodlight.io.datasets
    "list_matches": <callable> ,             # () -> list[dict] of available matches
    "load_match": <callable> ,               # (cache_subdir, match_id, on_progress, cancel) -> 4-tuple
    "file_inputs": {},                       # still required (empty for datasets)
    "loader_functions": {},
    "outputs": ["xy", "events", "teamsheets", "pitch"],
}
```

`loader_functions` entries may also use `class` + `extra_args`, and carry
`disabled` / `disabled_reason` to grey out a loader.

## MODEL_REGISTRY (`registry/models.py`)

**Required:** `class_path`, `category`, `display_name`, `description`, `inputs`,
`init_params`, `fit_params`, `outputs`.
**Optional:** `fit_xy_arity`, `overlay_adapter`, `fit_param_coerce`.

A single-team model:

```python
"velocity": {
    "class_path": "floodlight.models.kinematics.VelocityModel",
    "category": "kinematics",                # groups models under a tab-bar category
    "display_name": "Velocity",
    "description": "Frame-wise player velocity.",
    "inputs": {"xy": {"type": "XY", "required": True}},
    "init_params": {                         # constructor kwargs (cls.__init__)
        # "<name>": {"type": "enum", "options": [...], "default": ...},
    },
    "fit_params": {},                        # fit() kwargs (XY-typed params are positional)
    "outputs": {                             # which fitted results to expose
        "velocity": {
            "method": "velocity",            # method called on the fitted model
            "returns": "PlayerProperty",     # PlayerProperty | TeamProperty | DyadicProperty | DataFrame
            "label": "Velocity",
        },
    },
}
```

A multi-team model declares its extra XY input and the overlay adapter:

```python
"discrete_voronoi": {
    "class_path": "floodlight.models.space.DiscreteVoronoiModel",
    "category": "space",
    "display_name": "Discrete Voronoi",
    "description": "Per-player pitch control tessellation.",
    "inputs": {                              # two XY inputs -> two teams
        "xy_home": {"type": "XY", "required": True},
        "xy_away": {"type": "XY", "required": True},
    },
    "init_params": {...},
    "fit_params": {},
    "outputs": {...},
    "fit_xy_arity": 2,                       # fit() needs 2 XY args (default treated as 1)
    "overlay_adapter": "voronoi",            # OVERLAY_ADAPTER_REGISTRY key for the live overlay; None for no overlay
    # "fit_param_coerce": <callable>,        # optional hook to reshape fit kwargs before fit()
}
```

## TRANSFORM_REGISTRY (`registry/transforms.py`)

**Required:** `category`, `display_name`, `description`, `inputs`, `params`,
`returns`.
**Optional (XY-method entries):** `function_path`, `source`, `method`,
`in_place`, `build_args`.

A module-routed transform (calls a floodlight function):

```python
"butterworth_lowpass": {
    "function_path": "floodlight.transforms.filter.butterworth_lowpass",
    "category": "filter",                    # Filter | Interpolation | Spatial | Temporal | Permutation
    "display_name": "Butterworth Low-Pass",
    "description": "Zero-phase Butterworth low-pass smoothing.",
    "inputs": {"xy": {"type": "XY", "required": True}},
    "params": {
        "order": {"type": "int", "default": 3, "min": 1, "max": 10,
                  "description": "Butterworth filter order (1-10)."},
        # ...more params
    },
    "returns": "XY",                         # return type name
}
```

An XY-method entry (calls a method on the `XY` object) sets `source`:

```python
"slice": {
    "source": "xy.method",                   # marks this as an XY method, not a module function
    "method": "slice",                       # the XY method name
    "function_path": "floodlight.core.xy.XY.slice",  # retained for docstring (tooltip) resolution only
    "category": "temporal",
    "display_name": "Slice",
    "description": "Keep only frames in [start, end). Changes frame count.",
    "in_place": False,                       # informational
    "inputs": {"xy": {"type": "XY", "required": True}},
    "params": {"startframe": {"type": "int", "default": 0, "min": 0},
               "endframe": {"type": "int", "default": 0, "min": 0}},
    "build_args": lambda p: {"startframe": p["startframe"], "endframe": p["endframe"] or None},  # maps params -> method kwargs
    "returns": "XY",
}
```

## METRICS_REGISTRY (`registry/metrics.py`)

**Required:** `function_path`, `category`, `display_name`, `description`,
`inputs`, `params`, `returns`. No optional top-level keys.

```python
"approx_entropy": {
    "function_path": "floodlight.metrics.entropy.approx_entropy",
    "category": "complexity",
    "display_name": "Approximate Entropy",
    "description": "Time-series complexity of a signal.",
    "inputs": {                              # a metric input can be a model output
        "sig": {"type": "sig", "required": True,
                "description": "1-D time series (pick a model output and a player)."},
    },
    "params": {
        "m": {"type": "int", "default": 2, "min": 1, "max": 10,
              "description": "Length of compared runs of data."},
        "r": {"type": "float", "default": 0.5, "min": 0.01, "max": 5.0},
    },
    "returns": "float",                       # return type name
}
```

## Notes

- `category`, `display_name`, and `description` are GUI-facing labels; everything
  else mirrors the upstream floodlight callable named by `class_path` /
  `function_path` / `module`. Follow the thin-frontend rule: do not rename
  upstream methods or parameters.
- The parametrized tests in `tests/test_{model,transform,metrics}_registry.py`
  check each descriptor's declared params against the upstream signature, so a
  drifted param name fails the suite.
