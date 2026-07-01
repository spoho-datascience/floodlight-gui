"""Shared helpers used by both Load tab sections (file and dataset).

Provides: provider combo display/key maps, the inline provider help button
rebuilt on each combo change, and the app-accessor holder wired to
APP_INITIALIZED.

DPG carve-out: this module imports ``dearpygui`` at module scope because it
lives under ``tabs/`` (the DPG-aware layer); backend modules must not.
"""

from __future__ import annotations

import contextlib

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.registry.io import IO_REGISTRY
from floodlight_gui.tabs._shared.help_popup import render_help_button
from floodlight_gui.theme import PRIMARY


def prime_button(button_tag) -> None:
    """Apply the PRIMARY action color to a button widget.

    Parameters
    ----------
    button_tag : str or int
        DPG tag of the target button.

    Notes
    -----
    Failures are silently suppressed: the theme binding is best-effort and
    a failed tint must not block the caller's control flow.
    """
    with contextlib.suppress(Exception):
        with dpg.theme() as theme, dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (*PRIMARY, 255))
        dpg.bind_item_theme(button_tag, theme)


# --------------------------------------------------------------------------- #
# App capture (Events.APP_INITIALIZED -> module-level holder)
# --------------------------------------------------------------------------- #

_APP_HOLDER: dict[str, object] = {"app": None}


def get_app():
    """Return the captured FloodlightApp instance, or None before APP_INITIALIZED fires.

    Returns
    -------
    FloodlightApp or None
        The live app instance captured from the APP_INITIALIZED event payload,
        or None when called before the event has fired.
    """
    return _APP_HOLDER["app"]


def _on_app_initialized(app=None, **_) -> None:
    """Store the app instance from the APP_INITIALIZED event payload."""
    _APP_HOLDER["app"] = app


bus.subscribe(Events.APP_INITIALIZED, _on_app_initialized)


# --------------------------------------------------------------------------- #
# Combo display<->key mapping
# --------------------------------------------------------------------------- #


def display_label(key: str) -> str:
    """Return the combo display string for a provider key.

    Parameters
    ----------
    key : str
        Key into ``IO_REGISTRY``.

    Returns
    -------
    str
        The ``display_name`` from the descriptor, falling back to the key.

    Raises
    ------
    KeyError
        If ``key`` is not in ``IO_REGISTRY``.
    """
    return IO_REGISTRY[key].get("display_name", key)


def combo_items(keys: list[str]) -> tuple[list[str], dict[str, str]]:
    """Build a combo item list and the reverse display-to-key map for a key list.

    Parameters
    ----------
    keys : list[str]
        Ordered list of ``IO_REGISTRY`` keys to include in the combo.

    Returns
    -------
    items : list[str]
        Display labels in the same order as *keys*.
    reverse : dict[str, str]
        Maps each display label back to its registry key, used to recover the
        key when the combo's selected value changes.
    """
    items: list[str] = []
    reverse: dict[str, str] = {}
    for key in keys:
        label = display_label(key)
        items.append(label)
        reverse[label] = key
    return items, reverse


# --------------------------------------------------------------------------- #
# Inline provider help button
# --------------------------------------------------------------------------- #


def render_provider_help(help_group_tag: str, key: str) -> None:
    """Render the bare ``?`` help button for a provider, inline next to its combo.

    Matches the combo-level help convention used by the Model, Transforms, and
    Metrics tabs (a bare ``?`` beside the picker, no label). The caller clears
    ``help_group_tag`` before calling so the button rebuilds for the newly
    selected provider.

    Parameters
    ----------
    help_group_tag : str
        DPG group tag, adjacent to the provider combo, to render into.
    key : str
        Key into ``IO_REGISTRY`` for the currently selected provider.

    Notes
    -----
    Side-effect: adds the shared ``?`` help button (and tooltip) inside
    ``help_group_tag``; clicking it opens the singleton floodlight-docstring modal.
    """
    render_help_button(
        key,
        IO_REGISTRY[key],
        "IO",
        tag_prefix="load",
        container_hint="",
        parent=help_group_tag,
    )
