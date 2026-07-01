"""Three render-into-parent helpers for empty, loading, and error states.

Each tab calls one helper inside its result-zone child_window. The tab owns
its state machine (clear the parent, call the right helper). This module
only renders; it holds no hidden state.

The tab-local EventBus subscriber decides which state is active. The loading
helper is intended for known-long operations (dataset download, video export,
model fit on a full match).

DPG carve-out: this module imports ``dearpygui`` at module scope because it
lives under ``tabs/`` (the DPG-aware layer); backend modules must not.
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from floodlight_gui.theme import ERROR

__all__ = ["render_empty", "render_loading", "render_error"]


def render_empty(parent_tag: str, message: str, cta_label: str = "", cta_callback=None) -> None:
    """Replace *parent_tag*'s children with an empty-state view.

    Parameters
    ----------
    parent_tag : str
        DPG container tag (typically a child_window or group).
    message : str
        Plain-language status text (e.g. "No data loaded - go to Load tab").
    cta_label : str, optional
        Label for an optional call-to-action button. Omit or leave empty to
        suppress the button.
    cta_callback : callable or None
        Callback invoked when the CTA button is clicked. Required when
        *cta_label* is non-empty; ignored otherwise.

    Notes
    -----
    Side-effect: deletes all existing children of *parent_tag* before
    rendering the new content.
    """
    dpg.delete_item(parent_tag, children_only=True)
    with dpg.group(parent=parent_tag):
        dpg.add_text(message)
        if cta_label and cta_callback is not None:
            dpg.add_button(label=cta_label, callback=cta_callback)


def render_loading(parent_tag: str, message: str, cancellable: bool = False) -> None:
    """Replace *parent_tag*'s children with a loading-state view.

    Parameters
    ----------
    parent_tag : str
        DPG container tag.
    message : str
        Plain-language progress text.
    cancellable : bool, default False
        When True, render a Cancel button tagged
        ``{parent_tag}__loading_cancel_btn``. The caller must attach its
        callback via ``dpg.configure_item`` after this returns. Synchronous
        operations have no cancel path; only async operations (dataset
        download, video export) should pass True.

    Notes
    -----
    Side-effect: deletes all existing children of *parent_tag* before
    rendering the new content. When *cancellable* is True, owns the DPG
    widget tag ``{parent_tag}__loading_cancel_btn``.
    """
    dpg.delete_item(parent_tag, children_only=True)
    with dpg.group(parent=parent_tag):
        dpg.add_text(message)
        if cancellable:
            dpg.add_button(label="Cancel", tag=f"{parent_tag}__loading_cancel_btn")


def render_error(parent_tag: str, exc: BaseException, suggested_fix: str | None = None) -> None:
    """Replace *parent_tag*'s children with an error-state view.

    Parameters
    ----------
    parent_tag : str
        DPG container tag.
    exc : BaseException
        The caught exception; its message is formatted via
        ``friendly_error_message`` and the traceback is shown in a collapsing
        detail header.
    suggested_fix : str or None
        Optional plain-language hint shown below the error message.

    Notes
    -----
    Side-effect: deletes all existing children of *parent_tag* before
    rendering the new content.
    """
    # Lazy import to break the module-load import cycle with error_helpers.
    import traceback as _tb

    from floodlight_gui.tabs._shared.error_helpers import friendly_error_message

    dpg.delete_item(parent_tag, children_only=True)
    with dpg.group(parent=parent_tag):
        dpg.add_text(friendly_error_message(exc), color=ERROR)
        if suggested_fix:
            dpg.add_text(suggested_fix)
        # "Show details" is a collapsing section: toggling is the correct
        # affordance for an inline traceback (closable=True default is intentional).
        with dpg.collapsing_header(label="Show details", default_open=False):
            dpg.add_text(_tb.format_exception_only(type(exc), exc)[0])
