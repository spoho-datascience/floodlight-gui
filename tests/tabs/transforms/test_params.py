"""Behavioral contracts for ``tabs.transforms.params``.

The params layer owns two tab-specific jobs: collecting widget values into a
coerced kwargs dict for the active op, and rebinding the per-category active op
when the op combo changes. DPG and the descriptor-widget builder are the seams;
DPG is stubbed via ``make_dpg_stub`` and the active-op key is steered by seeding
the tab_bar value the same way a real user's category pick would.

Behavioral contracts guarded here
---------------------------------
_collect_params
  C3  Coerces each param to the descriptor's declared type: ``int``/``float``
      numeric strings become numbers; a blank ``ndarray`` widget becomes None;
      anything else passes through unchanged.

_on_op_changed
  C6  Derives the category from the combo tag suffix and rebinds that
      category's current op to the combo's selected display name.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.transforms.controls as controls
import floodlight_gui.tabs.transforms.params as params
import floodlight_gui.tabs.transforms.select as select
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install one shared fake DPG into both ``params`` and ``select``.

    ``_collect_params`` reads the active op through ``select`` and the widget
    values through ``params``; both modules must see the same recorder so a
    single seeded tab_bar value drives the whole path.

    Returns
    -------
    SimpleNamespace
        The shared recorder.
    """
    stub = make_dpg_stub()
    monkeypatch.setattr(params, "dpg", stub)
    monkeypatch.setattr(select, "dpg", stub)
    return stub


def _activate_category(stub, category):
    """Seed the category tab_bar so ``_get_active_op_key`` resolves ``category``."""
    stub.existing_items.add("transforms_category_tab_bar")
    stub.values["transforms_category_tab_bar"] = f"transforms_category_{category}_tab"


# --------------------------------------------------------------------------- #
# _collect_params                                                              #
# --------------------------------------------------------------------------- #


def test_collect_params_coerces_by_declared_type(dpg_stub):
    """C3: int/float strings coerce to numbers, blank ndarray maps to None.

    ``assign_roles`` carries an ``int`` param (``n_iter``) and an ``ndarray``
    param (``reference``); both real widget tags are seeded so the coercion
    branches run against the live descriptor.
    """
    _activate_category(dpg_stub, "permutation")
    dpg_stub.existing_items.add("transforms_param_assign_roles_n_iter")
    dpg_stub.existing_items.add("transforms_param_assign_roles_reference")
    dpg_stub.values["transforms_param_assign_roles_n_iter"] = "4"
    dpg_stub.values["transforms_param_assign_roles_reference"] = "  "

    out = params._collect_params()
    assert out["n_iter"] == 4
    assert out["reference"] is None


# --------------------------------------------------------------------------- #
# _on_op_changed                                                               #
# --------------------------------------------------------------------------- #


def test_on_op_changed_rebinds_category_op(dpg_stub):
    """C6: the op combo's selection rebinds that category's current op key."""
    combo = "transforms_op_combo_filter"
    dpg_stub.existing_items.add(combo)
    # Pick a non-default filter op by its real display name.
    dpg_stub.values[combo] = "Wiener Filter"

    params._on_op_changed(combo, None)
    assert controls._current_op_key["filter"] == "wiener"
