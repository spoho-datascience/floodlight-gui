"""Shared mutable state for the Metrics tab subpackage.

Single responsibility: typed module-level attributes that all metrics-tab
modules read and write through ``state.<name>`` so every reader sees the same
live references. DPG-free at module scope.

Sub-modules must attribute-assign (``state.app_instance = app``) and must not
redeclare these names locally.
"""

from __future__ import annotations

from typing import Any

# Live app shell reference, set once at APP_INITIALIZED.
app_instance: Any = None

# Active metric key from METRICS_REGISTRY. None means no metric has been selected
# yet; the selection callbacks treat None as the cold-start sentinel.
selected_metric_key: str | None = None

# Per-compute result cache. Insertion order drives the Results panel tab order.
#   key:   (metric_key, period_internal, team_or_source)
#   value: {"dataframe": pd.DataFrame} | {"value": float}
results: dict[tuple[str, str, str], dict] = {}

# Input-widget registry for the active metric: maps each input name to the DPG
# tags the compute step needs to read back as kwargs. Rebuilt on every metric change.
#   input_name -> {"type": str, "source_combo": tag, "column_combo": tag | None}
input_widgets: dict[str, dict] = {}

# ResultsPanel instance, created once in controls.create_metrics_tab and reused
# across refreshes.
panel: Any = None
