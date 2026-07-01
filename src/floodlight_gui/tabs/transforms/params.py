"""Transforms tab params layer: build per-op param widgets and collect call kwargs.

DPG-aware layer under ``tabs/``; imports ``dearpygui`` at module scope.
Backend modules must not import from here.
"""

from __future__ import annotations

import importlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.registry.transforms import TRANSFORM_REGISTRY
from floodlight_gui.tabs._shared.descriptor_widgets import build_param_widget
from floodlight_gui.tabs._shared.help_popup import render_help_button

logger = logging.getLogger(__name__)

__all__ = [
    "_build_params_ui",
    "_collect_params",
    "_resolve_upstream_function",
    "_on_op_changed",
]


def _resolve_upstream_function(function_path):
    """Resolve an upstream callable from a dotted ``function_path`` string.

    Walks the dotted path, trying each possible module/attribute split, and
    returns the first callable found. Used to extract upstream docstrings for
    the help popup.

    Parameters
    ----------
    function_path : str or None
        Dotted import path, e.g. ``"floodlight.transforms.filter.butterworth_lowpass"``.

    Returns
    -------
    callable or None
        The resolved callable, or ``None`` when the path is missing, has no
        dot, or no importable prefix resolves to a callable.
    """
    if not function_path or "." not in function_path:
        return None
    parts = function_path.split(".")
    for split in range(len(parts) - 1, 0, -1):
        module_path = ".".join(parts[:split])
        attr_chain = parts[split:]
        try:
            obj = importlib.import_module(module_path)
        except ImportError:
            continue
        for attr in attr_chain:
            obj = getattr(obj, attr, None)
            if obj is None:
                break
        if obj is not None and callable(obj):
            return obj
    return None


def _on_op_changed(sender, app_data, user_data=None):
    """Rebuild param widgets and the help button when the active op changes.

    Derives the active category from the combo tag suffix (e.g.
    ``"transforms_op_combo_filter"`` -> ``"filter"``). Falls back to
    ``"filter"`` on cold start (``sender`` is ``None`` or not a string).

    Writes to DPG tags:
    - ``transforms_combo_help_group_{category}``: help button container
      (children cleared and rebuilt).
    - ``transforms_params_container``: param widgets (via ``_build_params_ui``).

    Parameters
    ----------
    sender : str or None
        DPG tag of the combo that fired, or ``None`` on initial call.
    app_data : object
        DPG payload (unused).
    user_data : object, optional
        DPG user data (unused).
    """
    from floodlight_gui.tabs.transforms.controls import _cat_ops, _current_op_key, _display_to_key

    if isinstance(sender, str) and sender.startswith("transforms_op_combo_"):
        category = sender[len("transforms_op_combo_") :]
    else:
        category = "filter"

    if category not in _cat_ops:
        return

    combo_tag = f"transforms_op_combo_{category}"
    display = dpg.get_value(combo_tag) if dpg.does_item_exist(combo_tag) else None
    if display is None and _cat_ops[category]:
        op_key = _cat_ops[category][0]
    else:
        op_key = _display_to_key.get(display) if display else None

    if op_key is None:
        return
    _current_op_key[category] = op_key
    desc = TRANSFORM_REGISTRY[op_key]

    # Rebuild the help button in its per-category persistent container.
    help_group = f"transforms_combo_help_group_{category}"
    if dpg.does_item_exist(help_group):
        dpg.delete_item(help_group, children_only=True)
        with dpg.group(parent=help_group):
            render_help_button(
                op_key,
                desc,
                "TRANSFORMS",
                tag_prefix="transforms",
                container_hint="",
            )

    _build_params_ui(desc, category)


def _build_params_ui(desc, category: str):
    """Build per-op param widgets into the shared Step 3 container.

    Clears ``transforms_params_container`` and repopulates it with one widget
    per param entry from ``desc["params"]``, using ``build_param_widget``.
    All categories share the same container; it always reflects the active
    category's selected op.

    For the ``resample`` op an informational text widget is appended after the
    param widgets to surface the upstream NaN-padding behavior.

    Parameters
    ----------
    desc : dict
        TRANSFORM_REGISTRY entry for the active op.
    category : str
        Active category key (e.g. ``"filter"``, ``"temporal"``).

    Notes
    -----
    Writes to DPG tag ``transforms_params_container`` (children cleared on
    each call). Widget tags follow the pattern
    ``transforms_param_{op_key}_{param_name}``.
    """
    from floodlight_gui.tabs.transforms.controls import _current_op_key

    container_tag = "transforms_params_container"
    if not dpg.does_item_exist(container_tag):
        return
    dpg.delete_item(container_tag, children_only=True)

    upstream = _resolve_upstream_function(desc.get("function_path", ""))
    op_key = _current_op_key.get(category)
    if op_key is None:
        return

    for pname, pdesc in (desc.get("params") or {}).items():
        if not isinstance(pdesc, dict):
            continue
        tag = f"transforms_param_{op_key}_{pname}"
        build_param_widget(
            param_name=pname,
            param_descriptor=pdesc,
            parent_tag=container_tag,
            upstream_callable=upstream,
            tag=tag,
            param_container="params",
        )

    # Informational hint for `resample`: surfaces the upstream NaN-padding
    # behavior so users know to follow up with an interpolation step.
    if op_key == "resample":
        dpg.add_text(
            "Note: resampling to a target rate that is not a divisor of the "
            "source rate pads new frames with NaN. Use an interpolation method "
            "to fill them.",
            parent=container_tag,
            wrap=550,
            color=(180, 180, 180),
        )


def _collect_params():
    """Read current widget values and return a kwargs dict for the active op.

    Reads each param widget identified by the active op key and coerces
    values to the type declared in the TRANSFORM_REGISTRY descriptor
    (``"int"``, ``"float"``, ``"ndarray"``, or string). For ``"ndarray"``
    params, an empty widget value is mapped to ``None`` so the dispatcher
    drops the kwarg and the upstream callable uses its own default.

    Returns
    -------
    dict
        Mapping of param name to coerced value. Empty dict when no op is
        active or the op has no params.
    """
    from floodlight_gui.tabs.transforms.select import _get_active_op_key

    op_key = _get_active_op_key()
    if op_key is None:
        return {}
    desc = TRANSFORM_REGISTRY[op_key]
    out = {}
    for pname, pdesc in (desc.get("params") or {}).items():
        if not isinstance(pdesc, dict):
            continue
        tag = f"transforms_param_{op_key}_{pname}"
        if not dpg.does_item_exist(tag):
            continue
        raw = dpg.get_value(tag)
        ptype = pdesc.get("type", "string")
        if ptype == "int":
            try:
                out[pname] = int(raw)
            except (TypeError, ValueError):
                out[pname] = raw
        elif ptype == "float":
            try:
                out[pname] = float(raw)
            except (TypeError, ValueError):
                out[pname] = raw
        elif ptype == "ndarray":
            # Empty input maps to None so dispatch's `if v is not None` filter
            # drops it and the upstream callable uses its own default.
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                out[pname] = None
            else:
                out[pname] = raw
        else:
            out[pname] = raw
    return out
