"""HullAdapter silent-export and playback-perf contracts.

C1 the hull polygon parents to the overlay layer (z-order).
C2 update_frame draws once across a long playback and never deletes per frame.
C3 set_visible toggles via configure_item(show=...), never delete.

The adapter's dpg attribute is replaced with a recording fake, so no GUI
toolkit is imported.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np


def _make_hull_dpg_stub():
    """Recording fake dpg: tracks draw_polygon / configure_item / delete_item
    calls and item existence so the adapter's draw layer can be inspected."""
    delete_calls: list = []
    draw_calls: list = []
    configure_calls: list = []
    existing_items: set = set()

    def _draw_polygon(**kw):
        tag = kw.get("tag")
        if tag:
            existing_items.add(tag)
        draw_calls.append((tag, kw.get("parent"), kw.get("show", True)))
        return tag

    def _configure_item(tag, **kw):
        configure_calls.append((tag, kw))

    def _delete_item(tag, **_):
        delete_calls.append(tag)
        existing_items.discard(tag)

    def _does_item_exist(tag):
        return tag in existing_items

    stub = SimpleNamespace(
        does_item_exist=_does_item_exist,
        delete_item=_delete_item,
        configure_item=_configure_item,
        draw_polygon=_draw_polygon,
    )
    stub._delete_calls = delete_calls
    stub._draw_calls = draw_calls
    stub._configure_calls = configure_calls
    stub._existing_items = existing_items
    return stub


class _StubMapper:
    def pitch_to_pixel(self, x, y):
        return (x * 8.0, y * 8.0)


class _StubHullModel:
    class _Hull:
        points = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]])
        vertices = np.array([0, 1, 2, 3])

    _convex_hulls_ = [_Hull()] * 1000


def test_hull_adapter_init_attaches_polygon_to_overlay_layer(monkeypatch):
    """The hull polygon parents to the overlay layer. A wrong parent renders
    the hull behind the pitch, invisible until you compare layers."""
    stub = _make_hull_dpg_stub()
    monkeypatch.setattr("floodlight_gui.rendering.adapters.hull.dpg", stub)
    from floodlight_gui.rendering.adapters.hull import HullAdapter

    adapter = HullAdapter("stub_drawlist", _StubMapper())
    adapter.init(
        parent_layer_tag="__overlay_layer",
        team_name="Home",
        model=_StubHullModel(),
        color=[255, 0, 0, 255],
    )
    adapter.update_frame(0)
    assert len(stub._draw_calls) == 1
    tag, parent, _ = stub._draw_calls[0]
    assert parent == "__overlay_layer", "polygon must attach to overlay layer"
    assert "__overlay_hull_Home_" in tag


def test_hull_adapter_update_frame_does_not_delete_per_frame(monkeypatch):
    """Across long playback, update_frame draws the polygon once and never
    deletes per frame. Per-frame delete-and-recreate tanks FPS with no on-screen
    signal until profiled."""
    stub = _make_hull_dpg_stub()
    monkeypatch.setattr("floodlight_gui.rendering.adapters.hull.dpg", stub)
    from floodlight_gui.rendering.adapters.hull import HullAdapter

    adapter = HullAdapter("stub_drawlist", _StubMapper())
    adapter.init(
        parent_layer_tag="__overlay_layer",
        team_name="Home",
        model=_StubHullModel(),
        color=[255, 0, 0, 255],
    )
    for frame in range(1000):
        adapter.update_frame(frame)
    assert stub._delete_calls == [], (
        f"update_frame deleted per frame, churning the GPU: {stub._delete_calls}"
    )
    assert len(stub._draw_calls) == 1, (
        f"expected exactly 1 draw_polygon across 1000 frames; got {len(stub._draw_calls)}"
    )


def test_hull_adapter_set_visible_false_uses_configure_item_not_delete(monkeypatch):
    """set_visible toggles via configure_item(show=...), never delete. A
    delete-based toggle forces a recreate that only shows up as FPS churn."""
    stub = _make_hull_dpg_stub()
    monkeypatch.setattr("floodlight_gui.rendering.adapters.hull.dpg", stub)
    from floodlight_gui.rendering.adapters.hull import HullAdapter

    adapter = HullAdapter("stub_drawlist", _StubMapper())
    adapter.init(
        parent_layer_tag="__overlay_layer",
        team_name="Home",
        model=_StubHullModel(),
        color=[255, 0, 0, 255],
    )
    adapter.update_frame(0)
    pre_configure_count = len(stub._configure_calls)
    adapter.set_visible(False)
    assert stub._delete_calls == [], f"set_visible(False) called delete_item: {stub._delete_calls}"
    assert len(stub._configure_calls) > pre_configure_count, (
        "set_visible must call configure_item(show=False)"
    )
    show_false_calls = [c for c in stub._configure_calls if c[1].get("show") is False]
    assert show_false_calls, f"No configure_item(show=False) recorded; got {stub._configure_calls}"
