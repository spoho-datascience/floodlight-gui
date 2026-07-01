"""Pub/sub event bus: the sole cross-tab notification mechanism for Floodlight GUI.

Invariants upheld by this module:

- DPG-free: this module imports no DPG symbols; ``import floodlight_gui.core.event_bus``
  works without a running DPG context.
- Emit-only from the app shell: ``FloodlightApp`` is the only permitted emitter.
  ``DataStore`` and tabs must subscribe but never call ``bus.emit``.
- Single-instance contract: ``bus`` is the module-level singleton; all producers
  and consumers import and share it.

Layering: ``core/`` sits below ``tabs/`` and ``registry/``. Nothing in ``core/``
may import from ``tabs/`` or ``registry/`` (would create a circular dependency).

Dispatch guarantees (upheld by :meth:`EventBus.emit`):

- Snapshot-then-iterate: the subscriber list is snapshotted under the lock before
  iteration so that a subscriber may subscribe or unsubscribe during dispatch
  without affecting the current emit pass.
- Priority ordering: subscribers with lower priority values run before those with
  higher values. Ties preserve insertion order (``list.sort`` is stable).
- RLock re-entrancy: the underlying lock is a :class:`threading.RLock` so a
  subscriber that calls ``bus.emit`` or ``bus.subscribe`` on the same thread does
  not deadlock.
- Exception isolation: if a subscriber raises, the exception is logged and the
  remaining subscribers still run; one broken subscriber cannot silence the others.

Priority convention: priority 0 for data-store updates (store must be consistent
before tabs read from it); priority 10+ for tab UI refreshes.

Event payload convention: ``FloodlightApp`` always supplies ``app=self`` so
subscribers can reach the store without importing ``app.py``. Subscribers absorb
unknown kwargs via ``**_`` for forward compatibility.

Example usage::

    from floodlight_gui.core.event_bus import bus, Events

    def _on_data_loaded(app=None, **_):
        if app is None:
            return
        # read from app.store, update UI, etc.

    bus.subscribe(Events.DATA_LOADED, _on_data_loaded, priority=10)

    # Emit only from FloodlightApp:
    bus.emit(Events.DATA_LOADED, app=self, format=fmt, teams=teams)
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from enum import Enum, unique
from typing import Any

__all__ = ["Events", "EventBus", "bus"]

logger = logging.getLogger(__name__)


@unique
class Events(str, Enum):
    """Canonical event identifiers for the Floodlight GUI pub/sub bus.

    Every cross-tab notification flows through one of these members; string
    comparisons are forbidden (use the enum member). Inheriting from ``str``
    keeps DPG interop smooth when an event value is logged or stored.

    Emitter and subscriber notes
    ----------------------------
    APP_INITIALIZED
        Emitted once by ``FloodlightApp.create_ui`` after the full DPG layout is
        built. Subscribers: tabs that need a one-time post-layout setup pass.
    DATA_LOADED
        Emitted by ``FloodlightApp`` after a successful load+normalize cycle.
        Payload: ``app``, ``format``, ``teams``, ``periods``.
        Subscribers: all tabs (refresh selectors, clear stale results).
    DATA_CLEARED
        Emitted by ``FloodlightApp`` when the user clears loaded data.
        Subscribers: all tabs (reset UI to empty state).
    FRAME_CHANGED
        Emitted by the visualization playback clock on every frame tick.
        Payload: ``frame`` (int).
        Subscribers: visualization tab (render loop trigger).
    SELECTION_CHANGED
        Emitted by the visualization tab when the active period or team changes.
        Subscribers: visualization tab (re-resolves active XY).
    XY_STACK_CHANGED
        Emitted by ``FloodlightApp`` after an XY transform is applied or undone.
        Subscribers: transforms tab (refreshes op history), inspect tab (re-reads
        XY metadata), visualization tab (re-resolves active XY).
    MODEL_FITTED
        Emitted by ``FloodlightApp`` after a model fit completes.
        Payload: ``app``, ``model_key``, ``period``, ``team``.
        Subscribers: metrics tab (refreshes available model outputs).
    MODEL_OUTPUTS_CHANGED
        Emitted when the user toggles an output checkbox in the Models tab without
        a new fit (no fit happens; only the "available outputs" set changes).
        Subscribers: metrics tab (refreshes its model-output data-input dropdown
        so un-ticked outputs disappear).
    EXPORT_REQUESTED
        Emitted by export helpers after a successful write (one per click, after
        all leaf files are written). Payload: ``kind``, ``target``, ``base_name``.
        Subscribers: status bar (displays a confirmation message).
    IO_REGISTRY_CHANGED
        Emitted when a runtime change to ``IO_REGISTRY`` occurs (e.g. plugin load).
        Subscribers: load tab (rebuilds provider list).
    MODEL_REGISTRY_CHANGED
        Emitted when ``MODEL_REGISTRY`` changes at runtime.
        Subscribers: model tab (rebuilds picker).
    TRANSFORM_REGISTRY_CHANGED
        Emitted when ``TRANSFORM_REGISTRY`` changes at runtime.
        Subscribers: transforms tab (rebuilds picker).
    METRICS_REGISTRY_CHANGED
        Emitted when ``METRICS_REGISTRY`` changes at runtime.
        Subscribers: metrics tab (rebuilds picker).
    """

    APP_INITIALIZED = "app_initialized"
    DATA_LOADED = "data_loaded"
    DATA_CLEARED = "data_cleared"
    FRAME_CHANGED = "frame_changed"
    SELECTION_CHANGED = "selection_changed"
    XY_STACK_CHANGED = "xy_stack_changed"
    MODEL_FITTED = "model_fitted"
    # Fired when the user (un)ticks an output in the Models Step 2 checklist.
    # Distinct from MODEL_FITTED: no fit happens, only the "available outputs"
    # set changes. Subscribers: metrics tab refreshes its inputs dropdown so
    # unticked outputs disappear from the queryable list.
    MODEL_OUTPUTS_CHANGED = "model_outputs_changed"
    EXPORT_REQUESTED = "export_requested"
    IO_REGISTRY_CHANGED = "io_registry_changed"
    MODEL_REGISTRY_CHANGED = "model_registry_changed"
    TRANSFORM_REGISTRY_CHANGED = "transform_registry_changed"
    METRICS_REGISTRY_CHANGED = "metrics_registry_changed"


class EventBus:
    """Thread-safe publish/subscribe event bus.

    Subscribers are plain callables that receive all emitted kwargs. Each
    subscriber may specify a priority (lower values run first, default 0); ties
    preserve insertion order because ``list.sort`` is stable.

    Dispatch invariants:

    - Snapshot-then-iterate: the subscriber list is copied under the lock before
      iteration, so subscribers may call ``subscribe`` or ``unsubscribe`` during
      the current emit pass without affecting it.
    - RLock re-entrancy: the underlying :class:`threading.RLock` allows a
      subscriber to call ``bus.emit`` or ``bus.subscribe`` on the same thread
      without deadlocking.
    - Exception isolation: a subscriber that raises is logged via
      ``logger.exception``; remaining subscribers always run.
    """

    def __init__(self) -> None:
        """Initialise an empty subscriber registry and the reentrant lock."""
        # Maps each event to its sorted (priority, callback) list.
        self._subscribers: dict[Events, list[tuple[int, Callable[..., Any]]]] = {}
        self._lock = threading.RLock()

    def subscribe(
        self,
        event: Events,
        callback: Callable[..., Any],
        priority: int = 0,
    ) -> None:
        """Register *callback* for *event*.

        Duplicate registrations of the same callable are silently ignored.
        The subscriber list is re-sorted after insertion; ties preserve
        insertion order (stable sort).

        Parameters
        ----------
        event : Events
            One of the :class:`Events` enum members.
        callback : callable
            Callable that will receive ``**data`` when the event fires.
            Should absorb unknown kwargs via ``**_`` for forward compatibility.
        priority : int, default 0
            Lower values run before higher values. Use 0 for data-store updates
            and 10+ for UI refreshes so the store is consistent before tabs read.
        """
        with self._lock:
            subs = self._subscribers.setdefault(event, [])
            # Reject duplicate registration of the same callback object.
            for _, cb in subs:
                if cb is callback:
                    return
            subs.append((priority, callback))
            subs.sort(key=lambda t: t[0])

    def unsubscribe(self, event: Events, callback: Callable[..., Any]) -> None:
        """Remove *callback* from *event*. No-op if not registered.

        Parameters
        ----------
        event : Events
            The event from which to remove *callback*.
        callback : callable
            The exact callable object that was previously passed to
            :meth:`subscribe`.
        """
        with self._lock:
            subs = self._subscribers.get(event)
            if subs is None:
                return
            self._subscribers[event] = [(p, cb) for p, cb in subs if cb is not callback]

    def emit(self, event: Events, **data: Any) -> None:
        """Fire *event*, passing **data to every subscriber in priority order.

        The subscriber list is snapshotted under the lock before iteration so
        that subscribers may subscribe or unsubscribe during dispatch without
        affecting the current emit pass. If a subscriber raises, the error is
        logged and the next subscriber still runs (exception isolation).

        Because the lock is a :class:`threading.RLock`, a subscriber that calls
        ``bus.emit`` or ``bus.subscribe`` on the same thread does not deadlock.

        Parameters
        ----------
        event : Events
            The event to dispatch.
        **data : Any
            Keyword arguments forwarded verbatim to every subscriber.

        Notes
        -----
        Emitting is permitted only from ``FloodlightApp``; tabs and ``DataStore``
        must only subscribe.
        """
        with self._lock:
            # Snapshot the list so subscribers can modify registrations mid-emit.
            subs = list(self._subscribers.get(event, []))

        for _priority, callback in subs:
            try:
                callback(**data)
            except Exception:  # noqa: BLE001 - callback boundary; must not prevent other subscribers from running
                logger.exception("Subscriber %r failed on event %s", callback, event.value)

    def clear(self, event: Events | None = None) -> None:
        """Remove all subscribers for *event*, or all events if ``None``.

        Parameters
        ----------
        event : Events or None
            When an :class:`Events` member is given, only that event's
            subscribers are removed. ``None`` clears the entire registry.
        """
        with self._lock:
            if event is None:
                self._subscribers.clear()
            else:
                self._subscribers.pop(event, None)


# Module-level singleton: all producers and consumers import and share this instance.
bus = EventBus()
