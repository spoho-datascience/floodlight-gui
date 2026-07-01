"""Home of ``OVERLAY_ADAPTER_REGISTRY`` and the ``OverlayAdapter`` protocol.

``OVERLAY_ADAPTER_REGISTRY`` is the single lookup mapping string keys to
adapter classes; ``registry/*.py`` and the public ``__init__`` never import
from this package, keeping the DPG dependency out of the backend layer.

Registration is explicit and manual (no decorator magic, no autodiscovery)
so the registry is grep-able and auditable. Onboarding a new adapter requires
exactly three steps:
  1. Write the class (e.g. ``NewAdapter``) in ``adapters/new.py``.
  2. Add ``"new": NewAdapter`` to ``OVERLAY_ADAPTER_REGISTRY`` below.
  3. Set ``MODEL_REGISTRY[model_key]["overlay_adapter"] = "new"``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from floodlight_gui.rendering.adapters.hull import HullAdapter
from floodlight_gui.rendering.adapters.voronoi import VoronoiAdapter


@runtime_checkable
class OverlayAdapter(Protocol):
    """Lifecycle protocol for live-render model-overlay adapters.

    Each adapter owns its DPG state (tags, textures, configure_item call
    sites). Each render strategy (rasterized texture, stable-tag polygon)
    is isolated per adapter. Implementations live in sibling modules
    (``hull.py``, ``voronoi.py``).
    """

    def init(self, parent_layer_tag: str | int, **kwargs: Any) -> None:
        """Create DPG state. Called once per fitted model.

        Parameters
        ----------
        parent_layer_tag : str | int
            DPG draw_layer tag to attach all draw items to.
            Callers always pass the ``__overlay_layer`` tag.
        **kwargs
            Adapter-specific; see each implementation's docstring.
        """
        ...

    def update_frame(self, t: int) -> None:
        """Update DPG items for frame ``t``.

        Implementations must update in place via ``dpg.configure_item``;
        calling ``dpg.delete_item`` is forbidden here (use ``clear``).

        Parameters
        ----------
        t : int
            Zero-based frame index.
        """
        ...

    def set_visible(self, visible: bool) -> None:
        """Toggle visibility of all DPG items owned by this adapter.

        Implementations must use ``dpg.configure_item(..., show=bool)``;
        calling ``dpg.delete_item`` is forbidden here (use ``clear``).

        Parameters
        ----------
        visible : bool
            ``True`` to show, ``False`` to hide.
        """
        ...

    def clear(self) -> None:
        """Tear down all DPG state owned by this adapter.

        For adapters with textures, deletion order must be: image item,
        then texture, then registry entry (consumers before producers).
        Each deletion should be wrapped in
        ``contextlib.suppress(SystemError)`` to tolerate DPG teardown
        ordering.
        """
        ...


OVERLAY_ADAPTER_REGISTRY: dict[str, type] = {
    "hull": HullAdapter,
    "voronoi": VoronoiAdapter,
}


__all__ = ["OverlayAdapter", "OVERLAY_ADAPTER_REGISTRY"]
