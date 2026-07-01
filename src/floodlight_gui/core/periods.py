"""Single source of truth for the period display-name to internal-key vocabulary.

This module owns the canonical bidirectional mapping between period display names
(e.g. "First Half") and floodlight internal keys (e.g. "firstHalf"). Every tab
that needs to translate between the two must import from here; the mapping must
not be duplicated elsewhere.

Invariants
----------
- DPG-free: this module has no dearpygui import and must never acquire one.
- Single source of truth: PERIOD_DISPLAY_TO_INTERNAL and PERIOD_INTERNAL_TO_DISPLAY
  are the only authoritative period vocabularies in the codebase.

Place in the layering: ``core/`` backend; imported by tabs and registry modules.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Period display-name <-> internal-key mapping
# ---------------------------------------------------------------------------

PERIOD_DISPLAY_TO_INTERNAL: dict[str, str] = {
    "First Half": "firstHalf",
    "Second Half": "secondHalf",
    "Full Match": "fullMatch",
    # Lowercase variants from str.lower() on internal keys are also accepted
    # so that DPG tag construction (period_clean) round-trips without raising.
    "firsthalf": "firstHalf",
    "secondhalf": "secondHalf",
    "fullmatch": "fullMatch",
}
PERIOD_INTERNAL_TO_DISPLAY: dict[str, str] = {
    v: k
    for k, v in {
        "First Half": "firstHalf",
        "Second Half": "secondHalf",
        "Full Match": "fullMatch",
    }.items()
}


def period_display_to_internal(period: str) -> str:
    """Convert a period display name to its internal key.

    This is the authoritative translation from the UI vocabulary ("First Half")
    to the floodlight internal key ("firstHalf"). Pass-through if *period* is
    already an internal key. Logs a debug message and returns the input unchanged
    when neither form is recognised, so callers degrade gracefully instead of
    raising KeyError.

    Parameters
    ----------
    period : str
        A period display name (e.g. "First Half") or an internal key
        (e.g. "firstHalf").

    Returns
    -------
    str
        The corresponding internal key, or *period* unchanged when unrecognised.
    """
    if period in PERIOD_DISPLAY_TO_INTERNAL:
        return PERIOD_DISPLAY_TO_INTERNAL[period]
    if period in PERIOD_INTERNAL_TO_DISPLAY:
        return period
    logger.debug("period_display_to_internal: unknown period %r", period)
    return period


def period_internal_to_display(period: str) -> str:
    """Convert an internal period key to its display name.

    Inverse of :func:`period_display_to_internal` with identical pass-through
    semantics: returns *period* unchanged when unrecognised and logs a debug
    message.

    Parameters
    ----------
    period : str
        An internal key (e.g. "firstHalf") or a display name
        (e.g. "First Half").

    Returns
    -------
    str
        The corresponding display name, or *period* unchanged when unrecognised.
    """
    if period in PERIOD_INTERNAL_TO_DISPLAY:
        return PERIOD_INTERNAL_TO_DISPLAY[period]
    if period in PERIOD_DISPLAY_TO_INTERNAL:
        return period
    logger.debug("period_internal_to_display: unknown period %r", period)
    return period
