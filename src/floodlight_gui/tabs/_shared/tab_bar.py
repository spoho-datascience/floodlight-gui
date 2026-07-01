"""Shared DPG tab_bar active-tag resolution helper.

Provides ``resolve_active_category`` and ``resolve_active_category_from_bar``:
the single authoritative resolver for the ``{prefix}_category_{cat}_tab`` tag
pattern used by the transforms and model tabs.

DPG carve-out: this module imports ``dearpygui`` at module scope because it
lives under ``tabs/`` (the DPG-aware layer); backend modules must not.

Fallback (cold-start default) is intentionally left to each caller. Each
tab's distinct starting category is preserved rather than baked in here.
"""

from __future__ import annotations

from collections.abc import Collection

import dearpygui.dearpygui as dpg  # carve-out: tabs/_*.py UI-tier helper

__all__ = ["resolve_active_category", "resolve_active_category_from_bar"]


def resolve_active_category(
    active,
    *,
    prefix: str,
    valid_categories: Collection[str],
) -> str | None:
    """Resolve a DPG tab_bar active-tag value to a category key.

    Parameters
    ----------
    active:
        The raw value from a DPG ``tab_bar`` - either the ``app_data`` argument
        from a ``tab_bar`` callback, or the result of ``dpg.get_value(bar_tag)``.
        DPG may return either a *str* tag (e.g. ``"transforms_category_filter_tab"``)
        or an *int* alias-id for the same tag. Both forms are handled.
    prefix:
        The tag-prefix used when constructing the tab tags. For the transforms
        tab this is ``"transforms"``; for the model tab this is ``"models"``.
        The resolved tab tag takes the form ``"{prefix}_category_{cat}_tab"``.
    valid_categories:
        Collection of valid category keys (e.g. ``_cat_ops``, ``_cat_models``).
        A parsed category is returned only when it is a member of this set.

    Returns
    -------
    str | None
        The category key (e.g. ``"filter"``, ``"kinematics"``) when resolution
        succeeds, or ``None`` when:

        - ``active`` is a str that does not match the expected tag pattern, or
        - the parsed category is not in ``valid_categories``, or
        - ``active`` is an int but no alias matches.

    Notes
    -----
    The ``SystemError``-swallowing in the alias-id branch is load-bearing:
    ``dpg.get_alias_id`` raises ``SystemError`` on unknown aliases in a live
    DPG context. The ``except SystemError: continue`` guard must not be removed.
    """
    category: str | None = None

    if (
        isinstance(active, str)
        and active.startswith(f"{prefix}_category_")
        and active.endswith("_tab")
    ):
        category = active[len(f"{prefix}_category_") : -len("_tab")]

    elif isinstance(active, int):
        for cat in valid_categories:
            try:
                if active == dpg.get_alias_id(f"{prefix}_category_{cat}_tab"):
                    category = cat
                    break
            except SystemError:
                continue

    if category is not None and category in valid_categories:
        return category
    return None


def resolve_active_category_from_bar(
    bar_tag: str,
    *,
    prefix: str,
    valid_categories: Collection[str],
) -> str | None:
    """Resolve a category by reading a DPG ``tab_bar``'s active value by tag.

    Convenience wrapper over :func:`resolve_active_category` that performs the
    ``does_item_exist`` guard and ``get_value`` read internally, so non-callback
    call sites avoid repeating that two-line sequence.

    Returns ``None`` when the bar does not exist yet, or the active tag is
    unrecognized. Callers apply their own cold-start fallback (e.g.
    ``resolve_active_category_from_bar(...) or DEFAULT_CATEGORY``).

    Parameters
    ----------
    bar_tag : str
        DPG tag of the ``tab_bar`` widget to read.
    prefix : str
        Tag-prefix forwarded to :func:`resolve_active_category`.
    valid_categories : Collection[str]
        Valid category keys forwarded to :func:`resolve_active_category`.

    Returns
    -------
    str | None
        The category key, or ``None`` when the bar is missing or the active
        tag cannot be resolved.

    Notes
    -----
    This function collapses "bar missing" and "tag unrecognized" into a single
    ``None``. Call sites that must distinguish the two (e.g. a ``get_active_op``
    helper that returns a cold-start default for an unrecognized tag but ``None``
    for a missing bar) should keep their own ``does_item_exist`` guard and call
    :func:`resolve_active_category` directly.
    """
    if not dpg.does_item_exist(bar_tag):
        return None
    return resolve_active_category(
        dpg.get_value(bar_tag), prefix=prefix, valid_categories=valid_categories
    )
