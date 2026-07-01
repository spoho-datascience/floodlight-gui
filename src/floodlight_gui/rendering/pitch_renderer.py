"""Image-based pitch renderer for the DPG drawlist background layer.

Loads a pre-rendered PNG from the ``floodlight_gui.assets.pitches`` package and
draws it as the background layer on the DPG drawlist. Images are pre-rendered
offline to match the drawlist dimensions and CoordinateMapper padding so pitch
lines align pixel-perfectly with player circles drawn on top.

Layering: this module imports ``dearpygui`` at module scope and belongs to the
DPG-aware rendering layer; it must not be imported by backend or registry modules.
"""

from __future__ import annotations

import contextlib
import logging
from importlib.resources import as_file, files

import dearpygui.dearpygui as dpg

from floodlight_gui.rendering.coordinate_mapper import CoordinateMapper

logger = logging.getLogger(__name__)

# Resource anchor: a string resolved via importlib.resources at call time, not at import time.
_PITCH_PACKAGE = "floodlight_gui.assets.pitches"

_SPORT_FILENAME = {
    "football": "football.png",
    "handball": "handball.png",
}

# Pixel threshold for the "margin" classifier used by _detect_playing_area_uv:
# values at or above this on every channel are considered decorative
# margin (DPG's load_image returns RGBA floats in [0.0, 1.0]).
_MARGIN_PIXEL_THRESHOLD = 0.94
# Alpha threshold below which a pixel is treated as transparent margin.
_MARGIN_ALPHA_THRESHOLD = 0.04


def resolve_sport_from_pitch(pitch) -> str:
    """Resolve a sport string from a floodlight Pitch object.

    Call this once at the ``PitchRenderer`` instantiation site with the loaded
    pitch object to select the correct background image.

    Resolution order:
      1. ``pitch.sport`` if it equals 'football' or 'handball' (case-insensitive).
      2. xlim span ~40 AND ylim span ~20: inferred as 'handball' (EIGD template
         dimensions fallback when ``pitch.sport`` is absent or unrecognised).
      3. 'football' as the default.

    Parameters
    ----------
    pitch : floodlight.core.pitch.Pitch or None
        The loaded pitch object. ``None`` falls back to 'football'.

    Returns
    -------
    str
        One of {'football', 'handball'}, guaranteed to be a key in ``_SPORT_FILENAME``.

    Notes
    -----
    This helper does not import dearpygui and is safe to call from tests that
    do not load the DPG runtime.
    """
    if pitch is None:
        return "football"
    sport = getattr(pitch, "sport", None)
    if isinstance(sport, str) and sport.lower() in _SPORT_FILENAME:
        return sport.lower()
    # Fallback: infer from pitch dimensions (EIGD handball template = 40 x 20)
    try:
        xlim = getattr(pitch, "xlim", None)
        ylim = getattr(pitch, "ylim", None)
        if xlim is not None and ylim is not None:
            x_span = abs(xlim[1] - xlim[0])
            y_span = abs(ylim[1] - ylim[0])
            if 35 < x_span < 45 and 15 < y_span < 25:
                logger.warning(
                    "Pitch.sport not set; inferred 'handball' from dimensions (xlim=%s, ylim=%s)",
                    xlim,
                    ylim,
                )
                return "handball"
    except (TypeError, IndexError):
        pass
    if sport is not None:
        logger.warning("Unknown pitch.sport=%r; defaulting to 'football'", sport)
    return "football"


def _detect_playing_area_uv(image_path) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return ``(uv_min, uv_max)`` UV fractions of the inscribed non-margin region.

    Source PNGs have decorative white (and possibly transparent) margins around
    the colored playing surface. Cropping to the non-margin region avoids aspect
    ratio distortion when the pitch rectangle uses game-coord proportions.

    Reads the PNG via PIL directly (PIL gives a safe numpy-compatible buffer).
    Walks the RGBA array once, builds a tight bounding box of pixels that are
    neither near-white nor near-transparent, and returns fractions of
    (width, height). Returns ``((0,0),(1,1))`` on any detection failure so
    callers remain correct without special-casing the miss.

    Auto-detection from pixel data means the renderer keeps working when assets
    are replaced or regenerated, with no per-asset hardcoded table.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return (0.0, 0.0), (1.0, 1.0)
    try:
        with Image.open(image_path) as im:
            im = im.convert("RGBA")
            arr = np.asarray(im, dtype=np.uint8)
    except (OSError, ValueError):
        return (0.0, 0.0), (1.0, 1.0)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return (0.0, 0.0), (1.0, 1.0)
    height, width = arr.shape[:2]
    rgb = arr[:, :, :3]
    # 0..255 thresholds equivalent to the 0.0..1.0 module-level constants.
    white_thr = int(round(_MARGIN_PIXEL_THRESHOLD * 255))
    alpha_thr = int(round(_MARGIN_ALPHA_THRESHOLD * 255))
    is_white = (rgb >= white_thr).all(axis=-1)
    if arr.shape[2] >= 4:
        is_transparent = arr[:, :, 3] <= alpha_thr
        is_margin = is_white | is_transparent
    else:
        is_margin = is_white
    non_margin_rows = np.where(~is_margin.all(axis=1))[0]
    non_margin_cols = np.where(~is_margin.all(axis=0))[0]
    if not len(non_margin_rows) or not len(non_margin_cols):
        return (0.0, 0.0), (1.0, 1.0)
    y0, y1 = int(non_margin_rows[0]), int(non_margin_rows[-1])
    x0, x1 = int(non_margin_cols[0]), int(non_margin_cols[-1])
    return (x0 / width, y0 / height), (x1 / width, y1 / height)


class PitchRenderer:
    """Render a pitch background image on a DPG drawlist.

    Loads a sport-specific PNG asset, uploads it as a static DPG texture, and
    draws it onto the given drawlist layer. Call ``draw()`` to render and
    ``clear()`` to remove the image and free the texture.

    Parameters
    ----------
    drawlist_tag : str
        DPG tag of the target drawlist.
    mapper : CoordinateMapper
        Maps game coordinates to pixel positions; supplies the pitch bounding box.
    sport : str
        Sport identifier: 'football' or 'handball'. Normalised to lowercase.
    parent_layer_tag : str or int or None
        DPG layer tag for draw calls. When None, draw calls target the drawlist
        directly (``drawlist_tag``); when provided, all draw calls use this layer.

    Notes
    -----
    DPG widget tags owned by this instance:
      ``__pitch_tex_{id(self)}`` in ``__pitch_texture_registry`` (texture),
      ``__pitch_img_{id(self)}`` on the drawlist layer (image draw call).
    """

    def __init__(
        self,
        drawlist_tag: str,
        mapper: CoordinateMapper,
        sport: str = "football",
        parent_layer_tag: str | int | None = None,
    ):
        self.drawlist_tag = drawlist_tag
        self.mapper = mapper
        self.sport = sport.lower()
        # When parent_layer_tag is None, draw calls target the drawlist directly
        # so that legacy callers without an explicit layer still work correctly.
        self._parent_layer = parent_layer_tag if parent_layer_tag is not None else drawlist_tag
        self._image_tag: str | None = None
        self._texture_tag: str | None = None
        # Inscribed playing-area UV bounds; computed at draw() time from
        # the asset file so the renderer is decoupled from per-asset hardcoded coords.
        self._uv_min: tuple[float, float] = (0.0, 0.0)
        self._uv_max: tuple[float, float] = (1.0, 1.0)

    def draw(self):
        """Load the pitch PNG asset, upload it as a static texture, and draw it.

        Clears any existing pitch image first. The image is drawn at the
        letterboxed pitch bounds (``mapper.pitch_origin_px`` to
        ``mapper.pitch_end_px``) so aspect ratio is preserved. UV coordinates
        are auto-detected from the asset file to crop decorative margins.
        """
        self.clear()

        filename = _SPORT_FILENAME.get(self.sport)
        if filename is None:
            logger.warning("Unknown sport %r; no pitch image available", self.sport)
            return

        try:
            resource = files(_PITCH_PACKAGE) / filename
        except (ModuleNotFoundError, FileNotFoundError) as exc:
            logger.warning("Pitch image package not found for sport %r: %s", self.sport, exc)
            return

        # as_file() yields a real filesystem path even when the resource lives inside
        # a zipped wheel. dpg.add_static_texture must be called inside this block:
        # the materialised temp file is cleaned up on context-manager exit, but the
        # texture data is already on the GPU by then.
        with as_file(resource) as image_path:
            if not image_path.is_file():
                logger.warning("Pitch image not found for sport %r: %s", self.sport, image_path)
                return

            width, height, channels, data = dpg.load_image(str(image_path))
            if data is None:
                logger.error("Failed to load pitch image: %s", image_path)
                return

            # Create a static texture in the DPG texture registry.
            self._texture_tag = f"__pitch_tex_{id(self)}"
            if not dpg.does_item_exist("__pitch_texture_registry"):
                dpg.add_texture_registry(tag="__pitch_texture_registry")

            dpg.add_static_texture(
                width=width,
                height=height,
                default_value=data,
                tag=self._texture_tag,
                parent="__pitch_texture_registry",
            )

            # Auto-detect the inscribed playing-area bbox from the PNG file via PIL.
            # PIL gives a safe numpy-compatible buffer; the result is cached on the
            # instance so update_position() reuses the same UVs without re-detecting.
            self._uv_min, self._uv_max = _detect_playing_area_uv(image_path)

            # Draw at the inscribed pitch rectangle, not the full drawlist, so
            # the image preserves the pitch's xlim/ylim aspect ratio.
            pmin = list(self.mapper.pitch_origin_px)
            pmax = list(self.mapper.pitch_end_px)
            self._image_tag = f"__pitch_img_{id(self)}"
            dpg.draw_image(
                self._texture_tag,
                pmin=pmin,
                pmax=pmax,
                uv_min=list(self._uv_min),
                uv_max=list(self._uv_max),
                parent=self._parent_layer,
                tag=self._image_tag,
            )

    def clear(self):
        """Remove the pitch image and free the texture from the DPG registry."""
        for tag in (self._image_tag, self._texture_tag):
            if tag is not None:
                with contextlib.suppress(SystemError):  # DPG raises SystemError for missing items
                    dpg.delete_item(tag)
        self._image_tag = None
        self._texture_tag = None

    def update_position(self, mapper: CoordinateMapper) -> bool:
        """Resize the existing pitch image in place via configure_item.

        Uses ``configure_item`` rather than delete-and-redraw to preserve
        drawlist Z-order: player circles drawn after the pitch stay on top
        through viewport resizes.

        Parameters
        ----------
        mapper : CoordinateMapper
            Updated mapper supplying the new pixel bounds.

        Returns
        -------
        bool
            True when the in-place update succeeded; False when the underlying
            draw_image item is absent and a full ``draw()`` is required.
        """
        self.mapper = mapper
        if self._image_tag is None or not dpg.does_item_exist(self._image_tag):
            return False
        pmin = list(mapper.pitch_origin_px)
        pmax = list(mapper.pitch_end_px)
        try:
            dpg.configure_item(self._image_tag, pmin=pmin, pmax=pmax)
            return True
        except SystemError:
            return False

    def update_mapper(self, mapper: CoordinateMapper):
        """Replace the coordinate mapper and redraw the pitch, including on sport change.

        Clears the pitch layer's children (``children_only=True``) so the layer
        container and sibling layers (player, overlay) survive untouched. Z-order
        is preserved: the pitch layer remains at drawlist child 0, so the next
        ``draw()`` places the new pitch image at the back.

        The per-instance texture lives in ``__pitch_texture_registry``, outside
        the drawlist layer. ``children_only=True`` on the pitch layer does not
        reach it, so the texture is deleted explicitly to avoid leaking GPU memory
        across sport changes.

        Parameters
        ----------
        mapper : CoordinateMapper
            New coordinate mapper (may carry a different sport or pitch dimensions).

        Notes
        -----
        Layer-clear with ``children_only=True`` is appropriate in this data-load
        handler because it is called at most once per data-load event. Do not
        replicate this pattern in tick-path or resize-path callees; use
        ``update_position`` there instead.
        """
        # Explicit texture cleanup: children_only=True on the pitch layer
        # does not reach __pitch_texture_registry.
        if self._texture_tag is not None:
            with contextlib.suppress(SystemError):
                dpg.delete_item(self._texture_tag)
            self._texture_tag = None

        # Clear pitch-layer children; the layer container and sibling layers survive.
        with contextlib.suppress(SystemError):
            dpg.delete_item(self._parent_layer, children_only=True)

        # Reset cached image tag so update_position() does not attempt to
        # configure a stale tag between this clear and the redraw below.
        self._image_tag = None

        # Update mapper and redraw; draw() repopulates _image_tag and _texture_tag.
        self.mapper = mapper
        self.draw()
