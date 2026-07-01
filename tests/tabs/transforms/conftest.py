"""Local fixtures for the transforms-tab suite.

The transforms tab keeps its active-op-per-category selection in module-level
mutable state (``controls._current_op_key``). Op-selection callbacks
(``params._on_op_changed``) write into that dict, so a test that changes the
active op of a category leaves residue visible to later tests. The autouse
fixture below restores ``_current_op_key`` to its cold-start seeding (each
category's first registered op) before every test, making the suite
order-independent.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.transforms.controls as controls


@pytest.fixture(autouse=True)
def _reset_current_op_key():
    """Reset ``controls._current_op_key`` to its cold-start seeding per test.

    The cold-start value for each category is that category's first registered
    op, mirroring the seeding expression in ``controls`` at import time. The
    dict is mutated in place so every module that imported it keeps the same
    object identity.

    Yields
    ------
    None
        Control returns to the test with the module state freshly seeded.
    """
    seed = {
        cat: (controls._cat_ops[cat][0] if controls._cat_ops[cat] else None)
        for cat in controls._CATEGORIES
    }
    controls._current_op_key.clear()
    controls._current_op_key.update(seed)
    yield
    controls._current_op_key.clear()
    controls._current_op_key.update(seed)


@pytest.fixture
def app_double():
    """Return a minimal app double exposing the methods the tab reads.

    Provides ``get_temporal_divisions``, ``get_team_names``, ``loaded_data``,
    and the XY-op-stack mutators (``apply_xy_op`` / ``undo_xy_op`` /
    ``reset_xy_ops`` / ``get_xy_ops_stack`` / ``_get_pristine_xy`` /
    ``get_active_xy``) as recording or controllable stubs. The store attribute
    is a sentinel so broadcast-helper call assertions can identify it.

    Returns
    -------
    _AppDouble
        Recording app double; inspect ``.calls`` for routed mutations.
    """

    class _AppDouble:
        def __init__(self):
            self.store = object()
            self.loaded_data = (None, None, None, None)
            self._periods = ["firstHalf", "secondHalf"]
            self._teams = ["Home", "Away", "Ball"]
            self._stacks: dict[tuple[str, str], list] = {}
            self._pristine = object()
            self.calls: list[tuple] = []

        def get_temporal_divisions(self):
            return list(self._periods)

        def get_team_names(self):
            return list(self._teams)

        def get_xy_ops_stack(self, period, team):
            return self._stacks.get((period, team), [])

        def _get_pristine_xy(self, period, team):
            return self._pristine

        def get_active_xy(self, period, team):
            return None

        def apply_xy_op(self, period, team, op_key, params):
            self.calls.append(("apply_xy_op", period, team, op_key, params))

        def undo_xy_op(self, period, team):
            self.calls.append(("undo_xy_op", period, team))

        def reset_xy_ops(self, period=None, team=None):
            self.calls.append(("reset_xy_ops", period, team))

    return _AppDouble()
