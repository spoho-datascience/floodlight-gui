"""Behavioral contracts for ``floodlight_gui.core.event_bus``.

The pub/sub ``EventBus`` is the sole cross-tab notification mechanism. Its
job is to register callables against :class:`Events` members and, on
``emit``, hand every emitted kwarg to each registered callable in priority
order while isolating one subscriber's failure from the rest. The bus has no
heavy collaborators; the only seam is the callbacks themselves, which the
tests supply as plain recorder functions. Each test builds a FRESH
``EventBus()`` so nothing touches the module-level singleton.

Behavioral contracts guarded here
---------------------------------
emit (payload + fan-out)
  C1  emit forwards every emitted kwarg verbatim to a subscriber.
  C2  emit fans the same payload out to every subscriber of the event.
  C3  emit with no subscribers (and for an event never subscribed) is a
      silent no-op.

priority + ordering
  C4  Subscribers run in ascending priority order; ties preserve insertion
      order (stable).

exception isolation
  C5  A subscriber that raises is isolated: every other subscriber still
      runs and emit does not propagate the error.

subscribe / unsubscribe
  C6  Duplicate subscribe of the same callable is idempotent (one delivery).
  C7  unsubscribe removes a registered callback; an unknown callback or an
      unsubscribed event is a no-op.

clear
  C8  clear(event) removes only that event's subscribers; clear() wipes all.

re-entrancy (RLock)
  C9  A subscriber that calls emit / subscribe on the same thread does not
      deadlock.

snapshot semantics
  C10 A subscriber added during dispatch does not fire in the current pass
      but does fire on the next emit (snapshot-then-iterate).
"""

from __future__ import annotations

import pytest

from floodlight_gui.core.event_bus import EventBus, Events


@pytest.fixture
def fresh_bus():
    """Return a fresh, empty ``EventBus`` isolated from the singleton.

    Every test owns its own instance so subscriber registrations never leak
    across tests or into the module-level ``bus``.
    """
    return EventBus()


def _recorder(sink):
    """Build a subscriber that appends its received kwargs to ``sink``.

    Parameters
    ----------
    sink : list
        List the returned callable appends each call's kwargs dict to.

    Returns
    -------
    callable
        Subscriber accepting ``**data`` and recording it.
    """

    def _cb(**data):
        sink.append(data)

    return _cb


# --------------------------------------------------------------------------- #
# emit: payload delivery + fan-out                                            #
# --------------------------------------------------------------------------- #


def test_emit_forwards_payload_verbatim(fresh_bus):
    """C1: every emitted kwarg reaches the subscriber unchanged."""
    received = []
    fresh_bus.subscribe(Events.DATA_LOADED, _recorder(received))
    fresh_bus.emit(Events.DATA_LOADED, format="dfl", teams=["Home", "Away"])
    assert received == [{"format": "dfl", "teams": ["Home", "Away"]}]


def test_emit_fans_out_to_all_subscribers(fresh_bus):
    """C2: one emit delivers the same payload to every subscriber."""
    a, b, c = [], [], []
    for sink in (a, b, c):
        fresh_bus.subscribe(Events.FRAME_CHANGED, _recorder(sink))
    fresh_bus.emit(Events.FRAME_CHANGED, frame=7)
    assert a == b == c == [{"frame": 7}]


@pytest.mark.parametrize(
    "subscribe_event",
    [None, Events.DATA_CLEARED],
)
def test_emit_with_no_matching_subscribers_is_noop(fresh_bus, subscribe_event):
    """C3: emitting an event with no subscribers does nothing and does not raise.

    Covers both the wholly-empty registry (``subscribe_event`` is None) and a
    bus that has subscribers for a different event only.
    """
    seen = []
    if subscribe_event is not None:
        fresh_bus.subscribe(subscribe_event, _recorder(seen))
    fresh_bus.emit(Events.DATA_LOADED, app="x")
    assert seen == []


# --------------------------------------------------------------------------- #
# priority + ordering                                                         #
# --------------------------------------------------------------------------- #


def test_emit_runs_subscribers_in_priority_then_insertion_order(fresh_bus):
    """C4: lower priority runs first; equal priority keeps insertion order."""
    order = []

    def _make(label):
        def _cb(**_):
            order.append(label)

        return _cb

    # Register out of priority order; equal-priority pair (b, c) tests stability.
    fresh_bus.subscribe(Events.XY_STACK_CHANGED, _make("late"), priority=10)
    fresh_bus.subscribe(Events.XY_STACK_CHANGED, _make("b"), priority=0)
    fresh_bus.subscribe(Events.XY_STACK_CHANGED, _make("c"), priority=0)
    fresh_bus.emit(Events.XY_STACK_CHANGED)
    assert order == ["b", "c", "late"]


# --------------------------------------------------------------------------- #
# exception isolation                                                          #
# --------------------------------------------------------------------------- #


def test_emit_isolates_a_raising_subscriber(fresh_bus):
    """C5: a raising subscriber is logged and swallowed; others still run."""
    survivors = []

    def _boom(**_):
        raise RuntimeError("subscriber blew up")

    # Boom is first by priority so the others must still run despite its raise.
    fresh_bus.subscribe(Events.MODEL_FITTED, _boom, priority=0)
    fresh_bus.subscribe(Events.MODEL_FITTED, _recorder(survivors), priority=1)
    fresh_bus.emit(Events.MODEL_FITTED, model_key="centroid")
    assert survivors == [{"model_key": "centroid"}]


# --------------------------------------------------------------------------- #
# subscribe / unsubscribe                                                      #
# --------------------------------------------------------------------------- #


def test_duplicate_subscribe_delivers_once(fresh_bus):
    """C6: subscribing the same callable twice yields a single delivery."""
    received = []
    cb = _recorder(received)
    fresh_bus.subscribe(Events.DATA_LOADED, cb)
    fresh_bus.subscribe(Events.DATA_LOADED, cb, priority=5)
    fresh_bus.emit(Events.DATA_LOADED, n=1)
    assert received == [{"n": 1}]


def test_unsubscribe_removes_callback(fresh_bus):
    """C7: an unsubscribed callback no longer receives emits."""
    received = []
    cb = _recorder(received)
    fresh_bus.subscribe(Events.DATA_LOADED, cb)
    fresh_bus.unsubscribe(Events.DATA_LOADED, cb)
    fresh_bus.emit(Events.DATA_LOADED, n=1)
    assert received == []


def test_unsubscribe_unknown_is_noop(fresh_bus):
    """C7: unsubscribing an unknown callback or unseen event does not raise.

    Removing a never-registered callable, and removing from an event that has
    no subscriber list, both leave existing subscribers intact.
    """
    received = []
    cb = _recorder(received)
    fresh_bus.subscribe(Events.DATA_LOADED, cb)
    fresh_bus.unsubscribe(Events.DATA_LOADED, lambda **_: None)  # never registered
    fresh_bus.unsubscribe(Events.DATA_CLEARED, cb)  # event never subscribed
    fresh_bus.emit(Events.DATA_LOADED, n=1)
    assert received == [{"n": 1}]


# --------------------------------------------------------------------------- #
# clear                                                                        #
# --------------------------------------------------------------------------- #


def test_clear_event_removes_only_that_event(fresh_bus):
    """C8: clear(event) drops that event's subscribers and leaves others."""
    kept, dropped = [], []
    fresh_bus.subscribe(Events.DATA_LOADED, _recorder(dropped))
    fresh_bus.subscribe(Events.FRAME_CHANGED, _recorder(kept))
    fresh_bus.clear(Events.DATA_LOADED)
    fresh_bus.emit(Events.DATA_LOADED, n=1)
    fresh_bus.emit(Events.FRAME_CHANGED, frame=2)
    assert dropped == []
    assert kept == [{"frame": 2}]


def test_clear_all_removes_every_subscriber(fresh_bus):
    """C8: clear() with no argument empties the whole registry."""
    a, b = [], []
    fresh_bus.subscribe(Events.DATA_LOADED, _recorder(a))
    fresh_bus.subscribe(Events.FRAME_CHANGED, _recorder(b))
    fresh_bus.clear()
    fresh_bus.emit(Events.DATA_LOADED, n=1)
    fresh_bus.emit(Events.FRAME_CHANGED, frame=2)
    assert a == []
    assert b == []


# --------------------------------------------------------------------------- #
# re-entrancy (RLock)                                                          #
# --------------------------------------------------------------------------- #


def test_subscriber_may_emit_reentrantly_without_deadlock(fresh_bus):
    """C9: a subscriber that emits another event on the same thread completes.

    The RLock must permit re-entry; a deadlock here would hang rather than
    fail, so reaching the assertion at all is the contract.
    """
    inner = []
    fresh_bus.subscribe(Events.FRAME_CHANGED, _recorder(inner))

    def _outer(**_):
        fresh_bus.emit(Events.FRAME_CHANGED, frame=99)

    fresh_bus.subscribe(Events.DATA_LOADED, _outer)
    fresh_bus.emit(Events.DATA_LOADED)
    assert inner == [{"frame": 99}]


# --------------------------------------------------------------------------- #
# snapshot semantics                                                           #
# --------------------------------------------------------------------------- #


def test_subscribe_during_emit_defers_to_next_emit(fresh_bus):
    """C10: a subscriber added mid-dispatch fires only on the following emit.

    emit snapshots the subscriber list before iterating, so the late
    subscriber misses the in-flight pass and is delivered the next one.
    """
    late = []

    def _adder(**_):
        fresh_bus.subscribe(Events.DATA_LOADED, _recorder(late))

    fresh_bus.subscribe(Events.DATA_LOADED, _adder)
    fresh_bus.emit(Events.DATA_LOADED, pass_no=1)
    assert late == []  # added during pass 1, did not fire in pass 1
    fresh_bus.emit(Events.DATA_LOADED, pass_no=2)
    assert late == [{"pass_no": 2}]
