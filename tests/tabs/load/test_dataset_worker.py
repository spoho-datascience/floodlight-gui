"""Behavioral contracts for ``floodlight_gui.tabs.load.dataset_worker``.

This is the background dataset-import state machine. ``_worker_body`` runs the
loader off-thread and records success/error/cancel into a shared mailbox;
``_finish`` is the terminal GUI-thread step that dispatches the payload, shows
an error, or updates the status line. The loader (``load_provider_data``), the
payload finalizer, the partial-cache cleanup, the app's ``commit_loaded``, and
the error modal are the seams; all are stubbed so the tests assert only this
module's routing decisions, never the loader's behavior.

Behavioral contracts guarded here
---------------------------------
_worker_body
  C1  On success, calls the loader with the provider key, an empty file map,
      and the cancel/progress/match-id kwargs, finalizes the raw result into
      ``state['payload']``, and always marks ``state['done']``.
  C2  A cancelled run (cancel flag set) records no error, no payload, and
      cleans the partial cache.
  C3  A ``None`` loader result without cancellation records a RuntimeError and
      cleans the partial cache.
  C4  A raising loader records the exception and cleans the partial cache.

_finish
  C5  A payload with no error dispatches through the app once, then finishes.
  C6  An error opens the error modal and finishes, without dispatching.
  C7  Neither payload nor error (cancellation) finishes without dispatch or
      error modal.
  C8  A torn-down state is a no-op: no second dispatch.

_dispatch_loaded
  C9  Forwards the payload data, metadata, and provider to ``commit_loaded``,
      and is a no-op for a None app.

_teardown
  C10 Sets the torn-down flag and is safe to call twice.

start_dataset_download
  C11 Seeds the mailbox, spawns a daemon worker, and arms the first GUI poll.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.load.dataset_worker as worker


class _CancelEvent:
    """Minimal threading.Event stand-in with a settable flag."""

    def __init__(self, value=False):
        self._set = value

    def set(self):
        self._set = True

    def is_set(self):
        return self._set


@pytest.fixture
def patch_loader(monkeypatch):
    """Replace the loader + finalizer seams with controllable doubles.

    Returns a callable ``(raw, payload, raises)`` that installs a
    ``load_provider_data`` returning ``raw`` (or raising ``raises``) and a
    ``_finalize_dataset_payload`` returning ``payload``. Records every cleanup
    invocation on ``.cleanups`` and every loader call on ``.loader_calls``.
    """

    state = {"loader_calls": [], "cleanups": []}

    def _install(raw=None, payload=None, raises=None):
        def _loader(provider_key, file_paths, **kw):
            state["loader_calls"].append((provider_key, file_paths, kw))
            if raises is not None:
                raise raises
            return raw

        monkeypatch.setattr(worker, "load_provider_data", _loader)
        monkeypatch.setattr(worker, "_finalize_dataset_payload", lambda key, r: payload)
        monkeypatch.setattr(
            worker, "_cleanup_partial_cache", lambda key: state["cleanups"].append(key)
        )
        # Suppress the fallback set_frame_callback (no DPG context in tests).
        monkeypatch.setattr(
            worker,
            "dpg",
            type(
                "D",
                (),
                {
                    "set_frame_callback": staticmethod(lambda *a, **k: None),
                    "get_frame_count": staticmethod(lambda: 0),
                },
            )(),
        )
        return state

    _install.state = state
    return _install


def _mailbox(cancel):
    """Return a fresh worker mailbox with the given cancel event."""
    return {
        "done": False,
        "payload": None,
        "error": None,
        "message": "",
        "cancel": cancel,
        "app": object(),
        "provider_key": "eigd_h",
    }


# --------------------------------------------------------------------------- #
# _worker_body                                                                  #
# --------------------------------------------------------------------------- #


def test_worker_body_success_finalizes_payload(patch_loader):
    """C1: a successful load finalizes the payload and marks done."""
    patch_loader(raw="RAW", payload={"data": "D", "metadata": {}})
    state = _mailbox(_CancelEvent())
    worker._worker_body("eigd_h", "m1", state)

    assert state["done"] is True
    assert state["payload"] == {"data": "D", "metadata": {}}
    assert state["error"] is None
    key, file_paths, kw = patch_loader.state["loader_calls"][0]
    assert key == "eigd_h"
    assert file_paths == {}
    assert kw["match_id"] == "m1"
    assert kw["cancel_event"] is state["cancel"]
    assert callable(kw["on_progress"])


def test_worker_body_cancelled_records_no_error(patch_loader):
    """C2: a cancelled run yields no error, no payload, and cleans the cache."""
    patch_loader(raw="RAW")
    state = _mailbox(_CancelEvent(value=True))
    worker._worker_body("eigd_h", "m1", state)

    assert state["payload"] is None
    assert state["error"] is None
    assert patch_loader.state["cleanups"] == ["eigd_h"]
    assert state["done"] is True


def test_worker_body_none_result_records_runtime_error(patch_loader):
    """C3: a None result without cancel records a RuntimeError and cleans up."""
    patch_loader(raw=None)
    state = _mailbox(_CancelEvent())
    worker._worker_body("eigd_h", "m1", state)

    assert isinstance(state["error"], RuntimeError)
    assert state["payload"] is None
    assert patch_loader.state["cleanups"] == ["eigd_h"]


def test_worker_body_exception_records_error(patch_loader):
    """C4: a raising loader records the exception and cleans the cache."""
    boom = ValueError("download exploded")
    patch_loader(raises=boom)
    state = _mailbox(_CancelEvent())
    worker._worker_body("eigd_h", "m1", state)

    assert state["error"] is boom
    assert patch_loader.state["cleanups"] == ["eigd_h"]
    assert state["done"] is True


# --------------------------------------------------------------------------- #
# _finish                                                                       #
# --------------------------------------------------------------------------- #


@pytest.fixture
def patch_finish(monkeypatch):
    """Stub the dispatch + error-modal + teardown seams used by ``_finish``.

    Returns a dict recording each seam's invocations so a test can assert
    which branch ``_finish`` took.
    """
    rec = {"dispatched": [], "errors": [], "torn": []}

    def _dispatch(app, key, payload):
        rec["dispatched"].append((app, key, payload))

    monkeypatch.setattr(worker, "_dispatch_loaded", _dispatch)
    monkeypatch.setattr(worker, "_teardown", lambda state: rec["torn"].append(state))

    import floodlight_gui.tabs._shared.error_helpers as eh

    monkeypatch.setattr(eh, "show_error_modal", lambda *a, **kw: rec["errors"].append((a, kw)))
    return rec


def test_finish_dispatches_payload_then_tears_down(patch_finish):
    """C5: a payload with no error dispatches once, then tears down."""
    state = {"payload": {"data": "D"}, "error": None, "app": "APP", "provider_key": "eigd_h"}
    worker._finish(state)
    assert patch_finish["dispatched"] == [("APP", "eigd_h", {"data": "D"})]
    assert patch_finish["errors"] == []
    assert patch_finish["torn"] == [state]


def test_finish_shows_error_modal_then_tears_down(patch_finish):
    """C6: an error opens the error modal and tears down without dispatching."""
    state = {"payload": None, "error": ValueError("x"), "app": "APP", "provider_key": "eigd_h"}
    worker._finish(state)
    assert patch_finish["dispatched"] == []
    assert len(patch_finish["errors"]) == 1
    assert patch_finish["torn"] == [state]


def test_finish_cancellation_is_silent_teardown(patch_finish):
    """C7: no payload and no error tears down silently."""
    state = {"payload": None, "error": None, "app": "APP", "provider_key": "eigd_h"}
    worker._finish(state)
    assert patch_finish["dispatched"] == []
    assert patch_finish["errors"] == []
    assert patch_finish["torn"] == [state]


def test_finish_is_noop_when_already_torn_down(patch_finish):
    """C8: a torn-down state never dispatches a second time."""
    state = {
        "payload": {"data": "D"},
        "error": None,
        "app": "APP",
        "provider_key": "eigd_h",
        "torn_down": True,
    }
    worker._finish(state)
    assert patch_finish["dispatched"] == []
    assert patch_finish["torn"] == []


# --------------------------------------------------------------------------- #
# _dispatch_loaded                                                              #
# --------------------------------------------------------------------------- #


def test_dispatch_loaded_forwards_to_commit_loaded():
    """C9 (loaded): payload data/metadata/provider reach commit_loaded."""
    calls = []

    class _App:
        def commit_loaded(self, data, *, metadata, provider):
            calls.append((data, metadata, provider))

    payload = {"data": ("pitch",), "metadata": {"format_type": "eigd_h"}}
    worker._dispatch_loaded(_App(), "eigd_h", payload)
    assert calls == [(("pitch",), {"format_type": "eigd_h"}, "eigd_h")]


def test_dispatch_loaded_none_app_is_noop():
    """C9 (teardown race): a None app dispatches nothing."""
    worker._dispatch_loaded(None, "eigd_h", {"data": (), "metadata": {}})


# --------------------------------------------------------------------------- #
# _teardown                                                                     #
# --------------------------------------------------------------------------- #


def test_teardown_sets_torn_down_flag():
    """C10: teardown marks the run finished so a re-entrant _finish short-circuits."""
    state = {}
    worker._teardown(state)
    assert state["torn_down"] is True

    worker._teardown(state)
    assert state["torn_down"] is True


# --------------------------------------------------------------------------- #
# start_dataset_download                                                        #
# --------------------------------------------------------------------------- #


def test_start_dataset_download_seeds_mailbox_and_arms_poll(monkeypatch):
    """C11: the launcher seeds state, spawns a daemon worker, and arms a poll."""
    built = {}
    spawned = []
    armed = []

    def _begin_progress(key, state):
        built.update(key=key, state=state)

    monkeypatch.setattr(worker, "_begin_progress", _begin_progress)

    class _Thread:
        def __init__(self, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            spawned.append((self.target, self.daemon))

    monkeypatch.setattr(worker.threading, "Thread", _Thread)
    monkeypatch.setattr(
        worker,
        "dpg",
        type(
            "D",
            (),
            {
                "set_frame_callback": staticmethod(lambda frame, cb: armed.append(frame)),
                "get_frame_count": staticmethod(lambda: 7),
            },
        )(),
    )

    app = object()
    worker.start_dataset_download(app, "eigd_h", "m1", "load_dataset_status")

    seeded = built["state"]
    assert seeded["app"] is app
    assert seeded["provider_key"] == "eigd_h"
    assert seeded["status_tag"] == "load_dataset_status"
    assert seeded["done"] is False
    assert spawned and spawned[0][1] is True
    assert armed == [8]
