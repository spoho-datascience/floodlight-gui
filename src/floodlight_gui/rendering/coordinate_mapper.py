"""Bidirectional coordinate mapping between pitch (meters) and drawlist (pixels).

DPG-free at module scope: no dearpygui import. Usable by both the live DPG
render path and the Matplotlib export path.

Handles arbitrary pitch coordinate systems (floodlight Pitch objects define
xlim/ylim which vary by provider), aspect-ratio preservation with letterboxing,
and y-axis flipping (pitch y-up vs pixel y-down).
"""

from __future__ import annotations


class CoordinateMapper:
    """Bidirectional mapping between pitch coordinates and drawlist pixel coordinates.

    Pitch coordinate origin and extents vary by provider; the floodlight Pitch
    object's ``xlim`` and ``ylim`` define the authoritative bounds. Drawlist
    pixel space has (0, 0) at the top-left corner.

    The mapping preserves aspect ratio by fitting the pitch into the available
    drawlist area (minus padding) and centering with letterboxing.
    """

    def __init__(
        self,
        pitch,
        drawlist_width: int,
        drawlist_height: int,
        padding: int = 40,
    ):
        """
        Parameters
        ----------
        pitch : floodlight.core.pitch.Pitch
            Pitch object with ``xlim`` and ``ylim`` tuple attributes defining
            the coordinate bounds.
        drawlist_width : int
            Pixel width of the DPG drawlist (or Matplotlib canvas).
        drawlist_height : int
            Pixel height of the DPG drawlist (or Matplotlib canvas).
        padding : int, optional
            Pixel gap between the drawlist edge and the pitch boundary.
        """
        self.pitch = pitch
        self.drawlist_width = drawlist_width
        self.drawlist_height = drawlist_height
        self.padding = padding

        self._recalculate()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def pitch_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        """Convert pitch coordinates to drawlist pixel coordinates.

        The y-axis is flipped: pitch y increases upward, pixel y increases
        downward. The x-axis is not flipped.

        Parameters
        ----------
        x : float
            Pitch x-coordinate in pitch units (typically meters).
        y : float
            Pitch y-coordinate in pitch units (typically meters).

        Returns
        -------
        tuple[float, float]
            (px, py) in drawlist pixel coordinates.
        """
        px = self._offset_x + (x - self._pitch_x_min) * self._scale
        # Flip y: high pitch-y maps to low pixel-y.
        py = self._offset_y + (self._pitch_y_max - y) * self._scale
        return (px, py)

    def pixel_to_pitch(self, px: float, py: float) -> tuple[float, float]:
        """Convert drawlist pixel coordinates back to pitch coordinates.

        Parameters
        ----------
        px : float
            Pixel x-coordinate in the drawlist.
        py : float
            Pixel y-coordinate in the drawlist.

        Returns
        -------
        tuple[float, float]
            (x, y) in pitch coordinates.
        """
        x = (px - self._offset_x) / self._scale + self._pitch_x_min
        # Reverse the y-flip applied in pitch_to_pixel.
        y = self._pitch_y_max - (py - self._offset_y) / self._scale
        return (x, y)

    def scale_distance(self, distance_m: float) -> float:
        """Convert a distance in pitch units to the equivalent pixel distance.

        Parameters
        ----------
        distance_m : float
            Distance in pitch units (typically meters).

        Returns
        -------
        float
            The same distance in pixel units, using the current uniform scale.
        """
        return distance_m * self._scale

    def update(
        self,
        pitch=None,
        drawlist_width: int | None = None,
        drawlist_height: int | None = None,
    ):
        """Recalculate the mapping when the pitch or drawlist dimensions change.

        Only the supplied arguments are updated; omitted arguments keep their
        current values.

        Parameters
        ----------
        pitch : floodlight.core.pitch.Pitch or None, optional
            Replacement pitch object. ``None`` keeps the existing pitch.
        drawlist_width : int or None, optional
            New drawlist width in pixels. ``None`` keeps the existing value.
        drawlist_height : int or None, optional
            New drawlist height in pixels. ``None`` keeps the existing value.
        """
        if pitch is not None:
            self.pitch = pitch
        if drawlist_width is not None:
            self.drawlist_width = drawlist_width
        if drawlist_height is not None:
            self.drawlist_height = drawlist_height

        self._recalculate()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def pitch_origin_px(self) -> tuple[float, float]:
        """Pixel position of the pitch origin (xlim[0], ylim[1])."""
        return self.pitch_to_pixel(self._pitch_x_min, self._pitch_y_max)

    @property
    def pitch_end_px(self) -> tuple[float, float]:
        """Pixel position of the pitch far corner (xlim[1], ylim[0])."""
        return self.pitch_to_pixel(self._pitch_x_max, self._pitch_y_min)

    @property
    def pitch_pixel_top(self) -> float:
        """Pixel y-coordinate of the pitch's top edge inside the letterboxed canvas.

        Equals the vertical offset computed by ``_recalculate`` to center the
        pitch (the height of the letterbox bar above the pitch).
        """
        return self._offset_y

    @property
    def pitch_pixel_height(self) -> float:
        """Pixel height of the rendered pitch inside the drawlist canvas.

        Computed from the pitch's physical height and the uniform scale factor.
        """
        return (self._pitch_y_max - self._pitch_y_min) * self._scale

    @property
    def pitch_pixel_bottom(self) -> float:
        """Pixel y-coordinate of the pitch's bottom edge inside the letterboxed canvas.

        Useful for anchoring overlays (e.g. a timeline strip) directly below the
        pitch rather than at the drawlist container's nominal bottom.
        """
        return self.pitch_pixel_top + self.pitch_pixel_height

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _recalculate(self):
        """Recompute the uniform scale and pixel offsets from current pitch and canvas sizes."""
        self._pitch_x_min, self._pitch_x_max = self.pitch.xlim
        self._pitch_y_min, self._pitch_y_max = self.pitch.ylim

        pitch_w = self._pitch_x_max - self._pitch_x_min
        pitch_h = self._pitch_y_max - self._pitch_y_min

        available_w = max(1.0, self.drawlist_width - 2 * self.padding)
        available_h = max(1.0, self.drawlist_height - 2 * self.padding)

        # Uniform scale that preserves aspect ratio.
        scale_x = available_w / pitch_w if pitch_w else 1.0
        scale_y = available_h / pitch_h if pitch_h else 1.0
        self._scale = min(scale_x, scale_y)

        # Center the pitch in the available space (letterboxing).
        rendered_w = pitch_w * self._scale
        rendered_h = pitch_h * self._scale

        self._offset_x = self.padding + (available_w - rendered_w) / 2.0
        self._offset_y = self.padding + (available_h - rendered_h) / 2.0
