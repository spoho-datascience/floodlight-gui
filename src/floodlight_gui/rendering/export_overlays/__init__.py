"""Matplotlib overlay plotters for the export pipeline.

The export-side mirror of ``rendering.adapters``: the same string keys
(``"voronoi"``, ``"hull"``) resolve to stateless plotter functions here instead
of DPG-bearing adapter classes. Both registries must keep their key sets in
lock-step so the spec-list dispatcher resolves either from one descriptor field.

DPG-free invariant: this package and its submodules must not import dearpygui.
"""

from __future__ import annotations

from collections.abc import Callable

from floodlight_gui.rendering.export_overlays.hull import plot_hull
from floodlight_gui.rendering.export_overlays.voronoi import plot_voronoi

# Mirrors OVERLAY_ADAPTER_REGISTRY in adapters/__init__.py.
# Shared string keys match MODEL_REGISTRY[...]["overlay_adapter"] so the
# spec-list dispatcher in export_renderer.py can resolve either registry
# from the same descriptor field.
EXPORT_OVERLAY_REGISTRY: dict[str, Callable] = {
    "hull": plot_hull,
    "voronoi": plot_voronoi,
}

__all__ = ["EXPORT_OVERLAY_REGISTRY", "plot_hull", "plot_voronoi"]
