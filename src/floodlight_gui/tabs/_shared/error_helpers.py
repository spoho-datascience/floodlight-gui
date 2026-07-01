"""Friendly error catalog and modal renderer for DPG callback boundaries.

Every DPG callback that catches a broad exception (BLE001 boundary) routes here
to produce consistent user-facing messages. Two contracts this module upholds:

- No ``Events.ERROR_OCCURRED`` is emitted. Error surfacing stays local to the
  failing callback site; the status bar reflects errors via EXPORT_REQUESTED and
  per-tab status text.
- Raw tracebacks are always logged via ``logger.exception`` before or during any
  modal render, so no error is silently swallowed.

DPG carve-out: this module imports ``dearpygui`` at module scope because it lives
under ``tabs/`` (the DPG-aware layer); backend modules must not.
"""

from __future__ import annotations

import logging
import traceback as _tb

import dearpygui.dearpygui as dpg

from floodlight_gui.theme import ERROR

__all__ = ["friendly_error_message", "show_error_modal"]

logger = logging.getLogger(__name__)


def _import_name(exc: ImportError) -> str:
    """Extract the missing module name from an ImportError.

    Prefer the explicit ``name=`` attribute (set by the import machinery).
    Fall back to the first positional arg so that ``ImportError('foo')``
    callsites still yield a readable module name.
    """
    return (
        getattr(exc, "name", None) or (exc.args[0] if exc.args else None) or str(exc) or "<unknown>"
    )


def _file_not_found_path(exc: FileNotFoundError) -> str:
    """Extract the missing path from a FileNotFoundError.

    ``FileNotFoundError`` sets ``.filename`` when raised via
    ``OSError(errno, msg, path)``. When constructed positionally,
    ``.filename`` is None but ``args[0]`` holds the path string.
    """
    return getattr(exc, "filename", None) or (exc.args[0] if exc.args else None) or "<file>"


_FRIENDLY_TEMPLATES = {
    ImportError: lambda exc: f"Missing dependency: install {_import_name(exc)}",
    FileNotFoundError: lambda exc: f"Couldn't find {_file_not_found_path(exc)}",
    KeyError: lambda exc: f"Required key not found: {exc.args[0] if exc.args else '<unknown>'}",
    PermissionError: lambda exc: (
        "Permission denied — check the file is not open in another program"
    ),
    ValueError: lambda exc: f"Invalid value: {exc}",
}


def friendly_error_message(exc: BaseException, context: str = "") -> str:
    """Return a 1-2 sentence plain-language error message for a caught exception.

    Looks up a human-readable template from the internal catalog. Falls back to
    ``ExcType: message`` for unmapped types. An optional situational hint is
    appended on a new line.

    Parameters
    ----------
    exc : BaseException
        The caught exception.
    context : str, optional
        Situational hint appended after the base message
        (e.g. "Try the Load tab first.").

    Returns
    -------
    str
        A short plain-language message, optionally followed by *context*.
    """
    template = _FRIENDLY_TEMPLATES.get(type(exc))
    base = template(exc) if template else f"{type(exc).__name__}: {exc}"
    return f"{base}\n{context}" if context else base


_MODAL_TAG_COUNTER = {"n": 0}


def show_error_modal(
    parent_tag: str, exc: BaseException, context: str = "", suggested_fix: str | None = None
) -> None:
    """Show a modal popup with the friendly message and a collapsible traceback.

    Logs the full traceback via ``logger.exception`` before rendering the modal
    so the error is always captured regardless of whether the user expands the
    details pane.

    Each call creates a uniquely tagged top-level DPG window (``_error_modal_N``).
    DPG modals do not accept a ``parent=`` argument; *parent_tag* is logged only
    for callsite traceability.

    Parameters
    ----------
    parent_tag : str
        Identifier of the DPG container at the callsite (logged for traceability;
        not passed to DPG as a parent).
    exc : BaseException
        The caught exception.
    context : str, optional
        Situational hint forwarded to ``friendly_error_message``.
    suggested_fix : str or None, optional
        Optional actionable plain-language fix shown below the error text.

    Notes
    -----
    Side-effects:

    - Calls ``logger.exception`` (writes to the Python logging subsystem).
    - Creates a new top-level DPG modal window tagged ``_error_modal_N``.
    - Increments the module-level ``_MODAL_TAG_COUNTER`` to ensure tag uniqueness.
    """
    logger.exception("DPG callback boundary caught: %s", exc)
    _MODAL_TAG_COUNTER["n"] += 1
    modal_tag = f"_error_modal_{_MODAL_TAG_COUNTER['n']}"
    with dpg.window(
        label="Error", modal=True, tag=modal_tag, width=480, height=240, no_close=False
    ):
        dpg.add_text(friendly_error_message(exc, context), wrap=460, color=ERROR)
        if suggested_fix:
            dpg.add_text(suggested_fix, wrap=460)
        # The "Show details" collapsing header is intentionally closable by default:
        # opening reveals the traceback, closing hides it. closable=True is correct.
        with dpg.collapsing_header(label="Show details", default_open=False):
            dpg.add_text(_tb.format_exception_only(type(exc), exc)[0], wrap=460)
            dpg.add_text(_tb.format_exc(), wrap=460)
        dpg.add_button(label="Close", callback=lambda s=modal_tag: dpg.delete_item(s))
