"""Rasterized Voronoi texture overlay adapter for DiscreteVoronoiModel.

Lives in the rendering layer (DPG-aware); imported by the visualization tab's
overlay dispatcher. Reads public-by-convention fit-state attributes from the
model (_meshx_, _meshy_, _xpolysize_, _ypolysize_, _N1_, _cell_controls_).

Per-frame lifecycle: ``update_frame`` mutates a pre-allocated GPU texture via
``dpg.set_value`` without tearing down the texture or the draw_image item.
Teardown happens only in ``clear``.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import time
from typing import Any

import dearpygui.dearpygui as dpg
import numpy as np

from floodlight_gui.rendering.coordinate_mapper import CoordinateMapper
from floodlight_gui.rendering.voronoi_colors import (
    build_voronoi_palette,
    fill_voronoi_rgba,
)

logger = logging.getLogger(__name__)


_VORONOI_DEFAULT_ALPHA = 0.3

# Window size for the upload-time moving average. Must stay in sync with
# the FPS_SAMPLE_FRAMES constant in visualization_tab.py so the FPS overlay
# and the last_upload_ms property cover the same window.
_FPS_SAMPLE_FRAMES = 60


class VoronoiAdapter:
    """Rasterized texture overlay for DiscreteVoronoiModel.

    Per-frame updates mutate pixels of a pre-allocated GPU texture via
    ``dpg.set_value``; the texture and draw_image item are never re-created
    between frames. Hiding the overlay uses ``configure_item(show=False)``
    on the draw_image item only.

    Notes
    -----
    DPG tags owned by an instance: ``__overlay_vor_reg_<uid>``,
    ``__overlay_vor_tex_<uid>``, ``__overlay_vor_img_<uid>``.
    The draw_image is attached to ``parent_layer_tag`` passed to ``init``
    (always the ``__overlay_layer`` in the visualization tab).
    """

    # DPG tags for the alpha-slider row in the viz tab (spacer, label, slider).
    # The overlay dispatcher reveals these together with the Voronoi checkbox
    # when the adapter is bound.
    UI_WIDGET_TAGS: tuple[str, ...] = (
        "viz_voronoi_alpha_spacer",
        "viz_voronoi_alpha_label",
        "viz_voronoi_alpha",
    )

    @classmethod
    def build_init_kwargs(
        cls,
        *,
        model,
        team_name: str,  # noqa: ARG003 - unused; kept for uniform dispatcher signature
        payload: dict,
        viz_state: dict,
    ) -> dict:
        """Build the kwarg dict for ``VoronoiAdapter.init`` from a MODEL_FITTED payload.

        Lifts per-adapter kwarg-shape knowledge out of the visualization-tab
        dispatcher onto the adapter class itself.

        ``n_team1`` is resolved from the fitted model's ``_N1_`` attribute
        (with TypeError/ValueError fallback to 0). ``alpha`` is read from
        ``viz_state["voronoi_alpha_value"]`` to honour the persisted slider
        value. ``team1_color``, ``team2_color``, and ``fit_half`` are accepted
        pre-resolved via ``payload`` (the dispatcher resolves them before
        calling this method).

        ``team_name`` is part of the uniform dispatcher signature shared with
        HullAdapter; it is ignored here because Voronoi is a cross-team overlay
        that needs both teams' colors at init time.

        Parameters
        ----------
        model :
            Fitted DiscreteVoronoiModel instance.
        team_name : str
            Accepted for dispatcher-signature uniformity; not used.
        payload : dict
            Must contain ``team1_color``, ``team2_color``, and optionally
            ``fit_half``.
        viz_state : dict
            Visualization state dict; ``voronoi_alpha_value`` is read if
            present.

        Returns
        -------
        dict
            Kwarg dict suitable for passing as ``**kwargs`` to ``init``.

        Notes
        -----
        This method must not import ``dearpygui`` (called from a DPG-free path
        in the dispatcher).
        """
        try:
            n_team1 = int(getattr(model, "_N1_", 0))
        except (TypeError, ValueError):
            n_team1 = 0
        return {
            "model": model,
            "team1_color": payload["team1_color"],
            "team2_color": payload["team2_color"],
            "n_team1": n_team1,
            "alpha": viz_state.get("voronoi_alpha_value", _VORONOI_DEFAULT_ALPHA),
            "fit_half": payload.get("fit_half"),
        }

    @classmethod
    def ui_widget_tags(cls) -> tuple[str, ...]:
        """Return DPG tags this adapter wants revealed when bound."""
        return cls.UI_WIDGET_TAGS

    def __init__(self, drawlist_tag: str | int, mapper: CoordinateMapper) -> None:
        """Initialise instance state; does not allocate any DPG resources.

        Parameters
        ----------
        drawlist_tag : str or int
            Tag of the DPG drawlist this adapter renders into.
        mapper : CoordinateMapper
            Initial coordinate mapper (updated via ``update_mapper``).
        """
        self._drawlist = drawlist_tag
        self._mapper = mapper
        self._uid = id(self)

        self._parent_layer: str | int | None = None
        self._model: Any = None
        self._visible: bool = True

        self._registry_tag: str | None = None
        self._texture_tag: str | None = None
        self._image_tag: str | None = None

        self._buf: np.ndarray | None = None
        self._flat: np.ndarray | None = None

        self._c1: np.ndarray | None = None
        self._c2: np.ndarray | None = None
        # Pre-built palette and index-map buffer for palette-gather coloring.
        self._palette: np.ndarray | None = None  # (n_team1 + n_team2 + 1, 4) float32
        self._idx_map: np.ndarray | None = None  # (ny, nx) int64 gather index buffer
        self._n_team1: int = 0
        self._alpha: float = _VORONOI_DEFAULT_ALPHA

        # Square meshes map one texel per cell (regular grid). Hexagonal meshes
        # are staggered, so they are rasterized at an upscaled resolution via a
        # static texel-to-nearest-cell map (nearest-center regions of a staggered
        # hex grid are hexagons).
        self._hex_mode: bool = False
        self._hex_texel_to_cell: np.ndarray | None = None  # (tex_h, tex_w) flat cell idx
        self._hex_frame_buf: np.ndarray | None = None  # (tex_h, tex_w) gathered controls
        # Pitch-space texture bbox (x_lo, x_hi, y_lo, y_hi); pixel pmin/pmax are
        # derived from it in init() and re-derived on resize in update_mapper().
        self._tex_bbox: tuple[float, float, float, float] | None = None

        # Half the model was fit on; used by the fit/viz mismatch guard in
        # update_frame to suppress stale frames.
        self._fit_half_: str | None = None

        # Cache state for byte-identical-frame short-circuit.
        self._last_frame_data: np.ndarray | None = (
            None  # lazy-allocated; (ny, nx) float64 snapshot of the last served frame
        )
        self._last_t: int | None = None  # last served frame index
        self._palette_dirty: bool = True  # set True on alpha/colors/n_team1/mapper change

        # Upload performance counters.
        self._frames_uploaded: int = 0  # cumulative dpg.set_value invocations since last init
        self._frames_skipped: int = 0  # cumulative cache hits since last init
        self._upload_times_ms: collections.deque = collections.deque(maxlen=_FPS_SAMPLE_FRAMES)

        self._numpy_upload: bool = True

    # ---- hexagonal tessellation helper ---------------------------------- #

    @staticmethod
    def _build_hex_texture_geometry(meshx, meshy, xpolysize: float):
        """Build the upscaled texture dimensions and texel-to-nearest-cell map for a hex mesh.

        Returns ``(tex_w, tex_h, (x_lo, x_hi, y_lo, y_hi), texel_to_cell)`` where
        ``texel_to_cell`` is a ``(tex_h, tex_w)`` array of flat cell indices: each
        texel is assigned to the nearest mesh point. Because floodlight staggers
        the hex grid (odd rows offset by half a cell), the nearest-center region
        of each point is a hexagon, so the upscaled raster renders true hexagons
        while the per-frame path stays a single texture upload.

        The pitch-space bbox is padded by one circumradius (``xpolysize``) so the
        outermost hexagons are not clipped. Texel rows run top-down to match
        floodlight's top-down ``meshy`` convention.

        Parameters
        ----------
        meshx : np.ndarray
            2-D array of x mesh-point coordinates, shape (ny, nx).
        meshy : np.ndarray
            2-D array of y mesh-point coordinates, shape (ny, nx).
        xpolysize : float
            Circumradius of one hexagonal cell (used for bbox padding).

        Returns
        -------
        tex_w : int
        tex_h : int
        bbox : tuple of float
            (x_lo, x_hi, y_lo, y_hi) in pitch-space coordinates.
        texel_to_cell : np.ndarray
            Shape ``(tex_h, tex_w)``, dtype int; flat cell index for each texel.
        """
        from scipy.spatial import cKDTree

        ny, nx = meshx.shape
        # Upscale so hexagon edges resolve, capping the larger texture dim so the
        # per-frame upload stays bounded regardless of mesh density.
        scale = max(2, min(10, round(360 / max(nx, 1))))
        tex_w, tex_h = nx * scale, ny * scale

        pad = float(xpolysize)
        x_lo = float(meshx.min()) - pad
        x_hi = float(meshx.max()) + pad
        y_lo = float(meshy.min()) - pad
        y_hi = float(meshy.max()) + pad

        # Texel centers; rows top-down (y_hi to y_lo) to match meshy.
        xs = x_lo + (np.arange(tex_w) + 0.5) / tex_w * (x_hi - x_lo)
        ys = y_hi - (np.arange(tex_h) + 0.5) / tex_h * (y_hi - y_lo)
        gx, gy = np.meshgrid(xs, ys)

        centers = np.column_stack([meshx.ravel(), meshy.ravel()])
        _, idx = cKDTree(centers).query(np.column_stack([gx.ravel(), gy.ravel()]))
        return tex_w, tex_h, (x_lo, x_hi, y_lo, y_hi), idx.reshape(tex_h, tex_w)

    # ---- adapter lifecycle ---------------------------------------------- #

    def init(
        self,
        parent_layer_tag: str | int,
        *,
        model: Any,
        team1_color: list[int],
        team2_color: list[int],
        n_team1: int,
        alpha: float = _VORONOI_DEFAULT_ALPHA,
        fit_half: str | None = None,
    ) -> None:
        """Allocate a GPU texture and draw_image item; pre-allocate numpy buffers.

        Idempotent: calling ``init`` on an already-initialised adapter runs
        ``clear`` first so rapid re-fits on the same instance do not leak DPG
        resources.

        The texture dimensions are mesh-bound and determined once here; they
        do not change on viewport resize (only the pixel rect the texture is
        stretched into changes, see ``update_mapper``).

        ``n_team2`` is inferred from the maximum xID observed across the full
        time axis of ``_cell_controls_`` so the palette covers every xID that
        ever appears (the gather in ``update_frame`` is therefore IndexError-safe
        even for asymmetric team subsets).

        Parameters
        ----------
        parent_layer_tag : str or int
            DPG tag of the overlay drawlist layer the draw_image is attached to.
        model :
            Fitted DiscreteVoronoiModel instance. Must expose ``_meshx_``,
            ``_meshy_``, ``_xpolysize_``, ``_ypolysize_``, ``_N1_``,
            ``_cell_controls_``, and optionally ``_mesh_type``.
        team1_color, team2_color : list of int
            RGB triplets (0-255) for the two teams.
        n_team1 : int
            Number of team-1 players, used to split the palette.
        alpha : float
            Initial cell opacity (0.0 to 1.0).
        fit_half : str or None
            Half label the model was fit on; stored for the fit/viz mismatch
            guard in ``update_frame``.

        Notes
        -----
        DPG side-effects: creates ``__overlay_vor_reg_<uid>`` (texture registry),
        ``__overlay_vor_tex_<uid>`` (raw texture), and ``__overlay_vor_img_<uid>``
        (draw_image attached to ``parent_layer_tag``).
        """
        if self._texture_tag is not None or self._image_tag is not None:
            self.clear()

        self._parent_layer = parent_layer_tag
        self._model = model
        self._n_team1 = n_team1
        self._alpha = alpha
        # Stored so update_frame can hide the overlay when the active viz half
        # differs from the half the model was fit on.
        self._fit_half_ = fit_half

        meshx = model._meshx_  # public-by-convention attribute
        meshy = model._meshy_
        ny, nx = meshx.shape
        half_dx = model._xpolysize_ / 2.0
        half_dy = model._ypolysize_ / 2.0

        # Square meshes map one texel per cell on a regular grid.
        # Hexagonal meshes are staggered (odd rows offset by half a cell), so a
        # 1-texel-per-cell grid flattens hexagons into squares. Instead, rasterize
        # at an upscaled resolution and assign each texel to its nearest mesh
        # point; the nearest-center regions of a staggered hex grid are hexagons,
        # so the per-frame cost remains a single texture upload.
        self._hex_mode = getattr(model, "_mesh_type", "square") == "hexagonal"
        if self._hex_mode:
            tex_w, tex_h, self._tex_bbox, self._hex_texel_to_cell = (
                self._build_hex_texture_geometry(meshx, meshy, float(model._xpolysize_))
            )
            self._hex_frame_buf = np.empty((tex_h, tex_w), dtype=np.float64)
        else:
            tex_w, tex_h = nx, ny
            self._hex_texel_to_cell = None
            self._hex_frame_buf = None
            # floodlight builds the mesh top-down (y = linspace(ymax, ymin, ...)),
            # so meshy[0, :] is the TOP and meshy[-1, :] is the BOTTOM. The
            # mapper's pitch_to_pixel flips y. The bbox therefore sources y from
            # meshy[0, 0] (top) for the upper edge and meshy[-1, 0] (bottom) for
            # the lower edge, with half_dy extending outward from cell center to
            # cell edge.
            self._tex_bbox = (
                float(meshx[0, 0]) - half_dx,
                float(meshx[0, -1]) + half_dx,
                float(meshy[-1, 0]) - half_dy,
                float(meshy[0, 0]) + half_dy,
            )

        self._registry_tag = f"__overlay_vor_reg_{self._uid}"
        self._texture_tag = f"__overlay_vor_tex_{self._uid}"
        self._image_tag = f"__overlay_vor_img_{self._uid}"

        flat_default = [0.0] * (tex_w * tex_h * 4)
        with dpg.texture_registry(tag=self._registry_tag):
            dpg.add_raw_texture(
                width=tex_w,
                height=tex_h,
                default_value=flat_default,
                format=dpg.mvFormat_Float_rgba,
                tag=self._texture_tag,
            )

        x_lo, x_hi, y_lo, y_hi = self._tex_bbox
        pmin = self._mapper.pitch_to_pixel(x_lo, y_hi)  # upper-left (y flipped)
        pmax = self._mapper.pitch_to_pixel(x_hi, y_lo)  # lower-right
        dpg.draw_image(
            self._texture_tag,
            pmin=list(pmin),
            pmax=list(pmax),
            parent=self._parent_layer,
            tag=self._image_tag,
        )

        self._buf = np.zeros((tex_h, tex_w, 4), dtype=np.float32)
        self._flat = self._buf.ravel()

        self._c1 = np.array(team1_color[:3], dtype=np.float32) / 255.0
        self._c2 = np.array(team2_color[:3], dtype=np.float32) / 255.0

        # Pre-build the per-frame palette and allocate the gather-index buffer.
        # The palette is rebuilt only on set_alpha (or re-init); idx_map is
        # allocated once at texture dimensions and reused every frame.
        #
        # n_team2 is inferred from nanmax over the full time axis so the palette
        # covers every xID that ever appears, even for asymmetric team subsets.
        controls_max = int(np.nanmax(model._cell_controls_)) if model._cell_controls_.size else 0
        n_team2 = max(0, controls_max + 1 - n_team1)

        self._palette = build_voronoi_palette(self._c1, self._c2, self._alpha, n_team1, n_team2)
        # idx_map is the gather scratch sized to the texture (cell grid for
        # square; upscaled hex raster for hexagonal).
        self._idx_map = np.zeros((tex_h, tex_w), dtype=np.int64)

        # Fresh init starts from a clean cache and zero counters.
        self._last_frame_data = None
        self._last_t = None
        self._palette_dirty = False  # palette was just built
        self._frames_uploaded = 0
        self._frames_skipped = 0
        self._upload_times_ms.clear()

        self._numpy_upload = True

    def update_mapper(self, mapper: CoordinateMapper) -> None:
        """Update the coordinate mapper and re-anchor the texture's pixel bbox.

        Called after the mapper has been re-fitted to a new drawlist size.
        The texture dimensions are mesh-bound and do not change on resize;
        only the pixel rectangle the texture is stretched into is updated via
        ``configure_item`` on the existing draw_image item.

        Safe to call before ``init`` (no-op when ``_image_tag`` or ``_model``
        is ``None``).

        Parameters
        ----------
        mapper : CoordinateMapper
            The new coordinate mapper.

        Notes
        -----
        DPG side-effect: calls ``configure_item`` on the draw_image item with
        updated ``pmin`` / ``pmax``. The texture itself is not re-created.
        Invalidates the frame cache (sets ``_palette_dirty = True``) so the
        next ``update_frame`` recomputes the buffer against the new mapping.
        """
        self._mapper = mapper
        if self._model is None or self._image_tag is None or self._tex_bbox is None:
            return  # init() has not run yet; nothing to re-anchor

        # Re-anchor the pixel rect from the fixed pitch-space bbox stored at
        # init. Only the pixel mapping changes on resize, not the mesh extent.
        x_lo, x_hi, y_lo, y_hi = self._tex_bbox
        pmin = self._mapper.pitch_to_pixel(x_lo, y_hi)
        pmax = self._mapper.pitch_to_pixel(x_hi, y_lo)
        with contextlib.suppress(SystemError):
            dpg.configure_item(self._image_tag, pmin=list(pmin), pmax=list(pmax))
        # Invalidate the frame cache: the mapper change does not alter pixel
        # content, but a stale cache entry after resize would skip the first
        # recompute and render the old pixel rect momentarily.
        self._palette_dirty = True

    def update_frame(self, t: int) -> None:
        """Mutate the GPU texture pixels for frame *t* via ``dpg.set_value``.

        Called every render frame. Never tears down or re-creates the texture
        or the draw_image item. The hide path uses ``configure_item(show=...)``
        on the draw_image item only.

        Frame-identical short-circuit: if the frame data is byte-identical to
        the last served frame AND the palette has not changed since, the buffer
        rebuild and ``dpg.set_value`` upload are skipped entirely.

        ``equal_nan=True`` is required for the identity check because
        DiscreteVoronoiModel uses NaN as the "no controlling player" sentinel
        in ``_cell_controls_``; without it, frames containing NaNs would always
        compare unequal and the cache would never hit on production data.

        Fit/viz half mismatch guard: when the model was fit on a specific half
        but the visualization tab has switched to a different half, the overlay
        is hidden until the user re-fits for the active half. This prevents
        serving stale cell-controls frames at the wrong time axis.

        Parameters
        ----------
        t : int
            Frame index into ``model._cell_controls_`` (axis 0).

        Notes
        -----
        DPG side-effect: calls ``dpg.set_value`` on the texture tag and
        ``configure_item`` on the draw_image tag each frame.
        """
        if self._model is None or self._texture_tag is None:
            return

        # Hide the overlay when the active viz half differs from the fit half,
        # preventing stale cell-controls frames from being served at the wrong
        # time axis. Lazy import keeps this module free of module-level tabs.*
        # imports; the broad except guards the render-loop boundary.
        if self._fit_half_ is not None:
            try:
                from floodlight_gui.tabs.visualization import state as _state

                current_half = _state.viz_state.get("selected_half")
            except Exception:  # noqa: BLE001 - render-loop boundary
                current_half = None
            if current_half is not None and current_half != self._fit_half_:
                if self._image_tag is not None:
                    with contextlib.suppress(SystemError):
                        dpg.configure_item(self._image_tag, show=False)
                return

        if self._image_tag is not None:
            with contextlib.suppress(SystemError):
                dpg.configure_item(self._image_tag, show=self._visible)
        if not self._visible:
            return

        controls = self._model._cell_controls_  # public-by-convention attribute
        if t >= controls.shape[0]:
            return
        frame_data = controls[t]  # shape (ny, nx)

        # Cache skip: bypass buffer rebuild and DPG upload when frame data is
        # byte-identical to the last served frame and the palette is still clean.
        # This check runs after the fit-half guard and the visibility short-circuit
        # so hidden / wrong-half frames never pollute the skip counter.
        if (
            not self._palette_dirty
            and self._last_frame_data is not None
            and frame_data.shape == self._last_frame_data.shape
            and np.array_equal(frame_data, self._last_frame_data, equal_nan=True)
        ):
            self._frames_skipped += 1
            return

        # Palette-gather buffer rebuild. The palette is pre-built in init() and
        # rebuilt only on set_alpha, so the per-frame cost is one np.where plus
        # one np.take.
        if self._hex_mode:
            # Expand the (ny, nx) cell frame to the (tex_h, tex_w) raster via
            # the static texel-to-nearest-cell map, then classify at texture
            # resolution. NaN (uncontrolled) cells propagate through the gather
            # and are handled by fill_voronoi_rgba's transparent sentinel.
            np.take(frame_data.ravel(), self._hex_texel_to_cell, out=self._hex_frame_buf)
            classify_frame = self._hex_frame_buf
        else:
            classify_frame = frame_data
        fill_voronoi_rgba(
            classify_frame,
            self._n_team1,
            self._palette,
            out_buf=self._buf,
            idx_map=self._idx_map,
        )

        # Snapshot the served frame for the next cache check and mark the
        # palette clean (palette is now consistent with the computed buffer).
        if self._last_frame_data is None or self._last_frame_data.shape != frame_data.shape:
            self._last_frame_data = np.empty_like(frame_data)
        np.copyto(self._last_frame_data, frame_data)
        self._last_t = t
        self._palette_dirty = False

        # Time the upload. The ndarray-vs-list fallback is preserved: some DPG
        # builds reject a numpy view for set_value; fall back to .tolist() once
        # and stay there for the session.
        _t0 = time.perf_counter()
        if self._numpy_upload and self._flat is not None:
            try:
                dpg.set_value(self._texture_tag, self._flat)
                self._frames_uploaded += 1
                self._upload_times_ms.append((time.perf_counter() - _t0) * 1000.0)
                return
            except (SystemError, TypeError):
                self._numpy_upload = False
        dpg.set_value(self._texture_tag, self._buf.flatten().tolist())
        self._frames_uploaded += 1
        self._upload_times_ms.append((time.perf_counter() - _t0) * 1000.0)

    def set_visible(self, visible: bool) -> None:
        """Show or hide the overlay without tearing down any DPG state.

        Parameters
        ----------
        visible : bool
            Target visibility.

        Notes
        -----
        DPG side-effect: calls ``configure_item(show=visible)`` on the
        draw_image item.
        """
        self._visible = visible
        if self._image_tag is not None:
            with contextlib.suppress(SystemError):
                dpg.configure_item(self._image_tag, show=visible)

    def set_alpha(self, alpha: float) -> None:
        """Update the per-cell alpha; takes effect on the next ``update_frame`` call.

        Rebuilds the palette immediately so ``update_frame`` picks up the new
        RGBA values without an additional init cycle. Sets ``_palette_dirty``
        so the cache is invalidated and the next frame is uploaded with the
        new palette.

        Parameters
        ----------
        alpha : float
            New opacity (0.0 to 1.0).
        """
        self._alpha = float(alpha)
        if self._palette is not None and self._c1 is not None and self._c2 is not None:
            # Derive n_team2 from the existing palette shape: palette has
            # n_team1 + n_team2 + 1 rows.
            n_team1 = self._n_team1
            n_team2 = self._palette.shape[0] - 1 - n_team1
            self._palette = build_voronoi_palette(self._c1, self._c2, self._alpha, n_team1, n_team2)
        # Invalidate cache so the next update_frame uploads with the new palette.
        self._palette_dirty = True

    @property
    def frames_uploaded(self) -> int:
        """Cumulative ``dpg.set_value`` invocations since the last ``init``."""
        return self._frames_uploaded

    @property
    def frames_skipped(self) -> int:
        """Cumulative cache hits (frame-identical skips) since the last ``init``."""
        return self._frames_skipped

    @property
    def last_upload_ms(self) -> float:
        """Moving-average upload time in ms over the last ``_FPS_SAMPLE_FRAMES`` uploads.

        Returns 0.0 when no uploads have occurred since the last ``init`` or
        ``clear``.
        """
        n = len(self._upload_times_ms)
        return (sum(self._upload_times_ms) / n) if n else 0.0

    def clear(self) -> None:
        """Tear down all DPG resources owned by this adapter and reset state.

        Deletion order: draw_image, then texture, then texture registry
        (consumers before producers). Each deletion is wrapped in
        ``contextlib.suppress(SystemError)`` because a missing item raises
        ``SystemError`` in DPG. Numpy buffer references are dropped after DPG
        cleanup.

        Notes
        -----
        DPG side-effect: deletes ``_image_tag``, ``_texture_tag``, and
        ``_registry_tag`` if they exist.
        """
        for tag in (self._image_tag, self._texture_tag, self._registry_tag):
            if tag is not None:
                with contextlib.suppress(SystemError):
                    dpg.delete_item(tag)

        self._image_tag = None
        self._texture_tag = None
        self._registry_tag = None

        self._model = None
        self._buf = None
        self._flat = None
        self._c1 = None
        self._c2 = None
        self._n_team1 = 0
        # Reset tessellation state so a re-init (e.g. refit square after hex)
        # starts clean.
        self._hex_mode = False
        self._hex_texel_to_cell = None
        self._hex_frame_buf = None
        self._tex_bbox = None
        # Reset fit_half so re-init without the kwarg starts from a clean state.
        self._fit_half_ = None
        # Reset cache state and counters so a re-init starts from zero.
        self._palette = None
        self._idx_map = None
        self._last_frame_data = None
        self._last_t = None
        self._palette_dirty = True
        self._frames_uploaded = 0
        self._frames_skipped = 0
        self._upload_times_ms.clear()
        self._numpy_upload = True
