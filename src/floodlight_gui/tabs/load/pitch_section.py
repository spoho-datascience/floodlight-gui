"""Pitch builder section for the Load tab: collapsible form to construct or replace a Pitch.

Builds a ``Pitch(**fields)`` from xlim/ylim limits and optional extras. The
template combo pre-fills the editable form from ``Pitch.from_template(name)``
so the user starts from a provider's defaults and can adjust any field.
"Create Pitch" swaps the constructed Pitch into the loaded data and re-fires
DATA_LOADED via the single producer path (``app.replace_pitch``).

DPG-aware: this module imports ``dearpygui`` at module scope (lives under ``tabs/``).

Template names are introspected from ``Pitch.from_template`` source (walks its
``template_name == "x"`` comparisons, falls back to its docstring brace-list,
then a hardcoded list), plus a "_custom_" option.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import re

import dearpygui.dearpygui as dpg
from floodlight.core.pitch import Pitch

from floodlight_gui.tabs._shared.descriptor_widgets import build_param_widget
from floodlight_gui.theme import INFO

from .section_helpers import get_app, prime_button

logger = logging.getLogger(__name__)

_CUSTOM = "_custom_"
_TEMPLATE_COMBO_TAG = "load_pitch_template_combo"
_FORM_TAG = "load_pitch_form"
_STATUS_TAG = "load_pitch_status"

# Default length/width used to introspect a preset for the length/width-required
# templates (dfl/tracab/statsperform*/secondspectrum). The user edits the
# resulting xlim/ylim limits afterwards; these defaults only seed the form.
_PRESET_LW = (105.0, 68.0)

_FALLBACK_TEMPLATES = [
    "dfl",
    "opta",
    "statsperform_open",
    "secondspectrum",
    "statsperform_event",
    "statsperform_tracking",
    "tracab",
    "eigd",
    "statsbomb",
]

# build_param_widget spec per Pitch.__init__ param. xlim/ylim are the limits
# (primary); length/width/unit/boundaries/sport are optional. Enums get guided
# dropdowns so unit/boundaries/sport can't be set to invalid free-text values.
_PARAM_SPECS = {
    "xlim": {"type": "tuple[float, float]", "default": (-52.5, 52.5)},
    "ylim": {"type": "tuple[float, float]", "default": (-34.0, 34.0)},
    "unit": {"type": "enum", "options": ["m", "cm", "percent", "normed"], "default": "m"},
    "boundaries": {"type": "enum", "options": ["fixed", "flexible"], "default": "fixed"},
    # Optional numerics rendered as blank-able text inputs (empty -> omitted, so
    # they are genuinely "available but not necessary"); parsed to float on build.
    "length": {"type": "string", "default": ""},
    "width": {"type": "string", "default": ""},
    "sport": {"type": "enum", "options": ["football", "handball", "None"], "default": "football"},
}

# Params treated as optional floats: blank input -> omit; non-blank -> float().
_OPTIONAL_FLOAT = {"length", "width"}

_S: dict = {"widgets": {}}


def discover_templates() -> list[str]:
    """Template names from from_template source; fall back to docstring, then hardcode."""
    try:
        src = inspect.getsource(Pitch.from_template)
        names = re.findall(r'template_name\s*==\s*"([a-z_]+)"', src)
        if names:
            return names
    except (OSError, TypeError):
        pass
    doc = inspect.getdoc(Pitch.from_template) or ""
    brace = re.search(r"\{([^}]*)\}", doc)
    if brace:
        names = re.findall(r"'([a-z_]+)'", brace.group(1))
        if names:
            return names
    return list(_FALLBACK_TEMPLATES)


def _template_preset(name: str) -> Pitch | None:
    """Construct a preset Pitch for *name* to pre-fill the form, or None.

    Fixed-geometry templates (opta/eigd/statsbomb) build with no kwargs;
    length/width-required templates are seeded with ``_PRESET_LW`` so the user
    gets sensible starting limits to tweak. Returns None if construction fails.
    """
    with contextlib.suppress(Exception):
        return Pitch.from_template(name)
    with contextlib.suppress(Exception):
        return Pitch.from_template(name, length=_PRESET_LW[0], width=_PRESET_LW[1])
    return None


def build(parent_tag: str) -> None:
    """Mount the collapsible pitch builder into ``parent_tag``."""
    templates = discover_templates()
    with dpg.collapsing_header(
        label="Build / Replace Pitch", default_open=False, closable=False, parent=parent_tag
    ):
        dpg.add_text(
            "Construct a Pitch from its xlim/ylim limits. Pick a template to "
            "pre-fill provider conventions, then tweak any field. length/width "
            "are optional.",
            color=INFO,
            wrap=560,
        )
        with dpg.group(horizontal=True):
            dpg.add_text("Template:")
            dpg.add_combo(
                items=[*templates, _CUSTOM],
                tag=_TEMPLATE_COMBO_TAG,
                width=260,
                default_value=templates[0] if templates else _CUSTOM,
                callback=_on_template_change,
            )

        dpg.add_group(tag=_FORM_TAG)
        _build_form()

        dpg.add_spacer(height=6)
        btn = dpg.add_button(label="Create Pitch", callback=_on_create)
        prime_button(btn)

        dpg.add_text("", tag=_STATUS_TAG, wrap=560)

    # Seed the form from the initially-selected template preset.
    _on_template_change(None, dpg.get_value(_TEMPLATE_COMBO_TAG), None)


def _build_form() -> None:
    """Render one editable widget per Pitch.__init__ param into the form group."""
    dpg.delete_item(_FORM_TAG, children_only=True)
    _S["widgets"] = {}
    for name in inspect.signature(Pitch.__init__).parameters:
        if name == "self":
            continue
        spec = dict(_PARAM_SPECS.get(name, {"type": "string", "default": None}))
        tag = build_param_widget(
            name, spec, _FORM_TAG, upstream_callable=Pitch, param_container="init_params"
        )
        _S["widgets"][name] = (tag, spec["type"])


def _on_template_change(sender, app_data, user_data) -> None:
    """Pre-fill the form from the selected template preset (or defaults for custom)."""
    try:
        preset = None if app_data == _CUSTOM else _template_preset(app_data)
        _fill_form(preset)
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("Pitch template change failed")


def _fill_form(preset: Pitch | None) -> None:
    """Set every form widget from *preset* attrs (or the spec default if None/missing)."""
    for name, (tag, ptype) in _S["widgets"].items():
        default = _PARAM_SPECS.get(name, {}).get("default")
        val = default if preset is None else getattr(preset, name, default)
        if ptype == "tuple[float, float]":
            pair = val if isinstance(val, (tuple, list)) and len(val) == 2 else default
            with contextlib.suppress(Exception):
                dpg.set_value(f"{tag}__lo", float(pair[0]))
                dpg.set_value(f"{tag}__hi", float(pair[1]))
        elif name in _OPTIONAL_FLOAT:
            # Blank when the preset omits it (None); else the number as text.
            text = f"{val:g}" if isinstance(val, (int, float)) else ""
            with contextlib.suppress(Exception):
                dpg.set_value(tag, text)
        elif name == "sport":
            with contextlib.suppress(Exception):
                dpg.set_value(tag, val if val in ("football", "handball") else "None")
        else:  # enum (unit/boundaries) or string
            with contextlib.suppress(Exception):
                dpg.set_value(tag, val if val is not None else (default or ""))


def _collect_kwargs() -> dict:
    """Read the form into Pitch(**kwargs). 'None'/empty fall through to defaults."""
    kwargs: dict = {}
    for name, (tag, ptype) in _S["widgets"].items():
        if ptype == "tuple[float, float]":
            kwargs[name] = (dpg.get_value(f"{tag}__lo"), dpg.get_value(f"{tag}__hi"))
            continue
        val = dpg.get_value(tag)
        if name in _OPTIONAL_FLOAT:
            # Optional numeric: blank -> omit (use floodlight's None default);
            # non-blank -> float (silently omit an unparseable value).
            s = str(val).strip()
            if s:
                with contextlib.suppress(ValueError, TypeError):
                    kwargs[name] = float(s)
            continue
        # "None" (sport enum) and empty strings fall through to floodlight defaults.
        if val == "None" or (ptype == "string" and val == ""):
            val = None
        if val is not None:
            kwargs[name] = val
    return kwargs


def _build_pitch() -> Pitch:
    """Construct a Pitch directly from the form fields (xlim/ylim + optionals)."""
    return Pitch(**_collect_kwargs())


def _on_create(sender=None, app_data=None, user_data=None) -> None:
    """Collect form fields, construct a Pitch, and replace the active pitch.

    Notes
    -----
    Side-effects: calls ``app.replace_pitch(pitch)``, which swaps the Pitch into
    ``app.store.loaded_data`` and re-fires ``Events.DATA_LOADED``. DATA_LOADED is
    emitted through ``app.replace_pitch`` only (single producer path); this
    callback never emits it directly.
    """
    try:
        app = get_app()
        if app is None or getattr(app, "store", None) is None or app.store.loaded_data is None:
            dpg.set_value(_STATUS_TAG, "Load data first, then attach a pitch.")
            return

        pitch = _build_pitch()

        # Replace the pitch and re-fire DATA_LOADED via the single producer path.
        app.replace_pitch(pitch)
        dpg.set_value(_STATUS_TAG, f"Pitch created (xlim={pitch.xlim}, ylim={pitch.ylim}).")
    except Exception as exc:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("Create Pitch failed")
        with contextlib.suppress(Exception):
            dpg.set_value(_STATUS_TAG, f"Could not create pitch: {exc}")
