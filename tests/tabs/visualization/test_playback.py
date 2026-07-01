"""Behavioral contracts for ``visualization.playback``.

The playback engine wires DPG transport widgets and a worker thread. The
deadline-scheduling worker, the per-tick render pump, and the FPS overlay
drawing are perf / draw machinery and are carved out. The play/pause toggle and
its dispatch are loud (a button that visibly does nothing), so they are not
guarded here. The silent-and-corrupting logic that is guarded is the transport
arithmetic: a wrong speed multiplier, an unclamped frame jump that lands out of
range, or a period bound that misses corrupts the playhead without an obvious
on-screen tell. DPG and the lazily-imported controls/render_loop collaborators
are stubbed so the tests assert only this module's own state writes and emits.

Behavioral contracts guarded here
---------------------------------
_set_speed_multiplier
  C1  Sets play_speed to ``round(base * multiplier)`` clamped to [1, 60], where
      base is original_fps or the current play_speed.

_jump_frames
  C2  Moves current_frame by delta, clamped to [0, max_frames], and emits
      FRAME_CHANGED with the resulting frame.
  C3  Stops playback when it was running before seeking.

_jump_to_period_start / _jump_to_period_end
  C4  Home seeks to frame 0; End seeks to max_frames.
"""

from __future__ import annotations

import pytest

from floodlight_gui.tabs.visualization import playback, state
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def fresh_state(monkeypatch):
    """Install a fresh ViewerState as the module singleton and return it."""
    new_state = state.ViewerState()
    monkeypatch.setattr(state, "viz_state", new_state)
    return new_state


@pytest.fixture
def stub_dpg(monkeypatch):
    """Patch the playback module's ``dpg`` with the shared recorder stub."""
    stub = make_dpg_stub()
    monkeypatch.setattr(playback, "dpg", stub)
    return stub


@pytest.fixture
def stub_collaborators(monkeypatch):
    """Stub the lazily-imported controls/render_loop side-effect collaborators.

    These belong to other modules; replacing them isolates playback's own
    transport logic. Also neutralizes the playback clock so no real thread
    starts during pure-logic tests.
    """
    import floodlight_gui.tabs.visualization.controls as controls
    import floodlight_gui.tabs.visualization.render_loop as render_loop

    monkeypatch.setattr(controls, "_update_frame_info", lambda *a, **k: None)
    monkeypatch.setattr(controls, "_update_fps_info", lambda *a, **k: None)
    monkeypatch.setattr(render_loop, "_render_current_frame", lambda *a, **k: None)
    monkeypatch.setattr(playback, "_start_playback", lambda *a, **k: None)
    monkeypatch.setattr(playback, "_stop_playback", lambda *a, **k: None)


@pytest.fixture
def capture_emit(monkeypatch):
    """Capture bus emissions during the test and return the log list."""
    emitted = []
    monkeypatch.setattr(playback.bus, "emit", lambda ev, **kw: emitted.append((ev, kw)))
    return emitted


# --------------------------------------------------------------------------- #
# _set_speed_multiplier                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "original_fps, multiplier, expected",
    [
        (25, 2.0, 50),  # plain multiply off the native rate
        (25, 0.5, 12),  # round(12.5) -> 12 (Python banker's rounding)
        (25, 4.0, 60),  # clamp at the 60 ceiling
        (25, 0.01, 1),  # clamp at the 1 floor
    ],
)
def test_set_speed_multiplier_clamps(
    fresh_state, stub_dpg, stub_collaborators, original_fps, multiplier, expected
):
    """C1: speed = round(base * multiplier) clamped to [1, 60]."""
    fresh_state.original_fps = original_fps
    playback._set_speed_multiplier(multiplier)
    assert fresh_state.play_speed == expected


def test_set_speed_multiplier_falls_back_to_play_speed(fresh_state, stub_dpg, stub_collaborators):
    """C1: with no original_fps, the current play_speed is the base."""
    fresh_state.original_fps = None
    fresh_state.play_speed = 20
    playback._set_speed_multiplier(2.0)
    assert fresh_state.play_speed == 40


# --------------------------------------------------------------------------- #
# _jump_frames / period jumps                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "current, delta, max_frames, expected",
    [
        (10, 5, 100, 15),  # plain forward step
        (10, -3, 100, 7),  # plain backward step
        (98, 10, 100, 100),  # clamps at max_frames
        (2, -10, 100, 0),  # clamps at 0
    ],
)
def test_jump_frames_clamps_and_emits(
    fresh_state,
    stub_dpg,
    stub_collaborators,
    capture_emit,
    current,
    delta,
    max_frames,
    expected,
):
    """C2: jump moves and clamps current_frame, then emits the resulting frame."""
    fresh_state.current_frame = current
    fresh_state.max_frames = max_frames
    fresh_state.playing = False

    playback._jump_frames(delta)

    assert fresh_state.current_frame == expected
    assert capture_emit == [(playback.Events.FRAME_CHANGED, {"frame": expected})]


def test_jump_frames_stops_active_playback(
    fresh_state, stub_dpg, stub_collaborators, capture_emit, monkeypatch
):
    """C3: a jump while playing stops playback before seeking."""
    fresh_state.current_frame = 10
    fresh_state.max_frames = 100
    fresh_state.playing = True
    stopped = []
    monkeypatch.setattr(playback._clock, "stop", lambda: stopped.append(True))

    playback._jump_frames(1)

    assert fresh_state.playing is False
    assert stopped == [True]


@pytest.mark.parametrize(
    "jump_fn, current, max_frames, expected",
    [
        (playback._jump_to_period_start, 40, 100, 0),
        (playback._jump_to_period_end, 40, 100, 100),
    ],
)
def test_period_jumps_target_bounds(
    fresh_state,
    stub_dpg,
    stub_collaborators,
    capture_emit,
    jump_fn,
    current,
    max_frames,
    expected,
):
    """C4: Home seeks to frame 0; End seeks to max_frames."""
    fresh_state.current_frame = current
    fresh_state.max_frames = max_frames
    fresh_state.playing = False

    jump_fn()

    assert fresh_state.current_frame == expected
