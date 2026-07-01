"""Model tab package.

Public entry point: ``create_model_tab`` (no-arg, called once at startup). The
``state`` sub-module is a cross-tab contract, importable as
``floodlight_gui.tabs.model.state`` with ``fitted_models`` / ``output_checked`` /
``output_results`` / ``app_instance``.
"""

from floodlight_gui.tabs.model.controls import create_model_tab

__all__ = ["create_model_tab"]
