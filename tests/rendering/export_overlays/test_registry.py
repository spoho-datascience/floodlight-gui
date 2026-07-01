"""Export-overlay registry contracts.

C1 EXPORT_OVERLAY_REGISTRY keys mirror OVERLAY_ADAPTER_REGISTRY keys.
C2 every registry value is callable with signature (spec, ax, *, frame: int).
"""

from __future__ import annotations

import inspect

from floodlight_gui.rendering.adapters import OVERLAY_ADAPTER_REGISTRY
from floodlight_gui.rendering.export_overlays import EXPORT_OVERLAY_REGISTRY


def test_export_overlay_registry_mirrors_adapter_registry():
    """EXPORT_OVERLAY_REGISTRY keys mirror OVERLAY_ADAPTER_REGISTRY keys. Both
    are looked up by the same MODEL_REGISTRY[...]['overlay_adapter'] string; if
    the keys drift, export dispatch silently skips valid overlays."""
    assert set(EXPORT_OVERLAY_REGISTRY.keys()) == set(OVERLAY_ADAPTER_REGISTRY.keys()), (
        f"Registry key drift: export={sorted(EXPORT_OVERLAY_REGISTRY.keys())} "
        f"vs adapters={sorted(OVERLAY_ADAPTER_REGISTRY.keys())}"
    )


def test_export_overlay_registry_values_are_callable():
    """Every registry value is callable with signature (spec, ax, *, frame: int)."""
    for key, plotter in EXPORT_OVERLAY_REGISTRY.items():
        assert callable(plotter), f"registry value for {key!r} is not callable"
        sig = inspect.signature(plotter)
        params = list(sig.parameters.values())
        assert len(params) == 3, (
            f"{key!r} plotter has {len(params)} params, expected 3 (spec, ax, *, frame)"
        )
        # 'frame' must be keyword-only.
        frame_param = sig.parameters.get("frame")
        assert frame_param is not None, f"{key!r} plotter missing 'frame' parameter"
        assert frame_param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{key!r} 'frame' must be keyword-only, got {frame_param.kind}"
        )
