"""_build_overlay_specs_for_export contracts.

C1 an active+visible adapter with a matching fitted model yields one spec.
C2 a hidden adapter is filtered out; the visibility cascade honors both
   VoronoiAdapter._visible and HullAdapter._hull_visible.
C3 an active adapter with no fitted model yields an empty list (no warning).
C4 no active adapters yields an empty list.
C5 both overlays active+visible yields 2 specs.

viz_state is patched on the visualization state module; the function and
MODEL_REGISTRY live in tabs/visualization/overlay_dispatch.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def _make_fake_voronoi_adapter(*, visible: bool = True, alpha: float = 0.4, n_team1: int = 11):
    """Fake VoronoiAdapter exposing _visible / _alpha / _n_team1 (no
    _hull_visible). spec=[] suppresses auto-attrs so getattr defaults apply."""
    a = MagicMock(spec=[])
    a._visible = visible
    a._alpha = alpha
    a._n_team1 = n_team1
    return a


def _make_fake_hull_adapter(*, hull_visible: bool = True):
    """Fake HullAdapter exposing _hull_visible only, mirroring the real adapter
    which carries no _visible / _alpha / _n_team1 per-instance attrs. spec=[]
    suppresses auto-attrs so getattr defaults apply."""
    a = MagicMock(spec=[])
    a._hull_visible = hull_visible
    return a


def test_spec_builder_returns_active_visible(monkeypatch):
    """One active+visible adapter with a matching fitted model yields one spec."""
    from floodlight_gui.tabs.visualization import overlay_dispatch as vtab
    from floodlight_gui.tabs.visualization import state as _viz_state

    voronoi_adapter = _make_fake_voronoi_adapter(visible=True, alpha=0.42, n_team1=11)
    fake_model = MagicMock()

    fake_viz_state = {
        "active_adapters": {"voronoi": voronoi_adapter},
        "selected_half": None,
    }
    fake_fitted = {("H1", "Home", "discrete_voronoi"): (fake_model, {})}
    fake_model_registry = {"discrete_voronoi": {"overlay_adapter": "voronoi"}}

    monkeypatch.setattr(_viz_state, "viz_state", fake_viz_state)
    monkeypatch.setattr("floodlight_gui.tabs.model.state.fitted_models", fake_fitted, raising=False)
    monkeypatch.setattr(vtab, "MODEL_REGISTRY", fake_model_registry, raising=False)

    specs = vtab._build_overlay_specs_for_export()
    assert len(specs) == 1
    s = specs[0]
    assert s["key"] == "voronoi"
    assert s["model"] is fake_model
    assert s["alpha"] == 0.42
    assert s["n_team1"] == 11
    assert set(s.keys()) == {"key", "model", "alpha", "team1_color", "team2_color", "n_team1"}


def test_spec_builder_filters_hidden_voronoi(monkeypatch):
    """A hidden Voronoi adapter (_visible=False) is excluded."""
    from floodlight_gui.tabs.visualization import overlay_dispatch as vtab
    from floodlight_gui.tabs.visualization import state as _viz_state

    voronoi_adapter = _make_fake_voronoi_adapter(visible=False)
    fake_model = MagicMock()

    fake_viz_state = {
        "active_adapters": {"voronoi": voronoi_adapter},
        "selected_half": None,
    }
    fake_fitted = {("H1", "Home", "discrete_voronoi"): (fake_model, {})}
    fake_model_registry = {"discrete_voronoi": {"overlay_adapter": "voronoi"}}

    monkeypatch.setattr(_viz_state, "viz_state", fake_viz_state)
    monkeypatch.setattr("floodlight_gui.tabs.model.state.fitted_models", fake_fitted, raising=False)
    monkeypatch.setattr(vtab, "MODEL_REGISTRY", fake_model_registry, raising=False)

    specs = vtab._build_overlay_specs_for_export()
    assert specs == [], f"expected empty list for hidden adapter, got {specs}"


def test_spec_builder_filters_hidden_hull_via_hull_visible(monkeypatch):
    """HullAdapter exposes _hull_visible rather than _visible, so the
    visibility cascade must honor _hull_visible to filter a hidden hull."""
    from floodlight_gui.tabs.visualization import overlay_dispatch as vtab
    from floodlight_gui.tabs.visualization import state as _viz_state

    hull_adapter = _make_fake_hull_adapter(hull_visible=False)
    fake_model = MagicMock()

    fake_viz_state = {
        "active_adapters": {"hull": hull_adapter},
        "selected_half": None,
    }
    fake_fitted = {("H1", "Home", "convex_hull"): (fake_model, {})}
    fake_model_registry = {"convex_hull": {"overlay_adapter": "hull"}}

    monkeypatch.setattr(_viz_state, "viz_state", fake_viz_state)
    monkeypatch.setattr("floodlight_gui.tabs.model.state.fitted_models", fake_fitted, raising=False)
    monkeypatch.setattr(vtab, "MODEL_REGISTRY", fake_model_registry, raising=False)

    specs = vtab._build_overlay_specs_for_export()
    assert specs == [], (
        f"HullAdapter._hull_visible=False must filter the spec "
        f"(cascade is _visible -> _hull_visible -> True default). Got {specs}"
    )


def test_spec_builder_empty_when_no_fitted_model(monkeypatch):
    """An active adapter with no fitted model yields an empty list (no warning)."""
    from floodlight_gui.tabs.visualization import overlay_dispatch as vtab
    from floodlight_gui.tabs.visualization import state as _viz_state

    voronoi_adapter = _make_fake_voronoi_adapter(visible=True)

    fake_viz_state = {
        "active_adapters": {"voronoi": voronoi_adapter},
        "selected_half": None,
    }
    fake_fitted: dict = {}  # nothing fitted
    fake_model_registry = {"discrete_voronoi": {"overlay_adapter": "voronoi"}}

    monkeypatch.setattr(_viz_state, "viz_state", fake_viz_state)
    monkeypatch.setattr("floodlight_gui.tabs.model.state.fitted_models", fake_fitted, raising=False)
    monkeypatch.setattr(vtab, "MODEL_REGISTRY", fake_model_registry, raising=False)

    specs = vtab._build_overlay_specs_for_export()
    assert specs == []


def test_spec_builder_empty_when_no_active_adapters(monkeypatch):
    """No active adapters yields an empty list."""
    from floodlight_gui.tabs.visualization import state as _viz_state

    fake_viz_state = {"active_adapters": {}, "selected_half": None}
    monkeypatch.setattr(_viz_state, "viz_state", fake_viz_state)

    from floodlight_gui.tabs.visualization import overlay_dispatch as vtab

    specs = vtab._build_overlay_specs_for_export()
    assert specs == []


def test_spec_builder_returns_both_when_voronoi_and_hull_active(monkeypatch):
    """Both overlays active+visible yields 2 specs in the list."""
    from floodlight_gui.tabs.visualization import overlay_dispatch as vtab
    from floodlight_gui.tabs.visualization import state as _viz_state

    voronoi_adapter = _make_fake_voronoi_adapter(visible=True)
    hull_adapter = _make_fake_hull_adapter(hull_visible=True)
    v_model = MagicMock()
    h_model = MagicMock()

    fake_viz_state = {
        "active_adapters": {"voronoi": voronoi_adapter, "hull": hull_adapter},
        "selected_half": None,
    }
    fake_fitted = {
        ("H1", "Home", "discrete_voronoi"): (v_model, {}),
        ("H1", "Home", "convex_hull"): (h_model, {}),
    }
    fake_model_registry = {
        "discrete_voronoi": {"overlay_adapter": "voronoi"},
        "convex_hull": {"overlay_adapter": "hull"},
    }

    monkeypatch.setattr(_viz_state, "viz_state", fake_viz_state)
    monkeypatch.setattr("floodlight_gui.tabs.model.state.fitted_models", fake_fitted, raising=False)
    monkeypatch.setattr(vtab, "MODEL_REGISTRY", fake_model_registry, raising=False)

    specs = vtab._build_overlay_specs_for_export()
    assert len(specs) == 2
    keys = sorted(s["key"] for s in specs)
    assert keys == ["hull", "voronoi"]
