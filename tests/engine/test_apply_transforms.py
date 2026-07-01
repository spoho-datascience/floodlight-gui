"""Behavioral contracts for ``floodlight_gui.engine.apply_transforms``.

``apply_xy_op`` is the TRANSFORM_REGISTRY dispatcher. It picks one of two
branches from a descriptor, builds the upstream kwargs (either via the
descriptor ``build_args`` callable for XY-method entries, or by coercing the
raw GUI param dict for module-routed entries), dispatches, and returns either
the upstream result or the input ``xy`` when the upstream returned ``None``.

The seams are the upstream callables: the bound ``XY`` method (reached via
``getattr(xy, ...)``) and the module function (reached via
``importlib.import_module`` + ``getattr``). Both are stubbed so the tests
assert only this module's own decisions: which branch ran, what kwargs it
built, what it returned. No floodlight analytics are exercised.

Behavioral contracts guarded here
---------------------------------
C1  An ``op_key`` absent from TRANSFORM_REGISTRY raises ``KeyError``.
C2  An XY-method entry (``build_args`` present) dispatches via the bound
    method ``getattr(xy, desc["method"])`` and calls it with exactly the
    kwargs ``build_args`` produced.
C3  ``build_args`` is invoked even when ``params`` is ``None`` (the empty
    dict is substituted before the callable runs).
C4  A module-routed entry (``function_path``, no ``build_args``) imports the
    module half of the dotted path and calls ``fn(xy, **kwargs)`` on the
    attribute half.
C5  Module-routed param coercion: the string sentinel ``"none"`` and Python
    ``None`` are dropped from the kwargs; other values pass through verbatim.
C6  Module-routed param coercion: an ndarray-typed param whose raw value is a
    blank/whitespace string is treated as ``None`` and dropped, so the
    upstream default applies.
C7  The dispatcher returns the upstream result when it is non-``None``, and
    returns the input ``xy`` unchanged when the upstream returned ``None``
    (the in-place-op convention). Holds on both branches.
C8  A descriptor carrying neither ``source == "xy.method"`` (with the method
    seam) nor ``function_path`` raises ``KeyError`` (defensive guard: no real
    descriptor reaches this, every registry entry carries ``function_path``).
"""

from __future__ import annotations

import sys
import types

import pytest

import floodlight_gui.engine.apply_transforms as at
from floodlight_gui.engine.apply_transforms import apply_xy_op

# --------------------------------------------------------------------------- #
# Test doubles                                                                  #
# --------------------------------------------------------------------------- #


class _RecordingXY:
    """Minimal XY stand-in whose methods record their kwargs.

    Each attribute access for a method name returns a recorder that captures
    the keyword args it was called with, so tests can assert what
    ``build_args`` produced without invoking real XY math. The ``_return``
    map controls what a given method name returns (default ``None``, the
    in-place-op convention).
    """

    def __init__(self, returns=None):
        self.calls = {}
        self._returns = returns or {}

    def __getattr__(self, name):
        # Only intercept names the test asked about; everything else errors
        # normally so a typo does not silently pass.
        if name.startswith("_") or name in ("calls",):
            raise AttributeError(name)

        def _method(**kwargs):
            self.calls[name] = kwargs
            return self._returns.get(name)

        return _method


@pytest.fixture
def install_op(monkeypatch):
    """Install a single descriptor as the entire TRANSFORM_REGISTRY.

    Returns a callable ``(key, descriptor)`` that isolates the dispatcher from
    the real registry so a test sees only the entry it cares about.
    """

    def _install(key, descriptor):
        monkeypatch.setattr(at, "TRANSFORM_REGISTRY", {key: descriptor})
        return key

    return _install


@pytest.fixture
def fake_module(monkeypatch):
    """Install a fake importable module exposing one recording function.

    Returns a callable ``(module_path, fn_name)`` -> the recorder. The
    recorder captures the positional ``xy`` and keyword args it was dispatched
    with, and returns a unique sentinel so the dispatcher's return value is
    assertable. Registers the module in ``sys.modules`` so the dispatcher's
    ``importlib.import_module`` resolves it.
    """

    created: list[str] = []

    def _install(module_path, fn_name, returns="RESULT_SENTINEL"):
        recorder = types.SimpleNamespace(args=None, kwargs=None)

        def _fn(xy, **kwargs):
            recorder.args = (xy,)
            recorder.kwargs = kwargs
            return returns

        mod = types.ModuleType(module_path)
        setattr(mod, fn_name, _fn)
        monkeypatch.setitem(sys.modules, module_path, mod)
        created.append(module_path)
        recorder.fn = _fn
        return recorder

    return _install


# --------------------------------------------------------------------------- #
# C1: unknown key                                                               #
# --------------------------------------------------------------------------- #


def test_unknown_op_key_raises_keyerror(install_op):
    """C1: an op_key absent from the registry raises KeyError."""
    install_op("known", {"function_path": "m.f"})
    with pytest.raises(KeyError):
        apply_xy_op(_RecordingXY(), "absent", {})


# --------------------------------------------------------------------------- #
# C2 / C3: XY-method (build_args) branch                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "params, build_args, method, expected_kwargs",
    [
        # translate: two scalar widgets -> a single ``shift`` 2-tuple.
        (
            {"dx_meters": 2.0, "dy_meters": -3.0},
            lambda p: {"shift": (p["dx_meters"], p["dy_meters"])},
            "translate",
            {"shift": (2.0, -3.0)},
        ),
        # scale: ``axis == "both"`` collapses to None for the upstream method.
        (
            {"factor": 1.5, "axis": "both"},
            lambda p: {
                "factor": p["factor"],
                "axis": None if p["axis"] == "both" else p["axis"],
            },
            "scale",
            {"factor": 1.5, "axis": None},
        ),
    ],
)
def test_xy_method_dispatch_uses_build_args_kwargs(
    install_op, params, build_args, method, expected_kwargs
):
    """C2: XY-method entries dispatch the bound method with build_args kwargs."""
    install_op(
        "op",
        {
            "source": "xy.method",
            "method": method,
            "build_args": build_args,
            "function_path": f"floodlight.core.xy.XY.{method}",
        },
    )
    xy = _RecordingXY()
    apply_xy_op(xy, "op", params)
    assert xy.calls[method] == expected_kwargs


def test_xy_method_build_args_tolerates_none_params(install_op):
    """C3: build_args runs against an empty dict when params is None."""
    seen = {}

    def _build(p):
        seen["received"] = p
        return {"alpha": 0.0}

    install_op(
        "rotate",
        {
            "source": "xy.method",
            "method": "rotate",
            "build_args": _build,
            "function_path": "floodlight.core.xy.XY.rotate",
        },
    )
    apply_xy_op(_RecordingXY(), "rotate", None)
    assert seen["received"] == {}


# --------------------------------------------------------------------------- #
# C4: module-routed dispatch                                                    #
# --------------------------------------------------------------------------- #


def test_module_routed_imports_and_calls_function(install_op, fake_module):
    """C4: module-routed entries import the module and call fn(xy, **kwargs)."""
    recorder = fake_module("fake_pkg.filter", "butterworth_lowpass")
    install_op(
        "butterworth_lowpass",
        {"function_path": "fake_pkg.filter.butterworth_lowpass"},
    )
    xy = _RecordingXY()
    apply_xy_op(xy, "butterworth_lowpass", {"order": 3, "Wn": 1.0})
    assert recorder.args == (xy,)
    assert recorder.kwargs == {"order": 3, "Wn": 1.0}


# --------------------------------------------------------------------------- #
# C5 / C6: module-routed param coercion                                         #
# --------------------------------------------------------------------------- #


def test_module_routed_drops_none_and_none_string(install_op, fake_module):
    """C5: the "none" sentinel and Python None are dropped; others pass through."""
    recorder = fake_module("fake_pkg.temporal", "resample")
    install_op("resample", {"function_path": "fake_pkg.temporal.resample"})
    apply_xy_op(
        _RecordingXY(),
        "resample",
        {
            "target_framerate": 25,  # kept
            "interp_method": "none",  # string sentinel -> dropped
            "max_gap": None,  # Python None -> dropped
        },
    )
    assert recorder.kwargs == {"target_framerate": 25}


def test_module_routed_blank_ndarray_param_dropped(install_op, fake_module):
    """C6: a blank ndarray-typed param is coerced to None and dropped.

    Mirrors ``assign_roles.reference`` (type ``ndarray``): the default widget
    state is an empty string, which the dispatcher must drop so the upstream
    default applies. A non-ndarray param left blank is preserved.
    """
    recorder = fake_module("fake_pkg.permutation", "assign_roles")
    install_op(
        "assign_roles",
        {
            "function_path": "fake_pkg.permutation.assign_roles",
            "params": {
                "reference": {"type": "ndarray"},
                "note": {"type": "str"},
            },
        },
    )
    apply_xy_op(
        _RecordingXY(),
        "assign_roles",
        {"n_iter": 2, "reference": "   ", "note": "  "},
    )
    assert "reference" not in recorder.kwargs
    assert recorder.kwargs == {"n_iter": 2, "note": "  "}


# --------------------------------------------------------------------------- #
# C7: return result or input xy                                                 #
# --------------------------------------------------------------------------- #


def test_xy_method_in_place_returns_input_xy(install_op):
    """C7 (method branch): a None-returning in-place method yields the input xy."""
    install_op(
        "translate",
        {
            "source": "xy.method",
            "method": "translate",
            "build_args": lambda p: {"shift": (0.0, 0.0)},
            "function_path": "floodlight.core.xy.XY.translate",
        },
    )
    xy = _RecordingXY(returns={"translate": None})
    assert apply_xy_op(xy, "translate", {}) is xy


def test_xy_method_non_inplace_returns_result(install_op):
    """C7 (method branch): a method returning a new XY yields that result.

    Mirrors ``slice`` (``in_place == False``), which returns a fresh XY.
    """
    new_xy = object()
    install_op(
        "slice",
        {
            "source": "xy.method",
            "method": "slice",
            "build_args": lambda p: {"startframe": 0, "endframe": None, "inplace": False},
            "function_path": "floodlight.core.xy.XY.slice",
        },
    )
    xy = _RecordingXY(returns={"slice": new_xy})
    assert apply_xy_op(xy, "slice", {}) is new_xy


@pytest.mark.parametrize("returns, expects_input", [("NEW_XY", False), (None, True)])
def test_module_routed_returns_result_or_input(install_op, fake_module, returns, expects_input):
    """C7 (module branch): non-None result is returned; None yields the input xy."""
    fake_module("fake_pkg.filter", "f", returns=returns)
    install_op("f", {"function_path": "fake_pkg.filter.f"})
    xy = _RecordingXY()
    result = apply_xy_op(xy, "f", {})
    if expects_input:
        assert result is xy
    else:
        assert result == "NEW_XY"


# --------------------------------------------------------------------------- #
# C8: defensive guard                                                           #
# --------------------------------------------------------------------------- #


def test_descriptor_without_dispatch_keys_raises_keyerror(install_op):
    """C8: a descriptor with neither source nor function_path raises KeyError.

    Defensive guard. No real TRANSFORM_REGISTRY entry reaches this path: every
    descriptor carries ``function_path`` (and XY-method entries also carry
    ``source``). Guarded so a malformed future descriptor fails loudly.
    """
    install_op("broken", {"category": "spatial"})
    with pytest.raises(KeyError):
        apply_xy_op(_RecordingXY(), "broken", {})
