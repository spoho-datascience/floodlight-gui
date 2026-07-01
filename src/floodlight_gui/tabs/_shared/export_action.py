"""Shared export-action widgets: filename input, folder picker, Export button,
status text, and the write routing for CSV exports (Models / Metrics) and binary
exports (Visualization images / video).

DPG carve-out: this module imports ``dearpygui`` at module scope because it lives
under ``tabs/`` (the DPG-aware layer); backend modules must not.

BLE001 convention: every DPG callback wraps its body in
``try / except Exception: logger.exception(...)`` with a ``# noqa: BLE001``
marker so a callback error can never crash the render loop.

Export folder state: ``_export_dir`` is module-level and session-scoped (see
``_default_export_dir``). It is shared across the Models / Metrics / Visualization
tabs, overridden by the folder picker, and resets to the default on app restart.
Because module state survives across pytest runs, the autouse ``reset_export_dir``
fixture captures and restores it around each test.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from typing import Any

import dearpygui.dearpygui as dpg

from floodlight_gui.core.event_bus import Events, bus
from floodlight_gui.tabs._shared.error_helpers import friendly_error_message

__all__ = ["render_export_action", "render_binary_export_action"]

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Module-level singleton state
# --------------------------------------------------------------------------- #

# Singleton tag for the folder-selection dialog.
_FOLDER_DIALOG_TAG: str = "_export_folder_dialog"


def _default_export_dir() -> str:
    """Return the default export folder: a discoverable user location.

    Seeded from platformdirs (same dependency the dataset cache uses) so an
    installed app writes somewhere predictable instead of the launch-time CWD.
    The folder picker (``set_export_dir``) overrides it per session; nothing is
    created on disk until an export actually writes.
    """
    import platformdirs

    return os.path.join(platformdirs.user_documents_dir(), "floodlight-gui")


# Session-scoped export folder, seeded from a platformdirs user location.
_export_dir: str = _default_export_dir()


def get_export_dir() -> str:
    """Return the current session-scoped export folder."""
    return _export_dir


def set_export_dir(path: str) -> None:
    """Update the session-scoped export folder (does not persist across app restart)."""
    global _export_dir
    _export_dir = path


# --------------------------------------------------------------------------- #
# Filename resolution
# --------------------------------------------------------------------------- #


def _slugify(s: str) -> str:
    """Lowercase, underscore spaces, strip non-alphanumerics ("First Half" -> "first_half")."""
    s = (s or "").lower().strip().replace(" ", "_")
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _resolve_filename(
    *,
    user_typed: str,
    tab_name: str,  # noqa: ARG001 -- accepted for API symmetry with the rest of the helper
    artifact_name: str,
    period: str,
    team: str,
    broadcast: bool = False,
) -> str:
    """Build a CSV filename from a user-typed name or an auto-generated pattern.

    A typed name is used verbatim (``.csv`` appended if missing); an empty name
    falls back to ``{artifact}_{period}_{team}.csv``. In broadcast mode a typed
    name still gets the ``_{artifact}_{period}_{team}`` infix so each leaf of a
    multi-output/multi-leaf export lands in its own file instead of overwriting.
    Re-exporting the same selection overwrites the previous file by design.

    Parameters
    ----------
    user_typed : str
        Raw filename from the input widget (may be empty).
    tab_name : str
        Unused; accepted for call-signature symmetry with the rest of the helper.
    artifact_name, period, team : str
        Slug components for the auto pattern / broadcast infix.
    broadcast : bool, default False
        When True, apply the per-leaf infix even to a typed name.

    Returns
    -------
    str
        The resolved filename (basename only - any path component is stripped).
    """
    user_typed = (user_typed or "").strip()
    artifact_slug = _slugify(artifact_name) if artifact_name else ""
    period_slug = _slugify(period)
    team_slug = _slugify(team)
    if user_typed:
        # Strip any path component so a typed name can't escape the export folder.
        user_typed = os.path.basename(user_typed)
        if user_typed.lower().endswith(".csv"):
            stem, ext = user_typed[:-4], ".csv"
        else:
            stem, ext = user_typed, ".csv"
        if broadcast:
            # Per-leaf infix so a multi-output broadcast doesn't overwrite itself.
            infix_parts = [s for s in (artifact_slug, period_slug, team_slug) if s]
            return f"{stem}_{'_'.join(infix_parts)}{ext}" if infix_parts else f"{stem}{ext}"
        return f"{stem}{ext}"
    # Auto pattern: {artifact}_{period}_{team}.csv.
    auto_parts = [s for s in (artifact_slug, period_slug, team_slug) if s]
    return f"{'_'.join(auto_parts)}.csv" if auto_parts else "export.csv"


def _resolve_viz_filename(
    *,
    user_typed: str,
    mode: str,
    n: int | None,
    end: int | None,
    ext: str,
) -> str:
    """Build a visualization filename (image/video) from a typed name or frame numbers.

    Unlike the CSV resolver, the auto pattern is purely numeric (frame numbers,
    not slugged period/team). A typed name keeps its stem and is forced to *ext*
    (the format dropdown wins over any typed extension).

    Parameters
    ----------
    user_typed : str
        Raw filename from the input widget (may be empty).
    mode : {"frame", "clip"}
        Selects the auto pattern: ``frame_{n}.{ext}`` or ``clip_{n}-{end}.{ext}``.
    n : int or None
        Frame number (frame mode) or start frame (clip mode).
    end : int or None
        End frame (clip mode only).
    ext : str
        Output extension, e.g. "png" / "svg" / "pdf" / "mp4".

    Returns
    -------
    str
        The resolved filename (basename only - any path component is stripped).
    """
    user_typed = (user_typed or "").strip()
    ext = ext.lstrip(".")
    if user_typed:
        # Strip any path component so a typed name can't escape the export folder.
        user_typed = os.path.basename(user_typed)
        stem = user_typed.rsplit(".", 1)[0] if "." in user_typed else user_typed
        return f"{stem}.{ext}"
    if mode == "frame":
        return f"frame_{n}.{ext}"
    return f"clip_{n}-{end}.{ext}"


# --------------------------------------------------------------------------- #
# Folder picker
# --------------------------------------------------------------------------- #


def _folder_selected_callback(sender, app_data) -> None:
    """Persist a picked directory into the session-scoped export folder.

    Reads both DPG payload shapes - ``file_path_name`` (DPG 2.x) and
    ``current_path`` (DPG 1.x on Windows) - and only persists an existing dir.
    """
    folder = (app_data or {}).get("file_path_name", "") or (app_data or {}).get("current_path", "")
    if folder and os.path.isdir(folder):
        set_export_dir(folder)


def _open_folder_picker(sender, app_data, user_data) -> None:
    """Open a directory-selector dialog to change the export folder (DPG callback)."""
    try:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        if dpg.does_item_exist(_FOLDER_DIALOG_TAG):
            dpg.delete_item(_FOLDER_DIALOG_TAG)
        with dpg.file_dialog(
            directory_selector=True,
            show=True,
            callback=_folder_selected_callback,
            tag=_FOLDER_DIALOG_TAG,
            width=700,
            height=400,
            default_path=get_export_dir(),
        ):
            pass  # no extension filters in directory mode
    except Exception:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("Folder picker open failed")


# --------------------------------------------------------------------------- #
# CSV export - click callback
# --------------------------------------------------------------------------- #


def _on_export_clicked(sender, app_data, user_data) -> None:
    """Export-button click callback for CSV exports (DPG callback).

    Routes to ``_do_broadcast_export``, which writes one CSV per (period, team)
    leaf the payload yields (a one-leaf payload writes one file) and emits
    ``Events.EXPORT_REQUESTED``. ``user_data`` carries the per-call-site context
    (tab_name, artifact_name, kind, payload, status_tag, filename_input_tag, app,
    filename_broadcast).
    """
    ctx = user_data or {}
    status_tag = ctx.get("status_tag") or f"{ctx.get('tab_name', 'export')}_export_status"
    try:
        _do_broadcast_export(ctx, status_tag)
    except Exception as e:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("Export error: %s", e)
        try:
            dpg.set_value(
                status_tag,
                "Export error: "
                + friendly_error_message(e, context="Check folder permissions or filename."),
            )
        except Exception:  # noqa: BLE001 -- defensive: status tag may not exist
            logger.exception("Failed to render export-error status")


# --------------------------------------------------------------------------- #
# Broadcast export
# --------------------------------------------------------------------------- #


def _do_broadcast_export(ctx: dict, status_tag: str) -> None:
    """Write one CSV per (period, team) leaf the payload yields.

    Per-leaf errors are caught, logged, and accumulated so one bad leaf doesn't
    abort the rest (partial export). Exactly one ``Events.EXPORT_REQUESTED`` is
    emitted per click, after all writes.

    Parameters
    ----------
    ctx : dict
        Per-call-site context (payload, tab_name, artifact_name, kind, app,
        filename_input_tag, filename_broadcast).
    status_tag : str
        DPG tag of the status text to update with the result.
    """
    import pandas as pd  # lazy

    tab_name = ctx.get("tab_name", "export")
    artifact_name = ctx.get("artifact_name", "result")
    filename_input_tag = ctx.get("filename_input_tag") or f"{tab_name}_export_filename"
    kind = ctx.get("kind", "model_all")
    payload = ctx.get("payload")
    app = ctx.get("app")
    # Resolve `app` lazily when callers pass a thunk (`lambda: app_instance`)
    # - at widget-creation time the per-tab `app_instance` global may still
    # be None; the lambda is the only safe handle on the live app.
    if callable(app) and not hasattr(app, "get_player_slots"):
        try:
            app = app()
        except Exception:  # noqa: BLE001 -- defensive
            app = None

    # A typed name on a single-leaf "Export Results" is used verbatim (no infix);
    # filename_broadcast (default True) opts into the per-leaf infix.
    filename_broadcast = bool(ctx.get("filename_broadcast", True))

    # Broadcast contract: payload is a no-arg callable that returns a list of
    # per-leaf tuples (period, team, model, selected, method_name, fit_params, display_name).
    # The CALLER owns leaf enumeration - only the (period, team) combos that
    # actually have a fitted model are returned. Empty list => "no fits to export".
    leaves_payload: list[tuple] = []
    if callable(payload):
        try:
            leaves_payload = list(payload() or [])
        except TypeError:
            # Fallback payload shape: payload(period=..., team=...) per leaf -
            # enumerate the period x team cross-product from canonical app accessors.
            periods = (
                list(app.get_temporal_divisions() or [])
                if (app and hasattr(app, "get_temporal_divisions"))
                else []
            )
            teams = (
                list(app.get_team_names() or []) if (app and hasattr(app, "get_team_names")) else []
            )
            for p in periods:
                for t in teams:
                    result = payload(period=p, team=t)
                    if result is not None and len(result) >= 6:
                        # Re-shape to canonical leaf-payload tuple:
                        # (period, team, model, selected, method, params, name)
                        model, selected, _p, _t, method_name, fit_params, *rest = result
                        display_name = rest[0] if rest else artifact_name
                        leaves_payload.append(
                            (p, t, model, selected, method_name, fit_params, display_name)
                        )

    user_typed = ""
    with contextlib.suppress(Exception):  # defensive: tag may not be rendered yet
        user_typed = dpg.get_value(filename_input_tag) or ""

    export_folder = get_export_dir()
    os.makedirs(export_folder, exist_ok=True)
    written: list[str] = []
    skipped: int = 0
    first_error: str | None = None

    if not callable(payload):
        # Non-callable broadcast: single DataFrame or dict - write once
        leaves_payload = [("all", "all", payload, None, "", None, artifact_name)]

    for leaf in leaves_payload:
        (
            leaf_period,
            leaf_team,
            leaf_model,
            leaf_selected,
            leaf_method,
            leaf_params,
            leaf_display,
        ) = leaf

        # Routing happens PER-LEAF on leaf_model's type so a single
        # broadcast call can carry mixed payloads (e.g. Metrics broadcast
        # leaves are DataFrames/dicts; Models broadcast leaves are fitted
        # model objects that need export_model_metric_directly).
        leaf_uses_model_export = not isinstance(leaf_model, (pd.DataFrame, dict))

        # Defence-in-depth: skip leaves whose team has no player slots before
        # they reach export_model_metric_directly. Only relevant when leaf
        # actually goes through the Models export path; DataFrame/dict leaves
        # never touch player slots and shouldn't be filtered by them.
        if (
            leaf_uses_model_export
            and callable(payload)
            and app is not None
            and hasattr(app, "get_player_slots")
        ):
            try:
                _slots = list(app.get_player_slots(leaf_team) or [])
            except Exception:  # noqa: BLE001 -- defensive
                _slots = []
            if not _slots:
                skipped += 1
                continue

        try:
            # Use the leaf's method_name as the artifact tag so multi-output
            # broadcasts (e.g., Distance with both distance_covered and
            # cumulative_distance_covered checked) get distinct filenames
            # per output instead of overwriting one another. Falls back to
            # the caller-passed artifact_name for non-Models leaves.
            leaf_artifact = leaf_method or artifact_name
            filename = _resolve_filename(
                user_typed=user_typed,
                tab_name=tab_name,
                artifact_name=leaf_artifact,
                period=leaf_period,
                team=leaf_team,
                broadcast=filename_broadcast,
            )
            filepath = os.path.join(export_folder, filename)

            if isinstance(leaf_model, pd.DataFrame):
                leaf_model.to_csv(filepath, index=False)
            elif isinstance(leaf_model, dict):
                pd.DataFrame([leaf_model]).to_csv(filepath, index=False)
            elif callable(payload):
                from floodlight_gui.tabs._shared.model_export import (
                    export_model_metric_directly,  # noqa: PLC0415
                )

                success, msg = export_model_metric_directly(
                    app,
                    leaf_model,
                    leaf_selected,
                    filename[:-4] if filename.endswith(".csv") else filename,
                    leaf_period,
                    leaf_team,
                    leaf_display,
                    leaf_method,
                    fit_params=leaf_params,
                    output_dir=export_folder,  # honour session-scoped folder
                )
                if not success:
                    raise ValueError(msg)
            else:
                raise ValueError(f"Unsupported payload type: {type(leaf_model).__name__}")
            written.append(filepath)
        except Exception as e:  # noqa: BLE001 -- per-leaf error; continue to next leaf
            logger.exception(
                "Broadcast export error for period=%r, team=%r: %s", leaf_period, leaf_team, e
            )
            if first_error is None:
                first_error = friendly_error_message(
                    e, context="Check folder permissions or filename."
                )

    # One EXPORT_REQUESTED per click, after all writes.
    bus.emit(
        Events.EXPORT_REQUESTED,
        kind=kind,
        target=f"{export_folder} ({len(written)} files)",
        base_name="broadcast",
    )
    skipped_suffix = f" ({skipped} skipped — no player slots)" if skipped else ""
    if not leaves_payload:
        dpg.set_value(status_tag, "No fitted models to export (fit a model first)")
    elif first_error is None:
        dpg.set_value(status_tag, f"Wrote {len(written)} files{skipped_suffix}")
    else:
        dpg.set_value(
            status_tag,
            f"Wrote {len(written)} files{skipped_suffix}; first error: {first_error}",
        )


# --------------------------------------------------------------------------- #
# Public entry point - CSV export
# --------------------------------------------------------------------------- #


def render_export_action(
    parent_tag: str,
    *,
    tab_name: str,
    artifact_name: str,
    mode: str,
    kind: str,
    payload: Any,
    label: str = "Export Results",
    status_tag: str | None = None,
    filename_input_tag: str | None = None,
    app=None,
    secondary_button: dict | None = None,
) -> None:
    """Render the CSV export-action widget bundle inside *parent_tag*.

    Layout: a filename input and hint on top, a horizontal button row below
    ([Primary] [Secondary?] [Change folder...]), and a status line under that.
    On click the buttons write CSVs (one per leaf) and emit
    ``Events.EXPORT_REQUESTED`` via ``_do_broadcast_export``.

    Parameters
    ----------
    parent_tag : str
        DPG container tag the helper renders into.
    tab_name : str
        "models" or "metrics" - drives default filename + status tag.
    artifact_name : str
        e.g. "velocity" / "centroid" / "approx_entropy" / "all_outputs".
    mode : {"single", "all"}
        Drives the broadcast-hint text; it does not select an export path (every
        export writes one CSV per (period, team) leaf - a one-leaf payload writes
        one file).
    kind : {"model_single", "model_all", "metric"}
        Snake-case taxonomy consumed by the ``status_bar`` subscriber.
    payload : tuple or pd.DataFrame or dict or callable
        Discriminated at click time. Models call sites pass a callable; Metrics
        passes a DataFrame or dict.
    label : str
        Primary button label.
    status_tag : str or None
        DPG tag for the status text. Defaults to f"{tab_name}_export_status".
    filename_input_tag : str or None
        DPG tag for the filename override input. Defaults to
        f"{tab_name}_export_filename".
    app
        App accessor used for period/team resolution in broadcast exports.
    secondary_button : dict or None
        Optional second button (Models uses it for "Export Results" + "Export
        all"). Required keys: mode, kind, payload, label; inherits status_tag /
        filename_input_tag / app from the call.
    """
    resolved_status_tag = status_tag or f"{tab_name}_export_status"
    resolved_filename_tag = filename_input_tag or f"{tab_name}_export_filename"

    def _make_ctx(_mode: str, _kind: str, _payload: Any, *, _filename_broadcast: bool) -> dict:
        """Build the per-button click context dict passed as DPG user_data."""
        return {
            "tab_name": tab_name,
            "artifact_name": artifact_name,
            "mode": _mode,
            "kind": _kind,
            "payload": _payload,
            "status_tag": resolved_status_tag,
            "filename_input_tag": resolved_filename_tag,
            "app": app,
            # Drives _resolve_filename's broadcast flag - False means a typed
            # name is used verbatim (Models "Export Results" - 1 file).
            "filename_broadcast": _filename_broadcast,
        }

    # Primary defaults: 'single' kind doesn't get the infix; everything else does.
    primary_filename_broadcast = not str(kind).endswith("_single") and kind != "metric"
    primary_ctx = _make_ctx(mode, kind, payload, _filename_broadcast=primary_filename_broadcast)
    has_broadcast = mode == "all" or (
        secondary_button is not None and secondary_button.get("mode") == "all"
    )

    with dpg.group(parent=parent_tag):
        # Filename input on its own line so it has room to breathe.
        dpg.add_input_text(
            hint="Filename (leave blank for auto-generated)",
            tag=resolved_filename_tag,
            width=420,
        )
        # Hint clarifying broadcast behavior.
        if has_broadcast:
            dpg.add_text(
                "Note: when using Export all, the period and team names are appended"
                " to your filename so each leaf gets its own file.",
                wrap=600,
                color=(160, 160, 160),
            )

        # Button row below the filename input.
        with dpg.group(horizontal=True):
            dpg.add_button(
                label=label,
                callback=_on_export_clicked,
                user_data=primary_ctx,
                # Tag by kind (model_single/model_all/metric), not mode - two
                # buttons can share mode='all' and would collide on tag.
                tag=f"{tab_name}_export_btn_{kind}",
            )
            if secondary_button is not None:
                # Same default rule as primary - _single / metric => verbatim
                # filename when user-typed, everything else => broadcast infix.
                sec_kind = secondary_button["kind"]
                sec_filename_broadcast = (
                    not str(sec_kind).endswith("_single") and sec_kind != "metric"
                )
                secondary_ctx = _make_ctx(
                    secondary_button["mode"],
                    sec_kind,
                    secondary_button["payload"],
                    _filename_broadcast=sec_filename_broadcast,
                )
                dpg.add_button(
                    label=secondary_button.get("label", "Export"),
                    callback=_on_export_clicked,
                    user_data=secondary_ctx,
                    tag=f"{tab_name}_export_btn_{secondary_button['kind']}",
                )
            dpg.add_button(
                label="Change folder...",
                callback=_open_folder_picker,
                tag=f"{tab_name}_change_folder_btn_{kind}",
            )

    # Skip if the status tag already exists (avoid a duplicate-tag crash on re-render).
    if not dpg.does_item_exist(resolved_status_tag):
        dpg.add_text("Status: Ready", tag=resolved_status_tag, parent=parent_tag)


# --------------------------------------------------------------------------- #
# Binary export (visualization images / video)
# --------------------------------------------------------------------------- #


def _do_binary_export(ctx: dict, status_tag: str) -> None:
    """Write a single image/video file via a caller-supplied writer.

    Mirrors the CSV path but dispatches to ``ctx['writer_for_format'][fmt]``
    instead of ``to_csv``. Emits ``Events.EXPORT_REQUESTED`` before the writer
    runs (the before-write contract). ``ctx['n']`` / ``ctx['end']`` may be
    callables resolved at click time so the filename uses the live frame numbers.

    Parameters
    ----------
    ctx : dict
        Per-call-site context (formats, format_tag, writer_for_format, kind,
        viz_mode, n, end, filename_input_tag).
    status_tag : str
        DPG tag of the status text to update with the result.
    """
    formats = ctx.get("formats") or ["PNG"]
    fmt_tag = ctx.get("format_tag", "")
    try:
        fmt = dpg.get_value(fmt_tag) or formats[0]
    except Exception:  # noqa: BLE001 -- defensive: format tag may not exist
        fmt = formats[0]
    writer_map = ctx.get("writer_for_format") or {}
    writer = writer_map.get(fmt)
    if writer is None:
        raise ValueError(f"No writer registered for format {fmt!r}")

    user_typed = ""
    try:
        user_typed = dpg.get_value(ctx.get("filename_input_tag", "")) or ""
    except Exception:  # noqa: BLE001 -- defensive: input tag may not exist
        user_typed = ""

    # Resolve the frame providers into LOCAL vars - never write them back into
    # ctx. ctx holds the callables; mutating them would cache the first click's
    # frame, so every later export would reuse it.
    raw_n = ctx.get("n")
    resolved_n = raw_n() if callable(raw_n) else raw_n
    raw_end = ctx.get("end")
    resolved_end = raw_end() if callable(raw_end) else raw_end

    filename = _resolve_viz_filename(
        user_typed=user_typed,
        mode=ctx.get("viz_mode", "frame"),
        n=resolved_n,
        end=resolved_end,
        ext=fmt.lower(),
    )
    export_folder = get_export_dir()
    os.makedirs(export_folder, exist_ok=True)
    filepath = os.path.join(export_folder, filename)

    # Emit before the writer runs (before-write contract).
    bus.emit(
        Events.EXPORT_REQUESTED,
        kind=ctx.get("kind", "visualization_image"),
        target=filepath,
        base_name=filename.rsplit(".", 1)[0],
    )
    writer(filepath)
    dpg.set_value(status_tag, f"Exported to: {filepath}")


def _on_binary_export_clicked(sender, app_data, user_data) -> None:
    """Binary-export button click callback - single image/video only (DPG callback)."""
    ctx = user_data or {}
    status_tag = (
        ctx.get("status_tag") or f"{ctx.get('tab_name', 'binary_export')}_binary_export_status"
    )
    try:
        _do_binary_export(ctx, status_tag)
    except Exception as e:  # noqa: BLE001 -- DPG callback boundary; must not crash render loop
        logger.exception("Binary export error: %s", e)
        try:
            dpg.set_value(
                status_tag,
                f"Export error: {friendly_error_message(e, context='Check folder permissions.')}",
            )
        except Exception:  # noqa: BLE001 -- defensive: status tag may not exist
            logger.exception("Failed to render binary-export-error status")


def render_binary_export_action(
    parent_tag: str,
    *,
    tab_name: str,
    artifact_name: str,
    formats: list[str],
    writer_for_format: dict[str, Any],
    label: str = "Export",
    status_tag: str | None = None,
    filename_input_tag: str | None = None,
    enabled: bool = True,
    ffmpeg_tooltip: str | None = None,
    kind: str = "visualization_image",
    viz_mode: str = "frame",
    n_provider: Any = None,
    end_provider: Any = None,
    render_change_folder: bool = True,
    inline_widget_factory: Any = None,
    render_status: bool = True,
) -> None:
    """Render a binary (image/video) export panel inside *parent_tag*.

    Lays out a filename input, an optional format combo, the Export button, an
    optional "Change folder..." button, and a status line. The "Change folder..."
    button mutates the module-level ``_export_dir`` shared with the Models /
    Metrics tabs. On click the button writes one file and emits
    ``Events.EXPORT_REQUESTED``.

    Parameters
    ----------
    parent_tag : str
        DPG parent container tag.
    tab_name : str
        Identifier used to namespace default tags (e.g. "viz_image", "viz_video").
    artifact_name : str
        Logical artifact identifier; used only in default tag names (the actual
        filename comes from viz_mode + n + end at click time).
    formats : list[str]
        Format combo items, e.g. ["PNG", "SVG", "PDF"] or ["MP4"]. A single-item
        list suppresses the combo.
    writer_for_format : dict[str, Callable]
        Maps a format name to a writer callable taking ``filepath: str``; invoked
        after the EXPORT_REQUESTED emission.
    label : str
        Primary button label; "(unavailable)" is appended when ``enabled`` is False.
    status_tag, filename_input_tag : str or None
        DPG tags for the status text / filename input; default to
        f"{tab_name}_binary_export_status" / f"{tab_name}_binary_export_filename".
    enabled : bool
        Initial enabled state of the Export button (e.g. the ffmpeg gate for video).
    ffmpeg_tooltip : str or None
        When non-empty and ``enabled`` is False, shown as a tooltip on the button.
    kind : str
        EXPORT_REQUESTED kind, "visualization_image" or "visualization_video".
    viz_mode : {"frame", "clip"}
        Passed to ``_resolve_viz_filename`` via ctx.
    n_provider, end_provider : callable or int or None
        Zero-arg callables (or literal ints) returning the start/end frame at
        click time; default to 0 so the auto filename is never "frame_None".
    render_change_folder : bool
        Render a per-panel "Change folder..." button (False when a caller shares
        one button above multiple panels).
    inline_widget_factory : callable or None
        Optional factory rendering a per-panel widget (e.g. a clip-length input)
        between the Export button and the format combo.
    render_status : bool
        Render the status text in-row (False when a caller shares one status line
        below multiple panels).
    """
    resolved_status_tag = status_tag or f"{tab_name}_binary_export_status"
    resolved_filename_tag = filename_input_tag or f"{tab_name}_binary_export_filename"
    resolved_format_tag = f"{tab_name}_binary_export_format_{kind}"

    ctx = {
        "tab_name": tab_name,
        "artifact_name": artifact_name,
        "formats": formats,
        "writer_for_format": writer_for_format,
        "status_tag": resolved_status_tag,
        "filename_input_tag": resolved_filename_tag,
        "format_tag": resolved_format_tag,
        "kind": kind,
        "viz_mode": viz_mode,
    }
    # Store frame providers verbatim - _do_binary_export calls them at click time
    # for the live frame number. Default to 0 (not None) so the auto filename is
    # never "frame_None".
    ctx["n"] = n_provider if n_provider is not None else 0
    # end matters only for clip mode.
    ctx["end"] = end_provider if end_provider is not None else 0

    # Mark the disabled state in the label - greying alone is hard to see on dark themes.
    effective_label = f"{label} (unavailable)" if (not enabled) else label

    with dpg.group(parent=parent_tag):
        # Idempotent filename input: when a caller pre-creates one shared input
        # (and passes its tag), reuse it instead of creating a duplicate.
        if not dpg.does_item_exist(resolved_filename_tag):
            dpg.add_input_text(
                hint="Filename (leave blank for auto-generated)",
                tag=resolved_filename_tag,
                width=420,
            )
        # One horizontal row per panel: [Export] [inline widget] [Change folder?] [status?].
        btn_tag = f"{tab_name}_binary_export_btn_{kind}"
        with dpg.group(horizontal=True):
            dpg.add_button(
                label=effective_label,
                callback=_on_binary_export_clicked,
                user_data=ctx,
                tag=btn_tag,
                enabled=enabled,
            )
            # Optional per-panel inline widget (e.g. clip-length input), before the format combo.
            if inline_widget_factory is not None:
                inline_widget_factory()
            # Show the format combo only when there's a real choice; _do_binary_export
            # falls back to formats[0] when the tag is absent.
            if len(formats) > 1:
                dpg.add_combo(
                    items=formats,
                    default_value=formats[0],
                    tag=resolved_format_tag,
                    width=70,
                    label="Format",
                )
            # Per-panel "Change folder..." button; callers sharing one button above
            # multiple panels pass render_change_folder=False.
            if render_change_folder:
                dpg.add_button(
                    label="Change folder...",
                    callback=_open_folder_picker,
                    tag=f"{tab_name}_binary_change_folder_btn_{kind}",
                )
            # In-row status by default; callers sharing one status line below multiple
            # panels pass render_status=False.
            if render_status and not dpg.does_item_exist(resolved_status_tag):
                dpg.add_text("Status: Ready", tag=resolved_status_tag)
        if (not enabled) and ffmpeg_tooltip:
            with dpg.tooltip(parent=btn_tag):
                dpg.add_text(ffmpeg_tooltip, wrap=300)
