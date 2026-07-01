"""Behavioral tests for the public API surface (``floodlight_gui.__all__``).

The package root re-exports a deliberately small, frozen surface: the four
registries, their four ``register_*`` extension helpers, ``validate_all``, the
player-mapping helpers, the EventBus pair, and the ``run`` entry point. These
tests pin that exact set and assert every name is importable and visible via
``dir()``, so an accidental addition, removal, or broken re-export fails here.
"""

from __future__ import annotations

import floodlight_gui

# The frozen public surface. Changing this set is a deliberate API break.
_EXPECTED_ALL = {
    # Registries
    "IO_REGISTRY",
    "MODEL_REGISTRY",
    "TRANSFORM_REGISTRY",
    "METRICS_REGISTRY",
    # Registration helpers
    "register_io_provider",
    "register_model",
    "register_transform",
    "register_metric",
    # Validation
    "validate_all",
    # Player mapping
    "PlayerSlot",
    "build_player_slots",
    "get_xy_for_period_team",
    # Events
    "EventBus",
    "Events",
    # Entry point
    "run",
}


def test_all_matches_locked_surface():
    """``__all__`` is exactly the frozen set, with no duplicates."""
    assert set(floodlight_gui.__all__) == _EXPECTED_ALL
    assert len(floodlight_gui.__all__) == len(_EXPECTED_ALL)


def test_every_exported_name_is_importable_from_root():
    """Each name in ``__all__`` resolves to a real attribute on the package."""
    for name in floodlight_gui.__all__:
        assert hasattr(floodlight_gui, name), f"{name!r} in __all__ but not importable"


def test_every_exported_name_is_listed_in_dir():
    """Each name in ``__all__`` shows up in ``dir(floodlight_gui)``."""
    listing = dir(floodlight_gui)
    for name in floodlight_gui.__all__:
        assert name in listing, f"{name!r} in __all__ but missing from dir()"


def test_get_xy_for_period_team_is_sourced_from_xy_access():
    """``get_xy_for_period_team`` re-exports the ``core.xy_access`` callable.

    Player-mapping owns only ``PlayerSlot`` and ``build_player_slots``; the
    period/team resolver lives in ``core.xy_access`` and is re-exported as-is.
    """
    from floodlight_gui.core.xy_access import get_xy_for_period_team

    assert floodlight_gui.get_xy_for_period_team is get_xy_for_period_team


def test_events_pair_are_the_event_bus_types():
    """``EventBus`` and ``Events`` re-export the ``core.event_bus`` types."""
    from floodlight_gui.core.event_bus import EventBus, Events

    assert floodlight_gui.EventBus is EventBus
    assert floodlight_gui.Events is Events
