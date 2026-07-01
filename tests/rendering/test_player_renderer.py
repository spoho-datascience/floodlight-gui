"""Behavioral contracts for ``PlayerRenderer`` geometry/logic.

Most of ``PlayerRenderer`` is per-frame draw code: ``_show_slot`` /
``_hide_slot`` and the highlight/select paths exist to push ``configure_item``
calls at DPG, with epsilon throttles and dirty-radius pushes that are draw-loop
optimizations, not caller-observable contracts. Those are carved out per the
standard. The genuine logic worth guarding is:

  - radius computation in ``update_mapper`` (pitch-scale -> clamped pixel radius,
    plus the derived hit radius),
  - hit-testing in ``get_player_at`` (which visible non-ball player, if any, is
    under a pixel, with the radius threshold),
  - the position pipeline's observable consequence: a player placed by
    ``update_positions`` becomes hittable at its mapped pixel, and a NaN
    coordinate hides it,
  - the single-slot selection model (``select_player`` / ``get_selected_player``
    / ``deselect_player``),
  - the ``_brighten`` colour helper.

The DPG toolkit and the CoordinateMapper are the seams. A recording DPG stub is
swapped onto the renderer module's ``dpg`` name via ``monkeypatch.setattr`` (the
real source module is imported first, then its ``dpg`` attribute is replaced), and
a tiny mapper double with a known pitch->pixel mapping drives the geometry, so the
tests assert the renderer's own decisions, never any rendered pixels.

Behavioral contracts guarded here
---------------------------------
update_mapper (radius computation)
  C1  Non-ball and ball base radii come from ``mapper.scale_distance`` clamped
      to [7, 10] px and [5, 7] px respectively (floor, ceiling, and in-band
      scaling).
  C2  The hit radius is derived as ``max(10, non_ball_radius * 3)`` and is the
      value ``get_player_at`` defaults to.

get_player_at (hit-testing)
  C3  Returns the nearest visible player within the radius; the closest of
      several candidates wins.
  C4  Returns None when no eligible player is within the radius threshold.
  C5  Excludes the ball, NaN-hidden players, and players on a hidden team.
  C6  With ``hit_radius=None`` it uses the instance hit radius, so an
      ``update_mapper`` rescale changes which clicks register.

update_positions (position pipeline, observable via hit-testing)
  C7  A player placed at a pitch coordinate becomes hittable at its mapped
      pixel; a NaN coordinate hides the player so it is no longer hittable.

selection model
  C8  Selection is single-slot: select then get round-trips; selecting an
      unknown team/index clears it; deselect clears it.

module helper
  C9  ``_brighten`` raises each RGB channel by the amount (clamped at 255) and
      leaves alpha unchanged.
"""

from __future__ import annotations

import numpy as np
import pytest

from floodlight_gui.rendering import player_renderer as _pr
from floodlight_gui.rendering.player_renderer import PlayerRenderer, _brighten
from tests._dpg_stub import make_dpg_stub

# --------------------------------------------------------------------------- #
# DPG stub install (player_renderer binds ``dpg`` at module scope)              #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _stub_dpg(monkeypatch):
    """Swap a recording DPG stub onto ``player_renderer.dpg`` for every test.

    The real source module is imported above (importing ``dearpygui`` is
    harmless -- it triggers no DPG C calls), then this fixture replaces the
    module's bound ``dpg`` name with a stub. Because the swap targets the
    module attribute rather than ``sys.modules``, it works regardless of
    whether real ``dearpygui`` is already imported by a sibling suite, so the
    rendering tests are order-robust (no segfault from real DPG drawing
    without an active context).

    ``make_dpg_stub`` covers ``configure_item`` / ``delete_item`` / ``draw_text``
    but not the ``draw_circle`` drawlist primitive, so it is added here. The
    tests never assert on these draw calls (draw code is carved out); the
    recorders only need to exist so construction/update does not error.
    """
    stub = make_dpg_stub()
    stub.draw_circle = lambda *a, **kw: None
    monkeypatch.setattr(_pr, "dpg", stub)
    return stub


# --------------------------------------------------------------------------- #
# Test doubles                                                                  #
# --------------------------------------------------------------------------- #


class _IdentityMapper:
    """CoordinateMapper double with a controllable pitch->pixel and scale.

    ``pitch_to_pixel`` applies a fixed offset/scale so tests can place a player
    at a known pixel; ``scale_distance`` returns a fixed per-instance value so
    the radius-clamp logic can be exercised at chosen scales.
    """

    def __init__(self, scale_value: float = 8.0, ox: float = 0.0, oy: float = 0.0, k: float = 1.0):
        self._scale_value = scale_value
        self._ox = ox
        self._oy = oy
        self._k = k

    def pitch_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        return (self._ox + self._k * x, self._oy + self._k * y)

    def scale_distance(self, _d: float) -> float:
        return self._scale_value


def _make_renderer(team_configs=None, mapper=None) -> PlayerRenderer:
    """Build a PlayerRenderer with a default two-team-plus-ball config.

    The default mirrors a realistic viz session: two outfield teams and a ball
    entity, so the ball-exclusion and team-visibility hit-test rules have real
    slots to exclude.
    """
    if team_configs is None:
        team_configs = {
            "Home": {"color": [10, 20, 30, 255], "n_players": 2},
            "Away": {"color": [200, 100, 50, 255], "n_players": 2},
            "Ball": {"color": [255, 255, 255, 255], "n_players": 1, "is_ball": True},
        }
    if mapper is None:
        mapper = _IdentityMapper()
    return PlayerRenderer("__drawlist", mapper, team_configs)


# --------------------------------------------------------------------------- #
# C1 / C2: update_mapper radius computation                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "scale_value, expected_non_ball, expected_ball",
    [
        (0.1, 7.0, 5.0),  # below both floors -> clamped up
        (6.0, 7.0, 6.0),  # below non-ball floor (->7); in-band for ball
        (8.5, 8.5, 7.0),  # in-band for non-ball; above ball ceiling (->7)
        (50.0, 10.0, 7.0),  # above both ceilings -> clamped down
    ],
)
def test_update_mapper_clamps_scaled_radii(scale_value, expected_non_ball, expected_ball):
    """C1: base radii are the scaled distance clamped to [7,10] / [5,7] px."""
    renderer = _make_renderer(mapper=_IdentityMapper(scale_value=scale_value))
    renderer.update_mapper(_IdentityMapper(scale_value=scale_value))

    for slot in renderer._slots:
        if slot.is_ball:
            assert slot.base_radius == expected_ball
        else:
            assert slot.base_radius == expected_non_ball


@pytest.mark.parametrize(
    "scale_value, expected_hit_radius",
    [
        (0.1, 21.0),  # non_ball clamps to 7 -> 7*3 = 21
        (3.0, 21.0),  # 3 -> non_ball clamps to 7 -> 21 (still above the 10 floor)
        (50.0, 30.0),  # non_ball clamps to 10 -> 10*3 = 30
    ],
)
def test_update_mapper_derives_hit_radius(scale_value, expected_hit_radius):
    """C2: hit radius is max(10, non_ball_radius * 3) and drives get_player_at.

    The derived value is asserted via the observable consequence: a click just
    inside that radius hits while a click just outside misses, with hit_radius
    left at its default (None).
    """
    mapper = _IdentityMapper(scale_value=scale_value)
    renderer = _make_renderer(mapper=mapper)
    renderer.update_mapper(mapper)

    # Place a single player at pixel (100, 100).
    renderer.update_positions({"Home": np.array([100.0, 100.0])})

    inside = renderer.get_player_at(100.0 + (expected_hit_radius - 0.5), 100.0)
    outside = renderer.get_player_at(100.0 + (expected_hit_radius + 0.5), 100.0)
    assert inside is not None
    assert outside is None


# --------------------------------------------------------------------------- #
# C3 / C4: hit-testing core                                                     #
# --------------------------------------------------------------------------- #


def test_get_player_at_returns_nearest_candidate():
    """C3: among several in-range players the closest one is returned."""
    renderer = _make_renderer()
    # Home0 at (100,100), Home1 at (110,100); click at (104,100) is nearer Home0.
    renderer.update_positions({"Home": np.array([100.0, 100.0, 110.0, 100.0])})

    hit = renderer.get_player_at(104.0, 100.0, hit_radius=50.0)
    assert hit == {
        "team": "Home",
        "player_index": 0,
        "distance_px": pytest.approx(4.0),
    }


def test_get_player_at_returns_none_when_out_of_range():
    """C4: a click beyond hit_radius of every player returns None."""
    renderer = _make_renderer()
    renderer.update_positions({"Home": np.array([100.0, 100.0])})

    # Click 100 px away with only a 5 px radius -> no hit.
    assert renderer.get_player_at(200.0, 100.0, hit_radius=5.0) is None


# --------------------------------------------------------------------------- #
# C5: candidate eligibility (ball / NaN-hidden / hidden team excluded)          #
# --------------------------------------------------------------------------- #


def test_get_player_at_excludes_ball():
    """C5: the ball is never a hit-test candidate even when under the cursor."""
    renderer = _make_renderer()
    # Only the ball is positioned, at (50,50). Players stay offscreen.
    renderer.update_positions({"Ball": np.array([50.0, 50.0])}, ball_xy=(50.0, 50.0))

    assert renderer.get_player_at(50.0, 50.0, hit_radius=50.0) is None


def test_get_player_at_excludes_nan_hidden_player():
    """C5: a player hidden by a NaN coordinate is not a hit-test candidate."""
    renderer = _make_renderer()
    renderer.update_positions({"Home": np.array([100.0, 100.0])})  # Home0 visible
    renderer.update_positions({"Home": np.array([np.nan, np.nan])})  # now hidden

    assert renderer.get_player_at(100.0, 100.0, hit_radius=50.0) is None


def test_get_player_at_excludes_hidden_team():
    """C5: players on a team toggled invisible are excluded from hit-testing."""
    renderer = _make_renderer()
    renderer.update_positions({"Home": np.array([100.0, 100.0])})
    renderer.set_team_visible("Home", False)

    assert renderer.get_player_at(100.0, 100.0, hit_radius=50.0) is None


# --------------------------------------------------------------------------- #
# C6: default hit radius tracks the instance value                              #
# --------------------------------------------------------------------------- #


def test_get_player_at_uses_instance_hit_radius_when_none():
    """C6: hit_radius=None reads self._hit_radius_px, so rescaling propagates.

    A small instance hit radius rejects a click that a later, larger rescaled
    radius would accept, proving the default is the live instance value rather
    than a frozen module constant.
    """
    renderer = _make_renderer(mapper=_IdentityMapper(scale_value=8.0))
    renderer.update_positions({"Home": np.array([100.0, 100.0])})

    renderer._hit_radius_px = 5.0
    assert renderer.get_player_at(112.0, 100.0) is None  # 12 px > 5 px

    renderer._hit_radius_px = 20.0
    hit = renderer.get_player_at(112.0, 100.0)  # 12 px < 20 px
    assert hit is not None and hit["player_index"] == 0


# --------------------------------------------------------------------------- #
# C7: position pipeline observable through hit-testing                          #
# --------------------------------------------------------------------------- #


def test_update_positions_places_then_nan_hides():
    """C7: a placed player is hittable at its mapped pixel; NaN hides it.

    Uses an offset/scaled mapper so the hit lands at the mapped pixel, not the
    raw pitch coordinate, confirming update_positions routes through
    pitch_to_pixel before the slot becomes hittable.
    """
    mapper = _IdentityMapper(ox=10.0, oy=5.0, k=2.0)  # pixel = (10+2x, 5+2y)
    renderer = _make_renderer(mapper=mapper)
    renderer.update_positions({"Home": np.array([20.0, 30.0])})

    # Pitch (20,30) -> pixel (50,65).
    hit = renderer.get_player_at(50.0, 65.0, hit_radius=2.0)
    assert hit is not None and hit["team"] == "Home" and hit["player_index"] == 0

    renderer.update_positions({"Home": np.array([np.nan, np.nan])})
    assert renderer.get_player_at(50.0, 65.0, hit_radius=2.0) is None


# --------------------------------------------------------------------------- #
# C8: single-slot selection model                                              #
# --------------------------------------------------------------------------- #


def test_selection_is_single_slot_and_clears():
    """C8: select round-trips, unknown selection clears, deselect clears."""
    renderer = _make_renderer()

    renderer.select_player("Away", 1)
    assert renderer.get_selected_player() == {"team": "Away", "player_index": 1}

    # Selecting a second player replaces the first (only one selected at a time).
    renderer.select_player("Home", 0)
    assert renderer.get_selected_player() == {"team": "Home", "player_index": 0}

    # Selecting an unknown slot clears the selection.
    renderer.select_player("Home", 99)
    assert renderer.get_selected_player() is None

    renderer.select_player("Away", 0)
    renderer.deselect_player()
    assert renderer.get_selected_player() is None


# --------------------------------------------------------------------------- #
# C9: _brighten colour helper                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "color, amount, expected",
    [
        ([10, 20, 30, 255], 60, [70, 80, 90, 255]),  # plain raise, alpha kept
        ([230, 240, 250, 128], 60, [255, 255, 255, 128]),  # RGB clamps at 255
    ],
)
def test_brighten_raises_rgb_and_preserves_alpha(color, amount, expected):
    """C9: _brighten raises RGB by amount (clamped at 255), alpha unchanged."""
    assert _brighten(color, amount) == expected
