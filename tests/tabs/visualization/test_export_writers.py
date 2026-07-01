"""Behavioral contracts for ``visualization.export_writers``.

The two writer factories defer all heavy rendering (matplotlib, render_video,
ffmpeg) to lazy imports inside their bodies; that rendering is upstream-owned
and carved out. The self-contained logic worth guarding is the silent-and-
corrupting export math and scene assembly: clip frame-range arithmetic, frame
clamping, clip-seconds reading, the live team-visibility filter, and the
no-data / no-pitch guards. A wrong clip range or a leaked hidden team is invisible
until someone watches the exported file, so each is guarded here. The XY resolver,
color helper, and overlay-spec builder are seams and are stubbed.

Behavioral contracts guarded here
---------------------------------
_clamp_frame
  C2  Clamps a frame into ``[0, max_frames - 1]``.

_read_clip_seconds
  C3  Reads the clip-length widget, defaulting to 5 and flooring at 1.

clip_frame_range
  C4  start = clamped current frame; end = ``min(max_frames, start +
      clip_seconds * base_fps)`` with base_fps = original_fps or play_speed.

_build_scene
  C5  Includes only teams that are visible in the live view and have XY data.
  C6  Raises RuntimeError when no data is loaded, and when no pitch is attached.

export flow
  C7  Setting a clip start + length and a team-visibility map yields a clip range
      and a scene whose visible teams match exactly what was selected.
"""

from __future__ import annotations

import pytest

from floodlight_gui.tabs.visualization import export_writers, state


@pytest.fixture
def fresh_state(monkeypatch):
    """Install a fresh ViewerState as the module singleton and return it."""
    new_state = state.ViewerState()
    monkeypatch.setattr(state, "viz_state", new_state)
    return new_state


# --------------------------------------------------------------------------- #
# _clamp_frame                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "frame, max_frames, expected",
    [
        (50, 100, 50),  # in range
        (-5, 100, 0),  # below floor
        (200, 100, 99),  # above ceiling (max_frames - 1)
        (5, 0, 0),  # degenerate max_frames clamps to 0
    ],
)
def test_clamp_frame(fresh_state, frame, max_frames, expected):
    """C2: a frame is clamped into [0, max_frames - 1]."""
    fresh_state.max_frames = max_frames
    assert export_writers._clamp_frame(frame) == expected


# --------------------------------------------------------------------------- #
# _read_clip_seconds                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "widget_value, expected",
    [
        (8, 8),  # plain read
        (0, 1),  # floored at 1
        (-3, 1),  # negative floored at 1
    ],
)
def test_read_clip_seconds(monkeypatch, widget_value, expected):
    """C3: clip seconds reads the widget, floored at 1.

    ``_read_clip_seconds`` imports the real ``dearpygui.dearpygui`` lazily, so
    the seam is its ``get_value``; patching that returns a controlled widget
    value without a live DPG context.
    """
    import dearpygui.dearpygui as real_dpg

    monkeypatch.setattr(real_dpg, "get_value", lambda tag: widget_value)
    assert export_writers._read_clip_seconds() == expected


# --------------------------------------------------------------------------- #
# clip_frame_range                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "current, max_frames, original_fps, play_speed, clip_seconds, expected",
    [
        # base_fps from original_fps: 10 + 5*25 = 135, under max -> (10, 135)
        (10, 1000, 25, 30, 5, (10, 135)),
        # original_fps None -> base_fps from play_speed (30): 10 + 5*30 = 160
        (10, 1000, None, 30, 5, (10, 160)),
        # end clamps at max_frames: 900 + 5*25 = 1025 -> capped at 1000
        (900, 1000, 25, 25, 5, (900, 1000)),
    ],
)
def test_clip_frame_range(
    fresh_state,
    monkeypatch,
    current,
    max_frames,
    original_fps,
    play_speed,
    clip_seconds,
    expected,
):
    """C4: clip range is (clamped current, min(max, start + clip_seconds*base_fps))."""
    fresh_state.current_frame = current
    fresh_state.max_frames = max_frames
    fresh_state.original_fps = original_fps
    fresh_state.play_speed = play_speed
    monkeypatch.setattr(export_writers, "_read_clip_seconds", lambda: clip_seconds)

    assert export_writers.clip_frame_range() == expected


# --------------------------------------------------------------------------- #
# _build_scene                                                                 #
# --------------------------------------------------------------------------- #


class _FakeApp:
    """App double exposing the loaded_data / pitch reads _build_scene needs."""

    def __init__(self, *, loaded_data=("data",), pitch="PITCH"):
        self.loaded_data = loaded_data
        self.pitch = pitch


@pytest.fixture
def stub_scene_seams(monkeypatch):
    """Stub the XY resolver, color helper, and overlay-spec builder.

    Returns a callable ``(xy_present_teams)`` that makes the XY resolver return a
    sentinel XY only for the named teams (and None otherwise), so the
    visibility/XY filter can be exercised without real floodlight objects.
    """
    import floodlight_gui.core.xy_access as xy_access
    import floodlight_gui.tabs.visualization.colors as colors
    import floodlight_gui.tabs.visualization.overlay_dispatch as overlay_dispatch

    monkeypatch.setattr(colors, "team_color_for", lambda team, idx, **k: ([1, 2, 3, 4], True))
    monkeypatch.setattr(overlay_dispatch, "_build_overlay_specs_for_export", lambda: [])

    def _set(xy_present_teams):
        def _resolver(app, half, team):
            return f"XY::{team}" if team in xy_present_teams else None

        monkeypatch.setattr(xy_access, "get_xy_for_period_team", _resolver)

    return _set


def test_build_scene_filters_hidden_and_missing_teams(fresh_state, monkeypatch, stub_scene_seams):
    """C5: the scene includes only teams visible in the live view with XY data."""
    monkeypatch.setattr(state, "app_instance", _FakeApp())
    fresh_state.cached_team_names = ["Home", "Away", "Ball"]
    # Away is toggled OFF in the live view; Ball has no XY data.
    fresh_state.selected_teams = {"Home": True, "Away": False, "Ball": True}
    stub_scene_seams(xy_present_teams={"Home"})

    scene = export_writers._build_scene(error_noun="image")

    # Away excluded by visibility; Ball excluded by missing XY; only Home remains.
    assert scene.teams == ["Home"]
    assert "Home" in scene.xy_data
    assert "Away" not in scene.xy_data


@pytest.mark.parametrize(
    "app",
    [
        None,  # no app at all
        _FakeApp(loaded_data=None),  # app present, nothing loaded
        _FakeApp(pitch=None),  # data loaded but no pitch
    ],
)
def test_build_scene_guards_raise(fresh_state, monkeypatch, stub_scene_seams, app):
    """C6: no data and no pitch both raise RuntimeError before any render."""
    monkeypatch.setattr(state, "app_instance", app)
    fresh_state.cached_team_names = ["Home"]
    fresh_state.selected_teams = {"Home": True}
    stub_scene_seams(xy_present_teams={"Home"})

    with pytest.raises(RuntimeError):
        export_writers._build_scene(error_noun="image")


# --------------------------------------------------------------------------- #
# export-spec flow: selected clip range + visibility -> assembled export        #
# --------------------------------------------------------------------------- #


def test_export_flow_range_and_scene_match_selection(fresh_state, monkeypatch, stub_scene_seams):
    """C7: a chosen clip start + length and team-visibility map flow through to the
    assembled export's frame range and visible-team list.

    Drives the real producer path: ``clip_frame_range`` computes the frame window
    from the seeded transport state and clip length, and ``_build_scene`` computes
    the visible-team list from the seeded visibility toggles. The exported clip's
    range and team set must be exactly what was selected, not what happens to be
    in the cache.
    """
    monkeypatch.setattr(state, "app_instance", _FakeApp())
    # Selected: start at frame 100, 4-second clip at a 25 fps native rate.
    fresh_state.current_frame = 100
    fresh_state.max_frames = 1000
    fresh_state.original_fps = 25
    fresh_state.play_speed = 25
    monkeypatch.setattr(export_writers, "_read_clip_seconds", lambda: 4)
    # Selected visibility: Home shown, Away hidden, Ball shown.
    fresh_state.cached_team_names = ["Home", "Away", "Ball"]
    fresh_state.selected_teams = {"Home": True, "Away": False, "Ball": True}
    stub_scene_seams(xy_present_teams={"Home", "Away", "Ball"})

    start, end = export_writers.clip_frame_range()
    scene = export_writers._build_scene(error_noun="video")

    # Range is exactly the selected start and start + seconds*fps.
    assert (start, end) == (100, 100 + 4 * 25)
    # Scene carries exactly the teams left visible (Away dropped by its toggle).
    assert scene.teams == ["Home", "Ball"]
