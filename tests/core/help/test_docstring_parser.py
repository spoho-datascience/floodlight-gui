"""Behavioral contracts for ``floodlight_gui.core.help.docstring_parser``.

This module is a pure docstring text parser with no GUI or floodlight
coupling. It is driven entirely by crafted docstring strings (and a callable
whose ``__doc__`` is one), so there is no collaborator to stub: the inputs
are the seam. Tests assert the extracted text, never the source structure.

Behavioral contracts guarded here
---------------------------------
parse_param_docstring
  C1  Extracts a named param's first description line across all three
      supported styles (Sphinx ``:param:``, Google ``Args:``, numpy/RST
      ``Parameters``), whether the docstring arrives as a raw string or via
      a callable's ``__doc__``.
  C2  Returns None when there is nothing to parse: a None target, a blank
      string, or a callable with no docstring.
  C3  Returns None when the requested param is absent from the docstring.
  C4  Returns None when ``param_name`` is empty, even with a valid docstring.
  C5  A numpy multi-line description is truncated to its first sentence so
      continuation lines do not bleed into the tooltip.
  C6  Style precedence: a Sphinx ``:param:`` match wins over a Google match
      for the same name (defensive tie-breaker; real floodlight docstrings
      use exactly one style).

parse_full_docstring
  C7  None or empty input returns the empty-shape dict carrying all seven
      keys (never a partial shape).
  C8  A well-formed numpy docstring populates short/long descriptions, the
      params list, and the Returns / Notes / References section keys.
  C9  Never raises on malformed input; returns the dict shape regardless.
"""

from __future__ import annotations

import pytest

from floodlight_gui.core.help.docstring_parser import (
    parse_full_docstring,
    parse_param_docstring,
)

# --------------------------------------------------------------------------- #
# Crafted docstrings, one per supported style                                   #
# --------------------------------------------------------------------------- #

_SPHINX_DOC = """Short summary.

:param order: The Butterworth filter order.
:param Wn: Critical lowpass frequency.
"""

_GOOGLE_DOC = """Short summary.

Args:
    order: The Butterworth filter order.
    Wn: Critical lowpass frequency.
"""

_NUMPY_DOC = """Short summary.

Parameters
----------
order : int
    The Butterworth filter order.
Wn : float
    Critical lowpass frequency.
"""


def _callable_with_doc(doc: str):
    """Return a function whose ``__doc__`` is ``doc``.

    Exercises the callable branch of ``parse_param_docstring`` (which routes
    through ``inspect.getdoc``) as opposed to the raw-string branch.
    """

    def _fn():
        pass

    _fn.__doc__ = doc
    return _fn


# --------------------------------------------------------------------------- #
# parse_param_docstring                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("doc", [_SPHINX_DOC, _GOOGLE_DOC, _NUMPY_DOC])
@pytest.mark.parametrize("as_callable", [False, True])
def test_param_extracted_across_styles_and_input_kinds(doc, as_callable):
    """C1: a param description is extracted from every style, string or callable."""
    target = _callable_with_doc(doc) if as_callable else doc
    assert parse_param_docstring(target, "order") == "The Butterworth filter order."


@pytest.mark.parametrize(
    "target",
    [
        None,  # no target
        "",  # blank string
        "   \n  ",  # whitespace-only string
        _callable_with_doc(None),  # callable with no docstring
    ],
)
def test_param_returns_none_when_nothing_to_parse(target):
    """C2: a None / blank target or an undocumented callable yields None."""
    assert parse_param_docstring(target, "order") is None


@pytest.mark.parametrize("doc", [_SPHINX_DOC, _GOOGLE_DOC, _NUMPY_DOC])
def test_param_unknown_name_returns_none(doc):
    """C3: a param absent from the docstring resolves to None in every style."""
    assert parse_param_docstring(doc, "nonexistent") is None


def test_param_empty_name_returns_none():
    """C4: an empty param_name returns None even with a valid docstring."""
    assert parse_param_docstring(_NUMPY_DOC, "") is None


def test_numpy_multiline_description_truncated_to_first_sentence():
    """C5: a numpy description spanning lines is cut at the first sentence."""
    doc = """Summary.

Parameters
----------
order : int
    First sentence. Second sentence that must be dropped.
"""
    assert parse_param_docstring(doc, "order") == "First sentence."


def test_sphinx_style_wins_over_google_for_same_param():
    """C6: when both Sphinx and Google describe a param, the Sphinx text wins.

    Defensive tie-breaker. Real floodlight docstrings use a single style, so
    this precedence never fires in production; it is guarded so a future
    reorder of the style chain is caught.
    """
    doc = """Summary.

:param order: sphinx description.

Args:
    order: google description.
"""
    assert parse_param_docstring(doc, "order") == "sphinx description."


# --------------------------------------------------------------------------- #
# parse_full_docstring                                                          #
# --------------------------------------------------------------------------- #

_FULL_SHAPE_KEYS = {
    "short_description",
    "long_description",
    "params",
    "returns",
    "examples",
    "notes",
    "references",
}


@pytest.mark.parametrize("text", [None, ""])
def test_full_empty_input_returns_full_empty_shape(text):
    """C7: None / empty input returns the empty shape with all seven keys."""
    result = parse_full_docstring(text)
    assert set(result) == _FULL_SHAPE_KEYS
    assert result["short_description"] == ""
    assert result["params"] == []
    assert result["returns"] is None


def test_full_well_formed_numpy_populates_sections():
    """C8: a complete numpy docstring fills descriptions, params, and sections."""
    doc = """Compute a thing.

A longer paragraph describing the thing in more detail.

Parameters
----------
order : int
    The order.

Returns
-------
PlayerProperty
    The computed property.

Notes
-----
A note about the computation.

References
----------
.. [1] Some reference, 1991.
"""
    result = parse_full_docstring(doc)
    assert result["short_description"] == "Compute a thing."
    assert "longer paragraph" in result["long_description"]
    assert result["returns"] is not None
    assert result["notes"] is not None
    assert result["references"] is not None
    assert len(result["params"]) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Parameters\n----------\n:::malformed:::",
        "::::",
        "Returns\n----\n",
        "   \t   ",
    ],
)
def test_full_never_raises_on_malformed(text):
    """C9: malformed input returns the dict shape instead of raising."""
    result = parse_full_docstring(text)
    assert set(result) == _FULL_SHAPE_KEYS
