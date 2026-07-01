"""Behavioral contracts for ``floodlight_gui.tabs.inspect.leaves``.

Value-triage note
-----------------
Most of this module is display formatting on a path the user sees every session
(row counts, truncation lengths, decimal places, the Code summary text, the
Pitch block). A wrong format is loud and cosmetic -- the user reads it on sight
-- so those contracts are intentionally NOT guarded here.

The one surviving contract is the event-table column-to-source mapping: each
labelled column must read from the right event field (``eID`` under "Event
Type", ``gameclock`` under "Game Clock", ``pID`` under "Player ID", ``outcome``
under "Outcome"). Swapping a source field under a label silently presents one
piece of data as another (a player ID shown as an event type), which the user
cannot eyeball as wrong. That is a misrepresentation, not a format, so it stays.

DPG is the seam (a recording fake).

Behavioral contract guarded here
--------------------------------
_leaf_event_table
  C1  Each labelled column reads from its own event field: the four columns are
      "Event Type" <- eID, "Game Clock" <- gameclock, "Player ID" <- pID,
      "Outcome" <- outcome (the mislabel guard).
"""

from __future__ import annotations

import pandas as pd
import pytest

import floodlight_gui.tabs.inspect.leaves as leaves
from floodlight_gui.tabs.inspect.leaves import _leaf_event_table


@pytest.fixture
def patched_dpg(fake_dpg, monkeypatch):
    """Install the recording fake DPG onto the leaves module and seed a parent."""
    monkeypatch.setattr(leaves, "dpg", fake_dpg)
    fake_dpg.add_root("parent")
    return fake_dpg


def test_leaf_event_table_columns_map_to_correct_fields(patched_dpg):
    """C1: each labelled column reads from its own event field, not a sibling.

    Distinct, non-collidable values per field let us assert the column order
    and source mapping by position: column i's cell must be the i-th field's
    value. A swapped source (e.g. pID rendered under "Event Type") would surface
    as a misrepresentation -- the guard this whole module is reduced to.
    """
    df = pd.DataFrame(
        {
            "eID": ["EVT"],  # distinct from every other field
            "gameclock": [12.0],
            "pID": ["PLAYER42"],
            "outcome": ["OUT"],
        }
    )
    _leaf_event_table("parent", df)

    # Column labels in declared order.
    labels = [
        patched_dpg.get_item_label(tag)
        for tag, item in patched_dpg.items.items()
        if item["type"] == "mvAppItemType::mvTableColumn"
    ]
    assert labels == ["Event Type", "Game Clock", "Player ID", "Outcome"]

    # The body row's four cells, in order, are the four source fields' values.
    rows = [
        tag
        for tag, item in patched_dpg.items.items()
        if item["type"] == "mvAppItemType::mvTableRow"
    ]
    assert len(rows) == 1
    cell_texts = patched_dpg.texts_under(rows[0])
    assert cell_texts[0] == "EVT"  # Event Type <- eID
    assert cell_texts[1] == "12.0"  # Game Clock <- gameclock
    assert cell_texts[2] == "PLAYER42"  # Player ID <- pID (last 8 chars; exactly 8)
    assert cell_texts[3] == "OUT"  # Outcome <- outcome
