"""Public-dataset section of the Load tab: match selection and download.

Covers EIGD-H and IDSSE providers.

DPG-aware: imports ``dearpygui`` at module scope (tabs layer; backend modules must not).
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.engine.load_data import available_matches
from floodlight_gui.registry.io import dataset_provider_keys
from floodlight_gui.theme import INFO

from .dataset_worker import start_dataset_download
from .section_helpers import (
    combo_items,
    get_app,
    prime_button,
    render_provider_help,
)

logger = logging.getLogger(__name__)

_COMBO_TAG = "load_dataset_combo"
_HELP_GROUP_TAG = "load_dataset_help_group"
_FORM_TAG = "load_dataset_form"
_MATCH_COMBO_TAG = "load_dataset_match_combo"
_STATUS_TAG = "load_dataset_status"

# Module-level session state: reverse maps combo display labels to provider keys and
# match labels to match ids; key holds the currently selected provider key.
_S: dict = {"reverse": {}, "key": None, "match_reverse": {}}


def build(parent_tag: str) -> None:
    """Render the public-dataset section inside ``parent_tag``.

    Builds a collapsing header containing a dataset combo, a preamble group, a
    form group, and a status text. The form group is populated by ``_render_form``
    when the user picks a dataset.

    Parameters
    ----------
    parent_tag : str
        DPG container tag to render into.

    Notes
    -----
    Side-effects: creates DPG tags ``_COMBO_TAG``,
    ``_FORM_TAG``, ``_MATCH_COMBO_TAG`` (lazily, via ``_render_form``), and
    ``_STATUS_TAG``. Writes
    ``_S["reverse"]`` with the display-to-key mapping for all dataset providers.
    """
    keys = dataset_provider_keys()
    items, reverse = combo_items(keys)
    _S["reverse"] = reverse

    with dpg.collapsing_header(
        label="Import public dataset", default_open=True, closable=False, parent=parent_tag
    ):
        dpg.add_text("Select a public dataset:", color=INFO)
        with dpg.group(horizontal=True):
            dpg.add_combo(
                items=items, tag=_COMBO_TAG, width=320, callback=_on_combo, default_value=""
            )
            dpg.add_group(tag=_HELP_GROUP_TAG)
        dpg.add_group(tag=_FORM_TAG)
        dpg.add_text("", tag=_STATUS_TAG, wrap=560)


def _on_combo(sender, app_data, user_data) -> None:
    """Update session state and re-render the form when the dataset combo changes (DPG callback)."""
    try:
        key = _S["reverse"].get(app_data)
        _S["key"] = key
        _S["match_reverse"] = {}
        dpg.delete_item(_FORM_TAG, children_only=True)
        dpg.delete_item(_HELP_GROUP_TAG, children_only=True)
        dpg.set_value(_STATUS_TAG, "")
        if key is None:
            return
        render_provider_help(_HELP_GROUP_TAG, key)
        _render_form(key)
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("Dataset-combo change failed")


def _render_form(key: str) -> None:
    """Populate the form group with the match combo and Import button.

    Parameters
    ----------
    key : str
        Provider key from ``IO_REGISTRY`` identifying the selected dataset.
    """
    matches = available_matches(key)
    labels = [m.get("label", str(m.get("id"))) for m in matches]
    _S["match_reverse"] = {m.get("label", str(m.get("id"))): m.get("id") for m in matches}

    with dpg.group(horizontal=True, parent=_FORM_TAG):
        dpg.add_text("Match:")
        dpg.add_combo(
            items=labels,
            tag=_MATCH_COMBO_TAG,
            width=380,
            default_value=labels[0] if labels else "",
        )

    dpg.add_spacer(height=6, parent=_FORM_TAG)
    btn = dpg.add_button(label="Import Dataset", parent=_FORM_TAG, callback=_on_import)
    prime_button(btn)


def _resolve_match_id():
    """Return the selected match id from the match combo, or None when absent."""
    if dpg.does_item_exist(_MATCH_COMBO_TAG):
        return _S["match_reverse"].get(dpg.get_value(_MATCH_COMBO_TAG))
    return None


def _on_import(sender=None, app_data=None, user_data=None) -> None:
    """Resolve the selected match id and start the background dataset download (DPG callback)."""
    try:
        key = _S["key"]
        if key is None:
            return
        app = get_app()
        if app is None:
            dpg.set_value(_STATUS_TAG, "App not ready.")
            return
        match_id = _resolve_match_id()
        start_dataset_download(app, key, match_id, _STATUS_TAG)
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("Dataset import launch failed")
        with contextlib.suppress(Exception):
            dpg.set_value(_STATUS_TAG, "Import failed to start (see log).")
