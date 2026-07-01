"""Shared fixtures for the Load-tab suite.

Extends the project ``make_dpg_stub`` recorder with the handful of DPG
surface points the Load-tab modules touch that the base stub omits
(``add_progress_bar``, ``file_dialog``, ``set_frame_callback`` and friends,
theme binding). Each Load-tab module imports ``dearpygui.dearpygui as dpg`` at
module scope, so tests swap the module's ``dpg`` attribute for this recorder.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from tests._dpg_stub import make_dpg_stub


def make_load_dpg(**kwargs):
    """Return a ``make_dpg_stub`` recorder augmented for the Load-tab modules.

    The Load tab calls a few DPG entry points the base stub does not define.
    Each addition keeps the base recorder's contract: container managers log
    ``{kind}_enter`` / ``{kind}_exit`` and auto-register a ``tag=`` kwarg;
    add-widget calls append ``(name, args, kwargs)`` and return a synthetic tag.

    Parameters
    ----------
    **kwargs
        Forwarded to ``make_dpg_stub`` (``calls``, ``existing_items``,
        ``values``).

    Returns
    -------
    types.SimpleNamespace
        The shared recorder with extra Load-tab surface points attached.
    """
    stub = make_dpg_stub(**kwargs)
    calls = stub.calls
    existing = stub.existing_items

    @contextmanager
    def _ctx(kind, *args, **kw):
        tag = kw.get("tag")
        if tag:
            existing.add(tag)
        calls.append((f"{kind}_enter", args, kw))
        try:
            yield None
        finally:
            calls.append((f"{kind}_exit", args, kw))

    def _record(kind):
        def _fn(*args, **kw):
            tag = kw.get("tag")
            if tag:
                existing.add(tag)
                default = kw.get("default_value")
                if default is not None and tag not in stub.values:
                    stub.values[tag] = default
            calls.append((kind, args, kw))
            return f"{kind}_{len(calls)}"

        return _fn

    # Container managers used by the worker modal and pitch/file forms.
    stub.file_dialog = lambda *a, **kw: _ctx("file_dialog", *a, **kw)
    stub.theme = lambda *a, **kw: _ctx("theme", *a, **kw)
    stub.theme_component = lambda *a, **kw: _ctx("theme_component", *a, **kw)

    # Add-widget recorders the base stub omits.
    stub.add_progress_bar = _record("add_progress_bar")
    stub.add_file_extension = _record("add_file_extension")
    stub.add_theme_color = _record("add_theme_color")
    stub.add_input_text = _record("add_input_text")

    # No-op theme binding (best-effort in source, wrapped in suppress).
    stub.bind_item_theme = lambda *a, **kw: calls.append(("bind_item_theme", a, kw))

    # Frame-callback scheduling: record the requested frame + callable so tests
    # can drive the poll/finish chain deterministically without a real loop.
    stub.scheduled = []

    def _set_frame_callback(frame, callback):
        stub.scheduled.append((frame, callback))
        calls.append(("set_frame_callback", (frame,), {}))

    stub.set_frame_callback = _set_frame_callback
    stub.get_frame_count = lambda: 0

    # Theme constants referenced by prime_button.
    stub.mvButton = 1
    stub.mvThemeCol_Button = 2

    return stub


@pytest.fixture
def dpg_stub():
    """Yield a fresh Load-tab DPG recorder per test (no cross-test leakage)."""
    return make_load_dpg()
