"""Background worker for dataset download and import in the Load tab.

Threading model: ``start_dataset_download`` runs on the GUI thread and immediately
spawns a daemon worker thread. The worker does all heavy work (download, parse,
metadata extraction) without touching DPG widgets. Communication back to the GUI
thread uses a shared ``state`` dict as a mailbox: the worker writes
``done``/``payload``/``error``/``message``; a ``dpg.set_frame_callback`` poll loop
on the GUI thread reads them and writes progress to the Load tab status line. No
modal window, progress bar, or spinner is shown; progress is plain status text.

Commit-then-emit path: on success, ``_finish`` calls ``_dispatch_loaded``, which
routes through ``app.commit_loaded``. That single producer performs the locked
sequence: store write -> ``update_data_info`` -> ``emit(DATA_LOADED, ...)``.
Errors skip the commit and surface through an error dialog.

Layering: this module lives under ``tabs/`` and imports DPG at module scope. It
is the only place in the load tab that spawns threads.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import threading

import dearpygui.dearpygui as dpg

from floodlight_gui.engine.load_data import (
    _finalize_dataset_payload,
    _resolve_cache_root,
    load_provider_data,
)
from floodlight_gui.registry.io import IO_REGISTRY

logger = logging.getLogger(__name__)


def start_dataset_download(app, provider_key: str, match_id, status_tag: str) -> None:
    """Kick off a threaded dataset import, reporting progress on a status line.

    Creates the shared state mailbox, writes the initial status message, spawns
    the daemon worker thread, and arms the first GUI-thread poll tick via
    ``dpg.set_frame_callback``. Safe to call from a DPG button callback on the
    GUI thread.

    Parameters
    ----------
    app
        The live ``FloodlightApp`` instance, passed through to
        ``app.commit_loaded`` on success.
    provider_key : str
        Registry key into ``IO_REGISTRY`` identifying the dataset provider.
    match_id
        Provider-specific match identifier forwarded to ``load_provider_data``.
    status_tag : str
        DPG text-widget tag (owned by the dataset section) that the GUI poll
        writes progress messages into.

    Notes
    -----
    Side-effects: spawns one daemon thread (``_worker_body``), writes the
    *status_tag* text widget, and arms a ``set_frame_callback`` poll chain that
    runs until the worker finishes.
    """
    # Shared mailbox between worker thread and GUI poll. Only the worker writes
    # done/payload/error; only the poll reads and finishes.
    state: dict = {
        "done": False,
        "payload": None,
        "error": None,
        "message": "Starting...",
        "cancel": threading.Event(),
        "torn_down": False,
        "app": app,
        "provider_key": provider_key,
        "status_tag": status_tag,
    }

    _begin_progress(provider_key, state)

    worker = threading.Thread(
        target=_worker_body,
        args=(provider_key, match_id, state),
        daemon=True,
    )
    worker.start()

    # Arm the GUI-thread poll. _poll re-arms itself until the worker finishes.
    dpg.set_frame_callback(dpg.get_frame_count() + 1, lambda: _poll(state))


# --------------------------------------------------------------------------- #
# Status-line helpers
# --------------------------------------------------------------------------- #


def _begin_progress(provider_key: str, state: dict) -> None:
    """Write the initial ``Importing <provider>...`` message to the status line."""
    display = IO_REGISTRY.get(provider_key, {}).get("display_name", provider_key)
    _status(state, f"Importing {display}: starting...")


def _status(state: dict, message: str) -> None:
    """Write *message* to the status text widget named in the mailbox; no-op if absent."""
    status_tag = state.get("status_tag")
    with contextlib.suppress(Exception):
        if status_tag and dpg.does_item_exist(status_tag):
            dpg.set_value(status_tag, message)


def _teardown(state: dict) -> None:
    """Mark the run finished so ``_finish`` is idempotent. Safe to call twice."""
    state["torn_down"] = True


# --------------------------------------------------------------------------- #
# Worker thread body (no DPG widget calls here -- GUI thread only)
# --------------------------------------------------------------------------- #


def _worker_body(provider_key: str, match_id, state: dict) -> None:
    """Run download, parse, and metadata extraction on the worker thread.

    Writes ``state['payload']`` on success or ``state['error']`` on failure.
    Always sets ``state['done'] = True`` in ``finally`` and schedules a fallback
    ``_finish`` tick via ``set_frame_callback`` so teardown is guaranteed even
    if the GUI-thread poll stalls.

    Parameters
    ----------
    provider_key : str
        Registry key forwarded to ``load_provider_data``.
    match_id
        Provider-specific match identifier.
    state : dict
        Shared mailbox; ``state['cancel']`` is checked by the loader at its
        boundaries.
    """
    cancel_event: threading.Event = state["cancel"]

    def on_progress(msg: str) -> None:
        # Thread-safe: store the latest message; the GUI poll renders it.
        state["message"] = msg

    try:
        raw = load_provider_data(
            provider_key,
            {},
            on_progress=on_progress,
            cancel_event=cancel_event,
            match_id=match_id,
        )
        if raw is None or cancel_event.is_set():
            _cleanup_partial_cache(provider_key)
            state["error"] = (
                None if cancel_event.is_set() else RuntimeError("Dataset load failed (see log).")
            )
            return
        # Heavy metadata extraction stays on the worker thread.
        state["payload"] = _finalize_dataset_payload(provider_key, raw)
    except Exception as exc:  # noqa: BLE001 -- worker boundary; surface via state
        logger.exception("Dataset worker failed for %s: %s", provider_key, exc)
        _cleanup_partial_cache(provider_key)
        state["error"] = exc
    finally:
        state["done"] = True
        # Fallback final tick: if the GUI poll has stalled (e.g. an animation
        # tick raised and failed to re-arm), this guarantees a finish attempt.
        # set_frame_callback is thread-safe.
        with contextlib.suppress(Exception):
            dpg.set_frame_callback(dpg.get_frame_count() + 1, lambda: _finish(state))


def _cleanup_partial_cache(provider_key: str) -> None:
    """Remove the dataset's cache subdir on cancellation or error. Never raises."""
    try:
        cls_name = IO_REGISTRY.get(provider_key, {}).get("dataset_class", provider_key)
        subdir = os.path.join(_resolve_cache_root(), cls_name.lower())
        if os.path.isdir(subdir):
            shutil.rmtree(subdir, ignore_errors=True)
    except Exception:  # noqa: BLE001 -- cleanup is best-effort; swallow everything
        logger.warning("Partial-cache cleanup failed for %s", provider_key, exc_info=True)


# --------------------------------------------------------------------------- #
# GUI-thread poll + dispatch
# --------------------------------------------------------------------------- #


def _poll(state: dict) -> None:
    """Per-frame GUI tick: write the latest progress message and check for completion.

    Re-arms itself via ``set_frame_callback`` BEFORE any widget work so a raising
    status write never kills the poll loop. Delegates to ``_finish`` when the
    worker signals ``done``.

    Parameters
    ----------
    state : dict
        Shared mailbox read by the poll; not written.
    """
    if state.get("torn_down"):
        return

    if state.get("done"):
        _finish(state)
        return

    # Re-arm first so a raising status write never kills the poll loop.
    with contextlib.suppress(Exception):
        dpg.set_frame_callback(dpg.get_frame_count() + 1, lambda: _poll(state))

    _status(state, state.get("message", ""))


def _finish(state: dict) -> None:
    """Terminal GUI-thread step: dispatch the result (if any) and update the status line.

    Called by both the poll loop (on ``done``) and the worker's fallback
    ``set_frame_callback``. The ``torn_down`` guard makes it idempotent.

    Parameters
    ----------
    state : dict
        Shared mailbox. Reads ``payload``, ``error``, ``app``, ``provider_key``,
        and ``status_tag``.

    Notes
    -----
    Side-effects on the success path: calls ``_dispatch_loaded``, which calls
    ``app.commit_loaded`` (store write + ``update_data_info`` + ``emit(DATA_LOADED,
    ...)``). On the error path: opens an error modal. On cancellation (payload
    None, error None): updates the status line only.
    """
    if state.get("torn_down"):
        return

    payload = state.get("payload")
    error = state.get("error")
    display = IO_REGISTRY.get(state["provider_key"], {}).get("display_name", state["provider_key"])

    if payload is not None and error is None:
        with contextlib.suppress(Exception):
            _dispatch_loaded(state["app"], state["provider_key"], payload)
        _status(state, f"Imported {display}.")
    elif error is not None:
        _status(state, f"Import failed for {display} (see dialog).")
        with contextlib.suppress(Exception):
            from floodlight_gui.tabs._shared.error_helpers import show_error_modal

            show_error_modal(
                state.get("status_tag", "load_dataset"),
                error,
                context=f"Dataset import failed for {state['provider_key']}.",
            )
    else:
        _status(state, f"Import cancelled for {display}.")

    _teardown(state)


def _dispatch_loaded(app, provider_key: str, payload: dict) -> None:
    """Route a successfully loaded dataset through ``app.commit_loaded``.

    ``app.commit_loaded`` is the single producer for the locked sequence:
    store write -> ``update_data_info`` -> ``emit(DATA_LOADED, ...)``.
    ``payload["data"]`` is the ``(pitch, event_data, position_data, teamsheet)``
    4-tuple built by ``_finalize_dataset_payload``.

    Parameters
    ----------
    app
        Live ``FloodlightApp`` instance. A ``None`` app is a no-op (worker
        finished after app teardown).
    provider_key : str
        Forwarded as ``provider=`` to ``app.commit_loaded`` for metadata tagging.
    payload : dict
        Dict with keys ``"data"`` (the 4-tuple) and ``"metadata"`` (provider
        metadata dict).

    Notes
    -----
    Side-effects: calls ``app.commit_loaded``, which writes
    ``app.store.loaded_data``, calls ``app.update_data_info``, and emits
    ``Events.DATA_LOADED`` on the EventBus.
    """
    if app is None:
        return
    app.commit_loaded(payload["data"], metadata=payload["metadata"], provider=provider_key)
