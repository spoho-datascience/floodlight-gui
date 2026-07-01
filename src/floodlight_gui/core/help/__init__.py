"""DPG-free help-text backend: resolve + parse upstream floodlight docstrings
for the in-GUI help button. Lives under ``core/`` (the DPG-free backend):
``resolve`` does descriptor->ParsedHelp, ``docstring_parser`` the raw text.

Also owns ``_INIT_BOILERPLATE`` -- Python's inherited ``object.__init__``
docstring. Used by both ``tabs/_shared/descriptor_widgets.resolve_tooltip``
and ``floodlight_gui.core.help.resolve`` to detect the "class did not
author its own __init__ docstring" signal and fall back to the class-level
docstring (the canonical floodlight 1.2 pattern where the Parameters block
lives on the class, not on __init__).

Import-isolation invariant: this module MUST NOT import ``dearpygui`` or
``floodlight.<sub>`` at module scope. Stdlib-only.
"""

from __future__ import annotations

# Python's inherited boilerplate __init__ docstring. When introspecting
# ``cls.__init__`` for an ``init_params`` lookup, this is the signal that the
# class did NOT author its own __init__ docstring and the caller should fall
# back to the class-level docstring.
_INIT_BOILERPLATE: str = "Initialize self.  See help(type(self)) for accurate signature."
