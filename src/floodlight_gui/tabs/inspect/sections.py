"""Section descriptors for the Inspect tab - single source of truth for the SECTIONS list.

Every inspectable data kind is one ``_Section`` entry in ``SECTIONS``. The
renderer (``tabs/inspect/render.py``) drives tab layout, collect calls, and leaf
rendering entirely from these descriptors. Adding a new inspectable data type
means adding one entry here; no engine or controls changes are required.

Layering: this module is DPG-aware only indirectly (the ``leaf`` and
``controls`` callables reference DPG at call time, not at import time). It
imports from ``collect``, ``controls``, ``leaves``, and ``state`` - all within
the same ``tabs/inspect/`` subpackage.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from floodlight_gui.tabs.inspect.collect import (
    _collect_code,
    _collect_event,
    _collect_pitch,
    _collect_position,
    _collect_team,
)
from floodlight_gui.tabs.inspect.controls import (
    _controls_event,
    _controls_position,
    _rebuild_event_filters,
)
from floodlight_gui.tabs.inspect.leaves import (
    _leaf_code,
    _leaf_dataframe,
    _leaf_event_table,
    _leaf_pitch,
    _leaf_position,
)
from floodlight_gui.tabs.inspect.state import FLAT, GROUPED, PERIOD, SINGLE


@dataclass(frozen=True)
class _Section:
    """Descriptor for one inspectable data kind in the Inspect tab.

    The engine reads these fields to decide how to collect, lay out, and render
    a section. All callables are invoked at runtime (not at import time), so DPG
    widget creation is deferred until the tab is actually built.

    Parameters
    ----------
    key : str
        Stable identifier used for DPG widget tags and ``_SECTION_BY_KEY`` lookup.
    label : str
        Human-readable tab label displayed in the viewer.
    shape : str
        Layout shape constant from ``state``: ``FLAT``, ``GROUPED``, ``PERIOD``,
        or ``SINGLE``. Determines how the engine nests the collected tree.
    collect : Callable[[Any], Any]
        Extracts and normalises the relevant data from the app object. Returns
        the tree consumed by the engine and the ``info`` callback.
    leaf : Callable[[str, Any], None]
        Renders a single payload into a DPG container identified by the first arg.
    empty : str
        Status text shown when ``collect`` returns no data.
    info : Callable[[Any, Any], str] or None
        Optional summary string shown above the viewer (app, tree) -> str.
    controls : Callable[[], None] or None
        Optional factory for per-section filter/control widgets above the tree.
    on_load : Callable[[Any], None] or None
        Optional hook called after new data loads (e.g. to rebuild filter combos).
    hide_when_empty : bool
        When True, the engine hides this section's tab instead of showing the
        empty message. Use for optional data kinds (possession, ball status).
    """

    key: str
    label: str
    shape: str
    collect: Callable[[Any], Any]
    leaf: Callable[[str, Any], None]
    empty: str
    info: Callable[[Any, Any], str] | None = None
    controls: Callable[[], None] | None = None
    on_load: Callable[[Any], None] | None = None
    hide_when_empty: bool = False


# Ordered list of all inspectable sections; order determines tab display order.
SECTIONS: list[_Section] = [
    _Section(
        "event",
        "Event Data",
        GROUPED,
        _collect_event,
        _leaf_event_table,
        "No event data available",
        controls=_controls_event,
        on_load=_rebuild_event_filters,
        info=lambda app, tree: f"Periods: {len(tree)} | Teams: {', '.join(app.get_team_names())}",
    ),
    _Section(
        "position",
        "Position Data",
        GROUPED,
        _collect_position,
        _leaf_position,
        "No position data available",
        controls=_controls_position,
        info=lambda app, tree: (
            f"Format: {app.get_data_format()}\n"
            f"Periods: {len(tree)}\nEntities: {len(app.get_team_names())}"
        ),
    ),
    _Section(
        "team",
        "Team Information",
        FLAT,
        _collect_team,
        _leaf_dataframe,
        "No team data available",
        info=lambda app, tree: f"Teams: {list(tree.keys())}",
    ),
    _Section(
        "possession",
        "Possession",
        PERIOD,
        _collect_code(lambda app: app.possession_data),
        _leaf_code({0: "No possession", 1: "Home team possession", 2: "Away team possession"}),
        "No possession data available",
        hide_when_empty=True,
        info=lambda app, tree: f"Possession data - {len(tree)} period(s)",
    ),
    _Section(
        "ballstatus",
        "Ball Status",
        PERIOD,
        _collect_code(lambda app: app.ball_status),
        _leaf_code({0: "Ball dead (out of play)", 1: "Ball alive (in play)"}),
        "No ball status data available",
        hide_when_empty=True,
        info=lambda app, tree: f"Ball status data - {len(tree)} period(s)",
    ),
    _Section(
        "pitch",
        "Pitch Information",
        SINGLE,
        _collect_pitch,
        _leaf_pitch,
        "No pitch data available",
    ),
]
# Fast key-to-descriptor lookup used by the engine to resolve a section by key.
_SECTION_BY_KEY = {s.key: s for s in SECTIONS}
