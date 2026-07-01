"""Behavioral contracts for ``floodlight_gui.registry._validators``.

These validators are the shape gate for descriptor dicts: each
``_validate_<X>_descriptor(key, descriptor)`` returns ``None`` on a conforming
descriptor and raises ``ValueError`` on a shape violation. ``validate_all()``
(exported from ``registry/__init__``) walks every shipped descriptor and must
stay silent. The tests assert only the observable accept/reject decision, never
the internal key sets or the message text.

Behavioral contracts guarded here
---------------------------------
validate_all
  C1  Returns None when run over the real, shipped registries (every in-tree
      descriptor conforms to its schema). This is the CI/plugin gate.

per-registry validators (IO / MODEL / TRANSFORM / METRIC)
  C2  A minimal conforming descriptor is accepted (returns None). This is the
      happy path the ``register_*`` helpers depend on.
  C3  A descriptor missing a required key is rejected with ValueError.
  C4  A descriptor carrying an unknown top-level key is rejected with
      ValueError.

TRANSFORM dispatch-route rule (its distinguishing contract)
  C5  An entry must declare either ``function_path`` or
      (``source == "xy.method"`` and ``method``). With neither it is rejected;
      with either alone it is accepted.
  C6  When the optional XY-method fields are present they are type-checked:
      a wrong-typed ``source`` / ``method`` / ``in_place`` / ``build_args`` is
      rejected.
"""

from __future__ import annotations

import pytest

from floodlight_gui.registry import validate_all
from floodlight_gui.registry._validators import (
    _validate_io_descriptor,
    _validate_metric_descriptor,
    _validate_model_descriptor,
    _validate_transform_descriptor,
)

# --------------------------------------------------------------------------- #
# Minimal conforming descriptors (one per registry)                            #
# Each is the smallest dict the matching validator accepts; tests mutate a     #
# copy to provoke the missing-key / unknown-key / routing rejections.          #
# --------------------------------------------------------------------------- #


def _valid_io() -> dict:
    """Return a minimal descriptor that ``_validate_io_descriptor`` accepts."""
    return {
        "module": "pkg.io",
        "display_name": "Demo",
        "sport": "football",
        "file_inputs": {},
        "loader_functions": {},
        "outputs": [],
    }


def _valid_model() -> dict:
    """Return a minimal descriptor that ``_validate_model_descriptor`` accepts."""
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
    """Return a minimal module-routed descriptor the TRANSFORM validator accepts."""
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
    """Return a minimal descriptor that ``_validate_metric_descriptor`` accepts."""
    return {
        "function_path": "pkg.metric.demo",
        "category": "Complexity",
        "display_name": "Demo",
        "description": "demo",
        "inputs": {},
        "params": {},
        "returns": "PlayerProperty",
    }


# (validator, valid-factory, a-required-key) tuples drive the shared
# accept/missing-key/unknown-key contracts across all four registries.
_VALIDATORS = [
    pytest.param(_validate_io_descriptor, _valid_io, "module", id="io"),
    pytest.param(_validate_model_descriptor, _valid_model, "class_path", id="model"),
    pytest.param(_validate_transform_descriptor, _valid_transform, "category", id="transform"),
    pytest.param(_validate_metric_descriptor, _valid_metric, "function_path", id="metric"),
]


# --------------------------------------------------------------------------- #
# validate_all                                                                  #
# --------------------------------------------------------------------------- #


def test_validate_all_passes_on_shipped_registries():
    """C1: validate_all returns None over every shipped descriptor."""
    assert validate_all() is None


# --------------------------------------------------------------------------- #
# Shared validator contracts                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("validator, valid, _required", _VALIDATORS)
def test_validator_accepts_minimal_descriptor(validator, valid, _required):
    """C2: a minimal conforming descriptor is accepted (returns None)."""
    assert validator("demo", valid()) is None


@pytest.mark.parametrize("validator, valid, required", _VALIDATORS)
def test_validator_rejects_missing_required_key(validator, valid, required):
    """C3: dropping a required key is rejected with ValueError."""
    descriptor = valid()
    del descriptor[required]
    with pytest.raises(ValueError):
        validator("demo", descriptor)


@pytest.mark.parametrize("validator, valid, _required", _VALIDATORS)
def test_validator_rejects_unknown_key(validator, valid, _required):
    """C4: an unknown top-level key is rejected with ValueError."""
    descriptor = valid()
    descriptor["surprise"] = 1
    with pytest.raises(ValueError):
        validator("demo", descriptor)


# --------------------------------------------------------------------------- #
# TRANSFORM dispatch-route rule                                                  #
# --------------------------------------------------------------------------- #


def test_transform_requires_a_dispatch_route():
    """C5: neither function_path nor source/method present is rejected."""
    descriptor = _valid_transform()
    del descriptor["function_path"]
    with pytest.raises(ValueError):
        _validate_transform_descriptor("demo", descriptor)


@pytest.mark.parametrize(
    "route",
    [
        pytest.param({"function_path": "pkg.demo"}, id="function_path-only"),
        pytest.param({"source": "xy.method", "method": "slice"}, id="xy-method-only"),
    ],
)
def test_transform_accepts_either_dispatch_route(route):
    """C5: either function_path alone or source=='xy.method'+method alone is accepted."""
    descriptor = _valid_transform()
    del descriptor["function_path"]
    descriptor.update(route)
    assert _validate_transform_descriptor("demo", descriptor) is None


@pytest.mark.parametrize(
    "field, bad_value",
    [
        pytest.param("source", 1, id="source-not-str"),
        pytest.param("method", 1, id="method-not-str"),
        pytest.param("in_place", "yes", id="in_place-not-bool"),
        pytest.param("build_args", "nope", id="build_args-not-callable"),
    ],
)
def test_transform_typechecks_optional_xy_method_fields(field, bad_value):
    """C6: a present-but-wrong-typed XY-method field is rejected.

    The descriptor keeps a valid ``function_path`` route so the rejection is
    attributable to the bad optional field, not to a missing dispatch route.
    """
    descriptor = _valid_transform()
    descriptor[field] = bad_value
    with pytest.raises(ValueError):
        _validate_transform_descriptor("demo", descriptor)
