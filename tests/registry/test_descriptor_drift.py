"""Drift guard: registry descriptor params vs upstream floodlight signatures.

The GUI generates its UI from descriptor dicts in ``registry/`` (MODEL,
TRANSFORM, METRICS). Every tuneable param a descriptor declares is eventually
handed to an upstream floodlight callable as a keyword argument. If floodlight
renames or drops a parameter on a version bump, a descriptor that still names
the old parameter would generate a widget whose value the upstream call rejects
at runtime. This suite guards against exactly that regression: it introspects
the real upstream ``inspect.signature`` and asserts that every effective kwarg
a descriptor would pass is a real parameter of the callable named by its
``class_path`` / ``function_path``.

"Effective kwarg" mirrors what the engine executors actually dispatch, so the
small documented GUI-shorthand remaps are honored rather than flagged:

* XY-method transforms (``source: "xy.method"``) build their upstream kwargs
  through ``desc["build_args"]`` (e.g. ``translate``'s ``dx_meters`` /
  ``dy_meters`` widgets collapse to the upstream ``shift`` 2-vector). The drift
  check routes through ``build_args`` so it tests the shipped remap, not the
  widget names. See ``engine/apply_transforms.apply_xy_op``.
* Module-routed transforms and metric functions pass their descriptor param
  names through verbatim, so the param names ARE the upstream kwargs. The XY
  input slot itself (``xy`` / ``positions`` / ``obj``) is passed positionally
  and is never a tuneable param, so it is not in scope here.
* Model init_params route to ``__init__``; model fit_params route to ``fit``,
  skipping XY-typed fit params (those are positional, never kwargs) -- matching
  ``engine/fit_model.fit_model``.

A callable whose upstream signature cannot be resolved is SKIPPED (via
``pytest.skip``), never failed: drift can only be judged against a signature
that exists. A param covered by an upstream ``**kwargs`` catch-all is accepted,
because the upstream genuinely accepts it.

Behavioral contracts guarded here
---------------------------------
C1  Every ``class_path`` (MODEL_REGISTRY) and ``function_path`` (TRANSFORM_REGISTRY,
    METRICS_REGISTRY) resolves to a real importable callable.
C2  Every effective upstream kwarg a registry descriptor declares is a real
    parameter of its upstream callable (or absorbed by an upstream ``**kwargs``).
    One parametrized test over every (registry, key, param-slot) across all
    three registries; the XY-method remap is honored via ``build_args``.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

from floodlight_gui.registry.metrics import METRICS_REGISTRY
from floodlight_gui.registry.models import MODEL_REGISTRY
from floodlight_gui.registry.transforms import TRANSFORM_REGISTRY

# --------------------------------------------------------------------------- #
# Resolution helpers                                                            #
# --------------------------------------------------------------------------- #


def _resolve(dotted: str):
    """Import and return the object named by a dotted path, or None.

    Walks the dotted path module-first; if the module import fails, retries
    treating the trailing segments as attribute access on a parent (so
    XY-method paths like ``floodlight.core.xy.XY.translate`` resolve through
    the ``XY`` class). Returns None when nothing resolves, signalling the
    caller to skip rather than fail.
    """
    module_path, _, attr = dotted.rpartition(".")
    try:
        mod = importlib.import_module(module_path)
    except ModuleNotFoundError:
        # The path may dot through a class (e.g. ...xy.XY.translate): resolve
        # the parent recursively, then take the final attribute off it.
        parent = _resolve(module_path)
        if parent is None:
            return None
        return getattr(parent, attr, None)
    return getattr(mod, attr, None)


def _upstream_params(callable_obj):
    """Return (param_names, accepts_var_keyword) for a callable.

    ``param_names`` excludes ``self`` (introspecting an unbound ``__init__`` /
    ``fit`` includes it). ``accepts_var_keyword`` is True when the signature has
    a ``**kwargs`` catch-all, which absorbs any keyword the descriptor passes.
    """
    sig = inspect.signature(callable_obj)
    names = []
    var_keyword = False
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            var_keyword = True
            continue
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        if p.name == "self":
            continue
        names.append(p.name)
    return set(names), var_keyword


# --------------------------------------------------------------------------- #
# Param-slot enumeration                                                        #
# --------------------------------------------------------------------------- #
#
# A "slot" is one (registry, key, dotted_path, upstream_kwarg) tuple: a single
# keyword the descriptor would pass to a single upstream callable. Each slot is
# one parametrize case for the C2 drift test. Building slots up front and giving
# each a readable id keeps a drift failure pinpointable to the exact param.


def _xy_method_effective_kwargs(desc):
    """Return the upstream kwarg names an XY-method entry passes via build_args.

    ``build_args`` is the documented remap (e.g. ``dx_meters``/``dy_meters`` ->
    ``shift``). It is evaluated on the descriptor's declared default param
    values so its output keys reflect what the executor dispatches. ``inplace``
    is a build_args-supplied positional-or-keyword on the XY method itself, so
    it is in scope like any other resolved kwarg.
    """
    defaults = {pn: pdesc.get("default") for pn, pdesc in desc.get("params", {}).items()}
    built = desc["build_args"](defaults)
    return set(built.keys())


def _transform_slots():
    """Yield (key, dotted_path, kwarg) for every TRANSFORM_REGISTRY param slot."""
    for key, desc in TRANSFORM_REGISTRY.items():
        dotted = desc["function_path"]
        if desc.get("source") == "xy.method":
            kwargs = _xy_method_effective_kwargs(desc)
        else:
            kwargs = set(desc.get("params", {}).keys())
        for kw in sorted(kwargs):
            yield ("TRANSFORM", key, dotted, kw)


def _metric_slots():
    """Yield (key, dotted_path, kwarg) for every METRICS_REGISTRY param slot."""
    for key, desc in METRICS_REGISTRY.items():
        dotted = desc["function_path"]
        for kw in sorted(desc.get("params", {}).keys()):
            yield ("METRIC", key, dotted, kw)


def _model_slots():
    """Yield (key, dotted_path, routing, kwarg) for every MODEL_REGISTRY param slot.

    ``routing`` is ``"init"`` (route to ``__init__``) or ``"fit"`` (route to
    ``fit``). XY-typed fit params are excluded: ``fit_model`` passes XY
    positionally, never as a kwarg.
    """
    for key, desc in MODEL_REGISTRY.items():
        dotted = desc["class_path"]
        for kw in sorted(desc.get("init_params", {}).keys()):
            yield ("MODEL", key, dotted, "init", kw)
        for kw, pdesc in desc.get("fit_params", {}).items():
            if pdesc.get("type") == "XY":
                continue
            yield ("MODEL", key, dotted, "fit", kw)


def _all_slots():
    """Build the full list of param slots, each normalized to a 5-tuple + id.

    Returns a list of ``((registry, key, dotted, routing, kwarg), id_str)`` where
    ``routing`` is ``"call"`` for non-model callables (the dotted path resolves
    directly to the callable) or ``"init"`` / ``"fit"`` for models.
    """
    slots = []
    for registry, key, dotted, kw in _transform_slots():
        slots.append(((registry, key, dotted, "call", kw), f"{registry}:{key}:{kw}"))
    for registry, key, dotted, kw in _metric_slots():
        slots.append(((registry, key, dotted, "call", kw), f"{registry}:{key}:{kw}"))
    for registry, key, dotted, routing, kw in _model_slots():
        slots.append(((registry, key, dotted, routing, kw), f"{registry}:{key}:{routing}:{kw}"))
    return slots


_SLOTS = _all_slots()
_SLOT_CASES = [pytest.param(slot, id=sid) for slot, sid in _SLOTS]


def _resolve_callable_for_slot(dotted, routing):
    """Resolve the upstream callable a slot's kwarg is passed to, or None.

    For ``"call"`` slots the dotted path is the callable itself. For ``"init"`` /
    ``"fit"`` model slots the dotted path is the class; the kwarg routes to the
    named bound method.
    """
    target = _resolve(dotted)
    if target is None:
        return None
    if routing == "call":
        return target
    if routing == "init":
        return target.__init__
    if routing == "fit":
        return getattr(target, "fit", None)
    return None


# --------------------------------------------------------------------------- #
# C1 -- every class_path / function_path resolves to a real callable            #
# --------------------------------------------------------------------------- #


def _callable_paths():
    """Yield (registry, key, dotted_path) for every descriptor's upstream path."""
    for key, desc in MODEL_REGISTRY.items():
        yield ("MODEL", key, desc["class_path"])
    for key, desc in TRANSFORM_REGISTRY.items():
        yield ("TRANSFORM", key, desc["function_path"])
    for key, desc in METRICS_REGISTRY.items():
        yield ("METRIC", key, desc["function_path"])


@pytest.mark.parametrize(
    "registry, key, dotted",
    list(_callable_paths()),
    ids=[f"{r}:{k}" for r, k, _ in _callable_paths()],
)
def test_descriptor_path_resolves_to_callable(registry, key, dotted):
    """C1: each class_path/function_path imports to a real callable."""
    target = _resolve(dotted)
    assert target is not None, f"{registry} {key!r}: {dotted} did not resolve"
    assert callable(target), f"{registry} {key!r}: {dotted} resolved to a non-callable"


# --------------------------------------------------------------------------- #
# C2 -- every declared param is a real upstream kwarg (honoring the remap)      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("slot", _SLOT_CASES)
def test_descriptor_param_is_real_upstream_kwarg(slot):
    """C2: each effective upstream kwarg a descriptor passes is a real param.

    Skips (does not fail) any slot whose upstream callable cannot be resolved,
    since drift is only meaningful against an existing signature. Accepts a
    kwarg absorbed by an upstream ``**kwargs`` catch-all.
    """
    registry, key, dotted, routing, kwarg = slot
    callable_obj = _resolve_callable_for_slot(dotted, routing)
    if callable_obj is None or not callable(callable_obj):
        pytest.skip(
            f"{registry} {key!r}: upstream {dotted} ({routing}) unresolved -- cannot judge drift"
        )

    upstream_names, accepts_var_keyword = _upstream_params(callable_obj)
    if accepts_var_keyword:
        return  # **kwargs absorbs any keyword the descriptor passes.
    assert kwarg in upstream_names, (
        f"{registry} {key!r} declares param {kwarg!r} that is not a parameter of "
        f"upstream {dotted} ({routing}); upstream accepts {sorted(upstream_names)}"
    )
