"""Generic nested results-panel engine for Dear PyGui.

A :class:`ResultsPanel` owns a four-level DPG tab hierarchy::

    outer tab_bar
      key tab          (one per result key, e.g. a model/metric name)
        period tab_bar
          period tab   (one per temporal division)
            team tab_bar
              leaf tab (one per team; its body is the actual result)

It factors out everything the Models and Metrics tabs used to hand-roll: the
tag scheme, the tag sanitizer, a full insertion-order rebuild, a no-flicker
incremental single-leaf refresh, a clear, and an "active leaf" walk that reads
which tab the user is currently viewing (so export buttons can follow the
view).

The panel renders nothing inside a leaf itself; the per-leaf body is always
delegated to the injected ``render_leaf`` callback. That keeps the engine
domain-agnostic: a leaf renderer is free to drop a single ``add_text``, a
paginated array view, or its own nested ``tab_bar`` (the Models multi-output
case) into the leaf parent.

Widget ownership
----------------
The OWNING tab must create, ABOVE the outer bar, three widgets with these
``prefix``-derived tags:

  - ``{prefix}_results_placeholder``: text shown when the panel is empty.
  - ``{prefix}_results_info``: the "N result(s) across M ..." summary
    line, hidden when empty.
  - ``{prefix}_results_outer_tab_bar``: the outer ``tab_bar`` the panel
    fills. (A ``tab_bar`` accepts only ``tab`` children, so the placeholder
    and info text deliberately live OUTSIDE it.)

The panel only ever mutates children of the outer bar and toggles the
visibility / value of the placeholder + info widgets. It does not create them.

Defensiveness
-------------
Every method no-ops safely if the outer bar tag does not exist yet (the panel
may be driven by an event that fires before the UI is built), and all
live-widget mutations are wrapped in ``contextlib.suppress(SystemError)`` to
swallow the DPG ``SystemError`` raised when a tag is concurrently deleted.

DPG carve-out: this module imports ``dearpygui`` at module scope because it
lives under ``tabs/`` (the DPG-aware layer); backend modules must not.
``import floodlight_gui`` stays DPG-free.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterable

import dearpygui.dearpygui as dpg

from floodlight_gui.core.periods import period_internal_to_display

__all__ = ["ResultsPanel"]

logger = logging.getLogger(__name__)


# Characters replaced by "_" when sanitizing a fragment into a DPG tag.
_CLEAN_CHARS = (" ", "-", "/", ".", "(", ")", "|", ",")


def _clean(s: object) -> str:
    """Lowercase *s* and replace tag-hostile characters with ``_``.

    Replaces space, ``-``, ``/``, ``.``, ``(``, ``)``, ``|``, and ``,``
    with ``_``. Accepts any object and coerces to str first.
    """
    out = str(s).lower()
    for ch in _CLEAN_CHARS:
        out = out.replace(ch, "_")
    return out


class ResultsPanel:
    """A nested ``key -> period -> team -> leaf`` DPG results panel.

    Parameters
    ----------
    prefix :
        Tag namespace, e.g. ``"model"`` or ``"metrics"``. Every DPG tag this
        panel reads or writes is derived from it, including the three widgets
        the owning tab must create (see module docstring).
    display_name :
        ``callable(key) -> str``: maps a result key to the human label shown
        on its outer tab.
    render_leaf :
        ``callable(key, period_internal, team, leaf_tag) -> None``: renders
        the leaf body into ``leaf_tag`` (a DPG ``tab`` item). Called once per
        leaf create and again on every ``refresh_leaf`` for that leaf (the
        panel deletes the leaf's existing children first). The callback owns
        everything inside the leaf, so it may build its own nested ``tab_bar``.
    noun :
        Singular noun for the info line ("model" / "metric" / …). Default
        ``"result"``.
    count_provider :
        Optional ``callable() -> (entries_count, key_count)``. When supplied,
        :meth:`refresh_leaf` uses it to recompute the info-line counts so the
        incremental path stays in sync without the caller threading
        ``entries_count`` / ``key_count`` through every call. The panel does
        not own the data, so it cannot recount on its own; this hook lets the
        owning tab expose its result-store size through one path shared by both
        rebuild and refresh. Explicit ``entries_count`` / ``key_count`` args to
        :meth:`refresh_leaf` still win over the provider.
    """

    def __init__(
        self,
        *,
        prefix: str,
        display_name: Callable[[object], str],
        render_leaf: Callable[[object, str, str, str], None],
        noun: str = "result",
        count_provider: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self.prefix = prefix
        self.display_name = display_name
        self.render_leaf = render_leaf
        self.noun = noun
        self.count_provider = count_provider

    # ------------------------------------------------------------------ #
    # Derived tag scheme (namespaced by prefix, fragments sanitized).
    # ------------------------------------------------------------------ #

    @property
    def outer_bar_tag(self) -> str:
        """Tag of the outer ``tab_bar`` (created by the owning tab)."""
        return f"{self.prefix}_results_outer_tab_bar"

    @property
    def placeholder_tag(self) -> str:
        """Tag of the empty-state placeholder text (owning tab creates it)."""
        return f"{self.prefix}_results_placeholder"

    @property
    def info_tag(self) -> str:
        """Tag of the summary info text (owning tab creates it)."""
        return f"{self.prefix}_results_info"

    def _key_tab_tag(self, key: object) -> str:
        """Return the DPG tag for the outer key tab of *key*."""
        return f"{self.prefix}_results_key_tab_{_clean(key)}"

    def _period_bar_tag(self, key: object) -> str:
        """Return the DPG tag for the period tab_bar nested inside *key*'s tab."""
        return f"{self.prefix}_results_period_tab_bar_{_clean(key)}"

    def _period_tab_tag(self, key: object, period: str) -> str:
        """Return the DPG tag for the period tab of (*key*, *period*)."""
        return f"{self.prefix}_results_period_tab_{_clean(key)}_{_clean(period)}"

    def _team_bar_tag(self, key: object, period: str) -> str:
        """Return the DPG tag for the team tab_bar nested inside (*key*, *period*)."""
        return f"{self.prefix}_results_team_tab_bar_{_clean(key)}_{_clean(period)}"

    def _leaf_tab_tag(self, key: object, period: str, team: str) -> str:
        """Return the DPG tag for the leaf tab of (*key*, *period*, *team*)."""
        return f"{self.prefix}_results_leaf_{_clean(key)}_{_clean(period)}_{_clean(team)}"

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def rebuild(self, entries: Iterable[tuple[object, str, str]]) -> None:
        """Tear down and rebuild the whole hierarchy from *entries*.

        *entries* is an iterable of ``(key, period_internal, team)`` triples
        (already normalized by the caller). Outer tabs preserve first-seen key
        order; within a key, periods then teams preserve first-seen order.

        No-ops if the outer bar does not exist. An empty *entries* shows the
        placeholder and hides the info line.
        """
        if not dpg.does_item_exist(self.outer_bar_tag):
            return

        # Flatten to ordered, de-duplicated structure: key -> period -> [team].
        grouped: dict[object, dict[str, list[str]]] = {}
        total = 0
        for key, period, team in entries:
            periods = grouped.setdefault(key, {})
            teams = periods.setdefault(period, [])
            if team not in teams:
                teams.append(team)
                total += 1

        # Wipe the outer bar's children, then rebuild from scratch.
        with contextlib.suppress(SystemError):
            dpg.delete_item(self.outer_bar_tag, children_only=True)

        if not grouped:
            self._show_empty()
            return

        for key, periods in grouped.items():
            self._ensure_key_tab(key)
            for period, teams in periods.items():
                self._ensure_period_tab(key, period)
                for team in teams:
                    self._ensure_leaf(key, period, team)

        self._show_summary(total, len(grouped))

    def refresh_leaf(
        self,
        key: object,
        period_internal: str,
        team: str,
        *,
        entries_count: int | None = None,
        key_count: int | None = None,
    ) -> None:
        """Incrementally create / replace a single leaf without flicker.

        Creates ONLY the layers (outer key tab, period bar, period tab, team
        bar, leaf) that are missing; leaves every existing tab untouched.
        Renders (or re-renders) just this leaf's body, then selects the leaf in
        each bar (outer -> period -> team) so the user lands on it.

        If both *entries_count* and *key_count* are supplied, updates the info
        line; otherwise the info line is left as-is (but is made visible and
        the placeholder hidden, since the panel is now non-empty).

        No-ops if the outer bar does not exist.
        """
        if not dpg.does_item_exist(self.outer_bar_tag):
            return

        key_tab = self._ensure_key_tab(key)
        period_tab = self._ensure_period_tab(key, period_internal)
        leaf_tab = self._ensure_leaf(key, period_internal, team, replace=True)

        # Auto-select outer -> period -> team so the user lands on the new leaf.
        with contextlib.suppress(SystemError):
            if key_tab is not None:
                dpg.set_value(self.outer_bar_tag, key_tab)
            if period_tab is not None:
                dpg.set_value(self._period_bar_tag(key), period_tab)
            if leaf_tab is not None:
                dpg.set_value(self._team_bar_tag(key, period_internal), leaf_tab)

        # Non-empty now: hide placeholder, show info. Counts come from explicit
        # args first, else the count_provider hook, else the info text is left
        # as-is (just made visible).
        if entries_count is None and key_count is None and self.count_provider is not None:
            with contextlib.suppress(Exception):
                entries_count, key_count = self.count_provider()
        if entries_count is not None and key_count is not None:
            self._show_summary(entries_count, key_count)
        else:
            self._set_visible(self.placeholder_tag, show=False)
            self._set_visible(self.info_tag, show=True)

    def clear(self) -> None:
        """Delete every child of the outer bar and show the empty state."""
        if not dpg.does_item_exist(self.outer_bar_tag):
            return
        with contextlib.suppress(SystemError):
            dpg.delete_item(self.outer_bar_tag, children_only=True)
        self._show_empty()

    def active_leaf(self) -> tuple[object, str, str] | None:
        """Return the ``(key, period_internal, team)`` the user is viewing.

        Walks outer -> period -> team bars; for each bar reads
        ``dpg.get_value(bar)`` (the active child tab's tag) and that child's
        ``user_data``. Returns ``None`` if the panel is uninitialised, any
        level is missing / unselected, or a level's ``user_data`` is not a
        ``str``.
        """
        if not dpg.does_item_exist(self.outer_bar_tag):
            return None

        key = self._active_user_data(self.outer_bar_tag)
        if key is None:
            return None

        period_bar = self._period_bar_tag(key)
        period = self._active_user_data(period_bar)
        if period is None:
            return None

        team_bar = self._team_bar_tag(key, period)
        team = self._active_user_data(team_bar)
        if team is None:
            return None

        return (key, period, team)

    # ------------------------------------------------------------------ #
    # Layer builders: each returns the (existing or created) child tag.
    # ------------------------------------------------------------------ #

    def _ensure_key_tab(self, key: object) -> str | None:
        """Create the outer key tab (+ its empty period bar) if missing."""
        tab_tag = self._key_tab_tag(key)
        if dpg.does_item_exist(tab_tag):
            return tab_tag
        with contextlib.suppress(SystemError):
            with dpg.tab(
                label=self.display_name(key),
                parent=self.outer_bar_tag,
                tag=tab_tag,
                user_data=key,
            ):
                dpg.add_tab_bar(tag=self._period_bar_tag(key))
            return tab_tag
        return None

    def _ensure_period_tab(self, key: object, period: str) -> str | None:
        """Create the period tab (+ its empty team bar) under *key* if missing."""
        # The owning key tab + period bar must exist first.
        self._ensure_key_tab(key)
        period_bar = self._period_bar_tag(key)
        if not dpg.does_item_exist(period_bar):
            return None
        tab_tag = self._period_tab_tag(key, period)
        if dpg.does_item_exist(tab_tag):
            return tab_tag
        with contextlib.suppress(SystemError):
            with dpg.tab(
                label=period_internal_to_display(period),
                parent=period_bar,
                tag=tab_tag,
                user_data=period,
            ):
                dpg.add_tab_bar(tag=self._team_bar_tag(key, period))
            return tab_tag
        return None

    def _ensure_leaf(
        self,
        key: object,
        period: str,
        team: str,
        *,
        replace: bool = False,
    ) -> str | None:
        """Create the leaf tab under (*key*, *period*) and render its body.

        When *replace* is True and the leaf already exists, its children are
        deleted and ``render_leaf`` is called again (re-render in place, no
        flicker on neighbours). When False, an existing leaf is left untouched.
        """
        self._ensure_period_tab(key, period)
        team_bar = self._team_bar_tag(key, period)
        if not dpg.does_item_exist(team_bar):
            return None
        leaf_tag = self._leaf_tab_tag(key, period, team)

        if dpg.does_item_exist(leaf_tag):
            if not replace:
                return leaf_tag
            # Re-render in place: clear the existing leaf body, re-delegate.
            with contextlib.suppress(SystemError):
                dpg.delete_item(leaf_tag, children_only=True)
            self._render_leaf_body(key, period, team, leaf_tag)
            return leaf_tag

        # Fresh leaf.
        with contextlib.suppress(SystemError):
            dpg.add_tab(
                label=team,
                parent=team_bar,
                tag=leaf_tag,
                user_data=team,
            )
        if dpg.does_item_exist(leaf_tag):
            self._render_leaf_body(key, period, team, leaf_tag)
            return leaf_tag
        return None

    def _render_leaf_body(self, key: object, period: str, team: str, leaf_tag: str) -> None:
        """Delegate the leaf body to ``render_leaf``, guarding the callback."""
        try:
            self.render_leaf(key, period, team, leaf_tag)
        except Exception:  # noqa: BLE001 -- leaf-renderer boundary; must not crash the panel
            logger.exception(
                "ResultsPanel(%s): render_leaf failed for key=%r period=%r team=%r",
                self.prefix,
                key,
                period,
                team,
            )

    # ------------------------------------------------------------------ #
    # Placeholder / info helpers
    # ------------------------------------------------------------------ #

    def _show_empty(self) -> None:
        """Show the placeholder; hide the info line."""
        self._set_visible(self.placeholder_tag, show=True)
        self._set_visible(self.info_tag, show=False)

    def _show_summary(self, entries_count: int, key_count: int) -> None:
        """Hide placeholder; show + set the info summary line."""
        self._set_visible(self.placeholder_tag, show=False)
        if dpg.does_item_exist(self.info_tag):
            with contextlib.suppress(SystemError):
                dpg.set_value(
                    self.info_tag,
                    f"{entries_count} result(s) across "
                    f"{key_count} {self.noun}(s). Select a tab to inspect.",
                )
                dpg.configure_item(self.info_tag, show=True)

    def _set_visible(self, tag: str, *, show: bool) -> None:
        """Toggle a widget's visibility if it exists (swallows SystemError)."""
        if dpg.does_item_exist(tag):
            with contextlib.suppress(SystemError):
                dpg.configure_item(tag, show=show)

    # ------------------------------------------------------------------ #
    # active_leaf helper
    # ------------------------------------------------------------------ #

    def _active_user_data(self, bar_tag: str) -> str | None:
        """Return the active child's ``user_data`` for *bar_tag*, or None.

        Returns None if the bar is missing, nothing is selected, the active
        child no longer exists, or its ``user_data`` is not a ``str``.
        """
        if not dpg.does_item_exist(bar_tag):
            return None
        active_child = None
        with contextlib.suppress(SystemError):
            active_child = dpg.get_value(bar_tag)
        if not active_child:
            return None
        if not dpg.does_item_exist(active_child):
            return None
        user_data = None
        with contextlib.suppress(SystemError):
            user_data = dpg.get_item_user_data(active_child)
        if not isinstance(user_data, str):
            return None
        return user_data
