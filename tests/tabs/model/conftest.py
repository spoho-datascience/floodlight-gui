"""Local fixtures for the model-tab suite.

The select module keeps module-level mutable selection state
(``_mounted_team_count``) and several model-tab sub-modules write the shared
``state`` dicts (``fitted_models`` / ``output_checked`` / ``output_results`` /
``current_model_by_category``) plus ``state.app_instance`` and ``state.panel``.
Both are global, so a test that mutates them would otherwise leak into the next
test and make the suite order-dependent. The autouse fixture here snapshots and
restores every such handle around each test.
"""

from __future__ import annotations

import pytest

from floodlight_gui.tabs.model import select, state


@pytest.fixture(autouse=True)
def _reset_model_tab_state():
    """Snapshot and restore module-level model-tab state around each test.

    Resets ``select._mounted_team_count`` and the mutable attributes of
    ``state`` so the suite is order-independent. The shared dicts are cleared
    (not merely re-pointed) before the test so a test starts from empty state,
    then the original objects are restored afterwards.
    """
    saved_count = select._mounted_team_count
    saved_app = state.app_instance
    saved_panel = state.panel
    saved_period = state.selected_period_internal
    saved_current = dict(state.current_model_by_category)
    saved_fitted = dict(state.fitted_models)
    saved_checked = dict(state.output_checked)
    saved_results = dict(state.output_results)

    select._mounted_team_count = 1
    state.app_instance = None
    state.panel = None
    state.selected_period_internal = None
    state.current_model_by_category.clear()
    state.fitted_models.clear()
    state.output_checked.clear()
    state.output_results.clear()

    yield

    select._mounted_team_count = saved_count
    state.app_instance = saved_app
    state.panel = saved_panel
    state.selected_period_internal = saved_period
    state.current_model_by_category.clear()
    state.current_model_by_category.update(saved_current)
    state.fitted_models.clear()
    state.fitted_models.update(saved_fitted)
    state.output_checked.clear()
    state.output_checked.update(saved_checked)
    state.output_results.clear()
    state.output_results.update(saved_results)
