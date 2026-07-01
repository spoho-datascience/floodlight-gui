"""Registry-descriptor to DPG widget factory for the shared tab layer.

Converts registry param descriptors into DPG input widgets and resolves
hover tooltips from upstream floodlight docstrings (thin-frontend principle).

Tooltip resolution follows a 4-tier chain (see ``resolve_tooltip``). The
``description:`` field on registry params is informational only and is never
consulted as a tooltip tier; authors who want tooltips must author a literal
``tooltip:`` field or rely on the upstream docstring.

For class-based upstreams, ``param_container`` selects the sub-method to
introspect (``fit_params`` -> ``cls.fit``, ``init_params`` -> ``cls.__init__``).
When ``__init__`` carries the inherited ``object.__init__`` boilerplate the
class-level docstring is consulted as a fallback, covering the floodlight 1.2
pattern where the constructor's ``Parameters`` block appears on the class
(e.g. ``DiscreteVoronoiModel``).

DPG constraint: the tooltip parent widget must exist before
``dpg.tooltip(parent=widget_tag)`` opens. All factory helpers create the
widget first, then open the tooltip context manager.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import dearpygui.dearpygui as dpg

from floodlight_gui.core.help import _INIT_BOILERPLATE
from floodlight_gui.core.help.docstring_parser import parse_param_docstring
from floodlight_gui.registry.transforms import PARAM_LABEL_MAP

__all__ = ["build_param_widget", "resolve_tooltip"]


_NO_DESCRIPTION = "No description available"


def _resolve_target(upstream_callable: Callable | None, param_container: str) -> Callable | None:
    """Return the sub-callable whose docstring should be inspected for ``param_container``.

    Parameters
    ----------
    upstream_callable : callable or None
        The resolved upstream class or function.
    param_container : str
        One of ``"fit_params"``, ``"init_params"``, or any other value.
        ``"fit_params"`` routes to ``cls.fit``; ``"init_params"`` routes to
        ``cls.__init__``; any other value returns ``upstream_callable`` as-is.

    Returns
    -------
    callable or None
        The sub-callable to inspect, or ``None`` when ``upstream_callable`` is ``None``.
    """
    if upstream_callable is None:
        return None
    if param_container == "fit_params":
        return getattr(upstream_callable, "fit", upstream_callable)
    if param_container == "init_params":
        return getattr(upstream_callable, "__init__", upstream_callable)
    return upstream_callable


def _tier3_first_line(target: Callable) -> str | None:
    """Return the first usable line of ``inspect.getdoc(target)`` for Tier 3 tooltip resolution.

    A usable line is non-blank and does not start with ``":"`` (RST/Sphinx markers
    are skipped). Returns ``None`` when no such line exists or the docstring is the
    inherited ``object.__init__`` boilerplate.

    Parameters
    ----------
    target : callable
        The callable whose docstring to inspect.

    Returns
    -------
    str or None
        First usable docstring line, or ``None`` when nothing suitable is found.
    """
    doc = inspect.getdoc(target)
    if not doc:
        return None
    if doc == _INIT_BOILERPLATE:
        # Inherited __init__ with no real content; let the caller fall back to the class docstring.
        return None
    for line in doc.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(":"):  # skip RST/Sphinx markers
            continue
        return stripped
    return None


def resolve_tooltip(
    param_name: str,
    param_descriptor: dict,
    upstream_callable: Callable | None = None,
    param_container: str = "",
) -> str:
    """Resolve a hover tooltip for a registry param using a fixed 4-tier chain.

    Tooltips derive from the upstream floodlight docstring (thin-frontend principle);
    the literal ``tooltip:`` field is reserved for cases where no upstream is
    reachable or the resolved docstring is uninformative.

    Tier 1: literal ``tooltip:`` field on the descriptor (explicit override).
    Tier 2: ``parse_param_docstring(resolved_target, param_name)`` - per-param
            description from reST / NumPy / Google / Sphinx style docstrings.
    Tier 3: ``inspect.getdoc(resolved_target)`` first non-blank, non-``:``-prefixed
            line (legacy fallback).
    Tier 4: ``"No description available"`` (ultimate fallback).

    The ``description:`` field on registry params is NOT a tier and is NEVER
    consulted here. Adding it as a tier would bypass the upstream docstring and
    violate the thin-frontend principle.

    For class-based upstreams, ``param_container`` selects the sub-method for Tier 2
    and Tier 3: ``"fit_params"`` routes to ``cls.fit``, ``"init_params"`` routes to
    ``cls.__init__``, all other values use the upstream class as-is.

    When ``param_container == "init_params"`` and ``__init__`` carries the inherited
    ``object.__init__`` boilerplate, the class-level docstring is also consulted for
    Tier 2 and Tier 3. This covers the floodlight 1.2 pattern where the class
    docstring's ``Parameters`` block documents the constructor.

    Parameters
    ----------
    param_name : str
        The param's key in the registry descriptor; passed to
        ``parse_param_docstring`` for per-param extraction.
    param_descriptor : dict
        The registry descriptor dict for the param.
    upstream_callable : callable or None
        The resolved upstream class, function, or method (or ``None`` when
        no upstream is available).
    param_container : str
        Selects which sub-callable to introspect when ``upstream_callable`` is a
        class. One of ``"fit_params"``, ``"init_params"``, ``"file_inputs"``,
        ``"inputs"``, ``"params"``, or ``""``.

    Returns
    -------
    str
        The resolved tooltip text; never empty.
    """
    # Tier 1 -- explicit override
    tooltip_value = param_descriptor.get("tooltip")
    if tooltip_value:
        return tooltip_value

    # Resolve the sub-method once; shared by Tier 2 and Tier 3.
    target: Callable | None = _resolve_target(upstream_callable, param_container)

    if target is not None:
        # Tier 2 -- per-param docstring extraction
        per_param = parse_param_docstring(target, param_name)
        if per_param:
            return per_param

        # When __init__ carries only the boilerplate, also try the class docstring
        # for Tier 2 and Tier 3 before giving up.
        if (
            param_container == "init_params"
            and inspect.getdoc(target) == _INIT_BOILERPLATE
            and upstream_callable is not None
            and upstream_callable is not target
        ):
            class_per_param = parse_param_docstring(upstream_callable, param_name)
            if class_per_param:
                return class_per_param
            class_tier3 = _tier3_first_line(upstream_callable)
            if class_tier3:
                return class_tier3

        # Tier 3 -- first usable line of the resolved docstring
        tier3 = _tier3_first_line(target)
        if tier3:
            return tier3

    # Tier 4 -- ultimate fallback
    return _NO_DESCRIPTION


def _widget_tag(parent_tag: str, param_name: str, override: str = "") -> str:
    """Return a deterministic widget tag, or the caller's explicit override."""
    return override or f"{parent_tag}__{param_name}"


def build_param_widget(
    param_name: str,
    param_descriptor: dict,
    parent_tag: str,
    upstream_callable: Callable | None = None,
    tag: str = "",
    param_container: str = "",
) -> str:
    """Render a registry-descriptor param as the appropriate DPG widget.

    Dispatches on ``param_descriptor["type"]`` to create one DPG input widget
    inside ``parent_tag``. A tooltip is attached after widget creation (DPG
    requires the parent to exist first). When ``param_descriptor["advanced"]``
    is ``True``, the widget is placed inside a closed-by-default
    ``Advanced`` collapsing header; two advanced params under the same parent
    share the same header (idempotent via ``dpg.does_item_exist``).

    Widget dispatch:

    - ``"int"``                  -> ``dpg.add_input_int``
    - ``"float"``                -> ``dpg.add_input_float``
    - ``"enum"``                 -> ``dpg.add_combo``
    - ``"bool"``                 -> ``dpg.add_checkbox``
    - ``"list[int]"``            -> ``dpg.add_input_text`` (CSV string; caller parses)
    - ``"tuple[float, float]"``  -> two ``dpg.add_input_float`` widgets with
                                    derived tags ``{widget_tag}__lo`` /
                                    ``{widget_tag}__hi``; callers read via
                                    ``dpg.get_value`` on each. The canonical
                                    ``widget_tag`` is NOT bound to a DPG widget
                                    for this type; the tooltip attaches to the
                                    label widget instead.
    - ``"string"`` / unknown     -> ``dpg.add_input_text`` (safest fallback)

    Parameters
    ----------
    param_name : str
        The param's key in the registry descriptor.
    param_descriptor : dict
        The registry descriptor dict for the param (must contain ``"type"``
        and optionally ``"default"``, ``"options"``, ``"advanced"``,
        ``"tooltip"``).
    parent_tag : str
        DPG container tag the widget is rendered into.
    upstream_callable : callable or None
        Passed to ``resolve_tooltip`` for docstring-based tooltip resolution.
    tag : str
        Explicit widget tag override; when empty, a deterministic tag is derived
        from ``parent_tag`` and ``param_name``.
    param_container : str
        Passed to ``resolve_tooltip`` to select the correct sub-method for
        class-based upstreams.

    Returns
    -------
    str
        The widget tag, so callers can later call ``dpg.get_value`` /
        ``dpg.set_value`` / ``dpg.configure_item`` on the rendered widget.
    """
    ptype = param_descriptor.get("type", "string")
    # Labels derive from PARAM_LABEL_MAP (single source of truth); per-descriptor
    # ``label:`` fields must not be re-introduced. See tests/test_param_label_derivation.py.
    label = PARAM_LABEL_MAP.get(param_name, param_name)
    default = param_descriptor.get("default")
    widget_tag = _widget_tag(parent_tag, param_name, override=tag)

    # Advanced params go inside a closed-by-default collapsing header.
    # Two advanced params under the same parent share the same header (idempotent).
    is_advanced = bool(param_descriptor.get("advanced", False))
    if is_advanced:
        advanced_tag = f"{parent_tag}__advanced_group"
        if not dpg.does_item_exist(advanced_tag):
            dpg.add_collapsing_header(
                label="Advanced",
                default_open=False,
                tag=advanced_tag,
                parent=parent_tag,
            )
        effective_parent = advanced_tag
    else:
        effective_parent = parent_tag

    # Render label + value widget in one horizontal group.
    # Widget is created before the tooltip context manager (DPG requires parent to exist first).
    with dpg.group(parent=effective_parent, horizontal=True):
        dpg.add_text(f"{label}:", tag=f"{widget_tag}__label")

        if ptype == "int":
            dpg.add_input_int(
                default_value=int(default) if default is not None else 0,
                tag=widget_tag,
                width=150,
            )
        elif ptype == "float":
            dpg.add_input_float(
                default_value=float(default) if default is not None else 0.0,
                tag=widget_tag,
                width=150,
            )
        elif ptype == "enum":
            options = param_descriptor.get("options", [])
            # Stringify options for DPG (None -> "None")
            str_options = [str(o) if o is not None else "None" for o in options]
            default_str = str(default) if default is not None else "None"
            dpg.add_combo(
                items=str_options,
                default_value=default_str,
                tag=widget_tag,
                width=150,
            )
        elif ptype == "bool":
            dpg.add_checkbox(
                default_value=bool(default) if default is not None else False,
                tag=widget_tag,
            )
        elif ptype == "list[int]":
            csv = ",".join(str(i) for i in default) if default else ""
            dpg.add_input_text(
                default_value=csv,
                tag=widget_tag,
                width=200,
                hint="e.g., 1,2,3",
            )
        elif ptype == "tuple[float, float]":
            # Render a numeric pair as two side-by-side input_float widgets.
            # Derived tags ``__lo`` and ``__hi``; callers read via ``dpg.get_value`` on each.
            # The canonical widget_tag is NOT bound to a DPG widget for this type;
            # the tooltip attaches to the label widget so the parent-must-exist rule still holds.
            if default is not None and hasattr(default, "__len__") and len(default) >= 2:
                lo_default = float(default[0])
                hi_default = float(default[1])
            else:
                lo_default = 0.0
                hi_default = 0.0
            with dpg.group(horizontal=True):
                dpg.add_input_float(
                    default_value=lo_default,
                    tag=f"{widget_tag}__lo",
                    width=90,
                )
                dpg.add_text("to")
                dpg.add_input_float(
                    default_value=hi_default,
                    tag=f"{widget_tag}__hi",
                    width=90,
                )
        else:  # "string" + unknown fallback
            dpg.add_input_text(
                default_value=str(default) if default is not None else "",
                tag=widget_tag,
                width=200,
            )

    # Attach the tooltip after the widget exists (DPG constraint: parent must exist first).
    # ``param_container`` is forwarded so Tier 2 resolution picks the correct sub-method.
    tooltip_text = resolve_tooltip(
        param_name,
        param_descriptor,
        upstream_callable,
        param_container=param_container,
    )
    # For ``tuple[float, float]``, the canonical widget_tag has no DPG widget;
    # attach the tooltip to the label widget to satisfy the parent-must-exist rule.
    tooltip_parent = f"{widget_tag}__label" if ptype == "tuple[float, float]" else widget_tag
    with dpg.tooltip(parent=tooltip_parent):
        dpg.add_text(tooltip_text, wrap=300)

    return widget_tag
