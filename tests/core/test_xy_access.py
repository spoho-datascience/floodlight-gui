"""Behavioral contracts for ``floodlight_gui.core.xy_access``.

``get_xy_for_period_team(app, period, team)`` is the canonical resolver for
XY data across every provider structure. It prefers ``app.get_active_xy``
(so spatial-ops transforms apply), normalises the period display name to an
internal key, and otherwise falls back to extracting from ``app.loaded_data``,
handling both nested DFL-style ``{period: {team: XY}}`` and flat
Kinexon-style ``{team: XY}`` containers. The seam is the ``app`` object, so
tests pass small fake apps exposing only ``get_active_xy`` or ``loaded_data``
and use sentinel objects for XY. Period normalisation is owned by
``periods`` and exercised only through the values this resolver forwards.

Behavioral contracts guarded here
---------------------------------
C1  When ``get_active_xy`` is present and returns non-None, that XY is
    returned and the getter is invoked with the period normalised to its
    internal key.
C2  When ``get_active_xy`` returns None, the resolver falls through to the
    ``loaded_data`` extraction path.
C3  Fallback, nested DFL-style ``{internal_period: {team: XY}}``: the XY for
    the requested period/team is returned, with the display period
    normalised to its internal key first.
C4  Fallback, flat Kinexon-style ``{team: XY}`` with no period nesting: the
    XY for the requested team is returned.
C5  Returns None when there is no usable ``loaded_data`` (app is None, has
    no ``loaded_data``, or the tuple has fewer than three elements).
C6  Returns None when the position data is None or its container is not a
    dict.
C7  Returns None when the requested period or team is absent from an
    otherwise valid container.
"""

from __future__ import annotations

import pytest

from floodlight_gui.core.xy_access import get_xy_for_period_team

# Sentinels stand in for XY objects; identity is all the resolver cares about.
XY_HOME = object()
XY_AWAY = object()


# --------------------------------------------------------------------------- #
# Test doubles                                                                  #
# --------------------------------------------------------------------------- #


class _ActiveXYApp:
    """App exposing ``get_active_xy`` and recording its call args.

    The getter returns whatever ``result`` is set to, letting tests drive
    both the hit (non-None) and the miss (None) branches of the preferred
    path while asserting the period/team the resolver forwarded.
    """

    def __init__(self, result, loaded_data=None):
        self._result = result
        self.loaded_data = loaded_data
        self.calls = []

    def get_active_xy(self, period, team):
        self.calls.append((period, team))
        return self._result


class _LoadedDataApp:
    """App exposing only ``loaded_data`` (no ``get_active_xy``).

    Models the pre-initialisation call sites that read position data
    directly. ``loaded_data`` is the 4-tuple ``(pitch, events,
    position_data, teamsheet)``.
    """

    def __init__(self, loaded_data):
        self.loaded_data = loaded_data


def _loaded(position_data):
    """Build a 4-tuple loaded_data carrying the given position_data slot."""
    return (None, None, position_data, None)


# --------------------------------------------------------------------------- #
# Preferred path: app.get_active_xy                                             #
# --------------------------------------------------------------------------- #


def test_active_getter_returns_xy_with_internal_period():
    """C1: a non-None getter result is returned; period is normalised first."""
    app = _ActiveXYApp(result=XY_HOME)
    result = get_xy_for_period_team(app, "First Half", "Home")
    assert result is XY_HOME
    assert app.calls == [("firstHalf", "Home")]


def test_active_getter_miss_falls_through_to_loaded_data():
    """C2: a None getter result falls through to the loaded_data path."""
    xy_container = {"firstHalf": {"Home": XY_HOME}}
    app = _ActiveXYApp(result=None, loaded_data=_loaded((xy_container, None, None)))
    result = get_xy_for_period_team(app, "First Half", "Home")
    assert result is XY_HOME


# --------------------------------------------------------------------------- #
# Fallback path: loaded_data extraction                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "position_data",
    [
        # position_data as the (xy_dict, possession, ballstatus) tuple
        ({"firstHalf": {"Home": XY_HOME, "Away": XY_AWAY}}, None, None),
        # position_data already unwrapped to the bare xy_dict
        {"firstHalf": {"Home": XY_HOME, "Away": XY_AWAY}},
    ],
)
def test_fallback_nested_dfl_style_resolves_by_period_and_team(position_data):
    """C3: nested {period: {team: XY}} resolves with the period normalised."""
    app = _LoadedDataApp(_loaded(position_data))
    assert get_xy_for_period_team(app, "First Half", "Home") is XY_HOME
    assert get_xy_for_period_team(app, "First Half", "Away") is XY_AWAY


def test_fallback_flat_kinexon_style_resolves_by_team():
    """C4: flat {team: XY} with no period nesting resolves by team."""
    position_data = ({"Home": XY_HOME, "Away": XY_AWAY}, None, None)
    app = _LoadedDataApp(_loaded(position_data))
    assert get_xy_for_period_team(app, "First Half", "Away") is XY_AWAY


# --------------------------------------------------------------------------- #
# None / guard contracts                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "app",
    [
        None,
        _LoadedDataApp(None),
        _LoadedDataApp((None, None)),  # tuple shorter than 3 slots
    ],
)
def test_returns_none_without_usable_loaded_data(app):
    """C5: no usable loaded_data (None app, no data, short tuple) -> None."""
    assert get_xy_for_period_team(app, "First Half", "Home") is None


@pytest.mark.parametrize(
    "position_data",
    [
        None,  # position_data slot is None
        ([1, 2, 3], None, None),  # container is a list, not a dict
        "notadict",  # container is a scalar, not a dict
    ],
)
def test_returns_none_for_unusable_container(position_data):
    """C6: position data None or a non-dict container resolves to None."""
    app = _LoadedDataApp(_loaded(position_data))
    assert get_xy_for_period_team(app, "First Half", "Home") is None


@pytest.mark.parametrize(
    "period, team",
    [
        ("Second Half", "Home"),  # period absent from nested container
        ("First Half", "Ball"),  # team absent within the present period
    ],
)
def test_returns_none_for_missing_period_or_team(period, team):
    """C7: a missing period or missing team resolves to None."""
    position_data = ({"firstHalf": {"Home": XY_HOME}}, None, None)
    app = _LoadedDataApp(_loaded(position_data))
    assert get_xy_for_period_team(app, period, team) is None
