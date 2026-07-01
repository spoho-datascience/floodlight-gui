"""Behavioral contracts for the public ``register_*`` helpers.

``register_io_provider`` / ``register_model`` / ``register_transform`` /
``register_metric`` are the plugin-extension seam. Each validates the
descriptor, rejects a duplicate key, inserts the entry into its registry, and
emits the matching ``*_REGISTRY_CHANGED`` event with a ``{key, descriptor}``
payload. The four helpers are structurally identical, so their shared contracts
are parametrized over all four; the one cross-cutting ordering guarantee
(insert-before-emit) is a single test on one representative helper.

The real registries and the real singleton EventBus are used. A fixture removes
the throwaway key after each test so the shipped registries are left untouched;
the root conftest's autouse subscriber reset isolates the bus per test.

Behavioral contracts guarded here
---------------------------------
R1  A valid descriptor is inserted into the registry under its key.
R2  Registration emits the matching ``*_REGISTRY_CHANGED`` event once, carrying
    ``{key, descriptor}`` as payload.
R3  Registering a key that already exists raises ValueError and does not emit a
    second event.
R4  An invalid descriptor is rejected (delegated to the validator) and nothing
    is inserted or emitted.
R5  The insert lands before the emit: a subscriber that reads the registry
    inside its callback already sees the new entry.
"""

from __future__ import annotations

import pytest

from floodlight_gui.core.event_bus import Events
from floodlight_gui.core.event_bus import bus as event_bus
from floodlight_gui.registry import (
    IO_REGISTRY,
    METRICS_REGISTRY,
    MODEL_REGISTRY,
    TRANSFORM_REGISTRY,
    register_io_provider,
    register_metric,
    register_model,
    register_transform,
)

# --------------------------------------------------------------------------- #
# Minimal conforming descriptors per registry                                  #
# --------------------------------------------------------------------------- #


def _valid_io() -> dict:
    """Return a minimal descriptor accepted by the IO validator."""
    return {
        "module": "pkg.io",
        "display_name": "Demo",
        "sport": "football",
        "file_inputs": {},
        "loader_functions": {},
        "outputs": [],
    }


def _valid_model() -> dict:
    """Return a minimal descriptor accepted by the MODEL validator."""
    return {
        "class_path": "pkg.Model",
        "category": "Kinematics",
        "display_name": "Demo",
        "description": "demo",
        "inputs": {},
        "init_params": {},
        "fit_params": {},
        "outputs": {},
    }


def _valid_transform() -> dict:
    """Return a minimal module-routed descriptor accepted by the TRANSFORM validator."""
    return {
        "category": "Filter",
        "display_name": "Demo",
        "description": "demo",
        "inputs": {},
        "params": {},
        "returns": "XY",
        "function_path": "pkg.transform.demo",
    }


def _valid_metric() -> dict:
    """Return a minimal descriptor accepted by the METRIC validator."""
    return {
        "function_path": "pkg.metric.demo",
        "category": "Complexity",
        "display_name": "Demo",
        "description": "demo",
        "inputs": {},
        "params": {},
        "returns": "PlayerProperty",
    }


# Each case binds a helper to its registry, its change-event, and a factory for
# a fresh valid descriptor. The shared contracts parametrize over all four.
class _Case:
    """One register-helper target: its function, registry, event, and a descriptor factory."""

    def __init__(self, name, register, registry, event, factory):
        self.name = name
        self.register = register
        self.registry = registry
        self.event = event
        self.factory = factory


_CASES = [
    _Case("io", register_io_provider, IO_REGISTRY, Events.IO_REGISTRY_CHANGED, _valid_io),
    _Case("model", register_model, MODEL_REGISTRY, Events.MODEL_REGISTRY_CHANGED, _valid_model),
    _Case(
        "transform",
        register_transform,
        TRANSFORM_REGISTRY,
        Events.TRANSFORM_REGISTRY_CHANGED,
        _valid_transform,
    ),
    _Case(
        "metric",
        register_metric,
        METRICS_REGISTRY,
        Events.METRICS_REGISTRY_CHANGED,
        _valid_metric,
    ),
]
_CASE_PARAMS = [pytest.param(c, id=c.name) for c in _CASES]

_THROWAWAY_KEY = "__throwaway_demo__"


@pytest.fixture
def clean_key():
    """Yield the throwaway registry key and delete it from every registry on teardown.

    Registering into the real registries would otherwise leak the demo entry
    into the shipped state and break the next test's ``validate_all`` baseline.
    Teardown removes the key from all four registries regardless of which helper
    inserted it.
    """
    yield _THROWAWAY_KEY
    for registry in (IO_REGISTRY, MODEL_REGISTRY, TRANSFORM_REGISTRY, METRICS_REGISTRY):
        registry.pop(_THROWAWAY_KEY, None)


@pytest.fixture
def record_event():
    """Subscribe a recorder to one event and yield its captured-payload list.

    Returns a callable ``subscribe_to(event)`` that registers a recorder on the
    singleton bus and returns the list it appends ``(kwargs)`` to. The root
    conftest snapshots and restores the bus subscribers per test, so no explicit
    unsubscribe is required.
    """

    def _subscribe_to(event):
        captured: list[dict] = []
        event_bus.subscribe(event, lambda **kw: captured.append(kw))
        return captured

    return _subscribe_to


# --------------------------------------------------------------------------- #
# R1: insertion                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", _CASE_PARAMS)
def test_register_inserts_descriptor(case, clean_key):
    """R1: a valid descriptor lands in the registry under its key."""
    descriptor = case.factory()
    case.register(clean_key, descriptor)
    assert case.registry[clean_key] is descriptor


# --------------------------------------------------------------------------- #
# R2: emission                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", _CASE_PARAMS)
def test_register_emits_change_event_with_payload(case, clean_key, record_event):
    """R2: registration emits the change event once with a {key, descriptor} payload."""
    captured = record_event(case.event)
    descriptor = case.factory()
    case.register(clean_key, descriptor)
    assert captured == [{"key": clean_key, "descriptor": descriptor}]


# --------------------------------------------------------------------------- #
# R3: duplicate rejection                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", _CASE_PARAMS)
def test_register_rejects_duplicate_key(case, clean_key, record_event):
    """R3: a duplicate key raises ValueError and emits no second event."""
    case.register(clean_key, case.factory())
    captured = record_event(case.event)
    with pytest.raises(ValueError):
        case.register(clean_key, case.factory())
    assert captured == []


# --------------------------------------------------------------------------- #
# R4: invalid-descriptor rejection                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", _CASE_PARAMS)
def test_register_rejects_invalid_descriptor(case, clean_key, record_event):
    """R4: an invalid descriptor raises and nothing is inserted or emitted.

    A bare ``{}`` fails every per-registry validator (all required keys are
    missing), so this exercises the validate-before-mutate guard uniformly.
    """
    captured = record_event(case.event)
    with pytest.raises(ValueError):
        case.register(clean_key, {})
    assert clean_key not in case.registry
    assert captured == []


# --------------------------------------------------------------------------- #
# R5: insert-before-emit ordering (cross-cutting, one representative)          #
# --------------------------------------------------------------------------- #


def test_register_inserts_before_emitting(clean_key):
    """R5: a subscriber reading the registry inside its callback sees the new entry.

    One representative helper exercises the shared mutate-then-emit ordering;
    the four helpers run the same sequence, so a single test covers the
    guarantee.
    """
    seen_inside_callback: list[bool] = []

    def _on_change(key=None, **_):
        seen_inside_callback.append(key in MODEL_REGISTRY)

    event_bus.subscribe(Events.MODEL_REGISTRY_CHANGED, _on_change)
    register_model(clean_key, _valid_model())
    assert seen_inside_callback == [True]
