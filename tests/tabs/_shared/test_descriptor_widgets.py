"""Behavioral contracts for ``tabs/_shared/descriptor_widgets``.

Two public functions: ``resolve_tooltip`` (a 4-tier resolution chain over an
upstream docstring) and ``build_param_widget`` (registry descriptor -> one DPG
widget plus a tooltip). The DPG toolkit is the seam for ``build_param_widget``
and is replaced with the shared fake recorder; the upstream docstring is the
seam for ``resolve_tooltip`` and is driven by crafted callables.

Behavioral contracts guarded here
---------------------------------
resolve_tooltip
  C1  Tier 1: an explicit ``tooltip:`` field on the descriptor wins over
      every docstring tier.
  C2  Tier 2: a per-param docstring description is returned when no literal
      tooltip exists.
  C3  Tier 3: the first usable docstring line is returned when no per-param
      description matches.
  C4  Tier 4: ``"No description available"`` is returned when no upstream is
      reachable or nothing usable is found.
  C5  ``param_container`` routes Tier 2/3 to ``cls.fit`` for ``fit_params``
      and ``cls.__init__`` for ``init_params``.
  C6  When ``__init__`` carries only the inherited boilerplate, the
      class-level docstring is consulted for Tier 2 and Tier 3.
  C7  Tier 3 skips blank lines and RST/Sphinx ``:``-prefixed marker lines.

build_param_widget
  C8  Each descriptor ``type`` dispatches to its DPG widget kind.
  C9  Returns the widget tag: a deterministic ``{parent}__{param}`` by
      default, or the explicit ``tag`` override.
  C10 The widget label comes from PARAM_LABEL_MAP and falls back to the raw
      param name.
  C11 An ``advanced`` param is rendered inside a single shared, idempotent
      ``Advanced`` collapsing header.
  C12 ``tuple[float, float]`` renders two widgets under derived ``__lo`` /
      ``__hi`` tags and attaches the tooltip to the label widget.
  C13 A tooltip is created for the rendered widget using the resolved text.
  C14 The descriptor ``description`` field is never a tooltip source: a param
      carrying only ``description`` (no literal ``tooltip``) resolves its
      tooltip from the docstring tier / fallback, not the description text.
  C15 An enum with ``options=[None, "x", "y"]`` and ``default=None`` (the real
      kinematics ``axis`` shape) stringifies the ``None`` option and the
      ``None`` default to ``"None"`` in the combo.
  C16 A ``list[int]`` param with ``default=None`` (the real exclude_xIDs /
      xIDs shape) builds with an empty-string default_value.
"""

from __future__ import annotations

import inspect

import pytest

import floodlight_gui.tabs._shared.descriptor_widgets as dw
from floodlight_gui.core.help import _INIT_BOILERPLATE
from floodlight_gui.registry.transforms import PARAM_LABEL_MAP
from tests._dpg_stub import make_dpg_stub


@pytest.fixture
def dpg_stub(monkeypatch):
    """Install the shared fake-DPG recorder as the module's ``dpg`` binding.

    ``build_param_widget`` calls into ``dpg`` at module scope; redirecting
    that name to the recorder lets tests assert which widgets and tooltips
    were created without a live DPG context.
    """
    stub = make_dpg_stub()
    # The widget factory calls add_collapsing_header for advanced params; the
    # shared recorder only ships the container CM, so add the recorder here.
    stub.add_collapsing_header = lambda *a, **kw: (
        stub.existing_items.add(kw["tag"]) if kw.get("tag") else None,
        stub.calls.append(("add_collapsing_header", a, kw)),
    )[1]
    monkeypatch.setattr(dw, "dpg", stub)
    return stub


def _doc_callable(docstring):
    """Return a function carrying ``docstring`` for tier-resolution tests."""

    def _fn():
        pass

    _fn.__doc__ = docstring
    return _fn


# --------------------------------------------------------------------------- #
# resolve_tooltip                                                              #
# --------------------------------------------------------------------------- #


def test_tooltip_tier1_literal_overrides_docstring():
    """C1: an explicit tooltip field wins even when an upstream docstring exists."""
    upstream = _doc_callable("Parameters\n----------\norder\n    From the docstring.")
    result = dw.resolve_tooltip("order", {"tooltip": "Explicit override"}, upstream)
    assert result == "Explicit override"


def test_tooltip_tier2_per_param_description():
    """C2: a matching per-param docstring description is returned."""
    upstream = _doc_callable(
        "Summary line.\n\nParameters\n----------\norder\n    The filter order.\n"
    )
    result = dw.resolve_tooltip("order", {}, upstream)
    assert result == "The filter order."


def test_tooltip_tier3_first_usable_line():
    """C3: the first usable docstring line is returned when no param matches."""
    upstream = _doc_callable("A concise summary.\n\nMore detail below.")
    result = dw.resolve_tooltip("missing_param", {}, upstream)
    assert result == "A concise summary."


@pytest.mark.parametrize(
    "upstream",
    [None, _doc_callable("")],
)
def test_tooltip_tier4_ultimate_fallback(upstream):
    """C4: no reachable upstream or empty docstring yields the fixed fallback."""
    result = dw.resolve_tooltip("p", {}, upstream)
    assert result == "No description available"


@pytest.mark.parametrize(
    "container, fit_doc, init_doc, expected",
    [
        ("fit_params", "Fit doc summary.", "Init doc summary.", "Fit doc summary."),
        ("init_params", "Fit doc summary.", "Init doc summary.", "Init doc summary."),
    ],
)
def test_tooltip_param_container_routes_to_submethod(container, fit_doc, init_doc, expected):
    """C5: param_container selects cls.fit vs cls.__init__ for Tier 3 resolution."""

    class _Model:
        pass

    _Model.fit = _doc_callable(fit_doc)
    _Model.__init__ = _doc_callable(init_doc)
    result = dw.resolve_tooltip("p", {}, _Model, param_container=container)
    assert result == expected


def test_tooltip_init_boilerplate_falls_back_to_class_doc():
    """C6: a boilerplate __init__ defers to the class docstring for resolution."""

    class _Model:
        """Class summary line.

        Parameters
        ----------
        mesh
            The mesh kind.
        """

    # _Model defines no __init__, so cls.__init__ is the inherited object slot
    # whose docstring is the boilerplate signal the source detects.
    assert inspect.getdoc(_Model.__init__) == _INIT_BOILERPLATE
    # Per-param from the class Parameters block (Tier 2 via class fallback).
    assert dw.resolve_tooltip("mesh", {}, _Model, param_container="init_params") == "The mesh kind."
    # No per-param match falls through to the class first line (Tier 3 via class fallback).
    assert (
        dw.resolve_tooltip("absent", {}, _Model, param_container="init_params")
        == "Class summary line."
    )


def test_tooltip_tier3_skips_blank_and_marker_lines():
    """C7: Tier 3 skips leading blank and ``:``-prefixed RST marker lines."""
    upstream = _doc_callable("\n:rtype: int\nThe real first line.")
    result = dw.resolve_tooltip("missing", {}, upstream)
    assert result == "The real first line."


# --------------------------------------------------------------------------- #
# build_param_widget                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "descriptor, expected_widget",
    [
        ({"type": "int", "default": 3}, "add_input_int"),
        ({"type": "float", "default": 1.0}, "add_input_float"),
        ({"type": "enum", "options": ["a", "b"], "default": "a"}, "add_combo"),
        ({"type": "bool", "default": True}, "add_checkbox"),
        ({"type": "list[int]", "default": [1, 2]}, "add_input_text"),
        ({"type": "string", "default": "x"}, "add_input_text"),
        ({"type": "totally_unknown"}, "add_input_text"),
    ],
)
def test_build_widget_dispatches_on_type(dpg_stub, descriptor, expected_widget):
    """C8: the descriptor type dispatches to its DPG widget kind."""
    dw.build_param_widget("p", descriptor, "parent")
    assert dpg_stub.calls_of(expected_widget)


@pytest.mark.parametrize(
    "tag_override, expected",
    [
        ("", "parent__p"),
        ("custom_tag", "custom_tag"),
    ],
)
def test_build_widget_returns_tag(dpg_stub, tag_override, expected):
    """C9: the returned tag is the deterministic default or the explicit override."""
    result = dw.build_param_widget("p", {"type": "int"}, "parent", tag=tag_override)
    assert result == expected


@pytest.mark.parametrize(
    "param_name, expected_label",
    [
        (next(iter(PARAM_LABEL_MAP)), f"{PARAM_LABEL_MAP[next(iter(PARAM_LABEL_MAP))]}:"),
        ("zzz_unmapped", "zzz_unmapped:"),
    ],
)
def test_build_widget_label_from_map(dpg_stub, param_name, expected_label):
    """C10: a mapped param renders its friendly label; an unmapped one renders its raw name."""
    dw.build_param_widget(param_name, {"type": "int"}, "parent")
    label_text = dpg_stub.calls_of("add_text")[0][1][0]
    assert label_text == expected_label


def test_build_widget_advanced_header_idempotent(dpg_stub):
    """C11: advanced params share one idempotent ``Advanced`` collapsing header."""
    dw.build_param_widget("a", {"type": "int", "advanced": True}, "parent")
    dw.build_param_widget("b", {"type": "int", "advanced": True}, "parent")
    headers = dpg_stub.calls_of("add_collapsing_header")
    assert len(headers) == 1
    assert "parent__advanced_group" in dpg_stub.existing_items


def test_build_widget_tuple_renders_lo_hi(dpg_stub):
    """C12: tuple[float, float] creates __lo/__hi widgets and a label-anchored tooltip."""
    dw.build_param_widget("rng", {"type": "tuple[float, float]", "default": (0.0, 1.0)}, "parent")
    float_tags = [c[2].get("tag") for c in dpg_stub.calls_of("add_input_float")]
    assert "parent__rng__lo" in float_tags
    assert "parent__rng__hi" in float_tags
    # Tooltip parent is the label widget, since the canonical tag has no widget.
    tooltip_parents = [c[2].get("parent") for c in dpg_stub.calls_of("tooltip_enter")]
    assert "parent__rng__label" in tooltip_parents


def test_build_widget_attaches_tooltip(dpg_stub, monkeypatch):
    """C13: a tooltip is created carrying the resolved text for the widget."""
    monkeypatch.setattr(dw, "resolve_tooltip", lambda *a, **k: "RESOLVED TIP")
    dw.build_param_widget("p", {"type": "int"}, "parent")
    assert dpg_stub.calls_of("tooltip_enter")
    tooltip_texts = [c[1][0] for c in dpg_stub.calls_of("add_text")]
    assert "RESOLVED TIP" in tooltip_texts


def test_tooltip_description_is_never_a_source():
    """C14: a ``description`` field is ignored; the docstring tier wins instead.

    Real registry params commonly carry ``description`` (informational only).
    With an upstream docstring present and no literal ``tooltip``, the tooltip
    must resolve from the docstring, never echo the description text.
    """
    upstream = _doc_callable(
        "Summary line.\n\nParameters\n----------\norder\n    The filter order.\n"
    )
    descriptor = {"description": "INFORMATIONAL ONLY, NOT A TOOLTIP"}
    result = dw.resolve_tooltip("order", descriptor, upstream)
    assert result == "The filter order."
    assert "INFORMATIONAL" not in result


def test_tooltip_description_with_no_upstream_falls_back_not_to_description():
    """C14: with no upstream, a ``description``-only param still hits the fixed fallback."""
    result = dw.resolve_tooltip("order", {"description": "NOT A TOOLTIP"}, None)
    assert result == "No description available"


def test_build_widget_enum_none_option_and_default_stringified(dpg_stub):
    """C15: enum None option/default render as the literal string ``"None"`` in the combo.

    Mirrors the real kinematics ``axis`` param: ``options=[None, "x", "y"]``
    with ``default=None``.
    """
    dw.build_param_widget(
        "axis", {"type": "enum", "options": [None, "x", "y"], "default": None}, "parent"
    )
    combo_call = dpg_stub.calls_of("add_combo")[0]
    combo_kwargs = combo_call[2]
    assert combo_kwargs["items"] == ["None", "x", "y"]
    assert combo_kwargs["default_value"] == "None"


def test_build_widget_list_int_none_default_builds_empty(dpg_stub):
    """C16: a ``list[int]`` param defaulting to ``None`` builds with an empty default_value.

    Every real ``list[int]`` param (exclude_xIDs, xIDs) defaults to ``None``;
    no descriptor ships a populated list at widget-build time.
    """
    result = dw.build_param_widget("exclude_xIDs", {"type": "list[int]", "default": None}, "parent")
    text_call = dpg_stub.calls_of("add_input_text")[0]
    assert text_call[2]["default_value"] == ""
    assert result == "parent__exclude_xIDs"
