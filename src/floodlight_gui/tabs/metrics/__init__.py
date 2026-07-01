"""Metrics tab package.

A 5-step workflow tab (Select Data, Select Metric, Configure Parameters, Results,
Export) over ``METRICS_REGISTRY``. Public entry point: the no-arg
``create_metrics_tab``, called once at startup to build the tab into the current
DPG container.

Internal layout:
  - ``state``    : module-scope mutable state (single source of truth).
  - ``select``   : metric picker helpers + period/team scope resolution.
  - ``params``   : input + param widgets, model-output discovery, kwarg collection.
  - ``execute``  : the Compute producer (single / broadcast / non-XY source key).
  - ``results``  : the ResultsPanel wiring + leaf renderer + export payloads.
  - ``controls`` : the orchestrator (layout, event wiring, bootstrap).
"""

from floodlight_gui.tabs.metrics.controls import create_metrics_tab

__all__ = ["create_metrics_tab"]
