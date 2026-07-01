"""File-provider section of the Load tab: per-provider file pickers and the Load button.

Renders a collapsing header containing a provider combo, one file-picker button per
``file_inputs`` entry in the IO_REGISTRY descriptor, optional extra-param widgets, and
a Load Data button. Loading is synchronous: ``app.load_provider_data`` writes the store
and emits ``Events.DATA_LOADED`` before returning.

Layering: DPG-aware (imports ``dearpygui`` at module scope); lives under ``tabs/``.
Backend modules must not import from here.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.registry.io import IO_REGISTRY, file_provider_keys
from floodlight_gui.registry.transforms import PARAM_LABEL_MAP
from floodlight_gui.tabs._shared.descriptor_widgets import build_param_widget
from floodlight_gui.theme import INFO

from .section_helpers import (
    combo_items,
    get_app,
    prime_button,
    render_provider_help,
)

logger = logging.getLogger(__name__)

_COMBO_TAG = "load_file_combo"
_HELP_GROUP_TAG = "load_file_help_group"
_FORM_TAG = "load_file_form"
_STATUS_TAG = "load_file_status"

# Module-level mutable state for the currently selected provider:
#   reverse:       display-name -> registry-key mapping built at build() time
#   key:           active provider key (None when no provider is selected)
#   file_paths:    param-name -> chosen filesystem path (cleared on each combo change)
#   extra_widgets: param-name -> DPG widget tag for extra-param inputs
_S: dict = {"reverse": {}, "key": None, "file_paths": {}, "extra_widgets": {}}


def build(parent_tag: str) -> None:
    """Render the file-provider section into *parent_tag*.

    Builds a collapsing header containing a provider combo (``_COMBO_TAG``), a
    placeholder group for the per-provider form (``_FORM_TAG``), and a status
    text widget (``_STATUS_TAG``). The combo callback populates the form when
    the user selects a provider.

    Parameters
    ----------
    parent_tag : str
        DPG container tag to attach the section into.

    Notes
    -----
    Side-effects: creates DPG widgets tagged ``_COMBO_TAG``, ``_FORM_TAG``, and
    ``_STATUS_TAG``; populates ``_S["reverse"]`` from ``file_provider_keys()``.
    """
    keys = file_provider_keys()
    items, reverse = combo_items(keys)
    _S["reverse"] = reverse

    with dpg.collapsing_header(
        label="Load data file", default_open=True, closable=False, parent=parent_tag
    ):
        dpg.add_text("Select a local-file provider:", color=INFO)
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=items, tag=_COMBO_TAG, width=320, callback=_on_combo, default_value=""
            )
            dpg.add_group(tag=_HELP_GROUP_TAG)
        dpg.add_group(tag=_FORM_TAG)
        dpg.add_text("", tag=_STATUS_TAG, wrap=560)


def _on_combo(sender, app_data, user_data) -> None:
    """Resolve the provider key and rebuild the help button and form (DPG callback)."""
    try:
        key = _S["reverse"].get(app_data)
        _S["key"] = key
        _S["file_paths"] = {}
        _S["extra_widgets"] = {}
        dpg.delete_item(_FORM_TAG, children_only=True)
        dpg.delete_item(_HELP_GROUP_TAG, children_only=True)
        dpg.set_value(_STATUS_TAG, "")
        if key is None:
            return
        render_provider_help(_HELP_GROUP_TAG, key)
        _render_form(key)
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("File-combo change failed")


def _render_form(key: str) -> None:
    """Populate ``_FORM_TAG`` with file-picker buttons, extra-param widgets, and the Load button.

    Parameters
    ----------
    key : str
        Provider key from ``IO_REGISTRY``; must exist.
    """
    descriptor = IO_REGISTRY[key]

    # One file-picker button per file_inputs entry.
    for param, spec in descriptor.get("file_inputs", {}).items():
        label = PARAM_LABEL_MAP.get(param, param)
        required = spec.get("required", False)
        suffix = " *" if required else ""
        with dpg.group(horizontal=True, parent=_FORM_TAG):
            btn = dpg.add_button(
                label=f"Choose {label}{suffix}",
                callback=_make_picker(key, param, spec),
            )
            tooltip = spec.get("tooltip")
            if tooltip:
                with dpg.tooltip(parent=btn):
                    dpg.add_text(tooltip, wrap=300)
            dpg.add_text("(none)", tag=_path_label_tag(param))

    # IO registry uses "combo" as the type name; descriptor_widgets expects "enum".
    for param, spec in descriptor.get("extra_params", {}).items():
        widget_spec = dict(spec)
        if widget_spec.get("type") == "combo":
            widget_spec["type"] = "enum"
        upstream = _resolve_extra_param_upstream(descriptor, param)
        tag = build_param_widget(param, widget_spec, _FORM_TAG, upstream_callable=upstream)
        _S["extra_widgets"][param] = tag

    dpg.add_spacer(height=6, parent=_FORM_TAG)
    load_btn = dpg.add_button(label="Load Data", parent=_FORM_TAG, callback=_on_load)
    prime_button(load_btn)


def _resolve_extra_param_upstream(descriptor: dict, param_name: str):
    """Return the loader callable whose signature documents *param_name*, or None.

    Extra params are forwarded to the provider's loader functions, so the tooltip
    for one is read from that loader's upstream docstring (thin-frontend default,
    no hardcoded tooltip in the registry). The first loader function whose
    signature accepts *param_name* is returned.
    """
    module_path = descriptor.get("module")
    if not module_path:
        return None
    with contextlib.suppress(Exception):
        module = importlib.import_module(module_path)
        for fdesc in descriptor.get("loader_functions", {}).values():
            fn = getattr(module, fdesc.get("function", ""), None)
            if fn is not None and param_name in inspect.signature(fn).parameters:
                return fn
    return None


def _path_label_tag(param: str) -> str:
    """Return the DPG tag for the path-display text widget for *param*."""
    return f"load_file_path_{param}"


def _make_picker(key: str, param: str, spec: dict):
    """Return a DPG button callback that opens a file dialog for *param*.

    Parameters
    ----------
    key : str
        Provider registry key (unused in the callback; captured for clarity).
    param : str
        File-input parameter name; used to namespace the dialog tag and store the result.
    spec : dict
        File-input spec from ``IO_REGISTRY``; ``spec["extensions"]`` populates the
        dialog's extension filters.

    Returns
    -------
    callable
        A zero-argument-compatible DPG callback.
    """

    def _open(sender=None, app_data=None, user_data=None) -> None:
        """Open a file dialog for the enclosing *param* (DPG callback)."""
        try:
            dialog_tag = f"load_file_dialog_{param}"
            if dpg.does_item_exist(dialog_tag):
                dpg.delete_item(dialog_tag)
            with dpg.file_dialog(
                tag=dialog_tag,
                directory_selector=False,
                modal=True,
                width=620,
                height=420,
                callback=_make_picker_result(param),
            ):
                for ext in spec.get("extensions", []):
                    dpg.add_file_extension(ext)
                dpg.add_file_extension(".*")
        except Exception:  # noqa: BLE001 -- DPG callback boundary
            logger.exception("Opening file dialog failed for %s", param)

    return _open


def _make_picker_result(param: str):
    """Return a DPG file-dialog callback that stores the chosen path for *param*.

    Parameters
    ----------
    param : str
        File-input parameter name; used to key ``_S["file_paths"]`` and locate the
        path-display text widget.

    Returns
    -------
    callable
        A DPG callback compatible with ``dpg.file_dialog``'s ``callback`` argument.
    """

    def _result(sender, app_data, user_data) -> None:
        """Persist the chosen path into ``_S["file_paths"]`` and update the label (DPG callback)."""
        try:
            selections = (app_data or {}).get("selections") or {}
            path = next(iter(selections.values()), None) or app_data.get("file_path_name")
            if not path:
                return
            _S["file_paths"][param] = path
            if dpg.does_item_exist(_path_label_tag(param)):
                dpg.set_value(_path_label_tag(param), path)
        except Exception:  # noqa: BLE001 -- DPG callback boundary
            logger.exception("File-dialog result handling failed for %s", param)

    return _result


def _on_load(sender=None, app_data=None, user_data=None) -> None:
    """Validate required file inputs and trigger a synchronous provider load (DPG callback).

    Reads the current provider key and chosen file paths from ``_S``, checks that all
    required ``file_inputs`` have been selected, then calls ``app.load_provider_data``,
    which writes the data store and emits ``Events.DATA_LOADED``.

    Notes
    -----
    Side-effects: writes ``_STATUS_TAG`` widget text; calls ``app.load_provider_data``
    (which mutates the store and emits ``Events.DATA_LOADED``).
    """
    try:
        key = _S["key"]
        if key is None:
            return
        descriptor = IO_REGISTRY[key]
        # Validate required file_inputs are chosen.
        missing = [
            PARAM_LABEL_MAP.get(p, p)
            for p, spec in descriptor.get("file_inputs", {}).items()
            if spec.get("required") and not _S["file_paths"].get(p)
        ]
        if missing:
            dpg.set_value(_STATUS_TAG, f"Missing required file(s): {', '.join(missing)}")
            return

        extra = {p: dpg.get_value(t) for p, t in _S["extra_widgets"].items()}
        app = get_app()
        if app is None:
            dpg.set_value(_STATUS_TAG, "App not ready.")
            return

        dpg.set_value(_STATUS_TAG, "Loading...")
        ok = app.load_provider_data(key, dict(_S["file_paths"]), **extra)
        dpg.set_value(
            _STATUS_TAG,
            f"Loaded {descriptor.get('display_name', key)}." if ok else "Load failed (see log).",
        )
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("File load failed")
        with contextlib.suppress(Exception):
            dpg.set_value(_STATUS_TAG, "Load failed (see log).")
