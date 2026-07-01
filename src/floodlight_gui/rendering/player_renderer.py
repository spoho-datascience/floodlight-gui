"""Player rendering on a DPG drawlist.

Each player is drawn as an interactive circle with a jersey-number label.
Circles are created once at init and repositioned every frame via
``dpg.configure_item`` -- no per-frame allocation.

Typical usage::

    renderer = PlayerRenderer(drawlist_tag, mapper, team_configs)
    # each frame:
    renderer.update_positions({"Home": xy_home, "Away": xy_away}, ball_xy=(bx, by))
    # on mouse hover:
    hit = renderer.get_player_at(mouse_px, mouse_py)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import dearpygui.dearpygui as dpg
import numpy as np

from floodlight_gui.rendering.coordinate_mapper import CoordinateMapper

# ---------------------------------------------------------------------------
# Styling constants
# ---------------------------------------------------------------------------

_PLAYER_RADIUS = 8
_PLAYER_BORDER = 2
_BALL_RADIUS = 6
_BALL_BORDER = 2
_HOVER_RADIUS = 12
_HOVER_BORDER = 3
_SELECTED_RADIUS = 10
_SELECTED_BORDER = 3
_SELECTED_BORDER_COLOR = [255, 215, 0, 255]  # gold
_LABEL_SIZE = 10
_LABEL_COLOR = [255, 255, 255, 255]
_PLAYER_BORDER_COLOR = [255, 255, 255, 255]
_BALL_BORDER_COLOR = [0, 0, 0, 255]
_HIT_RADIUS_PX = 15
_OFFSCREEN = (-9999.0, -9999.0)

# Pitch-coordinate radii fed into mapper.scale_distance() by update_mapper()
# to derive viewport-proportional pixel radii. _M suffix signals meters/pitch
# units, distinguishing from the _PX-flavored pixel constants above.
_NON_BALL_RADIUS_M = 0.8
_BALL_RADIUS_M = 0.5

logger = logging.getLogger(__name__)


@dataclass
class _PlayerSlot:
    """Internal bookkeeping for a single draw-circle + label pair."""

    team: str
    index: int
    is_ball: bool
    circle_tag: str
    label_tag: str
    base_color: list[int]  # RGBA fill
    base_radius: float
    border_color: list[int]
    border_thickness: float
    # last known pixel position (for hit-testing)
    px: float = -9999.0
    py: float = -9999.0
    # Separate label position tracking: the epsilon throttle in _show_slot
    # compares label drift against the label's own last position, not slot.px/py.
    # The -9999.0 sentinel guarantees the first configure_item after construction
    # always fires.
    label_px: float = -9999.0
    label_py: float = -9999.0
    visible: bool = False
    hidden_nan: bool = False  # hidden because position was NaN
    # Tracks the last radius value pushed to DPG so _show_slot can detect when
    # base_radius has drifted (e.g. after update_mapper rescales on viewport
    # resize) and re-configure the live draw_circle item. Init -1 forces the
    # first _show_slot call after construction to push the construction-time radius.
    radius_pushed: float = -1.0


class PlayerRenderer:
    """Renders players and the ball as interactive circles on a DPG drawlist.

    Players are pre-allocated as ``draw_circle`` / ``draw_text`` items and
    repositioned each frame with ``dpg.configure_item``.  This avoids any
    per-frame widget creation, keeping the update path fast enough for
    25-30 FPS playback.

    Parameters
    ----------
    drawlist_tag : str | int
        Tag of the DPG drawlist to draw into.
    mapper : CoordinateMapper
        Converts between pitch coordinates (meters) and drawlist pixels.
    team_configs : dict
        Keyed by team name.  Each value is a dict with:
        - ``color``  : ``[R, G, B, A]`` fill colour
        - ``n_players`` : int
        - ``is_ball`` : bool (optional, default False)
    """

    def __init__(
        self,
        drawlist_tag: str | int,
        mapper: CoordinateMapper,
        team_configs: dict,
        parent_layer_tag: str | int | None = None,
    ):
        self._drawlist = drawlist_tag
        self._mapper = mapper
        self._uid = id(self)  # for tag uniqueness across instances
        # When a layer tag is provided, all draw_circle / draw_text calls
        # write into that layer. None falls back to drawing directly into the
        # drawlist so existing tests pass unchanged.
        self._parent_layer = parent_layer_tag if parent_layer_tag is not None else drawlist_tag

        # State
        self._slots: list[_PlayerSlot] = []
        self._team_slots: dict[str, list[_PlayerSlot]] = {}
        self._team_visible: dict[str, bool] = {}
        self._ball_visible: bool = True

        self._highlighted: _PlayerSlot | None = None
        self._selected: _PlayerSlot | None = None
        self._show_labels: bool = False
        # Operator-chosen on-canvas label content (jersey / name / position / pID ...).
        # When set, set_label_resolver walks all slots and pushes resolved text into each
        # label_tag. When None, labels use the construction-time str(idx+1) default.
        self._label_resolver: callable | None = None

        # _hit_radius_px is an instance attribute so update_mapper() can rescale it.
        # Module-level _HIT_RADIUS_PX is the documented initial default. A module-level
        # default argument would freeze at import time and never receive rescaling.
        self._hit_radius_px: float = float(_HIT_RADIUS_PX)

        self._build(team_configs)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _build(self, team_configs: dict) -> None:
        """Create all draw_circle and draw_text items from *team_configs*."""
        for team_name, cfg in team_configs.items():
            is_ball = cfg.get("is_ball", False)
            color = list(cfg["color"])
            n = cfg.get("n_players", 1)
            self._team_visible[team_name] = True
            team_list: list[_PlayerSlot] = []

            for idx in range(n):
                circle_tag = f"__player_{team_name}_{idx}_{self._uid}"
                label_tag = f"__plabel_{team_name}_{idx}_{self._uid}"

                if is_ball:
                    radius = _BALL_RADIUS
                    border_color = list(_BALL_BORDER_COLOR)
                    thickness = _BALL_BORDER
                else:
                    radius = _PLAYER_RADIUS
                    border_color = list(_PLAYER_BORDER_COLOR)
                    thickness = _PLAYER_BORDER

                dpg.draw_circle(
                    center=list(_OFFSCREEN),
                    radius=radius,
                    color=border_color,
                    fill=color,
                    thickness=thickness,
                    parent=self._parent_layer,  # layer-tag-aware
                    tag=circle_tag,
                )

                label_text = "" if is_ball else str(idx + 1)
                dpg.draw_text(
                    pos=list(_OFFSCREEN),
                    text=label_text,
                    color=_LABEL_COLOR,
                    size=_LABEL_SIZE,
                    parent=self._parent_layer,  # layer-tag-aware
                    tag=label_tag,
                )

                slot = _PlayerSlot(
                    team=team_name,
                    index=idx,
                    is_ball=is_ball,
                    circle_tag=circle_tag,
                    label_tag=label_tag,
                    base_color=list(color),
                    base_radius=radius,
                    border_color=border_color,
                    border_thickness=thickness,
                )
                self._slots.append(slot)
                team_list.append(slot)

            self._team_slots[team_name] = team_list

    # ------------------------------------------------------------------
    # Per-frame update
    # ------------------------------------------------------------------

    def update_positions(
        self,
        team_xy: dict[str, np.ndarray],
        ball_xy: tuple[float, float] | None = None,
    ) -> None:
        """Move every player circle to its current position.

        Parameters
        ----------
        team_xy : dict[str, np.ndarray]
            ``{team_name: array}`` where array is 1-D with layout
            ``[x0, y0, x1, y1, ...]`` for each player.
        ball_xy : tuple, optional
            ``(x, y)`` pitch coordinates of the ball.
        """
        for team_name, xy in team_xy.items():
            slots = self._team_slots.get(team_name)
            if slots is None:
                continue
            n_coords = len(xy)
            for slot in slots:
                i = slot.index
                xi = i * 2
                yi = xi + 1
                if yi >= n_coords:
                    self._hide_slot(slot)
                    continue

                x, y = float(xy[xi]), float(xy[yi])
                if math.isnan(x) or math.isnan(y):
                    self._hide_slot(slot)
                    continue

                self._show_slot(slot, x, y)

        # Ball
        if ball_xy is not None:
            bx, by = ball_xy
            for _team_name, slots in self._team_slots.items():
                for slot in slots:
                    if not slot.is_ball:
                        continue
                    if math.isnan(bx) or math.isnan(by):
                        self._hide_slot(slot)
                    else:
                        self._show_slot(slot, bx, by)

    def update_mapper(self, mapper: CoordinateMapper) -> None:
        """Update the coordinate mapper and rescale per-slot pixel radii.

        Called after the mapper has been re-fitted to the new drawlist size.
        Mutates Python state only: ``slot.base_radius`` is updated and
        ``self._hit_radius_px`` is rescaled. The DPG draw items are updated
        on the next ``_render_current_frame`` call from the resize-path caller.

        Notes
        -----
        This method does not issue any DPG per-item teardown calls. It is a
        resize-path handler and must not clear the player layer. Only the
        pitch renderer (a sport-change handler) is allowed the layer-clear.

        The ``max(3.0, ...)`` floor keeps players visible at tiny viewports
        where ``mapper.scale_distance(0.5)`` would otherwise drop below 1 px.

        ``_hit_radius_px`` is an instance attribute so the rescaling propagates
        to ``get_player_at`` at click time.

        Player radius is clamped to [7, 10] px (non-ball) and [5, 7] px (ball)
        so that both large pitches (soccer ~105x68 m) and small pitches
        (handball ~40x20 m) render at a visually consistent scale.
        """
        self._mapper = mapper

        non_ball_radius = max(7.0, min(10.0, mapper.scale_distance(_NON_BALL_RADIUS_M)))
        ball_radius = max(5.0, min(7.0, mapper.scale_distance(_BALL_RADIUS_M)))

        for slot in self._slots:
            slot.base_radius = ball_radius if slot.is_ball else non_ball_radius

        self._hit_radius_px = max(10.0, non_ball_radius * 3.0)

    def _show_slot(self, slot: _PlayerSlot, pitch_x: float, pitch_y: float) -> None:
        """Position a slot on the drawlist (if its team is visible)."""
        px, py = self._mapper.pitch_to_pixel(pitch_x, pitch_y)
        slot.hidden_nan = False
        prev_visible = slot.visible

        team_vis = self._team_visible.get(slot.team, True)
        ball_vis = self._ball_visible if slot.is_ball else True
        show = team_vis and ball_vis

        # Avoid redundant DPG configure calls when the slot did not move and
        # visibility did not change. This significantly reduces per-frame
        # overhead with many players.
        moved = (px != slot.px) or (py != slot.py)
        radius_dirty = slot.base_radius != slot.radius_pushed
        if moved or (show != slot.visible) or radius_dirty:
            # Push radius alongside center/show when base_radius has drifted
            # (set by update_mapper on viewport resize). Without this, circles
            # keep their construction-time radius until the next un-hover. The
            # dirty check is cheap and short-circuits on steady-state frames.
            if radius_dirty:
                dpg.configure_item(
                    slot.circle_tag,
                    center=(px, py),
                    show=show,
                    radius=slot.base_radius,
                )
                slot.radius_pushed = slot.base_radius
            else:
                dpg.configure_item(slot.circle_tag, center=(px, py), show=show)
            slot.px = px
            slot.py = py
            slot.visible = show

        label_show = show and (not slot.is_ball)
        label_show = label_show and self._show_labels
        if not slot.is_ball and (
            (label_show and moved) or (label_show != (prev_visible and self._show_labels))
        ):
            # Epsilon throttle: suppress sub-pixel-noise configure_item calls.
            # Compare against the label's own last position (slot.label_px/py),
            # not slot.px/py. Using slot.px as the anchor would compare against
            # a moving target because slot.px is updated on every visible-circle
            # move, causing the throttle to fire on every frame.
            label_pos_x = px - 4
            label_pos_y = py - slot.base_radius - _LABEL_SIZE - 2
            if abs(label_pos_x - slot.label_px) > 1.5 or abs(label_pos_y - slot.label_py) > 1.5:
                dpg.configure_item(
                    slot.label_tag,
                    pos=(label_pos_x, label_pos_y),
                    show=label_show,
                )
                # Update label position only when configure_item actually fires
                # to preserve the last-applied position as the throttle anchor.
                slot.label_px = label_pos_x
                slot.label_py = label_pos_y
        elif slot.is_ball and slot.visible:
            # Keep ball label hidden without touching it each frame.
            pass

    def _hide_slot(self, slot: _PlayerSlot) -> None:
        """Move a slot offscreen and mark it hidden (NaN frame)."""
        if slot.hidden_nan and (not slot.visible):
            return

        slot.hidden_nan = True
        slot.px = _OFFSCREEN[0]
        slot.py = _OFFSCREEN[1]
        # Reset label tracking so that when the slot is next shown, the
        # abs(label_pos - (-9999.0)) > 1.5 path guarantees a fresh
        # configure_item fires with no stale anchor from before the hide.
        slot.label_px = -9999.0
        slot.label_py = -9999.0
        slot.visible = False
        dpg.configure_item(slot.circle_tag, center=_OFFSCREEN, show=False)
        dpg.configure_item(slot.label_tag, pos=_OFFSCREEN, show=False)

    # ------------------------------------------------------------------
    # Hit-testing
    # ------------------------------------------------------------------

    def get_player_at(self, px: float, py: float, hit_radius: float | None = None) -> dict | None:
        """Return the nearest visible player within *hit_radius* pixels.

        Parameters
        ----------
        px, py : float
            Mouse position in drawlist pixel coordinates.
        hit_radius : float, optional
            Maximum distance in pixels for a hit. When None (default), reads
            ``self._hit_radius_px`` so viewport-proportional rescaling from
            ``update_mapper`` propagates correctly.

        Returns
        -------
        dict or None
            ``{"team": str, "player_index": int, "distance_px": float}``
            for the closest match, or ``None`` if nothing is within range.
        """
        if hit_radius is None:
            hit_radius = self._hit_radius_px
        best: dict | None = None
        hit_radius_sq = hit_radius * hit_radius
        best_dist_sq = hit_radius_sq + 1.0

        for slot in self._slots:
            if slot.hidden_nan or slot.is_ball:
                continue
            if not self._team_visible.get(slot.team, True):
                continue

            dx = px - slot.px
            dy = py - slot.py
            dist_sq = dx * dx + dy * dy
            if dist_sq < best_dist_sq:
                best_dist_sq = dist_sq
                best = {
                    "team": slot.team,
                    "player_index": slot.index,
                    "distance_px": math.sqrt(dist_sq),
                }

        return best if best is not None and best_dist_sq <= hit_radius_sq else None

    # ------------------------------------------------------------------
    # Hover highlight
    # ------------------------------------------------------------------

    def highlight_player(self, team: str, index: int) -> None:
        """Apply a hover highlight to one player (enlarged, brighter).

        Calling this again on a different player automatically clears the
        previous highlight first.
        """
        if self._highlighted is not None:
            self._restore_slot_style(self._highlighted)

        slot = self._find_slot(team, index)
        if slot is None:
            self._highlighted = None
            return

        bright = _brighten(slot.base_color, 60)
        dpg.configure_item(
            slot.circle_tag,
            radius=_HOVER_RADIUS,
            fill=bright,
            thickness=_HOVER_BORDER,
        )
        self._highlighted = slot

    def clear_highlight(self) -> None:
        """Remove the hover highlight and restore normal appearance."""
        if self._highlighted is not None:
            self._restore_slot_style(self._highlighted)
            self._highlighted = None

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def select_player(self, team: str, index: int) -> None:
        """Persistently select a player (gold border, slightly enlarged).

        Only one player can be selected at a time.
        """
        if self._selected is not None:
            self._restore_slot_style(self._selected)

        slot = self._find_slot(team, index)
        if slot is None:
            self._selected = None
            return

        dpg.configure_item(
            slot.circle_tag,
            radius=_SELECTED_RADIUS,
            color=_SELECTED_BORDER_COLOR,
            thickness=_SELECTED_BORDER,
        )
        self._selected = slot

    def deselect_player(self) -> None:
        """Clear the current selection and restore normal style."""
        if self._selected is not None:
            self._restore_slot_style(self._selected)
            self._selected = None

    def get_selected_player(self) -> dict | None:
        """Return the currently selected player.

        Returns
        -------
        dict or None
            ``{"team": str, "player_index": int}`` or ``None``.
        """
        if self._selected is None:
            return None
        return {"team": self._selected.team, "player_index": self._selected.index}

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def set_team_visible(self, team_name: str, visible: bool) -> None:
        """Show or hide every player on *team_name*."""
        self._team_visible[team_name] = visible
        slots = self._team_slots.get(team_name, [])
        for slot in slots:
            if slot.hidden_nan:
                continue  # already offscreen
            dpg.configure_item(slot.circle_tag, show=visible)
            dpg.configure_item(
                slot.label_tag,
                show=visible and (not slot.is_ball) and self._show_labels,
            )
            slot.visible = bool(visible)

    def set_ball_visible(self, visible: bool) -> None:
        """Show or hide the ball entity."""
        self._ball_visible = visible
        for slot in self._slots:
            if slot.is_ball and not slot.hidden_nan:
                dpg.configure_item(slot.circle_tag, show=visible)
                dpg.configure_item(slot.label_tag, show=False)
                slot.visible = bool(visible)

    def set_labels_visible(self, visible: bool) -> None:
        """Toggle jersey labels globally."""
        self._show_labels = bool(visible)
        for slot in self._slots:
            if slot.is_ball:
                continue
            show = self._show_labels and slot.visible and (not slot.hidden_nan)
            dpg.configure_item(slot.label_tag, show=show)

    def set_label_resolver(self, resolver) -> None:
        """Set a (team, idx) -> str | None callable to compute label text.

        Replaces the hard-coded ``str(idx+1)`` jersey labels with
        operator-chosen field content (jersey, name, position, pID, etc.).
        The resolver is called once per slot and the resolved text is pushed
        into the existing label_tag via configure_item -- no per-frame overhead.

        Passing ``None`` clears the resolver; labels keep their current text
        (typically ``str(idx+1)``). ``None`` returns from the resolver are
        coerced to empty strings.

        Parameters
        ----------
        resolver:
            Callable ``(team_name: str, player_index: int) -> str | None``,
            or ``None`` to clear the resolver.
        """
        self._label_resolver = resolver
        if resolver is None:
            return
        for slot in self._slots:
            if slot.is_ball:
                continue
            try:
                value = resolver(slot.team, slot.index)
            except Exception as exc:  # noqa: BLE001 -- resolver may raise on malformed teamsheets
                logger.debug(
                    "label_resolver raised for %s/%d: %s",
                    slot.team,
                    slot.index,
                    exc,
                )
                value = None
            text = "" if value is None else str(value)
            try:
                dpg.configure_item(slot.label_tag, text=text)
            except SystemError:
                logger.debug("Failed to configure label %s", slot.label_tag, exc_info=True)

    # ------------------------------------------------------------------
    # Cleanup / rebuild
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Delete every draw item owned by this renderer from the drawlist."""
        for slot in self._slots:
            try:
                dpg.delete_item(slot.circle_tag)
            except SystemError:  # DPG raises SystemError for missing items
                logger.debug("Failed to delete circle %s", slot.circle_tag, exc_info=True)
            try:
                dpg.delete_item(slot.label_tag)
            except SystemError:  # DPG raises SystemError for missing items
                logger.debug("Failed to delete label %s", slot.label_tag, exc_info=True)

        self._slots.clear()
        self._team_slots.clear()
        self._team_visible.clear()
        self._highlighted = None
        self._selected = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_slot(self, team: str, index: int) -> _PlayerSlot | None:
        """Look up a slot by team name and player index."""
        slots = self._team_slots.get(team)
        if slots is None:
            return None
        if 0 <= index < len(slots):
            return slots[index]
        return None

    def _restore_slot_style(self, slot: _PlayerSlot) -> None:
        """Reset a slot to its default radius, colour, and border."""
        dpg.configure_item(
            slot.circle_tag,
            radius=slot.base_radius,
            fill=slot.base_color,
            color=slot.border_color,
            thickness=slot.border_thickness,
        )
        # Keep the dirty tracker in sync -- un-hover pushes the same radius
        # that _show_slot would otherwise push on the next frame.
        slot.radius_pushed = slot.base_radius


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _brighten(color: list[int], amount: int = 60) -> list[int]:
    """Return a brighter version of an RGBA colour list."""
    return [min(c + amount, 255) if i < 3 else color[3] for i, c in enumerate(color)]
