"""Model tab select layer: model resolution, period/team slice, player selection, output selection.

DPG-aware (imports dearpygui at module scope). Backend modules must not import this module.
"""

from __future__ import annotations

import contextlib
import logging

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.core.periods import period_internal_to_display
from floodlight_gui.registry.models import MODEL_REGISTRY
from floodlight_gui.tabs._shared.broadcast import ALL_SENTINEL, bridge_period_to_internal
from floodlight_gui.tabs._shared.help_popup import render_help_button
from floodlight_gui.tabs._shared.selectors import period_team_selector
from floodlight_gui.tabs._shared.tab_bar import resolve_active_category_from_bar
from floodlight_gui.tabs.model import labels, state
from floodlight_gui.theme import INFO

logger = logging.getLogger(__name__)

# DPG tags for the category tab_bar and output checkbox container.
CATEGORY_BAR = "models_category_tab_bar"
OUTPUTS_CONTAINER = "models_outputs_container"

# Stable parent the period/team selector is (re)mounted into for arity-aware re-renders.
SELECTOR_PARENT = "model_selector_container"
SUMMARY = "model_selection_summary"
PLAYER_SCROLL = "model_player_scroll_window"
# Per-team player selection: one tab per team (Home / Away / Ball), each listing
# that team's players as checkboxes. Export reads each team's own ticks independently,
# so distinct per-team subsets are supported.
PLAYER_TAB_BAR = "model_player_team_tab_bar"

# team_count currently mounted in the period/team selector (1 or the model's arity).
_mounted_team_count = 1


# --------------------------------------------------------------------------- #
# Active-model resolution
# --------------------------------------------------------------------------- #


def active_category() -> str:
    """Return the currently active model category key, falling back to the default."""
    cat = resolve_active_category_from_bar(
        CATEGORY_BAR, prefix="models", valid_categories=labels.CATEGORY_ORDER
    )
    return cat or labels.DEFAULT_CATEGORY


def active_model_key() -> str | None:
    """Return the active MODEL_REGISTRY key for the current category, or None if unset."""
    return state.current_model_by_category.get(active_category())


# --------------------------------------------------------------------------- #
# Model change orchestration
# --------------------------------------------------------------------------- #


def on_model_change(category: str | None = None) -> None:
    """React to a category or model change: sync the current model, help, outputs, and params.

    Reads the picked display name from the category combo, updates the per-category
    current-model map in ``state``, then rebuilds the help button, the outputs
    checklist, and the params container.

    Parameters
    ----------
    category : str or None
        Category key to update. Defaults to the currently active category when None.

    Notes
    -----
    Writes ``state.current_model_by_category[category]``.
    Rebuilds DPG widgets owned by the help group, ``OUTPUTS_CONTAINER``, and the
    params container.
    """
    from floodlight_gui.tabs.model import params as _params

    cat = category or active_category()
    combo_tag = labels.model_combo_tag(cat)
    display = dpg.get_value(combo_tag) if dpg.does_item_exist(combo_tag) else ""
    model_key = labels.key_for_display(display)
    if model_key is None:
        names = labels.display_names_in_category(cat)
        model_key = labels.key_for_display(names[0]) if names else None
    if model_key is None:
        return
    state.current_model_by_category[cat] = model_key
    _rebuild_help(cat, model_key)
    rebuild_outputs(model_key)
    _params.rebuild_params(model_key)


def _rebuild_help(category: str, model_key: str) -> None:
    """Replace the help button in the category's help group with one for *model_key*."""
    group = labels.help_group_tag(category)
    if not dpg.does_item_exist(group):
        return
    with contextlib.suppress(SystemError):
        dpg.delete_item(group, children_only=True)
    with contextlib.suppress(SystemError):
        render_help_button(
            model_key,
            MODEL_REGISTRY[model_key],
            "MODELS",
            tag_prefix="models",
            parent=group,
        )


def rebuild_outputs(model_key: str) -> None:
    """Rebuild output checkboxes for *model_key* inside ``OUTPUTS_CONTAINER``.

    Parameters
    ----------
    model_key : str
        Key into ``MODEL_REGISTRY``.

    Notes
    -----
    Checkbox state persists in ``state.output_checked[(model_key, out_key)]``
    (default True on first encounter). Toggling a checkbox re-renders only the
    affected fitted leaves and emits ``Events.MODEL_OUTPUTS_CHANGED``.
    Writes ``state.output_checked``.
    """
    if not dpg.does_item_exist(OUTPUTS_CONTAINER):
        return
    with contextlib.suppress(SystemError):
        dpg.delete_item(OUTPUTS_CONTAINER, children_only=True)
    outputs = MODEL_REGISTRY[model_key].get("outputs", {})
    if not outputs:
        dpg.add_text("This model has no queryable outputs.", parent=OUTPUTS_CONTAINER, color=INFO)
        return
    for out_key, out_desc in outputs.items():
        ckey = (model_key, out_key)
        if ckey not in state.output_checked:
            state.output_checked[ckey] = True
        dpg.add_checkbox(
            label=out_desc.get("label", out_key),
            default_value=state.output_checked[ckey],
            tag=f"models_output_check_{model_key}_{out_key}",
            parent=OUTPUTS_CONTAINER,
            callback=_on_output_toggle,
            user_data=ckey,
        )


def _on_output_toggle(sender, app_data, user_data) -> None:  # noqa: ARG001 -- DPG cb
    """Output checkbox toggle callback: update state and refresh the results view (DPG callback)."""
    from floodlight_gui.tabs.model import results

    try:
        model_key, out_key = user_data
        state.output_checked[(model_key, out_key)] = bool(app_data)
        # Re-render only the leaves for this model; the active model_key equals
        # the toggled model_key at this point.
        results.refresh_model_leaves(model_key)
        with contextlib.suppress(Exception):
            bus.emit(Events.MODEL_OUTPUTS_CHANGED, model_key=model_key)
    except Exception:  # noqa: BLE001 -- DPG callback boundary
        logger.exception("model: output toggle failed")


# --------------------------------------------------------------------------- #
# Period/team selector (Step 1)
# --------------------------------------------------------------------------- #


def mount(on_change) -> None:
    """Mount a single-team period/team selector into ``SELECTOR_PARENT``.

    Parameters
    ----------
    on_change : callable
        Callback invoked by the selector whenever the period or team combo changes.

    Notes
    -----
    Resets ``_mounted_team_count`` to 1.
    """
    global _mounted_team_count
    _mounted_team_count = 1
    period_team_selector(SELECTOR_PARENT, on_change, on_change, tag_prefix="model", team_count=1)


def ensure_arity(model_key: str, on_change) -> None:
    """Re-render the period/team selector when the model's arity differs from the mounted count.

    Only an arity change triggers a teardown; re-rendering on every model change
    would reset the user's period/team pick unnecessarily.

    Parameters
    ----------
    model_key : str
        Key into ``MODEL_REGISTRY`` (used to read ``fit_xy_arity``).
    on_change : callable
        Callback wired into the replacement selector.

    Notes
    -----
    Updates ``_mounted_team_count`` when the selector is rebuilt.
    """
    global _mounted_team_count
    arity = int(MODEL_REGISTRY.get(model_key, {}).get("fit_xy_arity", 1))
    if arity == _mounted_team_count:
        return
    if dpg.does_item_exist(SELECTOR_PARENT):
        with contextlib.suppress(SystemError):
            dpg.delete_item(SELECTOR_PARENT, children_only=True)
    _mounted_team_count = arity
    period_team_selector(
        SELECTOR_PARENT, on_change, on_change, tag_prefix="model", team_count=arity
    )
    refresh(on_change)


def refresh(on_change) -> None:
    """Repopulate all period/team selector combos from loaded data.

    Called on DATA_LOADED and after re-mounting the selector.

    Parameters
    ----------
    on_change : callable
        Invoked once after all combos are updated to propagate the new selection.

    Notes
    -----
    Period combo: display-form items, "All" prepended, default "All".
    Single-team combo: team names, "All" prepended, default "All".
    Multi-team combos (_a/_b/...): team names without "All"; each slot defaults
    to a distinct team so a multi-team fit does not use the same team for every
    slot (fitting a team against itself produces invalid overlap results).
    """
    app = state.app_instance
    if app is None:
        return
    divisions = list(app.get_temporal_divisions() or [])
    teams = list(app.get_team_names() or [])
    period_items = [ALL_SENTINEL] + [period_internal_to_display(p) for p in divisions]

    if dpg.does_item_exist("model_period_combo"):
        dpg.configure_item("model_period_combo", items=period_items, default_value=ALL_SENTINEL)
    # Single-team combo (team_count == 1).
    if dpg.does_item_exist("model_team_combo"):
        dpg.configure_item(
            "model_team_combo", items=[ALL_SENTINEL] + teams, default_value=ALL_SENTINEL
        )
    # Multi-team combos: no "All". Default each slot to a distinct team
    # (slot i -> teams[i % len(teams)]) so the out-of-the-box fit uses different
    # teams per slot. Fitting a team against itself produces invalid overlap results
    # for models that compare two XY inputs.
    for i, tag in enumerate(_multi_team_tags()):
        if dpg.does_item_exist(tag):
            dpg.configure_item(
                tag,
                items=teams,
                default_value=(teams[i % len(teams)] if teams else ""),
            )
    on_change()


def _multi_team_tags() -> list[str]:
    """Return the DPG tags of all mounted multi-team combo widgets, in slot order."""
    tags: list[str] = []
    for i in range(26):
        tag = f"model_team_combo_{chr(ord('a') + i)}"
        if dpg.does_item_exist(tag):
            tags.append(tag)
        elif tags:
            break
    return tags


# --------------------------------------------------------------------------- #
# Selection change: summary + cached internal period + player checkboxes
# --------------------------------------------------------------------------- #


def on_selection_change() -> None:
    """React to a period/team combo change: update the cached internal period and the summary label.

    Notes
    -----
    Writes ``state.selected_period_internal``.
    Does not rebuild the player tabs; those are built on DATA_LOADED and must
    survive period/team navigation without losing per-team checkbox ticks.
    """
    period = _combo("model_period_combo")
    team = _combo("model_team_combo") or (
        _combo(_multi_team_tags()[0]) if _multi_team_tags() else ""
    )
    state.selected_period_internal = bridge_period_to_internal(period)
    if dpg.does_item_exist(SUMMARY):
        with contextlib.suppress(SystemError):
            dpg.set_value(SUMMARY, f"Selected: {period}, {team}")


# --------------------------------------------------------------------------- #
# Per-team player selection tabs
# --------------------------------------------------------------------------- #


def _clean(value: str) -> str:
    """Normalize a string to a safe DPG tag component (lowercase, special chars to underscores)."""
    out = str(value).lower()
    for ch in (" ", "-", "/", ".", "(", ")", "|", ","):
        out = out.replace(ch, "_")
    return out


def _player_team_tab(team: str) -> str:
    """Return the DPG tab tag for *team*'s player checkbox tab."""
    return f"model_player_team_tab_{_clean(team)}"


def _player_check_tag(team: str, identifier: str) -> str:
    """Return the DPG checkbox tag for a player identified by *identifier* within *team*."""
    return f"model_player_check_{_clean(team)}_{identifier}"


def _slots_for(team: str) -> list:
    """Return the PlayerSlot list for *team*, or an empty list on failure."""
    app = state.app_instance
    if app is None or not team:
        return []
    try:
        return list(app.get_player_slots(team) or [])
    except Exception:  # noqa: BLE001 -- defensive: provider may lack the team
        logger.exception("model: get_player_slots failed for %r", team)
        return []


def rebuild_player_checkboxes() -> None:
    """(Re)build the per-team player selection tabs from loaded data.

    Creates one tab per team that has at least one player slot, each listing that
    team's players as checkboxes (default checked). Built on DATA_LOADED and cleared
    on DATA_CLEARED. Per-team ticks persist across period/team combo navigation.
    Export reads each team's ticks via ``selected_player_ids``.

    Notes
    -----
    Rebuilds children of ``PLAYER_TAB_BAR`` in place.
    """
    if not dpg.does_item_exist(PLAYER_TAB_BAR):
        return
    with contextlib.suppress(SystemError):
        dpg.delete_item(PLAYER_TAB_BAR, children_only=True)
    app = state.app_instance
    if app is None:
        return
    for team in list(app.get_team_names() or []):
        slots = _slots_for(team)
        if not slots:
            continue
        try:
            with dpg.tab(label=team, tag=_player_team_tab(team), parent=PLAYER_TAB_BAR):
                for slot in slots:
                    dpg.add_checkbox(
                        label=_slot_label(slot),
                        default_value=True,
                        tag=_player_check_tag(team, _slot_identifier(slot)),
                    )
        except SystemError:
            pass


def set_all_players(checked: bool) -> None:
    """Set every player checkbox in the currently active team tab to *checked*."""
    if not dpg.does_item_exist(PLAYER_TAB_BAR):
        return
    with contextlib.suppress(SystemError):
        active_tab = dpg.get_value(PLAYER_TAB_BAR)
        if active_tab and dpg.does_item_exist(active_tab):
            for child in dpg.get_item_children(active_tab, slot=1) or []:
                dpg.set_value(child, checked)


def selected_player_ids(team: str) -> list:
    """Return ticked player identifiers for *team*'s player tab.

    Identifiers are in ``_slot_identifier`` priority order (pid / "x{xid}" /
    "col_{col_index}"), which is the form the export resolver accepts.
    Returns an empty list when the team has no tab (for example, virtual multi-XY
    keys whose per-team export is resolver-driven).

    Parameters
    ----------
    team : str
        Team name matching an entry from ``get_team_names()``.

    Returns
    -------
    list
        Identifier strings for every player whose checkbox is ticked.
    """
    out: list = []
    for slot in _slots_for(team):
        ident = _slot_identifier(slot)
        tag = _player_check_tag(team, ident)
        if dpg.does_item_exist(tag) and dpg.get_value(tag):
            out.append(ident)
    return out


# --------------------------------------------------------------------------- #
# Slot identifier / label helpers (fixed priority order)
# --------------------------------------------------------------------------- #


def _slot_identifier(slot) -> str:
    """Return the canonical identifier for *slot* using fixed priority: pid > xid > col_index."""
    pid = getattr(slot, "pid", None)
    if pid not in (None, ""):
        return str(pid)
    xid = getattr(slot, "xid", None)
    if xid not in (None, ""):
        return f"x{xid}"
    return f"col_{getattr(slot, 'col_index', 0)}"


def _slot_label(slot) -> str:
    """Return a human-readable label for *slot* (jersey+name, name, jersey, or positional index)."""
    jersey = getattr(slot, "jersey", None)
    name = getattr(slot, "name", None)
    if jersey not in (None, "") and name:
        return f"#{jersey} {name}"
    if name:
        return str(name)
    if jersey not in (None, ""):
        return f"#{jersey}"
    return f"Player {getattr(slot, 'col_index', 0) + 1}"


def _combo(tag: str) -> str:
    """Return the string value of a DPG combo widget, or an empty string if the tag is absent."""
    return dpg.get_value(tag) if dpg.does_item_exist(tag) else ""
