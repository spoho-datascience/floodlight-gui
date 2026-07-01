"""Singleton help modal for the ``?`` button on every registry descriptor row.

DPG carve-out: this module imports ``dearpygui`` at module scope because it
lives under ``tabs/`` (the DPG-aware layer); backend modules must not.

BLE001 convention: every DPG callback in this module wraps its body in
``try / except Exception: logger.exception(...)`` with a ``# noqa: BLE001``
marker so a callback error can never crash the render loop.

Singleton invariant: exactly one ``_HELP_MODAL_TAG`` window exists in the DPG
item tree at any moment. Re-opening the modal repopulates the existing window
(``delete_item(..., children_only=True)``) rather than recreating it, keeping
the item ID stable across rapid clicks.

Esc handler: registered exactly once per app session via
``_ensure_esc_handler_registered()``. It attaches to the existing global
keyboard registry (created by ``keyboard.register_global_handlers()`` during
``app.initialize()``). The handler is a no-op when no modal is open.
"""

from __future__ import annotations

import contextlib
import logging
import re
import webbrowser
from pathlib import Path
from typing import Any

import dearpygui.dearpygui as dpg

from floodlight_gui.core.help import resolve as _help_resolve

__all__ = ["render_help_button"]

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Module-level constants
# --------------------------------------------------------------------------- #

# Singleton tag for the one-and-only help modal.
_HELP_MODAL_TAG = "_help_modal"

# The Esc-key handler attaches to this registry via ``parent=`` so it shares
# the global keyboard context with playback handlers (opening a new
# handler_registry would create an isolated, unbound registry).
_GLOBAL_KEYBOARD_REGISTRY_TAG = "floodlight_gui_global_keyboard"

# Register-once flag for the Esc handler. Tests MUST reset this to False via
# ``monkeypatch.setattr`` because module state survives across pytest runs.
_esc_handler_registered: bool = False

# Canned line shown when ``ParsedHelp.available is False``. Kept in sync with
# ``_help_resolve._CANNED_UNAVAILABLE_BODY`` by value (two separate constants)
# to avoid importing a private symbol from the sibling module.
_CANNED_UNAVAILABLE_BODY = "Upstream documentation not available."

# Body wrap width (px) for non-table text. Derived from the 700 px modal minus
# approximately 40 px for window padding and the scrollbar.
_BODY_WRAP_PX = 660

# Regex matching a NumPy-style section header block (header + underline) in a
# docstring body. Used by ``_strip_duplicate_sections_from_body`` to locate
# section boundaries before deciding which sections to drop.
_SECTION_HEADER_BLOCK_RE = re.compile(
    r"^[ \t]*(Parameters|Returns|Yields|Receives|Other Parameters|Raises|Warns|"
    r"Warnings|See Also|Notes|References|Examples|Attributes|Methods)"
    r"[ \t]*\r?\n[ \t]*[-=~*]{2,}[ \t]*\r?$",
    re.MULTILINE,
)

# Maps a NumPy section name to the ``ParsedHelp`` field that renders it
# separately. Sections absent from this map (Parameters, Attributes, Methods,
# Raises, ...) have no dedicated render and are always preserved in the body.
_PARSED_SECTION_TO_PH_FIELD: dict[str, str] = {
    "Returns": "returns",
    "Notes": "notes",
    "Examples": "examples",
    "References": "references",
}


def _strip_duplicate_sections_from_body(body: str, ph: Any) -> str:
    """Remove from ``body`` only the sections that ``ph.*`` will re-render.

    For each NumPy-style section header found in ``body``:

    - When the corresponding ``ph.<field>`` is non-empty, drop the whole block
      (header + underline + content up to the next header or end-of-body).
      The block will render via the dedicated section path instead.
    - When ``ph.<field>`` is empty (parser returned nothing), keep the block in
      ``body`` so the user never loses upstream content.

    Sections without dedicated renders (Parameters, Attributes, Methods, Raises)
    are always preserved in ``body``.

    Idempotent on bodies with no section headers (short summaries, empty
    strings, the canned-unavailable text).

    Parameters
    ----------
    body : str
        The full docstring body from ``ParsedHelp.body``.
    ph : ParsedHelp
        The parsed help object; queried for ``returns``, ``notes``,
        ``examples``, ``references`` fields via ``getattr``.

    Returns
    -------
    str
        The body with duplicate sections removed, or the original body if
        no section headers were found.
    """
    if not body:
        return body
    matches = list(_SECTION_HEADER_BLOCK_RE.finditer(body))
    if not matches:
        return body

    parts: list[str] = []
    intro_end = matches[0].start()
    intro = body[:intro_end].rstrip()
    if intro:
        parts.append(intro)

    for i, m in enumerate(matches):
        section_name = m.group(1)
        section_start = m.start()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        ph_field = _PARSED_SECTION_TO_PH_FIELD.get(section_name)
        ph_value = getattr(ph, ph_field, None) if ph_field else None
        if ph_field and ph_value:
            # Will render via dedicated block - drop from body.
            continue
        # Keep block verbatim, including the numpy ``Header\n----`` underline.
        # The dedicated-render path emits a matching underline so both paths
        # render identically.
        block = body[section_start:section_end].rstrip()
        if block:
            parts.append(block)

    return "\n\n".join(parts).rstrip()


# Source footer color - dimmed grey.
_FONT_DIM = (180, 180, 180)

# Module-private monospace font tag, lazily resolved on first modal open.
# Attempt to bind a system monospace font to the source-footer text widget via
# ``dpg.bind_item_font``. When no candidate exists the footer falls back to the
# default proportional font (still dim-colored with the "Source: " prefix).
_MONO_FONT_TAG: int | None = None

_CANDIDATE_MONO_FONTS = (
    "C:/Windows/Fonts/consola.ttf",  # Windows (primary target)
    "C:/Windows/Fonts/cour.ttf",  # Windows fallback (Courier New)
    "/System/Library/Fonts/Menlo.ttc",  # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",  # Linux Debian/Ubuntu
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",  # Linux Fedora/RHEL
)


# --------------------------------------------------------------------------- #
# Monospace font helper
# --------------------------------------------------------------------------- #


def _ensure_mono_font() -> int | None:
    """Lazily register the first available system monospace font.

    Tries each path in ``_CANDIDATE_MONO_FONTS`` in order. Caches the result
    in ``_MONO_FONT_TAG`` so subsequent calls are free. When no candidate
    exists, returns ``None``; callers fall back to the default proportional
    font with dim coloring.

    Returns
    -------
    int or None
        DPG font tag, or ``None`` if no system monospace font was found.

    Notes
    -----
    Side-effect: writes the resolved tag to the module-level ``_MONO_FONT_TAG``.
    """
    global _MONO_FONT_TAG
    if _MONO_FONT_TAG is not None:
        return _MONO_FONT_TAG
    for path in _CANDIDATE_MONO_FONTS:
        if Path(path).exists():
            try:
                with dpg.font_registry():
                    _MONO_FONT_TAG = dpg.add_font(path, 13)
                return _MONO_FONT_TAG
            except Exception:  # noqa: BLE001 -- DPG font loader failures must not break help UI
                continue
    return None  # no system mono font available - caller falls back to default font with dim color


# --------------------------------------------------------------------------- #
# Public entry point: render_help_button
# --------------------------------------------------------------------------- #


def render_help_button(
    descriptor_key: str,
    descriptor: dict[str, Any],
    registry_name: str,
    tag_prefix: str,
    container_hint: str = "",
    parent: str = "",
) -> None:
    """Render an inline ``?`` button and tooltip. Click opens the singleton help modal.

    The button is rendered inline in the caller's current DPG container unless
    ``parent`` is supplied, in which case the button is added directly into that
    container. This lets callers target a specific group without wrapping the
    call in ``with dpg.group(parent=...)``.

    The extractor (``_on_help_click``) is invoked lazily on click, not at
    render time, so this function has no I/O side-effects.

    Parameters
    ----------
    descriptor_key : str
        Stable registry key used to construct a unique child tag
        (e.g. ``"centroid"``).
    descriptor : dict
        The registry descriptor dict, passed verbatim to the extractor on click.
    registry_name : str
        One of ``"MODELS"``, ``"TRANSFORMS"``, ``"METRICS"``, ``"IO"``,
        ``"XY_OPS"``.
    tag_prefix : str
        Caller-provided tag prefix from the descriptor row
        (e.g. ``"model_tab__row_centroid"``).
    container_hint : str
        Routing hint for MODELS (``""`` / ``"init"`` / ``"fit"``); ignored for
        other registries.
    parent : str
        Optional explicit parent container tag. When empty (default) the button
        lands in the caller's current DPG container stack.

    Notes
    -----
    DPG widget tag owned: ``f"{tag_prefix}__help_btn__{descriptor_key}"``.
    """
    button_tag = f"{tag_prefix}__help_btn__{descriptor_key}"

    # Create the button before opening the tooltip context manager so that
    # ``dpg.tooltip(parent=button_tag)`` resolves to an existing widget.
    button_kwargs: dict[str, Any] = {
        "label": "?",
        "small": True,
        "tag": button_tag,
        "user_data": {
            "descriptor": descriptor,
            "registry_name": registry_name,
            "container_hint": container_hint,
        },
        "callback": _on_help_click,
    }
    if parent:
        button_kwargs["parent"] = parent
    dpg.add_button(**button_kwargs)

    # Button now exists; tooltip parent is valid.
    with dpg.tooltip(parent=button_tag):
        dpg.add_text("Show floodlight documentation")


# --------------------------------------------------------------------------- #
# Click callback - BLE001-wrapped DPG callback boundary
# --------------------------------------------------------------------------- #


def _on_help_click(sender, app_data, user_data) -> None:
    """``?`` button click callback. BLE001-wrapped DPG callback boundary.

    Resolves the descriptor lazily, opens or rebuilds the singleton modal,
    and ensures the Esc handler is registered exactly once per app session.
    """
    try:
        ph = _help_resolve.get_descriptor_help(
            descriptor=user_data["descriptor"],
            registry_name=user_data["registry_name"],
            container_hint=user_data["container_hint"],
        )
        _open_help_modal(ph)
        _ensure_esc_handler_registered()
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        descriptor_name = "?"
        with contextlib.suppress(AttributeError):
            descriptor_name = user_data.get("descriptor", {}).get("display_name", "?")
        logger.exception("Help-button click failed for descriptor %s", descriptor_name)


# --------------------------------------------------------------------------- #
# Singleton modal lifecycle
# --------------------------------------------------------------------------- #


def _open_help_modal(ph) -> None:
    """Open or rebuild the singleton help modal with the given ``ParsedHelp``.

    When ``_HELP_MODAL_TAG`` already exists: clears children and repopulates
    inside the existing window (preserves the window's item ID across rapid
    clicks). When it does not exist: creates the window with
    ``on_close=_close_help_modal`` so the DPG X-button deletes the item
    rather than hiding it (the DPG default for X-button is ``show=False``,
    not delete).

    Parameters
    ----------
    ph : ParsedHelp
        Parsed help object returned by ``_help_resolve.get_descriptor_help``.
    """
    title = ph.title or "Help"
    if dpg.does_item_exist(_HELP_MODAL_TAG):
        dpg.delete_item(_HELP_MODAL_TAG, children_only=True)
        _populate_help_modal(ph, parent=_HELP_MODAL_TAG)
    else:
        with dpg.window(
            tag=_HELP_MODAL_TAG,
            modal=True,
            label=title,
            width=700,
            height=600,
            no_resize=True,
            on_close=_close_help_modal,
        ):
            _populate_help_modal(ph, parent=_HELP_MODAL_TAG)


def _close_help_modal(sender=None, app_data=None, user_data=None) -> None:
    """Idempotent close. Safe to call from the X-button, Close button, or Esc.

    Guards on ``does_item_exist`` so a second invocation when the modal is
    already gone is a no-op. Accepts the 3-arg DPG callback shape and
    0-arg invocation from the test harness (all three default to ``None``).
    """
    if dpg.does_item_exist(_HELP_MODAL_TAG):
        dpg.delete_item(_HELP_MODAL_TAG)


# --------------------------------------------------------------------------- #
# 7-section body renderer
# --------------------------------------------------------------------------- #


def _populate_help_modal(ph, parent: str = _HELP_MODAL_TAG) -> None:
    """Render the 7 modal sections in order, suppressing empty ones.

    Section order (available=True): descriptor_summary, upstream body, Returns,
    Examples, Notes, References (each hidden when empty), source footer (when
    ``ph.source_path`` is non-empty), readthedocs button (when source_path
    resolves to a URL).

    When ``ph.available is False``, only the descriptor_summary (if present)
    and the canned unavailability notice are rendered.

    Parameters
    ----------
    ph : ParsedHelp
        Parsed help object to render.
    parent : str
        DPG parent container tag. Defaults to ``_HELP_MODAL_TAG``.
    """
    # Short-circuit for unavailable docstrings.
    if not ph.available:
        if ph.descriptor_summary:
            dpg.add_text(ph.descriptor_summary, wrap=_BODY_WRAP_PX, parent=parent)
            dpg.add_spacer(height=6, parent=parent)
        dpg.add_text(_CANNED_UNAVAILABLE_BODY, parent=parent)
        _add_close_button(parent=parent)
        return

    # available=True: render the full 7-section body inside a scrollable
    # child_window, with Close button and readthedocs link outside the scroll.
    with dpg.child_window(width=-1, height=-50, border=False, parent=parent):
        # 1. Descriptor summary (hide-if-empty).
        if ph.descriptor_summary:
            dpg.add_text(ph.descriptor_summary, wrap=_BODY_WRAP_PX)
            dpg.add_spacer(height=6)

        # 2. Upstream body. Strip only the sections that are also populated in
        # ph.returns / ph.notes / ph.examples / ph.references - those render
        # separately below. Sections without dedicated renders (Parameters,
        # Attributes, Methods) and sections where the parser returned empty are
        # preserved so the user never loses upstream content.
        dpg.add_text(_strip_duplicate_sections_from_body(ph.body, ph), wrap=_BODY_WRAP_PX)
        dpg.add_spacer(height=10)

        # 3. Returns (hide-if-empty). Rendered as a single ``add_text`` call
        # (header + NumPy underline + content) so its layout matches body-kept
        # sections, which also arrive as one multi-line text widget.
        if ph.returns:
            dpg.add_spacer(height=6)
            dpg.add_text(
                f"Returns\n{'-' * len('Returns')}\n{ph.returns}",
                wrap=_BODY_WRAP_PX,
            )

        # 4-6. Examples / Notes / References (each hide-if-empty).
        for header, value in (
            ("Examples", ph.examples),
            ("Notes", ph.notes),
            ("References", ph.references),
        ):
            if value:
                dpg.add_spacer(height=6)
                dpg.add_text(
                    f"{header}\n{'-' * len(header)}\n{value}",
                    wrap=_BODY_WRAP_PX,
                )

        # 7. Source footer (hide-if-empty). Attempt monospace binding via
        # ``_ensure_mono_font()``; fall back to dim-color default font when no
        # system mono font exists.
        if ph.source_path:
            dpg.add_spacer(height=10)
            footer_tag = dpg.add_text(f"Source: {ph.source_path}", color=_FONT_DIM)
            mono = _ensure_mono_font()
            if mono is not None:
                dpg.bind_item_font(footer_tag, mono)
            # When mono is None, the footer uses the default proportional font
            # with dim color and the "Source: " prefix.

    # Close button outside the scrollable region so it stays pinned.
    _add_close_button(parent=parent)

    # readthedocs deeplink: placed below Close, outside child_window, so it
    # stays pinned in the modal footer regardless of body scroll.
    rtd_url = _build_rtd_url_from_source_path(ph.source_path)
    if rtd_url:
        dpg.add_spacer(height=4, parent=parent)
        dpg.add_button(
            label="View documentation on readthedocs ↗",
            callback=_open_rtd_url,
            user_data=rtd_url,
            parent=parent,
        )


def _add_close_button(parent: str) -> None:
    """Render the Close button at the bottom of the modal (third close affordance)."""
    dpg.add_button(label="Close", callback=_close_help_modal, parent=parent)


def _open_rtd_url(sender, app_data, user_data) -> None:  # noqa: ARG001 -- DPG callback signature
    """DPG callback wrapper for ``webbrowser.open(user_data)``.

    ``webbrowser.open`` can raise in headless, sandboxed, or browser-less
    environments. This wrapper prevents the exception from propagating into
    the DPG render loop.
    """
    try:
        webbrowser.open(user_data)
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("Failed to open readthedocs URL: %r", user_data)


# --------------------------------------------------------------------------- #
# readthedocs URL builder
# --------------------------------------------------------------------------- #


def _build_rtd_url_from_source_path(source_path: str) -> str | None:
    """Build a readthedocs deeplink URL from ``ph.source_path``.

    Returns ``None`` when ``source_path`` is empty or has fewer than 3 dotted
    segments (the button is hidden in those cases).

    URL scheme::

        https://floodlight.readthedocs.io/en/stable/modules/{category}/{module}.html#{path}

    where ``{category}`` is the second dotted segment (e.g. ``"models"``,
    ``"transforms"``, ``"io"``, ``"metrics"``) and ``{module}`` is the third
    (e.g. ``"kinematics"``, ``"datasets"``, ``"filter"``).

    Parameters
    ----------
    source_path : str
        Dotted Python path from ``ParsedHelp.source_path``
        (e.g. ``"floodlight.models.kinematics.DistanceModel"``).

    Returns
    -------
    str or None
        A fully-qualified readthedocs URL, or ``None`` if ``source_path``
        has too few segments to build one.

    Examples
    --------
    >>> _build_rtd_url_from_source_path("floodlight.models.kinematics.DistanceModel")
    'https://floodlight.readthedocs.io/en/stable/modules/models/kinematics.html#...'
    >>> _build_rtd_url_from_source_path("floodlight.io.dfl")
    'https://floodlight.readthedocs.io/en/stable/modules/io/dfl.html#floodlight.io.dfl'
    """
    if not source_path:
        return None
    parts = source_path.split(".")
    if len(parts) < 3:
        return None
    category = parts[1]
    module = parts[2]
    return (
        f"https://floodlight.readthedocs.io/en/stable/modules/"
        f"{category}/{module}.html#{source_path}"
    )


# --------------------------------------------------------------------------- #
# Esc-key handler: register-once + short-circuit callback
# --------------------------------------------------------------------------- #


def _on_esc_pressed(sender=None, app_data=None, user_data=None) -> None:
    """Esc-key handler: closes the help modal when it is open.

    Short-circuits via ``does_item_exist(_HELP_MODAL_TAG)`` so the handler is
    a no-op when no help modal is open, avoiding interference with playback
    keyboard handlers on the same registry.
    """
    if not dpg.does_item_exist(_HELP_MODAL_TAG):
        return  # Modal not open - no-op.
    try:
        _close_help_modal()
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("Esc-key close failed")


def _ensure_esc_handler_registered() -> None:
    """Register the Esc handler exactly once per app session.

    Attaches to the existing ``floodlight_gui_global_keyboard`` registry via
    the ``parent=`` kwarg rather than opening a fresh ``handler_registry()``
    context. Opening a new context would create an isolated, unbound registry
    that never receives DPG events.

    When the parent registry tag is absent (e.g. ``app.initialize()`` has not
    yet been called, or in DPG-free unit tests), logs a warning and returns
    without registering.

    The module-level ``_esc_handler_registered`` flag prevents double
    registration. Tests covering this path must reset the flag to ``False``
    via ``monkeypatch.setattr`` because module state survives across pytest
    runs.
    """
    global _esc_handler_registered
    if _esc_handler_registered:
        return
    if not dpg.does_item_exist(_GLOBAL_KEYBOARD_REGISTRY_TAG):
        logger.warning(
            "Cannot register help-modal Esc handler: keyboard registry %s "
            "missing (expected app.initialize() to have created it).",
            _GLOBAL_KEYBOARD_REGISTRY_TAG,
        )
        return
    dpg.add_key_press_handler(
        key=dpg.mvKey_Escape,
        callback=_on_esc_pressed,
        parent=_GLOBAL_KEYBOARD_REGISTRY_TAG,
    )
    _esc_handler_registered = True
