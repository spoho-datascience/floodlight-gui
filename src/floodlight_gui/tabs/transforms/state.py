"""Shared mutable state for the transforms tab subpackage.

All transforms sub-modules (controls, select, params, execute, results) import
from this module so they share a single set of references. Sub-modules must
read and write through ``state.<name>`` and must not redeclare these names
locally (that would create a private copy that diverges from the shared state).
"""

from __future__ import annotations

# Live FloodlightApp reference.
# None until controls._on_app_initialized receives the first DATA_LOADED event.
# Rebound as ``state.app_instance = app``; never reassign via ``global`` in
# sub-modules.
app_instance = None

# Cached internal-form period key, updated by the period combo callback
# in controls.py. Internal form is the key used by DataStore / XY accessors
# (distinct from the display label shown in the UI).
_transforms_selected_period_internal: str | None = None
