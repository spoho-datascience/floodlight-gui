"""Playback worker thread and cross-thread frame handoff for the visualization tab.

``PlaybackClock`` owns the mechanism: the daemon worker thread, its stop
``Event``, and the single pending-frame int handed from the worker (producer) to
the main-thread pump (consumer) behind a lock. The policy (drift-free deadline
scheduling and frame sequencing) stays in the caller's worker target, which reads
live ``viz_state`` each cycle (see ``playback._playback_worker``).

DPG-free: this module has no dearpygui import and is safe to load in tests without
a GUI backend.

Handoff protocol:
* The worker calls :meth:`post` with the next frame, or ``post(END)`` when playback
  walks past the last frame.
* The pump calls :meth:`take_pending` once per rendered frame. It returns the
  most-recently-posted frame (older un-consumed frames are overwritten, which is the
  intended frame-drop behavior under load) and clears the slot.
"""

from __future__ import annotations

import threading

# Sentinel posted by the worker when playback walks past the last frame.
# The pump treats this as "playback completed".
END = -1


class PlaybackClock:
    """Worker thread lifecycle manager and producer-consumer frame handoff.

    Separates mechanism (thread, stop event, pending-frame slot) from policy
    (frame sequencing, deadline math) so the clock can be unit-tested without a
    running render loop.

    Thread model: one daemon worker (producer) writes frames via :meth:`post`;
    the main-thread pump (consumer) reads via :meth:`take_pending` once per
    rendered frame. Only the most-recent posted frame is kept: if the pump falls
    behind the worker, intermediate frames are overwritten (intentional drop).
    """

    def __init__(self) -> None:
        """Initialise lock, stop event, and cleared thread/pending-frame slots."""
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pending: int | None = None

    @property
    def stop_event(self) -> threading.Event:
        """The worker waits/checks on this; ``set()`` requests shutdown."""
        return self._stop

    @property
    def is_running(self) -> bool:
        """Return True when the worker thread exists and is still alive."""
        t = self._thread
        return t is not None and t.is_alive()

    def start(self, target) -> None:
        """Stop any existing worker, clear state, then run *target* on a daemon thread."""
        self.stop()
        self._stop.clear()
        with self._lock:
            self._pending = None
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal stop and join the worker thread (idempotent)."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def post(self, frame: int) -> None:
        """Worker-thread side: publish the latest frame for the pump to consume."""
        with self._lock:
            self._pending = frame

    def take_pending(self) -> int | None:
        """Main-thread pump side: return + clear the latest posted frame (or ``None``)."""
        with self._lock:
            frame, self._pending = self._pending, None
            return frame
