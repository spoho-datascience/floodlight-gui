"""Behavioral contracts for ``floodlight_gui.core.help.resolve``.

This module resolves a registry descriptor to a ``ParsedHelp`` by importing
the upstream floodlight object named in ``class_path`` / ``function_path``,
reading its docstring, and composing a payload. The seams are the upstream
floodlight callables (reached through real descriptors) and the docstring
parser. Tests drive the resolver with REAL registry descriptors and assert
structure (non-empty body, summary kept out of the body, fallback on a broken
path), never the exact upstream prose. The docstring parser is stubbed only
where its output must be isolated from the resolver's own composition.

Behavioral contracts guarded here
---------------------------------
get_descriptor_help
  C1  A MODELS descriptor resolves the upstream class docstring to a
      non-empty body with ``available=True``; the ``init`` hint falls back to
      the class doc when ``__init__`` carries only inherited boilerplate, and
      the ``fit`` hint targets the fit method's own docstring.
  C2  TRANSFORMS / METRICS / XY_OPS function-based descriptors resolve the
      upstream function docstring to a non-empty body with ``available=True``,
      including a nested ``module.Class.method`` path.
  C3  An IO descriptor composes one ``### {loader}`` section per enabled
      loader into a single body with ``available=True``.
  C4  Disabled IO loaders contribute no section to the composite body.
  C5  A broken class_path / function_path never raises: it returns the canned
      fallback body with ``available=False`` (Tier-4).
  C6  A descriptor missing the path it needs (no class_path for MODELS, no
      function_path for XY_OPS) returns the canned fallback, ``available=False``.
  C7  The descriptor ``description`` is surfaced as ``descriptor_summary`` and
      never leaks into ``body``; the ``display_name`` becomes ``title``.
  C8  Repeat lookups of the same path reuse the cached resolution instead of
      re-importing and re-parsing the upstream object.
"""

from __future__ import annotations

import pytest

import floodlight_gui.core.help.resolve as resolve
from floodlight_gui.core.help.resolve import ParsedHelp, get_descriptor_help
from floodlight_gui.registry.io import IO_REGISTRY
from floodlight_gui.registry.metrics import METRICS_REGISTRY
from floodlight_gui.registry.models import MODEL_REGISTRY
from floodlight_gui.registry.transforms import TRANSFORM_REGISTRY

_CANNED = "Upstream documentation not available."


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    """Reset the lru_cache around the resolver before and after each test.

    The resolver memoizes ``(source_path, container_hint)`` for the whole
    session. Clearing per test keeps cache-hit assertions deterministic and
    stops one test's resolution from satisfying another's import.
    """
    resolve._resolve_and_parse_cached.cache_clear()
    yield
    resolve._resolve_and_parse_cached.cache_clear()


# --------------------------------------------------------------------------- #
# C1: MODELS class / init / fit resolution                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("container_hint", ["", "init", "fit"])
def test_models_resolve_non_empty_body_available(container_hint):
    """C1: a MODELS descriptor resolves to real upstream text on every hint.

    The ``init`` hint exercises the boilerplate fallback (VelocityModel
    inherits ``object.__init__``'s docstring, so the resolver substitutes the
    class doc); all three hints must yield a non-empty body and available=True.
    """
    ph = get_descriptor_help(MODEL_REGISTRY["velocity"], "MODELS", container_hint)
    assert ph.available is True
    assert ph.body and ph.body != _CANNED
    assert ph.source_path == "floodlight.models.kinematics.VelocityModel"


# --------------------------------------------------------------------------- #
# C2: function-based registries (TRANSFORMS / METRICS / XY_OPS)                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "descriptor, registry_name",
    [
        (TRANSFORM_REGISTRY["butterworth_lowpass"], "TRANSFORMS"),
        (METRICS_REGISTRY["approx_entropy"], "METRICS"),
        # XY-method entry: nested ``module.Class.method`` import path.
        (TRANSFORM_REGISTRY["translate"], "XY_OPS"),
    ],
)
def test_function_registries_resolve_non_empty_body(descriptor, registry_name):
    """C2: function-based descriptors resolve real upstream text, available=True."""
    ph = get_descriptor_help(descriptor, registry_name)
    assert ph.available is True
    assert ph.body and ph.body != _CANNED
    assert ph.source_path == descriptor["function_path"]


# --------------------------------------------------------------------------- #
# C3 / C4: IO composite                                                         #
# --------------------------------------------------------------------------- #


def test_io_descriptor_composes_loader_sections():
    """C3: an IO descriptor merges one ``### {loader}`` section per loader."""
    descriptor = IO_REGISTRY["dfl"]
    ph = get_descriptor_help(descriptor, "IO")
    assert ph.available is True
    enabled = [
        key for key, meta in descriptor["loader_functions"].items() if not meta.get("disabled")
    ]
    for loader_key in enabled:
        assert f"### {loader_key}" in ph.body


def test_io_skips_disabled_loaders():
    """C4: a disabled IO loader contributes no section to the composite body."""
    descriptor = IO_REGISTRY["statsperform"]
    disabled = [key for key, meta in descriptor["loader_functions"].items() if meta.get("disabled")]
    assert disabled, "fixture expects statsperform to declare a disabled loader"
    ph = get_descriptor_help(descriptor, "IO")
    for loader_key in disabled:
        assert f"### {loader_key}" not in ph.body


# --------------------------------------------------------------------------- #
# C5: Tier-4 fallback on a broken path                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "descriptor, registry_name",
    [
        (
            {
                "display_name": "Renamed Model",
                "description": "summary",
                "class_path": "floodlight.models.kinematics.GoneModel",
            },
            "MODELS",
        ),
        (
            {
                "display_name": "Renamed Transform",
                "description": "summary",
                "function_path": "floodlight.transforms.filter.gone_function",
            },
            "TRANSFORMS",
        ),
    ],
)
def test_broken_path_falls_back_without_raising(descriptor, registry_name):
    """C5: a renamed-upstream path returns the canned fallback, never raises."""
    ph = get_descriptor_help(descriptor, registry_name)
    assert ph.available is False
    assert ph.body == _CANNED


# --------------------------------------------------------------------------- #
# C6: descriptor missing the path it needs                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "descriptor, registry_name",
    [
        ({"display_name": "No Path Model", "description": "s"}, "MODELS"),
        ({"display_name": "No Path Op", "description": "s"}, "XY_OPS"),
    ],
)
def test_missing_path_returns_canned_fallback(descriptor, registry_name):
    """C6: a descriptor without its required path yields the canned fallback."""
    ph = get_descriptor_help(descriptor, registry_name)
    assert ph.available is False
    assert ph.body == _CANNED


# --------------------------------------------------------------------------- #
# C7: summary stays out of the body; title comes from display_name              #
# --------------------------------------------------------------------------- #


def test_descriptor_summary_and_title_are_separated_from_body():
    """C7: ``description`` becomes descriptor_summary (out of body); name -> title."""
    descriptor = MODEL_REGISTRY["velocity"]
    ph = get_descriptor_help(descriptor, "MODELS")
    assert ph.descriptor_summary == descriptor["description"]
    assert ph.title == descriptor["display_name"]
    assert descriptor["description"] not in ph.body


# --------------------------------------------------------------------------- #
# C8: caching                                                                   #
# --------------------------------------------------------------------------- #


def test_repeat_lookup_reuses_cache(monkeypatch):
    """C8: a second lookup of the same path does not re-parse the upstream doc.

    The docstring parser is the per-resolution work the cache exists to skip.
    Counting its calls isolates the cache decision from import side effects:
    two identical lookups must parse exactly once.
    """
    calls = {"n": 0}
    real_parser = resolve.parse_full_docstring

    def _counting_parser(text):
        calls["n"] += 1
        return real_parser(text)

    monkeypatch.setattr(resolve, "parse_full_docstring", _counting_parser)

    descriptor = MODEL_REGISTRY["velocity"]
    first = get_descriptor_help(descriptor, "MODELS")
    second = get_descriptor_help(descriptor, "MODELS")

    assert calls["n"] == 1
    assert isinstance(first, ParsedHelp)
    assert second.body == first.body
