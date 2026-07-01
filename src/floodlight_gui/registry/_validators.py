"""Per-registry descriptor shape validators backing ``validate_all``.

Each ``_validate_<X>_descriptor(key, descriptor) -> None`` raises ``ValueError``
on a shape violation and returns ``None`` on success. Consumed by:

- ``register_*`` helpers (validate-then-mutate-then-emit ordering)
- ``validate_all()`` in ``registry/__init__.py`` as the CI gate

DPG-free: this module never imports ``dearpygui``. It lives in ``registry/``,
the DPG-free backend layer.

Import-time validation is intentionally absent: module import stays silent and
drift surfaces loud-but-safely via the dedicated test
(``tests/test_registry_validate_all.py``).

ValueError message format:

    f"{REGISTRY_NAME} descriptor for '{key}': {reason}"

Schemas are intentionally permissive to match what the registries actually
contain rather than an idealized strict shape. Two known-permissive cases:

- TRANSFORM ``inputs.<k>.type == "ndarray"`` is accepted
  (used by ``min_max_normalize.positions``).
- METRIC ``params.<k>.required == True`` with no ``default`` is accepted
  (used by ``aggregate_property_by_zones.params.zones``).

Each per-param and per-file-input sub-dict may carry an optional ``tooltip``
(str) field for UI hint rendering. The validators check TOP-LEVEL descriptor
keys only; they do not iterate into ``params`` / ``init_params`` / ``fit_params``
/ ``inputs`` / ``file_inputs`` sub-dicts, so ``tooltip`` is silently accepted
(permissive-by-construction). The 4-tier resolution chain in
``tabs/_shared/descriptor_widgets`` consumes the field at UI build time; absence
falls back to the upstream docstring first line, then to "No description
available".
"""

from __future__ import annotations

# ----------------------------------------------------------------------------
# IO_REGISTRY schema
# ----------------------------------------------------------------------------
_IO_REQUIRED = frozenset(
    {
        "module",
        "display_name",
        "sport",
        "file_inputs",
        "loader_functions",
        "outputs",
    }
)
# Optional callable hooks for dataset providers.
# ``list_matches() -> list[dict]`` returns the per-provider match catalogue.
# ``load_match(cache_subdir, match_id, on_progress, cancel_event) -> 4-tuple``
# encapsulates each dataset class's per-provider call convention, replacing
# the per-class branching in ``load_data._load_dataset``.
_IO_OPTIONAL = frozenset(
    {
        "extra_params",
        "is_dataset",
        "dataset_class",
        "disabled",
        "list_matches",
        "load_match",
    }
)


def _validate_io_descriptor(key: str, descriptor: dict) -> None:
    """Raise ValueError on IO descriptor shape violation; return None on success."""
    if not isinstance(descriptor, dict):
        raise ValueError(
            f"IO_REGISTRY descriptor for '{key}': must be a dict, got {type(descriptor).__name__}"
        )
    missing = _IO_REQUIRED - descriptor.keys()
    if missing:
        raise ValueError(
            f"IO_REGISTRY descriptor for '{key}': missing required keys {sorted(missing)}"
        )
    extras = descriptor.keys() - (_IO_REQUIRED | _IO_OPTIONAL)
    if extras:
        raise ValueError(f"IO_REGISTRY descriptor for '{key}': unknown keys {sorted(extras)}")
    if not isinstance(descriptor["module"], str):
        raise ValueError(
            f"IO_REGISTRY descriptor for '{key}': 'module' must be a str, "
            f"got {type(descriptor['module']).__name__}"
        )
    if not isinstance(descriptor["display_name"], str):
        raise ValueError(
            f"IO_REGISTRY descriptor for '{key}': 'display_name' must be a str, "
            f"got {type(descriptor['display_name']).__name__}"
        )
    if not isinstance(descriptor["sport"], str):
        raise ValueError(
            f"IO_REGISTRY descriptor for '{key}': 'sport' must be a str, "
            f"got {type(descriptor['sport']).__name__}"
        )
    if not isinstance(descriptor["file_inputs"], dict):
        raise ValueError(
            f"IO_REGISTRY descriptor for '{key}': 'file_inputs' must be a dict, "
            f"got {type(descriptor['file_inputs']).__name__}"
        )
    if not isinstance(descriptor["loader_functions"], dict):
        raise ValueError(
            f"IO_REGISTRY descriptor for '{key}': 'loader_functions' must be a dict, "
            f"got {type(descriptor['loader_functions']).__name__}"
        )
    if not isinstance(descriptor["outputs"], list):
        raise ValueError(
            f"IO_REGISTRY descriptor for '{key}': 'outputs' must be a list, "
            f"got {type(descriptor['outputs']).__name__}"
        )


# ----------------------------------------------------------------------------
# MODEL_REGISTRY schema
# ----------------------------------------------------------------------------
_MODEL_REQUIRED = frozenset(
    {
        "class_path",
        "category",
        "display_name",
        "description",
        "inputs",
        "init_params",
        "fit_params",
        "outputs",
    }
)
# ``fit_xy_arity: int`` is an optional top-level field declaring how many
# XY inputs the model's ``fit(...)`` signature requires (default treated as 1 by
# consumer tabs). Models with a two-XY fit signature declare it explicitly.
# Absence is treated as arity=1 downstream.
#
# ``overlay_adapter: str | None`` is an optional top-level field
# declaring which adapter (lookup key into ``OVERLAY_ADAPTER_REGISTRY``) renders
# the model's overlay in the visualization tab. Plot-bearing entries carry a
# registered key; entries without a visualization overlay carry ``None``.
#
# ``fit_param_coerce: callable | None`` is an optional top-level field
# declaring a fit-kwarg coercion hook (e.g. convex_hull wraps exclude_xIDs
# as a list-of-lists for the upstream contract). The dispatcher in
# ``models/engine.py`` reads ``desc.get("fit_param_coerce")`` and invokes it
# on fit_kw when present; absent entries skip the coerce step.
_MODEL_OPTIONAL: frozenset = frozenset({"fit_xy_arity", "overlay_adapter", "fit_param_coerce"})


def _validate_model_descriptor(key: str, descriptor: dict) -> None:
    """Raise ValueError on MODEL descriptor shape violation; return None on success."""
    if not isinstance(descriptor, dict):
        raise ValueError(
            f"MODEL_REGISTRY descriptor for '{key}': must be a dict, "
            f"got {type(descriptor).__name__}"
        )
    missing = _MODEL_REQUIRED - descriptor.keys()
    if missing:
        raise ValueError(
            f"MODEL_REGISTRY descriptor for '{key}': missing required keys {sorted(missing)}"
        )
    extras = descriptor.keys() - (_MODEL_REQUIRED | _MODEL_OPTIONAL)
    if extras:
        raise ValueError(f"MODEL_REGISTRY descriptor for '{key}': unknown keys {sorted(extras)}")
    if not isinstance(descriptor["class_path"], str):
        raise ValueError(
            f"MODEL_REGISTRY descriptor for '{key}': 'class_path' must be a str, "
            f"got {type(descriptor['class_path']).__name__}"
        )
    if not isinstance(descriptor["category"], str):
        raise ValueError(
            f"MODEL_REGISTRY descriptor for '{key}': 'category' must be a str, "
            f"got {type(descriptor['category']).__name__}"
        )
    if not isinstance(descriptor["display_name"], str):
        raise ValueError(
            f"MODEL_REGISTRY descriptor for '{key}': 'display_name' must be a str, "
            f"got {type(descriptor['display_name']).__name__}"
        )
    if not isinstance(descriptor["description"], str):
        raise ValueError(
            f"MODEL_REGISTRY descriptor for '{key}': 'description' must be a str, "
            f"got {type(descriptor['description']).__name__}"
        )
    if not isinstance(descriptor["inputs"], dict):
        raise ValueError(
            f"MODEL_REGISTRY descriptor for '{key}': 'inputs' must be a dict, "
            f"got {type(descriptor['inputs']).__name__}"
        )
    if not isinstance(descriptor["init_params"], dict):
        raise ValueError(
            f"MODEL_REGISTRY descriptor for '{key}': 'init_params' must be a dict, "
            f"got {type(descriptor['init_params']).__name__}"
        )
    if not isinstance(descriptor["fit_params"], dict):
        raise ValueError(
            f"MODEL_REGISTRY descriptor for '{key}': 'fit_params' must be a dict, "
            f"got {type(descriptor['fit_params']).__name__}"
        )
    if not isinstance(descriptor["outputs"], dict):
        raise ValueError(
            f"MODEL_REGISTRY descriptor for '{key}': 'outputs' must be a dict, "
            f"got {type(descriptor['outputs']).__name__}"
        )
    # Optional fit_xy_arity must be a positive int when present.
    # Absence is treated as 1 by downstream consumer tabs.
    if "fit_xy_arity" in descriptor:
        arity = descriptor["fit_xy_arity"]
        # Reject bool first -- isinstance(True, int) is True in Python.
        if isinstance(arity, bool) or not isinstance(arity, int):
            raise ValueError(
                f"MODEL_REGISTRY descriptor for '{key}': 'fit_xy_arity' must "
                f"be an int, got {type(arity).__name__}"
            )
        if arity < 1:
            raise ValueError(
                f"MODEL_REGISTRY descriptor for '{key}': 'fit_xy_arity' must be >= 1, got {arity}"
            )
    # Optional overlay_adapter must be a str or None when present.
    # Plot-bearing entries carry a registered key in OVERLAY_ADAPTER_REGISTRY.
    if "overlay_adapter" in descriptor:
        adapter = descriptor["overlay_adapter"]
        if adapter is not None and not isinstance(adapter, str):
            raise ValueError(
                f"MODEL_REGISTRY descriptor for '{key}': 'overlay_adapter' must "
                f"be a str or None, got {type(adapter).__name__}"
            )
    # Optional fit_param_coerce must be callable or None.
    # The dispatcher reads desc.get("fit_param_coerce") and invokes it on
    # fit_kw when present; absent entries skip the coerce step.
    if "fit_param_coerce" in descriptor:
        hook = descriptor["fit_param_coerce"]
        if hook is not None and not callable(hook):
            raise ValueError(
                f"MODEL_REGISTRY descriptor for '{key}': 'fit_param_coerce' must "
                f"be callable or None, got {type(hook).__name__}"
            )


# ----------------------------------------------------------------------------
# TRANSFORM_REGISTRY schema
# ----------------------------------------------------------------------------
# Permissive: inputs.<k>.type may be "ndarray" (used by
# min_max_normalize.positions). Input types are not whitelisted.
#
# ``function_path`` is OPTIONAL (still required at dispatch time): the
# module-routed entries carry it; the XY-method entries also carry it for
# per-param docstring resolution, but use source='xy.method' for dispatch.
# The cross-check below enforces the function_path-OR-source/method invariant.
_TRANSFORM_REQUIRED = frozenset(
    {
        "category",
        "display_name",
        "description",
        "inputs",
        "params",
        "returns",
    }
)
_TRANSFORM_OPTIONAL: frozenset = frozenset(
    {
        "function_path",  # module-routed entries + XY-method entries (docstring resolution)
        "source",  # XY-method entries: must equal "xy.method"
        "method",  # XY-method entries: XY method name
        "in_place",  # XY-method entries: bool (informational)
        "build_args",  # XY-method entries: callable(dict) -> dict
    }
)


def _validate_transform_descriptor(key: str, descriptor: dict) -> None:
    """Raise ValueError on TRANSFORM descriptor shape violation; return None on success."""
    if not isinstance(descriptor, dict):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': must be a dict, "
            f"got {type(descriptor).__name__}"
        )
    missing = _TRANSFORM_REQUIRED - descriptor.keys()
    if missing:
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': missing required keys {sorted(missing)}"
        )
    extras = descriptor.keys() - (_TRANSFORM_REQUIRED | _TRANSFORM_OPTIONAL)
    if extras:
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': unknown keys {sorted(extras)}"
        )
    if "function_path" in descriptor and not isinstance(descriptor["function_path"], str):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'function_path' must be a str, "
            f"got {type(descriptor['function_path']).__name__}"
        )
    if not isinstance(descriptor["category"], str):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'category' must be a str, "
            f"got {type(descriptor['category']).__name__}"
        )
    if not isinstance(descriptor["display_name"], str):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'display_name' must be a str, "
            f"got {type(descriptor['display_name']).__name__}"
        )
    if not isinstance(descriptor["description"], str):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'description' must be a str, "
            f"got {type(descriptor['description']).__name__}"
        )
    if not isinstance(descriptor["inputs"], dict):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'inputs' must be a dict, "
            f"got {type(descriptor['inputs']).__name__}"
        )
    if not isinstance(descriptor["params"], dict):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'params' must be a dict, "
            f"got {type(descriptor['params']).__name__}"
        )
    if not isinstance(descriptor["returns"], str):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'returns' must be a str, "
            f"got {type(descriptor['returns']).__name__}"
        )
    # Dispatch-route cross-check. Every entry must declare either a
    # function_path (module-routed dispatch + docstring resolution) or
    # source='xy.method' + method (XY core method dispatch).
    has_function_path = "function_path" in descriptor
    has_xy_method = (
        isinstance(descriptor.get("source"), str)
        and descriptor.get("source") == "xy.method"
        and isinstance(descriptor.get("method"), str)
    )
    if not has_function_path and not has_xy_method:
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': must have either "
            f"'function_path' (module-routed) or source='xy.method' + 'method' "
            f"(XY core method)"
        )
    # Type-check the optional XY-method fields when present.
    if "source" in descriptor and not isinstance(descriptor["source"], str):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'source' must be a str, "
            f"got {type(descriptor['source']).__name__}"
        )
    if "method" in descriptor and not isinstance(descriptor["method"], str):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'method' must be a str, "
            f"got {type(descriptor['method']).__name__}"
        )
    if "in_place" in descriptor and not isinstance(descriptor["in_place"], bool):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'in_place' must be a bool, "
            f"got {type(descriptor['in_place']).__name__}"
        )
    if "build_args" in descriptor and not callable(descriptor["build_args"]):
        raise ValueError(
            f"TRANSFORM_REGISTRY descriptor for '{key}': 'build_args' must be callable, "
            f"got {type(descriptor['build_args']).__name__}"
        )


# ----------------------------------------------------------------------------
# METRICS_REGISTRY schema
# ----------------------------------------------------------------------------
# Permissive: params.<k>.required == True with no 'default' is legal
# (used by aggregate_property_by_zones.params.zones). 'default' is not
# enforced when 'required' is True.
_METRIC_REQUIRED = frozenset(
    {
        "function_path",
        "category",
        "display_name",
        "description",
        "inputs",
        "params",
        "returns",
    }
)
_METRIC_OPTIONAL: frozenset = frozenset()


def _validate_metric_descriptor(key: str, descriptor: dict) -> None:
    """Raise ValueError on METRIC descriptor shape violation; return None on success."""
    if not isinstance(descriptor, dict):
        raise ValueError(
            f"METRICS_REGISTRY descriptor for '{key}': must be a dict, "
            f"got {type(descriptor).__name__}"
        )
    missing = _METRIC_REQUIRED - descriptor.keys()
    if missing:
        raise ValueError(
            f"METRICS_REGISTRY descriptor for '{key}': missing required keys {sorted(missing)}"
        )
    extras = descriptor.keys() - (_METRIC_REQUIRED | _METRIC_OPTIONAL)
    if extras:
        raise ValueError(f"METRICS_REGISTRY descriptor for '{key}': unknown keys {sorted(extras)}")
    if not isinstance(descriptor["function_path"], str):
        raise ValueError(
            f"METRICS_REGISTRY descriptor for '{key}': 'function_path' must be a str, "
            f"got {type(descriptor['function_path']).__name__}"
        )
    if not isinstance(descriptor["category"], str):
        raise ValueError(
            f"METRICS_REGISTRY descriptor for '{key}': 'category' must be a str, "
            f"got {type(descriptor['category']).__name__}"
        )
    if not isinstance(descriptor["display_name"], str):
        raise ValueError(
            f"METRICS_REGISTRY descriptor for '{key}': 'display_name' must be a str, "
            f"got {type(descriptor['display_name']).__name__}"
        )
    if not isinstance(descriptor["description"], str):
        raise ValueError(
            f"METRICS_REGISTRY descriptor for '{key}': 'description' must be a str, "
            f"got {type(descriptor['description']).__name__}"
        )
    if not isinstance(descriptor["inputs"], dict):
        raise ValueError(
            f"METRICS_REGISTRY descriptor for '{key}': 'inputs' must be a dict, "
            f"got {type(descriptor['inputs']).__name__}"
        )
    if not isinstance(descriptor["params"], dict):
        raise ValueError(
            f"METRICS_REGISTRY descriptor for '{key}': 'params' must be a dict, "
            f"got {type(descriptor['params']).__name__}"
        )
    if not isinstance(descriptor["returns"], str):
        raise ValueError(
            f"METRICS_REGISTRY descriptor for '{key}': 'returns' must be a str, "
            f"got {type(descriptor['returns']).__name__}"
        )
