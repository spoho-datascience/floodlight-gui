"""Semantic UI color-role constants: single source of truth for named RGB roles.

No imports of any kind are permitted here. This module is stdlib-free and
DPG-free; consumers import named constants instead of raw ``color=(R, G, B)``
literals.

Each constant is a 3-tuple ``(R, G, B)`` matching DPG's ``color=(R, G, B)``
signature. Callsites that need a 4-tuple append the alpha themselves.

Roles:

- ``PRIMARY``: muted blue, primary-action buttons (Load/Run/Apply)
- ``SECONDARY``: grey, secondary buttons
- ``INFO``: dim blue-grey, intro text and hints
- ``WARNING``: amber, non-blocking cautions
- ``ERROR``: muted red, error states
- ``DISABLED``: dim grey, inactive controls
"""

from __future__ import annotations

__all__ = ["PRIMARY", "SECONDARY", "INFO", "WARNING", "ERROR", "DISABLED"]


PRIMARY = (70, 130, 200)  # muted blue: primary-action buttons (Load/Run/Apply)
SECONDARY = (110, 110, 110)  # grey: secondary buttons
INFO = (140, 160, 180)  # dim blue-grey: intro text and hints
WARNING = (210, 170, 60)  # amber: non-blocking cautions
ERROR = (200, 80, 80)  # muted red: error states
DISABLED = (90, 90, 90)  # dim grey: inactive controls
