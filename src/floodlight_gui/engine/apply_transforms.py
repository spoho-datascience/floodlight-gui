"""TRANSFORM_REGISTRY executor: dispatches every registered XY op against a
``floodlight.core.xy.XY`` object and returns the result.

DPG-free at module scope. Stdlib + the pure-Python TRANSFORM_REGISTRY
descriptor dict are the only module-level imports.

Dispatch contract
-----------------
Two entry kinds are recognised:

1. XY-method entries: carry ``source: "xy.method"`` and (separately)
   ``function_path: "floodlight.core.xy.XY.<method>"``. The ``source`` branch
   runs the method via ``getattr(xy, desc["method"])``. The ``function_path``
   field is retained for docstring resolution in ``core/help/resolve.py``; it
   is not used for dispatch.

2. Module-routed entries: carry ``function_path`` and no ``source`` sentinel.
   Dispatch splits ``function_path`` via ``rpartition(".")``,
   ``importlib.import_module``s the module half, and calls
   ``getattr(mod, fn_name)(xy, **kwargs)``.

Branch ordering: the ``source`` check runs before the ``function_path``
check. XY-method entries carry both fields, and
``importlib.import_module("floodlight.core.xy.XY")`` would raise
``ModuleNotFoundError`` because ``XY`` is a class, not a module.

Layering: ``engine/`` sits between ``registry/`` (descriptor source of truth)
and the tab layer (which calls ``apply_xy_op``). Import from here; never from
``registry/transforms`` directly for dispatch purposes.
"""

from __future__ import annotations

import importlib
from typing import Any

from floodlight_gui.registry.transforms import TRANSFORM_REGISTRY

__all__ = ["apply_xy_op"]

# Module paths accepted for the module-routed dispatch branch. Hardcoded to keep
# the trust boundary explicit (fail-fast on unknown sources). XY-method entries
# use ``source: "xy.method"``, which is not in this dict (that branch handles
# them separately). Module-routed entries dispatch via ``function_path`` alone,
# so this dict is informational; it is referenced only when a descriptor
# explicitly carries ``source`` pointing at one of the keys.
SOURCE_MODULES: dict[str, str] = {
    "floodlight.transforms.filter": "floodlight.transforms.filter",
    "floodlight.transforms.interpolation": "floodlight.transforms.interpolation",
    "floodlight.transforms.temporal": "floodlight.transforms.temporal",
}


def apply_xy_op(xy, op_key: str, params: dict[str, Any]):
    """Dispatch a TRANSFORM_REGISTRY op against ``xy`` and return the result.

    Parameters
    ----------
    xy : floodlight.XY
        The XY object to operate on. In-place ops mutate ``xy.xy`` and return
        ``None``; the dispatcher returns ``xy`` itself in that case.
    op_key : str
        Key into ``TRANSFORM_REGISTRY``. Unknown keys raise ``KeyError``.
    params : dict[str, Any]
        Raw GUI param dict. For XY-method entries, ``desc["build_args"](params)``
        coerces this into upstream method-signature kwargs. For module-routed
        entries, ``params`` is filtered (``None`` values dropped, the string
        ``"none"`` converted to ``None``) and passed as kwargs directly.

    Returns
    -------
    floodlight.XY
        The transformed XY returned by the upstream call (non-in-place ops:
        filter, interpolate, slice, resample), or the same ``xy`` passed in
        (in-place ops: translate, scale, reflect, rotate).

    Raises
    ------
    KeyError
        If ``op_key`` is not in ``TRANSFORM_REGISTRY``, or if a descriptor has
        neither ``source: "xy.method"`` nor ``function_path``.
    """
    if op_key not in TRANSFORM_REGISTRY:
        raise KeyError(f"Unknown XY op: {op_key!r}")

    desc = TRANSFORM_REGISTRY[op_key]

    # XY-method entries carry ``build_args``; module-routed entries do not.
    if "build_args" in desc:
        kwargs = desc["build_args"](params or {})
    else:
        # For module-routed ops: pass params as kwargs, filtering Python None
        # and converting the string sentinel "none" to Python None (the UI
        # represents interp_method=None as the string "none" in dropdown
        # widgets; upstream floodlight functions expect Python None).
        # Additionally coerce ndarray-typed params where raw is an empty string
        # (the default widget state) to None so they are dropped by the filter
        # below. Upstream callables (e.g. assign_roles) then use their own
        # default values.
        # preserved: no DPG import here.
        _desc_params = desc.get("params") or {}
        _ndarray_keys = {
            k
            for k, pdesc in _desc_params.items()
            if isinstance(pdesc, dict) and pdesc.get("type") == "ndarray"
        }

        def _coerce_value(k: str, v: Any) -> Any:
            """Coerce a single param value for dispatch. Returns None to drop."""
            if k in _ndarray_keys and isinstance(v, str) and v.strip() == "":
                return None
            return None if v == "none" else v

        kwargs = {}
        for k, v in (params or {}).items():
            coerced = _coerce_value(k, v)
            if coerced is not None:
                kwargs[k] = coerced

    # The ``source`` check runs before ``function_path`` (see dispatch contract
    # in the module docstring). XY-method entries carry both fields; resolving
    # via ``function_path`` first would raise ``ModuleNotFoundError`` because
    # ``floodlight.core.xy.XY`` is a class, not an importable module.
    if desc.get("source") == "xy.method":
        method = getattr(xy, desc["method"])
        result = method(**kwargs)
    elif "function_path" in desc:
        mod_path, _, fn_name = desc["function_path"].rpartition(".")
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, fn_name)
        result = fn(xy, **kwargs)
    else:
        raise KeyError(
            f"Cannot dispatch op {op_key!r}: descriptor has neither "
            f"'source'=='xy.method' nor 'function_path'"
        )

    # In-place ops (translate/scale/reflect/rotate) return None; non-in-place
    # ops (filter / interpolate / slice / resample) return a new XY.
    return result if result is not None else xy
