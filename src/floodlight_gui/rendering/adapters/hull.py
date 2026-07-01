"""Stable-tag polygon overlay adapter for ConvexHullModel(s).

Lives in the DPG-aware rendering layer; must not be imported from backend modules.
One adapter instance owns all team hulls for a single viz session.
Reads the public-by-convention post-fit attribute ``model._convex_hulls_``.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import dearpygui.dearpygui as dpg
import numpy as np

from floodlight_gui.rendering.coordinate_mapper import CoordinateMapper

logger = logging.getLogger(__name__)


_HULL_ALPHA = 90  # 0-255
_HULL_BORDER = 2  # polygon line thickness


class HullAdapter:
    """Stable-tag polygon overlay for ConvexHullModel(s), supporting one or more teams.

    Per-frame update path calls ``dpg.configure_item(poly_tag, points=...)`` on a
    stable per-team tag. The tag is created once on first draw and reused every
    frame; ``dpg.delete_item`` is never called from ``update_frame`` or
    ``set_visible``. Only ``clear`` and rebind (via ``_bind_team``) may delete tags.
    """

    # DPG widget tags this adapter wants the dispatcher to reveal when bound.
    # HullAdapter has no companion UI widgets (the hull overlay is controlled
    # solely by its checkbox), so the tuple is empty.
    UI_WIDGET_TAGS: tuple[str, ...] = ()

    @classmethod
    def build_init_kwargs(
        cls,
        *,
        model,
        team_name: str,
        payload: dict,
        viz_state: dict,
    ) -> dict:
        """Build the kwarg dict for ``HullAdapter.init`` from a MODEL_FITTED payload.

        Lifts per-adapter kwarg-shape knowledge out of the visualization-tab
        dispatcher and onto the adapter class itself. Color resolution stays in
        the dispatcher (``_TEAM_COLORS`` / ``_COLOR_CYCLE``) and is passed in via
        ``payload["color"]``; this classmethod only normalizes the shape.

        This method must not import dearpygui. ``init`` (called by the dispatcher
        with the returned kwargs) is the only DPG-bearing entry point.

        Parameters
        ----------
        model : ConvexHullModel
            Fitted model instance to bind.
        team_name : str
            Display name for the team.
        payload : dict
            MODEL_FITTED event payload; must contain a ``"color"`` key.
        viz_state : dict
            Unused; accepted for dispatcher call-signature uniformity.

        Returns
        -------
        dict
            Kwargs suitable for passing directly to ``HullAdapter.init``.
        """
        return {
            "team_name": team_name,
            "model": model,
            "color": payload["color"],
        }

    @classmethod
    def ui_widget_tags(cls) -> tuple[str, ...]:
        """Return DPG tags this adapter wants revealed when bound (empty for HullAdapter)."""
        return cls.UI_WIDGET_TAGS

    def __init__(self, drawlist_tag: str | int, mapper: CoordinateMapper) -> None:
        """Initialise the adapter without touching DPG; no polygons are created yet.

        Parameters
        ----------
        drawlist_tag : str or int
            DPG drawlist tag the adapter draws into.
        mapper : CoordinateMapper
            Active coordinate mapper for pitch-to-pixel conversion.
        """
        self._drawlist = drawlist_tag
        self._mapper = mapper
        self._uid = id(self)  # per-instance UID; guarantees tag uniqueness across instances
        self._parent_layer: str | int | None = None
        # {team_name: {"model": ConvexHullModel, "color": [R,G,B,A], "poly_tag": str | None}}
        self._teams: dict[str, dict[str, Any]] = {}
        self._hull_visible: bool = True

    # ---- lifecycle --------------------------------------------------------- #

    def init(
        self,
        parent_layer_tag: str | int,
        *,
        team_name: str,
        model,
        color: list[int],
    ) -> None:
        """Bind the first team and record the parent draw layer.

        Subsequent teams are added via ``add_team``. The parent layer tag must
        be the overlay layer (``__overlay_layer``); polygons are always parented
        to it.

        Parameters
        ----------
        parent_layer_tag : str or int
            DPG draw_layer tag that owns the hull polygons.
        team_name : str
            Display name for the team; used in the stable per-team polygon tag.
        model : ConvexHullModel
            Fitted floodlight model exposing ``_convex_hulls_`` (public-by-convention
            post-fit attribute).
        color : list[int]
            RGBA color for the polygon outline / fill. List of 3 or 4 ints (0-255).
        """
        self._parent_layer = parent_layer_tag
        self._bind_team(team_name, model, color)

    def add_team(self, team_name: str, model, color: list[int]) -> None:
        """Bind another team's hull to this adapter (multi-team support).

        Parameters
        ----------
        team_name : str
            Display name for the team.
        model : ConvexHullModel
            Fitted model to bind for this team.
        color : list[int]
            RGBA color list (3 or 4 ints, 0-255).
        """
        self._bind_team(team_name, model, color)

    def has_team(self, team_name: str) -> bool:
        """Return True if this adapter already holds a hull binding for ``team_name``.

        The viz tab dispatcher uses this to choose between ``init`` (first bind)
        and ``add_team`` (subsequent fits).

        Parameters
        ----------
        team_name : str
            Team name to check.

        Returns
        -------
        bool
        """
        return team_name in self._teams

    def update_frame(self, t: int) -> None:
        """Render hull polygons for frame ``t`` using stable per-team DPG tags.

        For each bound team, the polygon tag is created once on first draw and
        reused every frame via ``configure_item`` (no delete-and-recreate per
        frame). When the hull is invisible or absent for frame ``t``, the polygon
        is hidden via ``configure_item(show=False)`` rather than deleted.

        Parameters
        ----------
        t : int
            Frame index into each model's ``_convex_hulls_`` list.
        """
        for team_name, entry in self._teams.items():
            model = entry["model"]
            color = entry["color"]
            poly_tag = f"__overlay_hull_{team_name}_{self._uid}"

            if not self._hull_visible:
                if dpg.does_item_exist(poly_tag):
                    with contextlib.suppress(SystemError):
                        dpg.configure_item(poly_tag, show=False)
                continue

            hulls = model._convex_hulls_  # public-by-convention post-fit attribute
            if t >= len(hulls) or hulls[t] is None:
                if dpg.does_item_exist(poly_tag):
                    with contextlib.suppress(SystemError):
                        dpg.configure_item(poly_tag, show=False)
                continue

            hull = hulls[t]
            try:
                verts = hull.points[hull.vertices]
                verts = np.vstack([verts, verts[0:1]])  # close polygon
            except (AttributeError, IndexError, TypeError):
                continue

            px_pts = [list(self._mapper.pitch_to_pixel(float(v[0]), float(v[1]))) for v in verts]
            fill = list(color[:3]) + [_HULL_ALPHA]
            border = list(color[:3]) + [200]

            if dpg.does_item_exist(poly_tag):
                # Steady-state: update in place.
                with contextlib.suppress(SystemError):
                    dpg.configure_item(
                        poly_tag,
                        points=px_pts,
                        color=border,
                        fill=fill,
                        show=True,
                    )
                entry["poly_tag"] = poly_tag
                continue

            # First-time create.
            try:
                dpg.draw_polygon(
                    points=px_pts,
                    color=border,
                    fill=fill,
                    thickness=_HULL_BORDER,
                    parent=self._parent_layer,  # always __overlay_layer
                    tag=poly_tag,
                )
                entry["poly_tag"] = poly_tag
            except (SystemError, ValueError, TypeError):
                logger.debug(
                    "Failed to draw hull polygon for %s",
                    team_name,
                    exc_info=True,
                )

    def set_visible(self, visible: bool) -> None:
        """Show or hide all team polygons via ``configure_item`` (no teardown).

        Parameters
        ----------
        visible : bool
            Target visibility state.
        """
        self._hull_visible = visible
        for entry in self._teams.values():
            tag = entry.get("poly_tag")
            if tag is not None:
                with contextlib.suppress(SystemError):
                    dpg.configure_item(tag, show=visible)

    def clear(self) -> None:
        """Tear down all team polygons and reset adapter state.

        ``delete_item`` is safe here because this is not the per-frame path.
        """
        for entry in self._teams.values():
            tag = entry.get("poly_tag")
            if tag is not None:
                with contextlib.suppress(SystemError):
                    dpg.delete_item(tag)
                entry["poly_tag"] = None
        self._teams.clear()

    # ---- internals --------------------------------------------------------- #

    def _bind_team(self, team_name: str, model, color: list[int]) -> None:
        """Register or replace the hull binding for a single team.

        On rebind, the prior polygon tag is deleted so the next ``update_frame``
        call recreates it with the new model's data. ``delete_item`` is safe here
        because this is not the per-frame path.

        Parameters
        ----------
        team_name : str
            Team identifier used in the stable polygon tag.
        model : ConvexHullModel
            Fitted model to bind.
        color : list[int]
            RGBA color list (3 or 4 ints, 0-255).
        """
        existing = self._teams.get(team_name)
        if existing is not None:
            prior_tag = existing.get("poly_tag")
            if prior_tag is not None:
                with contextlib.suppress(SystemError):
                    dpg.delete_item(prior_tag)
            existing["model"] = model
            existing["color"] = list(color)
            existing["poly_tag"] = None
            return
        self._teams[team_name] = {
            "model": model,
            "color": list(color),
            "poly_tag": None,
        }
