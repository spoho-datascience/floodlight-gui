"""Behavioral contracts for ``visualization.playback_clock``.

``PlaybackClock`` is the DPG-free mechanism layer: a worker-thread lifecycle
manager plus a single-slot producer/consumer frame handoff behind a lock. The
thread lifecycle itself (start launches a daemon, stop is idempotent) is loud:
a stuck or never-started worker shows up immediately as frozen or runaway
playback. The silent-and-corrupting part is the single-slot handoff: a stale or
dropped frame in the slot desyncs the playhead without an obvious tell, so the
latest-wins drop, the END sentinel round-trip, and the stale-slot clear on start
are guarded here. Targets are deterministic (no sleeps, no wall-clock asserts).

Behavioral contracts guarded here
---------------------------------
post / take_pending
  C1  ``take_pending`` returns the most-recently posted frame and clears the
      slot, so a second take with no intervening post returns ``None``.
  C2  Latest-wins drop: when multiple posts precede one take, only the last
      posted frame survives (intermediate frames are intentionally dropped).
  C3  The END sentinel round-trips through the handoff like any other frame.

start
  C5  ``start`` clears any pending frame left from a previous run, so a stale
      frame cannot leak across playback cycles.
"""

from __future__ import annotations

from floodlight_gui.tabs.visualization.playback_clock import END, PlaybackClock


def test_take_pending_returns_then_clears():
    """C1: a posted frame is returned once, then the slot reads None."""
    clock = PlaybackClock()
    clock.post(7)
    assert clock.take_pending() == 7
    assert clock.take_pending() is None


def test_take_pending_keeps_only_latest_post():
    """C2: when posts outpace takes, only the most recent frame survives."""
    clock = PlaybackClock()
    clock.post(1)
    clock.post(2)
    clock.post(3)
    assert clock.take_pending() == 3


def test_end_sentinel_round_trips():
    """C3: the END sentinel is delivered through the handoff unchanged."""
    clock = PlaybackClock()
    clock.post(END)
    assert clock.take_pending() == END


def test_start_clears_stale_pending_frame():
    """C5: start resets the pending slot left behind by a previous cycle."""
    clock = PlaybackClock()
    clock.post(42)

    def _noop():
        return None

    clock.start(_noop)
    clock.stop()
    assert clock.take_pending() is None
