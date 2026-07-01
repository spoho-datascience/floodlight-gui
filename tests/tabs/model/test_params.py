"""Behavioral contracts for ``floodlight_gui.tabs.model.params``.

This module reads the rendered parameter widgets back into the ``ui_params`` dict
handed verbatim to the fit engine. Two contracts here are silent-corrupting: the
collected dict must EXCLUDE engine-supplied parameter types (XY / Pitch) so a
GUI-typed bogus value is never fed where the engine supplies the real object, and
each collected value must coerce to the descriptor's declared Python type. A
miscoercion or a leaked engine param flows straight into ``fit_model`` with no
visible signal. The DPG toolkit is the seam; the widget builder / empty-state
view (visible build chrome) are out of scope here.

Behavioral contracts guarded here
---------------------------------
collect_ui_params
  C5  Returns a value for every non-engine-supplied param keyed by name and
      excludes engine-supplied (XY / Pitch) params from the executor kwargs.

_read_param (value coercion)
  C6  Type coercions feeding the executor: tuple reads lo/hi sub-tags; enum
      "None" maps to Python None; list[int] splits CSV/whitespace to ints; other
      types pass the raw DPG value through; a missing widget tag falls back to
      the descriptor default.
"""

from __future__ import annotations

import pytest

import floodlight_gui.tabs.model.params as params_mod
from floodlight_gui.tabs.model import params
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def stub(monkeypatch):
    """Install a fake DPG into the params module and return it."""
    s = make_dpg_stub(existing_items={params.PARAMS_CONTAINER})
    monkeypatch.setattr(params_mod, "dpg", s)
    return s


def _install_registry(monkeypatch, desc):
    """Patch MODEL_REGISTRY in the params module to a single ``{"m": desc}`` entry."""
    monkeypatch.setattr(params_mod, "MODEL_REGISTRY", {"m": desc})


# --------------------------------------------------------------------------- #
# collect_ui_params (executor kwargs + engine-type exclusion)                  #
# --------------------------------------------------------------------------- #


def test_collect_ui_params_excludes_engine_types_and_keys_by_name(stub, monkeypatch):
    """C5: collect_ui_params returns each non-engine param by name and drops XY/Pitch.

    The engine supplies ``XY`` / ``Pitch`` itself; leaking a GUI-collected value
    for one into the kwargs dict would silently override the real object fed to
    ``fit_model``. The two engine-typed params here must be absent from the result.
    """
    desc = {
        "init_params": {
            "pitch": {"type": "Pitch"},  # excluded
            "mesh": {"type": "enum", "default": "square"},
        },
        "fit_params": {
            "xy2": {"type": "XY"},  # excluded
            "difference": {"type": "enum", "default": "central"},
        },
    }
    _install_registry(monkeypatch, desc)
    # No widgets registered -> each present param falls back to its default.
    collected = params.collect_ui_params("m")
    assert set(collected) == {"mesh", "difference"}
    assert collected == {"mesh": "square", "difference": "central"}


# --------------------------------------------------------------------------- #
# _read_param coercion (value feeding the executor)                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tag, ptype, pdesc, seeded, expected",
    [
        # tuple reads the lo/hi sub-tags
        ("t", "tuple[float, float]", {}, {"t__lo": 1.5, "t__hi": 3.5}, (1.5, 3.5)),
        # enum sentinel "None" maps to Python None
        ("t", "enum", {}, {"t": "None"}, None),
        # enum non-None passes through
        ("t", "enum", {}, {"t": "x"}, "x"),
        # list[int] splits CSV / whitespace and casts to int
        ("t", "list[int]", {}, {"t": "1, 2  3"}, [1, 2, 3]),
        # any other type passes the raw widget value through
        ("t", "float", {}, {"t": 4.2}, 4.2),
        # missing widget tag falls back to the descriptor default
        ("absent", "int", {"default": 99}, {}, 99),
    ],
    ids=["tuple", "enum-none", "enum-passthrough", "list-int", "scalar", "missing-default"],
)
def test_read_param_coercions(stub, tag, ptype, pdesc, seeded, expected):
    """C6: _read_param coerces each descriptor type to its Python value, default on absence."""
    for k, v in seeded.items():
        stub.existing_items.add(k)
        stub.values[k] = v
    assert params._read_param(tag, ptype, pdesc) == expected
