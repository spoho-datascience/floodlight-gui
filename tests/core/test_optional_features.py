"""Behavioral contracts for ``floodlight_gui.core.optional_features``.

This module exposes ``HAS_FFMPEG``: an eager, import-time capability probe that
is ``True`` only when both ``imageio`` and ``imageio_ffmpeg`` import cleanly,
and ``False`` (never raising) otherwise. The probe runs once when the module is
imported, so the seam these tests drive is ``importlib.reload`` under a
controlled import environment: blocking or restoring the dependency imports and
re-running the module top to bottom.

Behavioral contracts guarded here
----------------------------------
HAS_FFMPEG
  C1  It is a plain ``bool`` exported in the module's ``__all__``.
  C2  A reload whose dependency import fails sets it to ``False`` and swallows
      the failure (no exception escapes the import).
  C3  A reload whose dependency imports both succeed sets it to ``True``.
"""

from __future__ import annotations

import builtins
import importlib

import pytest

import floodlight_gui.core.optional_features as of

_BLOCKED = {"imageio", "imageio_ffmpeg"}


@pytest.fixture
def reload_with_imports(monkeypatch):
    """Reload ``optional_features`` under a patched import environment.

    Returns a callable ``(blocked)`` that patches ``builtins.__import__`` to
    raise ``ImportError`` for any module name in ``blocked`` (and its
    submodules), reloads the module so its top-level probe re-runs, and returns
    the reloaded module. The module is restored to its real state on teardown
    so later tests see the genuine probe result.

    Parameters
    ----------
    blocked : set of str
        Top-level module names whose import should fail during the reload.

    Returns
    -------
    module
        The freshly reloaded ``optional_features`` module.
    """
    real_import = builtins.__import__

    def _make(blocked):
        def _fake_import(name, *args, **kwargs):
            root = name.split(".")[0]
            if root in blocked:
                raise ImportError(f"blocked: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        return importlib.reload(of)

    yield _make

    # Restore the genuine probe result for the rest of the session.
    monkeypatch.undo()
    importlib.reload(of)


def test_has_ffmpeg_is_bool_in_all():
    """C1: ``HAS_FFMPEG`` is a bool listed in the module ``__all__``."""
    assert isinstance(of.HAS_FFMPEG, bool)
    assert "HAS_FFMPEG" in of.__all__


def test_has_ffmpeg_false_when_dependency_import_fails(reload_with_imports):
    """C2: a failed dependency import yields ``False`` without propagating.

    Blocking both ``imageio`` and ``imageio_ffmpeg`` makes the probe's import
    raise; the probe must catch it and report the capability as absent.
    """
    reloaded = reload_with_imports(_BLOCKED)
    assert reloaded.HAS_FFMPEG is False


def test_has_ffmpeg_true_when_both_dependencies_import(reload_with_imports):
    """C3: when both dependencies import cleanly the probe reports ``True``.

    This anchors the positive branch: with nothing blocked, a reload of the
    probe in this environment resolves to ``True`` (both packages installed).
    """
    pytest.importorskip("imageio")
    pytest.importorskip("imageio_ffmpeg")
    reloaded = reload_with_imports(set())
    assert reloaded.HAS_FFMPEG is True
