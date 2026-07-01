"""Rendering package: native DPG drawlist pitch/player visualization plus the
matplotlib export renderer.

DPG-dependent modules (PitchRenderer, PlayerRenderer, export_renderer, and the
overlay adapters under ``rendering.adapters``) are imported lazily via
``__getattr__`` so the DPG-free ``coordinate_mapper`` can be imported without a
DPG context.
"""

from __future__ import annotations

from floodlight_gui.rendering.coordinate_mapper import CoordinateMapper


def __getattr__(name: str):
    """Lazy-import DPG-dependent renderers on first access."""
    if name == "PitchRenderer":
        from floodlight_gui.rendering.pitch_renderer import PitchRenderer

        return PitchRenderer
    if name == "PlayerRenderer":
        from floodlight_gui.rendering.player_renderer import PlayerRenderer

        return PlayerRenderer
    if name == "export_renderer":
        from floodlight_gui.rendering import export_renderer

        return export_renderer
    raise AttributeError(f"module 'floodlight_gui.rendering' has no attribute {name!r}")


__all__ = [
    "CoordinateMapper",
    "PitchRenderer",
    "PlayerRenderer",
    "export_renderer",
]
