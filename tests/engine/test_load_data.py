"""Behavioral contracts for ``floodlight_gui.engine.load_data``.

This executor is the IO_REGISTRY loader/normalizer. Its testable own-logic is
the pure structural normalization of a loaded 4-tuple into the metadata dict
(``extract_metadata``), the provider-agnostic match-listing dispatch
(``available_matches``), the Kinexon flat-dict adapter (``_adapt_kinexon``),
the payload bundler (``_finalize_dataset_payload``), and the guard/dispatch
decisions at the head of ``load_provider_data``.

The real provider loaders (floodlight IO functions) and the dataset downloads
are the seam: they are never called here. ``extract_metadata`` is pure
structural logic over dict shapes, so tests use sentinel objects for XY and
assert only structure, counts, and flags, never analytics values or network.

Behavioral contracts guarded here
---------------------------------
extract_metadata
  C1  A short or falsy 4-tuple yields the canonical empty/unknown metadata
      dict.
  C2  Nested ``{period: {team: XY}}`` position data reports every period as a
      temporal division and the inner keys as teams.
  C3  Flat ``{team: XY}`` position data reports the single ``fullMatch``
      division and the dict keys as teams.
  C4  ``has_possession`` / ``has_ballstatus`` track the presence of non-None
      2nd / 3rd elements of the position tuple.
  C5  Adapted dict-shaped position data (the Kinexon adapter output) reports
      its keys as teams under a single ``fullMatch`` division.
  C6  Teams fall back to teamsheet keys when position data names no teams.
  C7  ``has_ball`` is set iff some team name equals ``"ball"`` case-insensitively.
  C8  ``format_type`` echoes the provider_key and ``num_halves`` equals the
      temporal-division count.

available_matches
  C9  Dispatches to the provider's ``list_matches`` hook and returns its
      catalogue verbatim (real dataset providers, no network).
  C10 An unknown provider, or a provider with no ``list_matches`` hook,
      returns an empty list.

_adapt_kinexon
  C11 Wraps a flat positions/teamsheets result into the 4-tuple
      ``(None, None, (xy,), teamsheets)`` with pitch and events None.

_finalize_dataset_payload
  C12 Bundles the raw 4-tuple under ``data`` and its computed metadata under
      ``metadata``.

load_provider_data (guard/dispatch only)
  C13 An unknown provider_key returns None via the boundary except.
  C14 A disabled provider returns None via the boundary except.
  C15 A dataset provider is routed to the dataset dispatcher, bypassing the
      disabled guard, and its ``match_id`` is forwarded as a direct kwarg.
"""

from __future__ import annotations

import pytest

import floodlight_gui.engine.load_data as ld
from floodlight_gui.engine.load_data import (
    _adapt_kinexon,
    _finalize_dataset_payload,
    available_matches,
    extract_metadata,
    load_provider_data,
)

# --------------------------------------------------------------------------- #
# Sentinels                                                                     #
# --------------------------------------------------------------------------- #

# extract_metadata never reads XY contents, only dict shape, so any object
# stands in for an XY.
_XY = object()


def _pos_tuple(xy_dict, possession=None, ballstatus=None):
    """Build a standard-provider position tuple ``(xy_dict, poss, ballstatus)``."""
    return (xy_dict, possession, ballstatus)


# --------------------------------------------------------------------------- #
# extract_metadata                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("loaded", [None, (), (1, 2, 3)])
def test_extract_metadata_short_tuple_is_unknown(loaded):
    """C1: a falsy or too-short loaded tuple yields the empty/unknown dict."""
    meta = extract_metadata("dfl", loaded)
    assert meta["format_type"] == "unknown"
    assert meta["temporal_divisions"] == []
    assert meta["teams"] == []
    assert meta["num_halves"] == 0
    assert meta["has_possession"] is False
    assert meta["has_ballstatus"] is False
    assert meta["has_ball"] is False


def test_extract_metadata_nested_reports_periods_and_inner_teams():
    """C2: nested ``{period: {team: XY}}`` lists periods and inner team keys."""
    xy_dict = {
        "firstHalf": {"Home": _XY, "Away": _XY},
        "secondHalf": {"Home": _XY, "Away": _XY},
    }
    meta = extract_metadata("dfl", (None, None, _pos_tuple(xy_dict), None))
    assert meta["temporal_divisions"] == ["firstHalf", "secondHalf"]
    assert meta["teams"] == ["Home", "Away"]
    assert meta["num_halves"] == 2


def test_extract_metadata_flat_reports_fullmatch_and_team_keys():
    """C3: flat ``{team: XY}`` reports a single ``fullMatch`` division."""
    xy_dict = {"teamA": _XY, "teamB": _XY, "ball": _XY}
    meta = extract_metadata("eigd_h", (None, None, _pos_tuple(xy_dict), None))
    assert meta["temporal_divisions"] == ["fullMatch"]
    assert meta["teams"] == ["teamA", "teamB", "ball"]
    assert meta["num_halves"] == 1


@pytest.mark.parametrize(
    "possession, ballstatus, exp_poss, exp_ball",
    [
        (None, None, False, False),
        (object(), None, True, False),
        (None, object(), False, True),
        (object(), object(), True, True),
    ],
)
def test_extract_metadata_possession_ballstatus_flags(possession, ballstatus, exp_poss, exp_ball):
    """C4: possession/ballstatus flags track non-None 2nd/3rd tuple elements."""
    xy_dict = {"Home": _XY}
    pos = _pos_tuple(xy_dict, possession, ballstatus)
    meta = extract_metadata("dfl", (None, None, pos, None))
    assert meta["has_possession"] is exp_poss
    assert meta["has_ballstatus"] is exp_ball


def test_extract_metadata_adapted_dict_reports_team_keys():
    """C5: a flat dict position_data (Kinexon adapter shape) lists its keys."""
    position_data = {"Home": _XY, "Away": _XY}
    meta = extract_metadata("kinexon", (None, None, position_data, None))
    assert meta["teams"] == ["Home", "Away"]
    assert meta["temporal_divisions"] == ["fullMatch"]


def test_extract_metadata_falls_back_to_teamsheet_keys():
    """C6: with no team names from position data, teamsheet keys are used."""
    teamsheet = {"Home": _XY, "Away": _XY}
    # position_data carries no usable team names.
    meta = extract_metadata("opta", (None, None, None, teamsheet))
    assert meta["teams"] == ["Home", "Away"]


@pytest.mark.parametrize(
    "teams_in, expected",
    [
        (["Home", "Away", "Ball"], True),
        (["Home", "Away", "ball"], True),
        (["Home", "Away"], False),
    ],
)
def test_extract_metadata_has_ball_case_insensitive(teams_in, expected):
    """C7: has_ball is True iff a team name equals 'ball' case-insensitively."""
    xy_dict = {name: _XY for name in teams_in}
    meta = extract_metadata("dfl", (None, None, _pos_tuple(xy_dict), None))
    assert meta["has_ball"] is expected


def test_extract_metadata_format_type_and_num_halves():
    """C8: format_type echoes provider_key; num_halves counts divisions."""
    xy_dict = {"P1": {"Home": _XY}, "P2": {"Home": _XY}, "P3": {"Home": _XY}}
    meta = extract_metadata("my_provider", (None, None, _pos_tuple(xy_dict), None))
    assert meta["format_type"] == "my_provider"
    assert meta["num_halves"] == 3


# --------------------------------------------------------------------------- #
# available_matches                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "provider_key, expected_count, sample_keys",
    [
        ("eigd_h", 25, {"id", "match_id", "label"}),
        ("idsse", 7, {"id", "match_id", "label"}),
    ],
)
def test_available_matches_dispatches_to_hook(provider_key, expected_count, sample_keys):
    """C9: available_matches returns the provider's static catalogue verbatim.

    Real dataset providers expose ``list_matches`` hooks that return static
    catalogues without network, so the assertion is on entry count and the
    presence of the documented ``id``/``match_id``/``label`` keys.
    """
    entries = available_matches(provider_key)
    assert len(entries) == expected_count
    assert sample_keys <= set(entries[0])


@pytest.mark.parametrize("provider_key", ["nonexistent_provider", "dfl"])
def test_available_matches_no_hook_returns_empty(provider_key):
    """C10: an unknown provider or one lacking list_matches returns ``[]``.

    ``dfl`` is a real file provider with no ``list_matches`` hook, so it
    exercises the present-but-no-hook branch; the unknown key exercises the
    absent-descriptor branch. Both fail for the same reason (no hook).
    """
    assert available_matches(provider_key) == []


# --------------------------------------------------------------------------- #
# _adapt_kinexon                                                                #
# --------------------------------------------------------------------------- #


def test_adapt_kinexon_wraps_flat_dict_into_4tuple():
    """C11: the Kinexon adapter wraps positions/teamsheets into the 4-tuple."""
    xy = object()
    teamsheets = object()
    results = {"positions": xy, "teamsheets": teamsheets}
    pitch, events, position_data, ts = _adapt_kinexon(results, {}, {})
    assert pitch is None
    assert events is None
    assert position_data == (xy,)
    assert ts is teamsheets


# --------------------------------------------------------------------------- #
# _finalize_dataset_payload                                                     #
# --------------------------------------------------------------------------- #


def test_finalize_dataset_payload_bundles_data_and_metadata():
    """C12: the payload carries the raw tuple under ``data`` and computed metadata."""
    xy_dict = {"Home": _XY, "Away": _XY}
    raw = (None, None, _pos_tuple(xy_dict), None)
    payload = _finalize_dataset_payload("idsse", raw)
    assert payload["data"] is raw
    assert payload["metadata"]["teams"] == ["Home", "Away"]
    assert payload["metadata"]["format_type"] == "idsse"


# --------------------------------------------------------------------------- #
# load_provider_data (guard/dispatch decisions only)                           #
# --------------------------------------------------------------------------- #


def test_load_provider_data_unknown_key_returns_none():
    """C13: an unknown provider_key is swallowed by the boundary except -> None."""
    assert load_provider_data("no_such_provider", {}) is None


def test_load_provider_data_disabled_provider_returns_none(monkeypatch):
    """C14: a disabled descriptor returns None via the boundary except.

    No shipped provider sets ``disabled=True`` (only nested loader-function
    disables exist), so the disabled guard is reached only via a synthetic
    descriptor. The contract still protects direct/headless callers.
    """
    monkeypatch.setattr(
        ld,
        "IO_REGISTRY",
        {"broken": {"disabled": True, "disabled_reason": "x", "module": "m"}},
    )
    assert load_provider_data("broken", {}) is None


def test_load_provider_data_routes_dataset_before_disabled_guard(monkeypatch):
    """C15: dataset providers route to the dispatcher with match_id forwarded.

    The dataset branch precedes the disabled guard and import. Stubbing
    ``_load_dataset`` asserts the routing decision and the direct ``match_id``
    kwarg without touching the network or a real dataset class.
    """
    calls = {}

    def _fake_load_dataset(provider_key, *, on_progress, cancel_event, match_id):
        calls["provider_key"] = provider_key
        calls["match_id"] = match_id
        return "ROUTED"

    monkeypatch.setattr(ld, "_load_dataset", _fake_load_dataset)
    result = load_provider_data("idsse", {}, match_id="J03WMX")
    assert result == "ROUTED"
    assert calls == {"provider_key": "idsse", "match_id": "J03WMX"}
