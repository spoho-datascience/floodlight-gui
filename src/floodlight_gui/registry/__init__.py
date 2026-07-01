"""Feature registry: declarative descriptors for all floodlight features the GUI exposes.

Each registry is a dict keyed by a short identifier, with values describing the
feature's import path, parameters, inputs, outputs, and UI metadata. Tabs read
these descriptors to generate their UI; adding a descriptor surfaces a new feature
automatically without touching tab code.

This module is the public plugin-extension seam. Third-party plugins add features
by calling the ``register_*`` helpers exported here, then optionally calling
``validate_all()`` to verify descriptor shape before use.

Invariants
----------
- DPG-free: this module and everything it imports must load without ``dearpygui``.
  Verified by the test suite (importing ``floodlight_gui.registry`` must not pull
  in DPG transitively).
- No eager validation on import: module load is silent. Call ``validate_all()``
  explicitly (the CI gate does this via ``tests/test_registry_validate_all.py``).

Extension example
-----------------
>>> from floodlight_gui.registry import register_model
>>> register_model("my_model", {
...     "class_path": "my_package.MyModel",
...     "display_name": "My Model",
...     "category": "Kinematics",
...     "init_params": {},
...     "fit_params": {},
...     "outputs": {},
... })

The key ``"my_model"`` appears in ``MODEL_REGISTRY`` immediately and the model
tab picks it up on next render.
"""

from __future__ import annotations

from floodlight_gui.registry.io import IO_REGISTRY, register_io_provider
from floodlight_gui.registry.metrics import METRICS_REGISTRY, register_metric
from floodlight_gui.registry.models import MODEL_REGISTRY, register_model
from floodlight_gui.registry.transforms import TRANSFORM_REGISTRY, register_transform


def validate_all() -> None:
    """Walk all four registries and raise on the first descriptor shape violation.

    This is the explicit CI/plugin validation gate. Module import is intentionally
    silent; call this function to confirm all registered descriptors conform to
    their per-registry schema.

    Validators are lazy-imported inside the body to avoid a potential cyclic
    dependency between ``registry/_validators.py`` and the registry source modules
    that import the ``register_*`` helpers.

    Raises
    ------
    ValueError
        On the first shape violation encountered, with the locked message format
        ``f"{REGISTRY_NAME} descriptor for '{key}': {reason}"``.
    """
    from floodlight_gui.registry._validators import (
        _validate_io_descriptor,
        _validate_metric_descriptor,
        _validate_model_descriptor,
        _validate_transform_descriptor,
    )

    for k, d in IO_REGISTRY.items():
        _validate_io_descriptor(k, d)
    for k, d in MODEL_REGISTRY.items():
        _validate_model_descriptor(k, d)
    for k, d in TRANSFORM_REGISTRY.items():
        _validate_transform_descriptor(k, d)
    for k, d in METRICS_REGISTRY.items():
        _validate_metric_descriptor(k, d)


__all__ = [
    # Registries (4): do not add or remove without updating the public API contract
    "MODEL_REGISTRY",
    "IO_REGISTRY",
    "TRANSFORM_REGISTRY",
    "METRICS_REGISTRY",
    # Registration helpers (4) -- plugin extension seam
    "register_model",
    "register_io_provider",
    "register_transform",
    "register_metric",
    # Validation
    "validate_all",
]
