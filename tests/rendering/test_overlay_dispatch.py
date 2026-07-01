"""Behavioral contracts for the overlay-adapter registry and dispatch.

The dispatch decision under test is purely a lookup: a fitted model carries a
``MODEL_REGISTRY[model_key]["overlay_adapter"]`` string (or ``None``), and the
visualization tab resolves that string against ``OVERLAY_ADAPTER_REGISTRY`` to
pick the adapter class. This suite asserts the resolution outcome (which class,
or no overlay) and the registry's shape (each entry is constructible and
exposes the lifecycle/dispatch interface the tab relies on). The adapters'
own draw code (``init`` / ``update_frame`` texture and polygon math) is carved
out: it is exercised by the adapters' own draw tests, not here.

Both adapter modules import ``dearpygui`` at module scope, but this suite never
reaches any DPG call: it only resolves the registry, reads the ``overlay_adapter``
field, constructs adapters (whose ``__init__`` just stores its args), and calls the
DPG-free classmethods (``ui_widget_tags`` / ``build_init_kwargs``). Importing real
``dearpygui`` triggers no DPG C calls, so no stub is needed -- the adapter modules
are imported directly and none of the asserted code paths touch ``dpg``.

Behavioral contracts guarded here
---------------------------------
OVERLAY_ADAPTER_REGISTRY (the lookup table)
  C1  The two registered keys resolve to their adapter classes: "hull" ->
      HullAdapter, "voronoi" -> VoronoiAdapter.

Model -> adapter dispatch (MODEL_REGISTRY ``overlay_adapter`` field)
  C2  Every model declaring a non-None ``overlay_adapter`` names a key that is
      present in OVERLAY_ADAPTER_REGISTRY, so the dispatcher can resolve a class
      for it. Asserted over the real plot-bearing models (convex_hull,
      discrete_voronoi).
  C3  Models that declare ``overlay_adapter`` as None (or omit the field)
      resolve to no overlay, so the dispatcher takes its skip path. Asserted
      over the real overlay-less models.

Registry shape / adapter interface
  C4  Each registered adapter class is constructible with the
      ``(drawlist_tag, mapper)`` signature the dispatcher uses and exposes the
      lifecycle/dispatch methods the dispatcher calls (init, update_frame,
      set_visible, clear, plus the DPG-free ui_widget_tags / build_init_kwargs
      classmethods).
  C5  ``ui_widget_tags()`` returns each adapter's declared companion-widget
      tags that the dispatcher reveals on bind: empty for HullAdapter, the
      three alpha-row tags for VoronoiAdapter.
"""

from __future__ import annotations

import pytest

from floodlight_gui.registry.models import MODEL_REGISTRY
from floodlight_gui.rendering.adapters import OVERLAY_ADAPTER_REGISTRY
from floodlight_gui.rendering.adapters.hull import HullAdapter
from floodlight_gui.rendering.adapters.voronoi import VoronoiAdapter


class _StubMapper:
    """Minimal CoordinateMapper stand-in for adapter construction.

    Adapter ``__init__`` only stores the mapper reference; no method on it is
    invoked during construction, so an attribute-free object suffices.
    """


# --------------------------------------------------------------------------- #
# C1: registry lookup table                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key, expected_cls",
    [
        ("hull", HullAdapter),
        ("voronoi", VoronoiAdapter),
    ],
)
def test_registry_keys_resolve_to_adapter_classes(key, expected_cls):
    """C1: each registered key maps to its adapter class."""
    assert OVERLAY_ADAPTER_REGISTRY[key] is expected_cls


# --------------------------------------------------------------------------- #
# C2 / C3: model -> adapter dispatch resolution                                 #
# --------------------------------------------------------------------------- #


def _models_with_overlay():
    """Return ``(model_key, adapter_key)`` for every plot-bearing model.

    Reads the live MODEL_REGISTRY so a newly plot-enabled model is covered
    without editing the test.
    """
    return [
        (mk, desc["overlay_adapter"])
        for mk, desc in MODEL_REGISTRY.items()
        if desc.get("overlay_adapter") is not None
    ]


def _models_without_overlay():
    """Return every model_key that declares no overlay (None or absent field)."""
    return [mk for mk, desc in MODEL_REGISTRY.items() if desc.get("overlay_adapter") is None]


@pytest.mark.parametrize("model_key, adapter_key", _models_with_overlay())
def test_plot_bearing_model_resolves_to_registered_adapter(model_key, adapter_key):
    """C2: a non-None overlay_adapter names a key present in the registry.

    This is the dispatch contract: every fitted model that advertises an
    overlay must resolve to a constructible adapter class, otherwise the
    visualization tab would log a warning and draw nothing.
    """
    assert adapter_key in OVERLAY_ADAPTER_REGISTRY
    assert isinstance(OVERLAY_ADAPTER_REGISTRY[adapter_key], type)


def test_known_models_carry_expected_adapter_keys():
    """C2: the two real plot-bearing models name their expected adapters.

    Pins the concrete model->adapter wiring (convex_hull->hull,
    discrete_voronoi->voronoi) so a mis-edit of either descriptor is caught.
    """
    assert MODEL_REGISTRY["convex_hull"]["overlay_adapter"] == "hull"
    assert MODEL_REGISTRY["discrete_voronoi"]["overlay_adapter"] == "voronoi"


@pytest.mark.parametrize("model_key", _models_without_overlay())
def test_overlay_less_model_resolves_to_no_adapter(model_key):
    """C3: an overlay-less model yields no adapter key (dispatcher skip path)."""
    assert MODEL_REGISTRY[model_key].get("overlay_adapter") is None


# --------------------------------------------------------------------------- #
# C4: registry shape / adapter interface                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("adapter_cls", list(OVERLAY_ADAPTER_REGISTRY.values()))
def test_adapter_is_constructible_and_exposes_lifecycle_interface(adapter_cls):
    """C4: each registered adapter constructs and exposes the dispatch interface.

    The dispatcher constructs an adapter as ``adapter_cls(drawlist_tag,
    mapper)`` then drives it through init / update_frame / set_visible / clear
    and reads tags via the DPG-free classmethods. Construction stores the args
    and touches no DPG; only the presence and callability of the interface is
    asserted here, never any rendered output.
    """
    adapter = adapter_cls("__drawlist", _StubMapper())
    for method in ("init", "update_frame", "set_visible", "clear"):
        assert callable(getattr(adapter, method))
    assert callable(adapter_cls.ui_widget_tags)
    assert callable(adapter_cls.build_init_kwargs)


# --------------------------------------------------------------------------- #
# C5: declared companion-widget tags                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "adapter_cls, expected_tags",
    [
        (HullAdapter, ()),
        (
            VoronoiAdapter,
            (
                "viz_voronoi_alpha_spacer",
                "viz_voronoi_alpha_label",
                "viz_voronoi_alpha",
            ),
        ),
    ],
)
def test_ui_widget_tags_reports_declared_companion_widgets(adapter_cls, expected_tags):
    """C5: ui_widget_tags returns the tags the dispatcher reveals on bind.

    HullAdapter has no companion widgets (controlled solely by its checkbox);
    VoronoiAdapter declares the three alpha-slider-row tags.
    """
    assert adapter_cls.ui_widget_tags() == expected_tags
