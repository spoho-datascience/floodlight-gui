"""Model tab params layer: build per-parameter DPG widgets and collect their values into
call kwargs for the fit/execute layer.

Layering role: DPG-aware (imports dearpygui at module scope). Reads MODEL_REGISTRY descriptors
and writes widget values back into a dict that is passed verbatim to floodlight (thin frontend).
Engine-supplied parameter types (XY, Pitch) are excluded from widget rendering and collection.
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.engine.fit_model import _import_class
from floodlight_gui.registry.models import MODEL_REGISTRY
from floodlight_gui.tabs._shared.descriptor_widgets import build_param_widget
from floodlight_gui.tabs._shared.state_views import render_empty
from floodlight_gui.tabs.model import state
from floodlight_gui.theme import INFO

logger = logging.getLogger(__name__)

PARAMS_CONTAINER = "models_params_container"

# Param types the engine supplies; never rendered as widgets.
_ENGINE_SUPPLIED_TYPES = {"Pitch", "XY"}


def rebuild_params(model_key: str) -> None:
    """Rebuild the shared params container with widgets for init_params then fit_params.

    Parameters
    ----------
    model_key : str
        Key into ``MODEL_REGISTRY`` identifying the active model descriptor.

    Notes
    -----
    Side-effects: clears and repopulates the DPG container ``PARAMS_CONTAINER``
    with one widget per non-engine-supplied parameter. Engine-supplied parameter
    types (XY, Pitch) are skipped. When no data is loaded, renders the empty-state
    view in the container instead. Each widget is tagged
    ``model_param_{model_key}_{pname}`` for later collection by ``collect_ui_params``.
    """
    if not dpg.does_item_exist(PARAMS_CONTAINER):
        return
    if state.app_instance is None or not getattr(state.app_instance, "loaded_data", None):
        render_empty(PARAMS_CONTAINER, "No data loaded — load data in the Load tab first.")
        return
    with contextlib.suppress(SystemError):
        dpg.delete_item(PARAMS_CONTAINER, children_only=True)

    desc = MODEL_REGISTRY[model_key]
    upstream = _resolve_upstream(desc)
    rendered = False
    for container in ("init_params", "fit_params"):
        for pname, pdesc in desc.get(container, {}).items():
            if pdesc.get("type") in _ENGINE_SUPPLIED_TYPES:
                continue
            build_param_widget(
                pname,
                pdesc,
                PARAMS_CONTAINER,
                upstream_callable=upstream,
                tag=f"model_param_{model_key}_{pname}",
                param_container=container,
            )
            rendered = True
    if not rendered:
        dpg.add_text("No configurable parameters.", parent=PARAMS_CONTAINER, color=INFO)


def collect_ui_params(model_key: str) -> dict:
    """Read every rendered param widget back into a dict for the fit/execute layer.

    Values are passed verbatim to floodlight (thin frontend): no cleaning,
    clamping, or coercion. Type-specific conversions applied per descriptor type:
    ``enum`` "None" -> Python ``None``; ``list[int]`` split from CSV; ``tuple[float,float]``
    read from the ``__lo`` / ``__hi`` sub-tags.

    Parameters
    ----------
    model_key : str
        Key into ``MODEL_REGISTRY`` identifying the active model descriptor.

    Returns
    -------
    dict
        Mapping of parameter name to collected widget value, ready to pass to
        ``fit_model`` as ``ui_params``. Engine-supplied types (XY, Pitch) are excluded.
    """
    desc = MODEL_REGISTRY[model_key]
    ui_params: dict = {}
    for container in ("init_params", "fit_params"):
        for pname, pdesc in desc.get(container, {}).items():
            ptype = pdesc.get("type")
            if ptype in _ENGINE_SUPPLIED_TYPES:
                continue
            tag = f"model_param_{model_key}_{pname}"
            ui_params[pname] = _read_param(tag, ptype, pdesc)
    return ui_params


def _read_param(tag: str, ptype, pdesc: dict):
    """Read one parameter widget value and coerce it to the descriptor's Python type.

    Parameters
    ----------
    tag : str
        DPG widget tag as produced by ``collect_ui_params``.
    ptype : str or None
        Parameter type string from the descriptor (e.g. "enum", "list[int]",
        "tuple[float, float]", or a scalar type).
    pdesc : dict
        Full parameter descriptor; its ``default`` key is the fallback when the
        widget tag does not exist.

    Returns
    -------
    object
        The coerced value: a 2-tuple for tuple types, ``None`` for enum "None",
        a list of ints for list[int], or the raw DPG value for all other types.
    """
    if ptype == "tuple[float, float]":
        lo = dpg.get_value(f"{tag}__lo") if dpg.does_item_exist(f"{tag}__lo") else None
        hi = dpg.get_value(f"{tag}__hi") if dpg.does_item_exist(f"{tag}__hi") else None
        return (lo, hi)
    if not dpg.does_item_exist(tag):
        return pdesc.get("default")
    raw = dpg.get_value(tag)
    if ptype == "enum":
        return None if raw == "None" else raw
    if ptype == "list[int]":
        return [int(s) for s in str(raw).replace(",", " ").split() if s.strip()]
    return raw


def _resolve_upstream(desc: dict):
    """Import and return the floodlight class from ``desc['class_path']``, or None on failure."""
    class_path = desc.get("class_path")
    if not class_path:
        return None
    with contextlib.suppress(Exception):
        return _import_class(class_path)
    return None
