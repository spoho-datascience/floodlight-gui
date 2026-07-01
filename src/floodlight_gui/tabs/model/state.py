"""Shared mutable state for the Models tab subpackage.

Single responsibility: typed module-level attributes that all model-tab modules
read and write through ``state.<name>`` so every reader sees the same live
references.

Cross-tab contract: ``tabs/metrics/`` and ``tabs/visualization/`` import
this module directly and read ``fitted_models``, ``output_checked``, and
``output_results`` by their exact key/value shapes. Those three dicts and their
key/value types must not change without coordinating with all three consumers.

Sub-modules must attribute-assign (``state.app_instance = app``) and must not
redeclare these names locally.
"""

from __future__ import annotations

from typing import Any

# Live app shell reference, set once at APP_INITIALIZED.
app_instance: Any = None

# Internal-period string for the current selection (None means "All periods").
# Updated on every period/team selection change in select.py.
selected_period_internal: str | None = None

# Maps category_key to the currently selected model_key within that category.
# The active model is resolved by reading the active category tab and looking up here.
current_model_by_category: dict[str, str] = {}

# ---- CROSS-TAB CONTRACT DICTS (fixed shapes) ---------------------------------

# Fitted-model cache.
#   key:   (period_internal, team, model_key)  -- team is "BothTeams" for multi-team fits
#   value: (model_obj, fit_params_dict)
# Keys prefixed with "_" in fit_params_dict are internal (e.g. "_team_names"); strip
# them before displaying to users.
fitted_models: dict[tuple[str, str, str], tuple[Any, dict]] = {}

# Output-checkbox state: True when the user has ticked a model output for export/use.
#   key:   (model_key, output_key)
#   value: bool
# Read by tabs/metrics/ to filter available model-output inputs to checked outputs only.
output_checked: dict[tuple[str, str], bool] = {}

# Per-leaf lazy-compute cache for model results.
#   key:   (model_key, period_internal, team, output_key)
#   value: computed result object (type depends on the model)
output_results: dict[tuple[str, str, str, str], Any] = {}

# ResultsPanel instance, created once in controls.create_model_tab and reused across
# refreshes.
panel: Any = None
