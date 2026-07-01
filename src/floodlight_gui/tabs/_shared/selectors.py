"""Shared period-and-team selector widget used across multiple tabs.

This module is the single source of truth for the period+team combo pair
rendered in the load, inspect, transforms, model, and metrics tabs. Each
caller supplies a unique ``tag_prefix`` so the resulting DPG combo tags
do not collide across tabs.

Period-mapping invariant: this module re-exports ``period_display_to_internal``
and ``period_internal_to_display`` from ``core.periods`` rather than defining
its own mapping dict. All callers that need period translation should import
those helpers from here or from ``core.periods`` directly -- never define a
second mapping locally.
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

# Re-export canonical period-mapping helpers from the single source of truth so
# callers can import them from here without reaching into core directly.
# noqa: F401 suppresses the unused-import warning -- the symbols are exported
# via the __all__.extend() at the bottom of the module, which ruff cannot trace.
from floodlight_gui.core.periods import (  # noqa: F401
    period_display_to_internal,
    period_internal_to_display,
)

__all__ = ["period_team_selector"]


def period_team_selector(
    parent_tag: str,
    period_callback,
    team_callback,
    tag_prefix: str,
    team_count: int = 1,
) -> tuple[str, str] | tuple[str, list[str]]:
    """Render a period combo and one or more team combos inside *parent_tag*.

    Each combo is tagged with ``{tag_prefix}_period_combo`` and
    ``{tag_prefix}_team_combo`` (single-team) or
    ``{tag_prefix}_team_combo_a`` / ``..._b`` / ... (multi-team). A summary
    text line is added below the combos at tag ``{tag_prefix}_selection_summary``.

    Parameters
    ----------
    parent_tag : str
        DPG container tag to render into.
    period_callback : callable
        DPG callback ``(sender, app_data, user_data)`` fired on period change.
    team_callback : callable
        DPG callback ``(sender, app_data, user_data)`` fired on team change.
    tag_prefix : str
        Unique-per-tab prefix (e.g. "model", "metrics", "inspect") that
        namespaces all widget tags created by this call.
    team_count : int, default 1
        Number of team combos to render. The default of 1 renders a single
        team combo and returns the two-string tuple form. Values greater than 1
        render labeled combos ("Select Team A:", "Select Team B:", ...) and
        return the list form. This parameter exists to support multi-XY model
        signatures such as ``DiscreteVoronoiModel.fit(xy1, xy2)`` and
        ``NearestOpponentModel.fit(xy1, xy2)``; the tag and return-type contract
        below is fixed and must not change without updating all callers.

    Returns
    -------
    tuple[str, str]
        When ``team_count == 1``: ``(period_combo_tag, team_combo_tag)``.
        Callers tuple-unpack into two strings.
    tuple[str, list[str]]
        When ``team_count > 1``: ``(period_combo_tag, [team_combo_tag_a, ...])``.
        Team tags follow the ``{tag_prefix}_team_combo_a`` / ``..._b`` letter
        suffix convention (lowercase); labels shown to the user use uppercase
        letters ("Select Team A:", "Select Team B:", ...).

    Raises
    ------
    ValueError
        If ``team_count < 1`` (a descriptor with ``fit_xy_arity <= 0`` is a bug).
    """
    if team_count < 1:
        raise ValueError(f"period_team_selector: team_count must be >= 1; got {team_count}")

    period_tag = f"{tag_prefix}_period_combo"
    summary_tag = f"{tag_prefix}_selection_summary"

    # Tag convention (fixed -- callers tuple-unpack and must not be surprised):
    #   team_count == 1: single tag "{prefix}_team_combo" (no suffix)
    #   team_count >= 2: tags "{prefix}_team_combo_a", "{prefix}_team_combo_b", ...
    if team_count == 1:
        team_tags: list[str] = [f"{tag_prefix}_team_combo"]
    else:
        team_tags = [f"{tag_prefix}_team_combo_{chr(ord('a') + i)}" for i in range(team_count)]

    with dpg.group(parent=parent_tag, horizontal=True):
        with dpg.group():
            dpg.add_text("Select Period:")
            dpg.add_combo(
                items=["No data loaded"],
                default_value="No data loaded",
                callback=period_callback,
                width=150,
                tag=period_tag,
            )
        for idx, team_tag in enumerate(team_tags):
            dpg.add_spacer(width=20)
            with dpg.group():
                label = "Select Team:" if team_count == 1 else f"Select Team {chr(ord('A') + idx)}:"
                dpg.add_text(label)
                dpg.add_combo(
                    items=["No data loaded"],
                    default_value="No data loaded",
                    callback=team_callback,
                    width=150,
                    tag=team_tag,
                )
    dpg.add_spacer(parent=parent_tag, height=10)
    dpg.add_text("Selected: No data loaded", parent=parent_tag, tag=summary_tag)

    if team_count == 1:
        return period_tag, team_tags[0]  # Two-string tuple for single-team callers.
    return period_tag, team_tags  # (period_tag, list[str]) for multi-team callers.


# Re-export the canonical period-mapping helpers so callers needing them can
# import from this module rather than reaching into core directly.
# Both symbols must live here -- never define a second mapping locally.
__all__.extend(["period_display_to_internal", "period_internal_to_display"])
