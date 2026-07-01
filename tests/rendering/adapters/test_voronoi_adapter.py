"""VoronoiAdapter silent-export and playback-perf contracts.

Playback perf (a quiet failure tanks FPS, invisible until profiled):
  C1 update_frame mutates the texture via set_value, never deletes or re-allocs.
  C2 set_visible toggles via configure_item(show=...), not delete.
  C3 update_mapper re-anchors the draw_image in place, not delete+recreate.
  C4 a second init() tears down prior DPG state before re-allocating.
  C5 clear() tears down image -> texture -> registry (consumers before
     producers; a wrong order leaks a texture via a suppressed SystemError).

Silent correctness (a wrong or stale overlay you only catch by cross-checking):
  C6 a fit-half mismatch hides the overlay and suppresses the stale upload;
     a match uploads normally; the guard short-circuits before the cache check.
  C7 frame cache: an identical frame skips upload; a distinct frame, an alpha
     change, or a mapper change busts it.
  C8 upload/skip counters track a known frame sequence.
  C9 multi-team buffer: which cell maps to team1/team2/NaN.
  C10 the texture bbox is not y-flipped and not edge-shrunk.
  C11 the hex stagger is not flattened to a square grid.
  C12 if set_value rejects the ndarray, upload self-heals to a list and stays so.
  C13 _FPS_SAMPLE_FRAMES stays in sync with the playback module.

The adapter's dpg attribute is replaced with a recording fake, so no GUI
toolkit is imported.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest


def _make_voronoi_dpg_stub():
    """Recording fake dpg: tracks texture-registry, raw-texture, draw_image,
    set_value, configure_item and delete_item calls plus item existence, so the
    adapter's texture lifecycle and draw layer can be inspected."""
    delete_calls: list = []
    set_value_calls: list = []
    draw_image_calls: list = []
    configure_calls: list = []
    add_raw_texture_calls: list = []
    existing_items: set = set()
    textures_created: set = set()

    def _texture_registry(*, tag=None):
        class _Ctx:
            def __enter__(self_):
                if tag is not None:
                    existing_items.add(tag)
                return self_

            def __exit__(self_, *exc):
                return False

        return _Ctx()

    def _add_raw_texture(*, width, height, default_value, format, tag, **_):
        textures_created.add(tag)
        existing_items.add(tag)
        add_raw_texture_calls.append((width, height, tag))
        return tag

    def _draw_image(texture_tag, *, pmin, pmax, parent, tag, **_):
        existing_items.add(tag)
        draw_image_calls.append(
            {
                "texture_tag": texture_tag,
                "parent": parent,
                "tag": tag,
                "pmin": list(pmin),
                "pmax": list(pmax),
            }
        )
        return tag

    def _set_value(tag, val):
        set_value_calls.append((tag, type(val).__name__))

    def _configure_item(tag, **kw):
        configure_calls.append((tag, kw))

    def _delete_item(tag, **_):
        delete_calls.append(tag)
        existing_items.discard(tag)
        textures_created.discard(tag)

    def _does_item_exist(tag):
        return tag in existing_items

    stub = SimpleNamespace(
        texture_registry=_texture_registry,
        add_raw_texture=_add_raw_texture,
        draw_image=_draw_image,
        set_value=_set_value,
        configure_item=_configure_item,
        delete_item=_delete_item,
        does_item_exist=_does_item_exist,
        mvFormat_Float_rgba=0,  # sentinel; only attribute existence matters
    )
    stub._delete_calls = delete_calls
    stub._set_value_calls = set_value_calls
    stub._draw_image_calls = draw_image_calls
    stub._configure_calls = configure_calls
    stub._add_raw_texture_calls = add_raw_texture_calls
    stub._existing_items = existing_items
    stub._textures_created = textures_created
    return stub


class _StubMapper:
    def pitch_to_pixel(self, x, y):
        return (x * 8.0, y * 8.0)


class _StubVoronoiModel:
    """Minimal DiscreteVoronoiModel-shaped stub with the attributes the adapter
    reads (square mesh)."""

    _meshx_ = np.tile(np.linspace(0, 100, 10), (6, 1))  # (6, 10)
    _meshy_ = np.tile(np.linspace(0, 60, 6)[:, None], (1, 10))  # (6, 10)
    _xpolysize_ = 10.0
    _ypolysize_ = 10.0
    _N1_ = 11
    # 100 frames of controls; xIDs 0..21, some NaNs to exercise the mask branches.
    _cell_controls_ = np.random.default_rng(0).integers(0, 22, size=(100, 6, 10)).astype(float)
    _cell_controls_[:, 0, 0] = np.nan


class _StubHexVoronoiModel:
    """Hexagonal DiscreteVoronoiModel stub: staggered odd rows + _mesh_type."""

    # 3-row staggered grid: odd row (index 1) offset +5 in x is the hex stagger.
    _meshx_ = np.array([[0.0, 10.0, 20.0], [5.0, 15.0, 25.0], [0.0, 10.0, 20.0]], dtype=float)
    _meshy_ = np.array([[20.0, 20.0, 20.0], [10.0, 10.0, 10.0], [0.0, 0.0, 0.0]], dtype=float)
    _xpolysize_ = 5.0
    _ypolysize_ = 5.0
    _N1_ = 1
    _mesh_type = "hexagonal"
    _cell_controls_ = np.random.default_rng(1).integers(0, 2, size=(5, 3, 3)).astype(float)


def _init_square(stub, monkeypatch, model=None, **overrides):
    """Patch the adapter's dpg with the fake and return a freshly init'd
    square-mesh VoronoiAdapter. Overrides patch individual init kwargs."""
    monkeypatch.setattr("floodlight_gui.rendering.adapters.voronoi.dpg", stub)
    from floodlight_gui.rendering.adapters.voronoi import VoronoiAdapter

    adapter = VoronoiAdapter("stub_drawlist", _StubMapper())
    kwargs = {
        "parent_layer_tag": "__overlay_layer",
        "model": model or _StubVoronoiModel(),
        "team1_color": [1, 1, 1, 255],
        "team2_color": [2, 2, 2, 255],
        "n_team1": 11,
    }
    kwargs.update(overrides)
    adapter.init(**kwargs)
    return adapter


# ---------------------------------------------------------------------------
# z-order wiring
# ---------------------------------------------------------------------------


def test_voronoi_adapter_draw_image_attaches_to_overlay_layer(monkeypatch):
    """The draw_image parents to the overlay layer. A wrong parent renders the
    texture behind the pitch."""
    stub = _make_voronoi_dpg_stub()
    _init_square(stub, monkeypatch)
    assert len(stub._draw_image_calls) == 1
    parent = stub._draw_image_calls[0]["parent"]
    assert parent == "__overlay_layer", "draw_image must attach to overlay layer"


# ---------------------------------------------------------------------------
# Playback perf
# ---------------------------------------------------------------------------


def test_voronoi_adapter_update_frame_no_delete_or_realloc(monkeypatch):
    """Across playback, update_frame mutates via set_value and never deletes,
    re-allocs the texture, or re-creates the draw_image. Per-frame churn tanks
    FPS with no on-screen signal."""
    stub = _make_voronoi_dpg_stub()
    adapter = _init_square(stub, monkeypatch)
    pre_delete = len(stub._delete_calls)
    pre_raw_tex = len(stub._add_raw_texture_calls)
    pre_draw_image = len(stub._draw_image_calls)
    pre_set_value = len(stub._set_value_calls)
    for f in range(100):
        adapter.update_frame(f)
    assert len(stub._delete_calls) - pre_delete == 0, (
        f"update_frame called delete_item: {stub._delete_calls}"
    )
    assert len(stub._add_raw_texture_calls) - pre_raw_tex == 0, "update_frame re-allocated texture"
    assert len(stub._draw_image_calls) - pre_draw_image == 0, "update_frame re-created draw_image"
    assert len(stub._set_value_calls) - pre_set_value >= 100, "expected >= 1 set_value per frame"


def test_voronoi_adapter_set_visible_false_uses_configure_item_not_delete(monkeypatch):
    """set_visible toggles via configure_item(show=False), never delete; a
    subsequent hidden-frame update stays delete-free too."""
    stub = _make_voronoi_dpg_stub()
    adapter = _init_square(stub, monkeypatch)
    pre_delete = len(stub._delete_calls)
    pre_configure = len(stub._configure_calls)
    adapter.set_visible(False)
    assert len(stub._delete_calls) - pre_delete == 0, (
        f"set_visible called delete_item: {stub._delete_calls}"
    )
    assert len(stub._configure_calls) - pre_configure >= 1, "set_visible must configure_item"
    show_false_calls = [c for c in stub._configure_calls if c[1].get("show") is False]
    assert show_false_calls, f"No configure_item(show=False) recorded; got {stub._configure_calls}"

    pre_delete2 = len(stub._delete_calls)
    adapter.update_frame(5)
    assert len(stub._delete_calls) == pre_delete2, (
        "update_frame while hidden must remain delete_item-free"
    )


def test_voronoi_adapter_update_mapper_reanchors_image_in_place(monkeypatch):
    """On a mapper change, update_mapper re-anchors the texture bbox via
    configure_item, not delete+recreate. A delete+recreate on every viewport
    resize churns the GPU texture."""
    stub = _make_voronoi_dpg_stub()
    adapter = _init_square(stub, monkeypatch)
    image_tag = adapter._image_tag
    pre_delete = len(stub._delete_calls)
    pre_add_raw_tex = len(stub._add_raw_texture_calls)
    pre_draw_image = len(stub._draw_image_calls)
    pre_configure = len(stub._configure_calls)

    class _LargerMapper:
        def pitch_to_pixel(self, x, y):
            return (x * 16.0, y * 16.0)  # 2x zoom

    adapter.update_mapper(_LargerMapper())

    assert len(stub._delete_calls) == pre_delete, "update_mapper called delete_item"
    assert len(stub._add_raw_texture_calls) == pre_add_raw_tex, "update_mapper re-allocated texture"
    assert len(stub._draw_image_calls) == pre_draw_image, "update_mapper re-created draw_image"
    image_reanchors = [
        (tag, kw)
        for tag, kw in stub._configure_calls[pre_configure:]
        if tag == image_tag and "pmin" in kw and "pmax" in kw
    ]
    assert len(image_reanchors) == 1, "expected exactly 1 in-place re-anchor configure_item"
    assert image_reanchors[0][1]["pmax"][0] > 100.0, "new pmax must reflect the larger mapper scale"


def test_voronoi_adapter_idempotent_init_tears_down_prior_state(monkeypatch):
    """A second init() on the same instance tears down the 3 prior items
    (image, texture, registry) before re-allocating; otherwise the old texture
    leaks and tags collide."""
    stub = _make_voronoi_dpg_stub()
    adapter = _init_square(stub, monkeypatch)
    pre_delete = len(stub._delete_calls)
    pre_raw_tex = len(stub._add_raw_texture_calls)
    adapter.init(
        parent_layer_tag="__overlay_layer",
        model=_StubVoronoiModel(),
        team1_color=[1, 1, 1, 255],
        team2_color=[2, 2, 2, 255],
        n_team1=11,
    )
    assert len(stub._delete_calls) - pre_delete == 3, "second init must tear down 3 prior items"
    assert len(stub._add_raw_texture_calls) - pre_raw_tex == 1, "second init re-allocs 1 texture"


def test_voronoi_adapter_clear_teardown_order_image_then_texture_then_registry(monkeypatch):
    """clear() tears down image -> texture -> registry (consumers before
    producers). A wrong order trips a SystemError that clear() suppresses,
    leaking the GPU texture; the leak accumulates per re-fit."""
    stub = _make_voronoi_dpg_stub()
    adapter = _init_square(stub, monkeypatch)
    image_tag = adapter._image_tag
    texture_tag = adapter._texture_tag
    registry_tag = adapter._registry_tag
    assert image_tag and texture_tag and registry_tag, "init must populate all 3 tags"

    pre_delete = len(stub._delete_calls)
    adapter.clear()
    assert stub._delete_calls[pre_delete:] == [image_tag, texture_tag, registry_tag], (
        "teardown order must be image -> texture -> registry"
    )
    assert adapter._image_tag is None
    assert adapter._texture_tag is None
    assert adapter._registry_tag is None


# ---------------------------------------------------------------------------
# Fit-half guard (stale wrong-half frame)
# ---------------------------------------------------------------------------


def test_voronoi_adapter_update_frame_suppresses_on_fit_half_mismatch(monkeypatch):
    """When fit_half differs from the viz selected_half, update_frame hides the
    overlay (configure_item show=False) and does not upload the stale frame.
    The wrong-half overlay is only caught by cross-checking the active half."""
    stub = _make_voronoi_dpg_stub()
    from floodlight_gui.tabs.visualization import state as _vt_state

    monkeypatch.setattr(_vt_state, "viz_state", {"selected_half": "secondHalf"})
    adapter = _init_square(stub, monkeypatch, fit_half="firstHalf")
    image_tag = adapter._image_tag
    pre_set_value = len(stub._set_value_calls)
    pre_configure = len(stub._configure_calls)

    adapter.update_frame(0)

    assert len(stub._set_value_calls) == pre_set_value, (
        "update_frame uploaded a stale frame when fit_half != selected_half"
    )
    show_false_on_image = [
        (tag, kw)
        for tag, kw in stub._configure_calls[pre_configure:]
        if tag == image_tag and kw.get("show") is False
    ]
    assert show_false_on_image, "expected configure_item(image_tag, show=False) on half mismatch"


def test_voronoi_adapter_update_frame_runs_normally_on_fit_half_match(monkeypatch):
    """When fit_half matches selected_half the guard passes and the frame
    uploads normally, so the guard does not over-trigger and blank the overlay
    on the happy path."""
    stub = _make_voronoi_dpg_stub()
    from floodlight_gui.tabs.visualization import state as _vt_state

    monkeypatch.setattr(_vt_state, "viz_state", {"selected_half": "firstHalf"})
    adapter = _init_square(stub, monkeypatch, fit_half="firstHalf")
    pre_set_value = len(stub._set_value_calls)
    adapter.update_frame(0)
    assert len(stub._set_value_calls) == pre_set_value + 1, (
        "update_frame should upload normally when fit_half matches selected_half"
    )


def test_voronoi_adapter_fit_half_guard_runs_before_cache_check(monkeypatch):
    """The fit-half guard short-circuits before the cache check. If the cache
    ran first, a wrong-half replay would register a cache skip and could leak a
    stale frame; the guard keeps frames_skipped flat on the wrong-half call."""
    stub = _make_voronoi_dpg_stub()
    from floodlight_gui.tabs.visualization import state as _vt_state

    monkeypatch.setattr(_vt_state, "viz_state", {"selected_half": "firstHalf"})
    adapter = _init_square(stub, monkeypatch, fit_half="firstHalf")

    adapter.update_frame(5)  # matching half — seeds the cache
    assert adapter.frames_uploaded == 1
    assert adapter.frames_skipped == 0
    pre_skipped = adapter.frames_skipped
    pre_uploaded = adapter.frames_uploaded

    _vt_state.viz_state["selected_half"] = "secondHalf"
    adapter.update_frame(5)
    assert adapter.frames_skipped == pre_skipped, (
        "fit-half mismatch must short-circuit before the cache check"
    )
    assert adapter.frames_uploaded == pre_uploaded, "wrong-half update must not upload"


# ---------------------------------------------------------------------------
# Frame cache (a stuck cache freezes the overlay)
# ---------------------------------------------------------------------------


def test_voronoi_adapter_cache_hit_skips_repeated_frame(monkeypatch):
    """update_frame(t) twice uploads exactly once (the second is a cache hit).
    Re-uploading an unchanged texture every frame burns GPU bandwidth
    invisibly."""
    stub = _make_voronoi_dpg_stub()
    adapter = _init_square(stub, monkeypatch)
    pre = len(stub._set_value_calls)
    adapter.update_frame(5)
    adapter.update_frame(5)
    assert len(stub._set_value_calls) - pre == 1, "repeated frame must upload exactly once"
    assert adapter.frames_skipped == 1
    assert adapter.frames_uploaded == 1
    assert adapter.last_upload_ms >= 0.0


@pytest.mark.parametrize("buster", ["advance", "set_alpha", "update_mapper"])
def test_voronoi_adapter_cache_busts(monkeypatch, buster):
    """The cache busts on a distinct frame, an alpha change, and a mapper
    change. A stuck cache freezes the overlay on a stale frame or stale
    translucency while the rest of the pitch animates."""
    stub = _make_voronoi_dpg_stub()
    adapter = _init_square(stub, monkeypatch)
    pre = len(stub._set_value_calls)
    adapter.update_frame(5)

    if buster == "advance":
        adapter.update_frame(6)  # distinct frame data (rng integers differ)
    elif buster == "set_alpha":
        adapter.set_alpha(0.5)
        adapter.update_frame(5)
    else:  # update_mapper

        class _OtherMapper:
            def pitch_to_pixel(self, x, y):
                return (x * 4.0, y * 4.0)

        adapter.update_mapper(_OtherMapper())
        adapter.update_frame(5)

    assert len(stub._set_value_calls) - pre == 2, (
        f"{buster} must invalidate the cache (expected 2 uploads)"
    )


def test_voronoi_adapter_upload_skip_counters_track_sequence(monkeypatch):
    """Upload/skip counters track a known sequence (5,5,6,6,6,7) = 3 uploads
    (first sightings) + 3 skips. These counters drive the FPS/upload overlay;
    a miscount mis-reports playback health."""
    stub = _make_voronoi_dpg_stub()
    adapter = _init_square(stub, monkeypatch)
    for t in (5, 5, 6, 6, 6, 7):
        adapter.update_frame(t)
    assert adapter.frames_uploaded == 3, "uploads = first sightings of 5, 6, 7"
    assert adapter.frames_skipped == 3, "skips = repeated 5 + two repeats of 6"
    assert adapter.last_upload_ms >= 0.0


# ---------------------------------------------------------------------------
# Frame->overlay data + geometry mapping
# ---------------------------------------------------------------------------


def test_voronoi_adapter_multi_team_buffer_classification(monkeypatch):
    """Cells with xID < n_team1 get team1 RGBA, >= n_team1 get team2, NaN stays
    transparent. A flipped team/NaN classification is not obviously wrong on
    sight (red vs blue both look plausible)."""

    class _CtrlModel:
        _meshx_ = np.tile(np.linspace(0, 30, 3), (3, 1))
        _meshy_ = np.tile(np.linspace(0, 20, 3)[:, None], (1, 3))
        _xpolysize_ = 10.0
        _ypolysize_ = 10.0
        _N1_ = 11
        _cell_controls_ = np.zeros((1, 3, 3), dtype=float)

    m = _CtrlModel()
    m._cell_controls_[0, 0, :] = 5  # team1
    m._cell_controls_[0, 1, :] = 17  # team2
    m._cell_controls_[0, 2, :] = np.nan  # uncontrolled

    adapter = _init_square(
        stub := _make_voronoi_dpg_stub(),
        monkeypatch,
        model=m,
        team1_color=[255, 0, 0, 255],  # red -> c1 == [1, 0, 0]
        team2_color=[0, 0, 255, 255],  # blue -> c2 == [0, 0, 1]
        n_team1=11,
        alpha=0.5,
    )
    del stub
    adapter.update_frame(0)

    buf = adapter._buf
    assert buf is not None
    # Row 0 = team1 (red, alpha 0.5); row 1 = team2 (blue, alpha 0.5);
    # row 2 = NaN -> transparent. Broadcast the expected RGBA across the row.
    np.testing.assert_allclose(buf[0, :, :], np.tile([1.0, 0.0, 0.0, 0.5], (3, 1)))
    np.testing.assert_allclose(buf[1, :, :], np.tile([0.0, 0.0, 1.0, 0.5], (3, 1)))
    np.testing.assert_allclose(buf[2, :, :], 0.0)


def test_voronoi_adapter_init_bbox_is_not_y_flipped(monkeypatch):
    """The bbox pmin (upper-left) sits above pmax in pixel-y and reaches the
    full pitch edge (not shrunk by ypolysize). A y-flip renders the texture
    upside-down and an edge shrink gaps it, both surviving a glance."""
    stub = _make_voronoi_dpg_stub()
    monkeypatch.setattr("floodlight_gui.rendering.adapters.voronoi.dpg", stub)
    from floodlight_gui.rendering.adapters.voronoi import VoronoiAdapter

    class _FloodlightMeshStub:
        _meshx_ = np.tile(np.linspace(5.0, 95.0, 10), (6, 1))
        _meshy_ = np.tile(np.linspace(55.0, 5.0, 6)[:, None], (1, 10))  # top->bottom, pad=5
        _xpolysize_ = 10.0
        _ypolysize_ = 10.0
        _N1_ = 11
        _cell_controls_ = np.zeros((1, 6, 10), dtype=float)

    class _YFlippingMapper:
        pitch_y_max = 60.0
        scale = 8.0

        def pitch_to_pixel(self, x, y):
            return (x * self.scale, (self.pitch_y_max - y) * self.scale)

    adapter = VoronoiAdapter("stub_drawlist", _YFlippingMapper())
    adapter.init(
        parent_layer_tag="__overlay_layer",
        model=_FloodlightMeshStub(),
        team1_color=[1, 1, 1, 255],
        team2_color=[2, 2, 2, 255],
        n_team1=11,
    )

    assert len(stub._draw_image_calls) == 1, stub._draw_image_calls
    pmin = stub._draw_image_calls[0]["pmin"]
    pmax = stub._draw_image_calls[0]["pmax"]
    assert pmin[1] < pmax[1], f"y-flipped: pmin.y must be above pmax.y; pmin={pmin}, pmax={pmax}"
    assert pmin[0] < pmax[0], f"pmin.x must be left of pmax.x; pmin={pmin}, pmax={pmax}"
    # Bbox reaches the full pitch (ylim 0..60), not shrunk by ypolysize.
    assert pmin[1] == 0.0, f"pmin.y should sit at the top edge (pixel y=0); got {pmin[1]}"
    assert pmax[1] == 480.0, f"pmax.y should sit at the bottom edge (pixel y=480); got {pmax[1]}"


def test_build_hex_texture_geometry_honors_stagger(monkeypatch):
    """The hex texel->cell map is upscaled (not 1-texel-per-cell) and reflects
    the odd-row x stagger. Dropping the stagger to a square grid renders the
    cells as squares, subtle on a busy pitch and never an obvious break."""
    monkeypatch.setattr("floodlight_gui.rendering.adapters.voronoi.dpg", _make_voronoi_dpg_stub())
    from floodlight_gui.rendering.adapters.voronoi import VoronoiAdapter

    model = _StubHexVoronoiModel()
    ny, nx = model._meshx_.shape
    tex_w, tex_h, bbox, texel_to_cell = VoronoiAdapter._build_hex_texture_geometry(
        model._meshx_, model._meshy_, model._xpolysize_
    )
    assert (tex_w, tex_h) == texel_to_cell.shape[::-1]
    assert tex_w > nx and tex_h > ny, "hex raster must be upscaled, not 1 texel/cell"
    assert set(np.unique(texel_to_cell).tolist()) == set(range(ny * nx)), "every cell represented"
    # Cell (1,1) centre = staggered (15, 10): its own texel must map to itself.
    x_lo, x_hi, y_lo, y_hi = bbox
    col = int((15.0 - x_lo) / (x_hi - x_lo) * tex_w)
    row = int((y_hi - 10.0) / (y_hi - y_lo) * tex_h)
    assert texel_to_cell[row, col] == 1 * nx + 1, "odd-row staggered cell must map to itself"


# ---------------------------------------------------------------------------
# Self-healing upload + constant mirror
# ---------------------------------------------------------------------------


def test_voronoi_adapter_self_healing_ndarray_to_list_upload(monkeypatch):
    """If dpg.set_value rejects the flat ndarray (some DPG builds do), the
    adapter falls through to a list-based upload on the same frame and stays
    sticky. Without it the overlay stops updating on those builds."""
    stub = _make_voronoi_dpg_stub()
    state = {"raised": False}
    original_set_value = stub.set_value

    def _flaky_set_value(tag, val):
        if not state["raised"] and isinstance(val, np.ndarray):
            state["raised"] = True
            raise TypeError("simulated DPG build rejects ndarray default_value")
        return original_set_value(tag, val)

    stub.set_value = _flaky_set_value
    adapter = _init_square(stub, monkeypatch)
    assert adapter._numpy_upload is True

    adapter.update_frame(0)
    assert adapter._numpy_upload is False, "fallback must flip _numpy_upload to False"
    list_uploads = [c for c in stub._set_value_calls if c[1] == "list"]
    assert list_uploads, f"expected a list-typed set_value; got {stub._set_value_calls}"

    adapter.update_frame(1)
    adapter.update_frame(2)
    assert adapter._numpy_upload is False, "fallback must stay sticky"
    assert len([c for c in stub._set_value_calls if c[1] == "list"]) >= 3


def test_voronoi_fps_sample_frames_mirrors_playback():
    """voronoi._FPS_SAMPLE_FRAMES equals playback's, so the upload-ms
    moving-average window aligns with the FPS overlay window. A drifted constant
    desyncs the two and is never visible on the pitch."""
    from floodlight_gui.rendering.adapters import voronoi as _voronoi
    from floodlight_gui.tabs.visualization import playback as _pb

    assert _voronoi._FPS_SAMPLE_FRAMES == _pb._FPS_SAMPLE_FRAMES, (
        f"voronoi._FPS_SAMPLE_FRAMES ({_voronoi._FPS_SAMPLE_FRAMES}) must equal "
        f"playback._FPS_SAMPLE_FRAMES ({_pb._FPS_SAMPLE_FRAMES})"
    )
