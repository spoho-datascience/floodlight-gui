"""Behavioral contracts for ``floodlight_gui.tabs.load.section_helpers``.

Value-triage gate: the only silent-corrupting seam in this module is the
APP_INITIALIZED app holder. If ``_on_app_initialized`` fails to capture the
app, every subsequent Load/Import silently no-ops ("App not ready.") with no
crash and no obvious cause -- a session-corrupting failure with no signal. The
rest of the module (combo display labels, the provider help button, the
KeyError guard) is loud-or-cosmetic on the provider-combo path the user clicks
every session, so it is not guarded here.

Behavioral contracts guarded here
---------------------------------
get_app / APP_INITIALIZED holder
  C1  ``get_app`` returns None before the event fires and the captured app
      instance after the APP_INITIALIZED handler runs.
"""

from __future__ import annotations

import floodlight_gui.tabs.load.section_helpers as common


def test_get_app_reflects_app_initialized_payload():
    """C1: get_app is None until the handler captures the event's app instance."""
    common._APP_HOLDER["app"] = None
    try:
        assert common.get_app() is None
        sentinel = object()
        common._on_app_initialized(app=sentinel)
        assert common.get_app() is sentinel
    finally:
        common._APP_HOLDER["app"] = None
