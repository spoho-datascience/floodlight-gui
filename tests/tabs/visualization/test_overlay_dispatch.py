"""Behavioral contracts for ``visualization.overlay_dispatch``.

The dispatch callbacks (``_on_overlay_toggle``, ``_on_model_fitted``,
``_bind_adapter_from_fitted_models``) are thin DPG-widget orchestration over the
real adapter classes; their decisions are dominated by side effects on live DPG
items and adapter construction. The one piece of self-contained selection logic
worth guarding is ``_build_overlay_specs_for_export``: from the live adapters
and the model-tab fit cache it must decide which adapter becomes an export spec.
Color resolution is delegated to ``colors`` and tested there, so the specs are
asserted by shape and key, not by exact RGBA.

Behavioral contracts guarded here
---------------------------------
_build_overlay_specs_for_export
  C1  Emits one spec per visible adapter that has a matching fitted model,
      carrying that model and the adapter's alpha / n_team1.
  C2  An adapter whose visibility flag is OFF (either ``_visible`` False or, for
      hull, ``_hull_visible`` False) is excluded.
  C3  An adapter with no matching fitted model is skipped silently.
  C4  Only fits whose model descriptor's ``overlay_adapter`` resolves to the
      adapter key and whose half matches the selected half are matched.
"""

from __future__ import annotations

import pytest

from floodlight_gui.tabs.visualization import overlay_dispatch, state


class _FakeAdapter:
    """Minimal adapter double exposing the visibility / alpha / n_team1 reads.

    The export-spec builder reads ``_visible`` (or ``_hull_visible``),
    ``_alpha``, and ``_n_team1``; nothing else of the real adapter is touched.
    """

    def __init__(self, *, visible=True, hull_visible=None, alpha=0.4, n_team1=5):
        if hull_visible is None:
            self._visible = visible
        else:
            self._hull_visible = hull_visible
        self._alpha = alpha
        self._n_team1 = n_team1


@pytest.fixture
def fresh_viz_state(monkeypatch):
    """Install a fresh ViewerState as the module singleton and return it."""
    new_state = state.ViewerState()
    new_state.cached_team_names = ["Home", "Away"]
    new_state.selected_half = "firstHalf"
    monkeypatch.setattr(state, "viz_state", new_state)
    return new_state


@pytest.fixture
def install_fits(monkeypatch):
    """Patch the model-tab fit cache and MODEL_REGISTRY for the export builder.

    Returns a callable ``(fitted_models, registry)`` that installs both so the
    builder resolves adapter keys from real-shaped fit entries.
    """
    import floodlight_gui.tabs.model.state as model_state

    def _install(fitted_models, registry):
        monkeypatch.setattr(model_state, "fitted_models", fitted_models)
        monkeypatch.setattr(overlay_dispatch, "MODEL_REGISTRY", registry)

    return _install


# Real-shaped fit cache: keyed by (half, team, model_key) -> (model, params).
_MODEL = object()
_REGISTRY = {
    "convex_hull": {"overlay_adapter": "hull"},
    "discrete_voronoi": {"overlay_adapter": "voronoi"},
    "centroid": {},  # no overlay adapter
}


def test_visible_adapter_with_model_yields_spec(fresh_viz_state, install_fits):
    """C1: a visible, model-backed adapter produces one spec with its model and alpha."""
    fresh_viz_state.active_adapters = {"hull": _FakeAdapter(alpha=0.7, n_team1=11)}
    install_fits({("firstHalf", "Home", "convex_hull"): (_MODEL, {})}, _REGISTRY)

    specs = overlay_dispatch._build_overlay_specs_for_export()

    assert len(specs) == 1
    spec = specs[0]
    assert spec["key"] == "hull"
    assert spec["model"] is _MODEL
    assert spec["alpha"] == 0.7
    assert spec["n_team1"] == 11


@pytest.mark.parametrize(
    "adapter",
    [
        _FakeAdapter(visible=False),  # VoronoiAdapter-style flag
        _FakeAdapter(hull_visible=False),  # HullAdapter-style flag
    ],
)
def test_hidden_adapter_excluded(fresh_viz_state, install_fits, adapter):
    """C2: an adapter whose visibility flag is OFF is excluded from specs."""
    fresh_viz_state.active_adapters = {"hull": adapter}
    install_fits({("firstHalf", "Home", "convex_hull"): (_MODEL, {})}, _REGISTRY)

    assert overlay_dispatch._build_overlay_specs_for_export() == []


def test_adapter_without_matching_model_skipped(fresh_viz_state, install_fits):
    """C3: a visible adapter with no matching fitted model is silently skipped."""
    fresh_viz_state.active_adapters = {"voronoi": _FakeAdapter()}
    # The only fit resolves to the hull adapter, not voronoi.
    install_fits({("firstHalf", "Home", "convex_hull"): (_MODEL, {})}, _REGISTRY)

    assert overlay_dispatch._build_overlay_specs_for_export() == []


def test_match_requires_overlay_key_and_half(fresh_viz_state, install_fits):
    """C4: only fits resolving to the adapter key and the selected half match."""
    fresh_viz_state.active_adapters = {"hull": _FakeAdapter()}
    fresh_viz_state.selected_half = "firstHalf"
    install_fits(
        {
            # Right adapter key, wrong half: must not match.
            ("secondHalf", "Home", "convex_hull"): (object(), {}),
            # A non-overlay model in the cache: must not match.
            ("firstHalf", "Home", "centroid"): (object(), {}),
            # Right adapter key and half: the one true match.
            ("firstHalf", "Away", "convex_hull"): (_MODEL, {}),
        },
        _REGISTRY,
    )

    specs = overlay_dispatch._build_overlay_specs_for_export()

    assert len(specs) == 1
    assert specs[0]["model"] is _MODEL
