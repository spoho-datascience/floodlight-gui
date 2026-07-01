"""Behavioral suite for ``rendering/export_renderer.render_video(overlays=...)``.

``render_video`` iterates a frame range and delegates each frame to
``render_frame``, forwarding the ``overlays`` spec list. The seams are
``render_frame`` (stubbed to record its calls) and the imageio writer (a
MagicMock); the tests assert what ``render_video`` decides to call rather than
any rendered pixels.

Behavioral contracts guarded here
---------------------------------
C1  render_video forwards the same ``overlays`` list object to render_frame on
    every frame (identity, no defensive copy).
C2  render_video with ``overlays=None`` forwards None per frame, so the
    overlay-free path is taken.
C3  render_video returns immediately without calling render_frame when ffmpeg
    is absent (HAS_FFMPEG=False).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_imageio_writer():
    """Stub imageio writer that records append_data calls."""
    writer = MagicMock()
    writer.__enter__ = MagicMock(return_value=writer)
    writer.__exit__ = MagicMock(return_value=False)
    writer.append_data = MagicMock()
    writer.close = MagicMock()
    return writer


def test_render_video_forwards_overlays_per_frame(tmp_path, monkeypatch, fake_imageio_writer):
    """C1: render_video forwards the same overlays list to render_frame per frame.

    Forwarded by identity rather than a copy, so the exact spec snapshot reaches
    every frame.
    """
    import floodlight_gui.rendering.export_renderer as _er

    rendered_calls = []

    def _fake_render_frame(pitch, xy_data, frame_idx, teams, team_colors, options, overlays=None):
        rendered_calls.append({"frame": frame_idx, "overlays": overlays})
        return MagicMock()

    monkeypatch.setattr(_er, "HAS_FFMPEG", True)
    monkeypatch.setattr(_er, "render_frame", _fake_render_frame)
    monkeypatch.setattr(_er, "_fig_to_rgba", lambda fig: MagicMock())
    monkeypatch.setattr(_er, "plt", MagicMock())

    fake_iio = MagicMock()
    fake_iio.get_writer.return_value = fake_imageio_writer
    monkeypatch.setitem(sys.modules, "imageio", MagicMock())
    monkeypatch.setitem(sys.modules, "imageio.v3", fake_iio)

    overlays = [{"key": "hull", "model": MagicMock(), "alpha": 0.5}]
    _er.render_video(
        path=str(tmp_path / "test.mp4"),
        pitch=MagicMock(),
        xy_data={},
        frame_range=(0, 2),
        teams=[],
        team_colors={},
        overlays=overlays,
    )

    assert len(rendered_calls) == 2, (
        f"expected 2 render_frame calls for frame_range=(0,2), got {len(rendered_calls)}"
    )
    assert all(c["overlays"] is overlays for c in rendered_calls), (
        "render_video must forward the same overlays list to render_frame on every "
        "frame (identity, not a copy)"
    )


def test_render_video_no_overlays_short_circuits(tmp_path, monkeypatch, fake_imageio_writer):
    """C2: render_video with overlays=None forwards None to render_frame per frame.

    render_frame's ``if overlays:`` guard then short-circuits the overlay branch,
    taking the overlay-free path.
    """
    import floodlight_gui.rendering.export_renderer as _er

    rendered_calls = []

    def _fake_render_frame(pitch, xy_data, frame_idx, teams, team_colors, options, overlays=None):
        rendered_calls.append({"frame": frame_idx, "overlays": overlays})
        return MagicMock()

    monkeypatch.setattr(_er, "HAS_FFMPEG", True)
    monkeypatch.setattr(_er, "render_frame", _fake_render_frame)
    monkeypatch.setattr(_er, "_fig_to_rgba", lambda fig: MagicMock())
    monkeypatch.setattr(_er, "plt", MagicMock())

    fake_iio = MagicMock()
    fake_iio.get_writer.return_value = fake_imageio_writer
    monkeypatch.setitem(sys.modules, "imageio", MagicMock())
    monkeypatch.setitem(sys.modules, "imageio.v3", fake_iio)

    _er.render_video(
        path=str(tmp_path / "test.mp4"),
        pitch=MagicMock(),
        xy_data={},
        frame_range=(0, 2),
        teams=[],
        team_colors={},
        overlays=None,
    )

    assert len(rendered_calls) == 2, (
        f"expected 2 render_frame calls for frame_range=(0,2), got {len(rendered_calls)}"
    )
    assert all(c["overlays"] is None for c in rendered_calls), (
        "when overlays=None, render_video must pass None (not []) to render_frame; "
        "render_frame's 'if overlays:' guard handles both"
    )


def test_render_video_noop_when_no_ffmpeg(tmp_path, monkeypatch):
    """C3: render_video returns immediately without rendering when HAS_FFMPEG=False.

    With ffmpeg/imageio_ffmpeg absent the function logs a warning and returns, so
    render_frame is never called.
    """
    import floodlight_gui.rendering.export_renderer as _er

    render_frame_calls = []

    def _spy_render_frame(*args, **kwargs):
        render_frame_calls.append(1)
        return MagicMock()

    monkeypatch.setattr(_er, "HAS_FFMPEG", False)
    monkeypatch.setattr(_er, "render_frame", _spy_render_frame)

    _er.render_video(
        path=str(tmp_path / "test.mp4"),
        pitch=MagicMock(),
        xy_data={},
        frame_range=(0, 5),
        teams=[],
        team_colors={},
        overlays=None,
    )

    assert render_frame_calls == [], (
        "render_video must not call render_frame when HAS_FFMPEG=False. "
        f"Got {len(render_frame_calls)} render_frame call(s)."
    )
