"""Single source of truth for all I/O provider descriptors the GUI can expose.

``IO_REGISTRY`` maps provider keys (e.g. ``"dfl"``, ``"kinexon"``, ``"eigd_h"``)
to descriptor dicts that drive the Load tab UI automatically. Adding an entry
here surfaces the provider in the UI without any tab-layer changes.

Descriptor schema (validated by ``registry/_validators._validate_io_descriptor``):

    Required keys:
        module, display_name, sport, file_inputs, loader_functions, outputs
    Optional keys:
        extra_params, is_dataset, dataset_class, disabled
    Fixed ValueError format on shape violation:
        f"IO_REGISTRY descriptor for '{key}': {reason}"

    Each ``file_inputs`` entry may carry an optional ``tooltip`` field
    (str, default None) -- short hover hint shown by the UI. Falls back to
    ``inspect.getdoc(<upstream callable>)`` first line, then to "No description
    available". Validators stay permissive.

    The ``file_inputs`` tooltips below are static text. ``IO_REGISTRY`` entries
    carry no ``class_path`` / ``function_path`` (they use ``module`` + per-loader
    ``function`` / ``class`` strings instead), so ``resolve_tooltip``'s walker
    resolves the upstream callable as ``None`` and the dynamic tooltip tiers
    cannot fire. The file-input key names (e.g. ``filepath_pos``) are GUI-side
    conventions that often diverge from the upstream kwarg names, so even a
    per-input ``function_path`` hook would resolve only a subset. The literals
    are therefore hardcoded.

DPG-free: this module never imports ``dearpygui``; it must remain importable
without a display or DPG context. It lives in ``registry/`` -- the
source-of-truth layer that tabs read; not in ``tabs/``.
"""

from __future__ import annotations

from floodlight.io.datasets import (
    EIGDDataset,
    IDSSEDataset,
)

from floodlight_gui.core.event_bus import Events, bus

# ---------------------------------------------------------------------------
# Match catalogues for dataset providers
# ---------------------------------------------------------------------------

_EIGDH_MATCHES = {
    "48dcd3": ["00-06-00", "00-15-00", "00-25-00", "01-05-00", "01-10-00"],
    "ad969d": ["00-00-30", "00-15-00", "00-43-00", "01-11-00", "01-35-00"],
    "e0e547": ["00-00-00", "00-08-00", "00-15-00", "00-50-00", "01-00-00"],
    "e8a35a": ["00-02-00", "00-07-00", "00-14-00", "01-05-00", "01-14-00"],
    "ec7a6a": ["00-30-00", "00-53-00", "01-19-00", "01-30-00", "01-40-00"],
}
# Full upstream catalogue verbatim from IDSSEDataset._IDSSE_FILE_IDS_INFO keys
# with human-readable labels from the same module's docstring.
_IDSSE_MATCHES = {
    "J03WMX": "1. FC Koeln vs. FC Bayern Muenchen",
    "J03WN1": "VfL Bochum 1848 vs. Bayer 04 Leverkusen",
    "J03WPY": "Fortuna Duesseldorf vs. 1. FC Nuernberg",
    "J03WOH": "Fortuna Duesseldorf vs. SSV Jahn Regensburg",
    "J03WQQ": "Fortuna Duesseldorf vs. FC St. Pauli",
    "J03WOY": "Fortuna Duesseldorf vs. F.C. Hansa Rostock",
    "J03WR9": "Fortuna Duesseldorf vs. 1. FC Kaiserslautern",
}


def _list_matches_eigd_h() -> list[dict]:
    """Return the EIGD-H match catalogue as ``[{id, match_id, label}, ...]``.

    25 entries: 5 matches x 5 segments. ``id`` and ``match_id`` are synonyms
    (composite ``"<match>|<segment>"`` because EIGDDataset.get takes BOTH).
    """
    return [
        {
            "id": f"{m}|{s}",
            "match_id": f"{m}|{s}",
            "label": f"{m} - segment {s}",
        }
        for m, segs in _EIGDH_MATCHES.items()
        for s in segs
    ]


def _list_matches_idsse() -> list[dict]:
    """Return the IDSSE match catalogue as ``[{id, match_id, label}, ...]``.

    Two entries: J03WMX (1. FC Koeln vs. FC Bayern Muenchen) and J03WN1
    (VfL Bochum 1848 vs. Bayer 04 Leverkusen).
    """
    return [
        {
            "id": mid,
            "match_id": mid,
            "label": f"{mid} - {label}",
        }
        for mid, label in _IDSSE_MATCHES.items()
    ]


def _synthetic_statsbomb_pitch():
    """Return a synthetic StatsBomb-template football Pitch.

    StatsBomb open event data has no associated tracking XY and upstream
    floodlight.io.statsbomb exposes no pitch loader. This helper returns the
    canonical statsbomb-template pitch so the GUI's load path produces a
    non-None Pitch (downstream tabs assume ``pitch is not None``).
    """
    from floodlight.core.pitch import Pitch

    return Pitch.from_template("statsbomb", sport="football")


# ---------------------------------------------------------------------------
# Per-provider load_match hooks for dataset descriptors
# ---------------------------------------------------------------------------
# Each function returns the standard 4-tuple
# ``(pitch, event_data, position_data, teamsheets)`` or ``None`` on cancel.
# DPG-free. The EIGD-H hook defers its ``_download_eigdh_to`` import to
# call-time to avoid a circular registry/io -> load_data -> registry/io chain.


def _load_match_eigd_h(
    cache_subdir: str,
    match_id: str | None,
    on_progress,
    cancel_event,
):
    """EIGD-H: pre-download (upstream DATA_DIR workaround) then ``EIGDDataset.get(match, segment)``.

    Returns ``(pitch, None, position_data, None)`` or ``None`` on cancel.
    See ``load_data._download_eigdh_to`` for the pre-download workaround.
    That helper still lives in ``load_data.py``; this hook imports it
    lazily to avoid a circular import.
    """
    import logging

    # Deferred import: _download_eigdh_to lives in load_data.py (which
    # imports IO_REGISTRY from THIS module); deferring avoids circular import.
    from floodlight_gui.engine.load_data import _download_eigdh_to

    if on_progress:
        on_progress("Downloading EIGD-H (Public Dataset)...")
    if not _download_eigdh_to(cache_subdir, on_progress=on_progress, cancel_event=cancel_event):
        return None
    ds = EIGDDataset(dataset_dir_name=cache_subdir)
    if cancel_event is not None and cancel_event.is_set():
        return None
    # Resolve match + segment from match_id (default fallback to first
    # documented (match, segment) when match_id is missing or malformed).
    match_name, segment = "48dcd3", "00-06-00"
    if match_id and "|" in match_id:
        m_part, s_part = match_id.split("|", 1)
        if m_part in _EIGDH_MATCHES and s_part in _EIGDH_MATCHES[m_part]:
            match_name, segment = m_part, s_part
        else:
            logging.getLogger(__name__).warning(
                "EIGD-H: unknown match_id %r, falling back to %s|%s",
                match_id,
                match_name,
                segment,
            )
    if on_progress:
        on_progress(f"Loading sample {match_name} - {segment}...")
    teamA, teamB, ball = ds.get(match_name=match_name, segment=segment)
    pitch = ds.get_pitch()
    xy_dict = {"teamA": teamA, "teamB": teamB, "ball": ball}
    position_data = (xy_dict,)
    return pitch, None, position_data, None


def _load_match_idsse(
    cache_subdir: str,
    match_id: str | None,
    on_progress,
    cancel_event,
):
    """IDSSE: instantiate dataset, call ``.get(match_id)``, unpack 6-tuple.

    Returns ``(pitch_obj, event_data_tuple, position_data, teamsheets_obj)``
    or ``None`` on cancel. ``event_data_tuple`` mirrors the upstream-shaped
    ``(events_dict, teamsheets, pitch)`` 3-tuple so inspect_tab's
    ``event_data[0]`` unpacking works the same as the DFL flow.

    Per-step boundary log breadcrumbs are emitted at IDSSE-specific
    transitions so a frozen-GUI repro can be triaged via log inspection.
    """
    import logging
    import time

    log = logging.getLogger("floodlight_gui.engine.load_data")
    _t0 = time.monotonic()
    chosen = match_id if match_id in _IDSSE_MATCHES else "J03WMX"
    log.info(
        "[G-06] %s %s | t=%.1fs",
        "idsse",
        "IDSSEDataset(..) start",
        time.monotonic() - _t0,
    )
    ds = IDSSEDataset(dataset_dir_name=cache_subdir, match_id=chosen)
    log.info(
        "[G-06] %s %s | t=%.1fs",
        "idsse",
        "IDSSEDataset(..) done -- downloads complete",
        time.monotonic() - _t0,
    )
    if cancel_event is not None and cancel_event.is_set():
        return None
    if on_progress:
        on_progress(
            f"Parsing match {chosen} XML -- this may take 30-60 seconds (~135K frames per team)..."
        )
    log.info(
        "[G-06] %s %s | t=%.1fs",
        "idsse",
        "ds.get(match_id) start",
        time.monotonic() - _t0,
    )
    result = ds.get(chosen)
    log.info(
        "[G-06] %s %s | t=%.1fs",
        "idsse",
        "ds.get(match_id) done -- XML parsed",
        time.monotonic() - _t0,
    )
    events_obj, xy_obj, possession_obj, ballstatus_obj, teamsheets_obj, pitch_obj = result
    log.info(
        "[G-06] %s %s | t=%.1fs",
        "idsse",
        "unpack 6-tuple ok",
        time.monotonic() - _t0,
    )
    position_data = (xy_obj, possession_obj, ballstatus_obj)
    # Wrap event_data in the upstream-shaped (events_dict, teamsheets, pitch)
    # 3-tuple so inspect_tab's ``event_data[0]`` unpacking works the same as
    # the DFL flow. Without the wrap, IDSSE's bare nested dict would raise
    # KeyError on ``event_data[0]`` and inspect_tab silently returns an empty
    # DataFrame.
    event_data_tuple = (events_obj, teamsheets_obj, pitch_obj)
    log.info(
        "[G-06] %s %s | t=%.1fs",
        "idsse",
        "return 4-tuple ok",
        time.monotonic() - _t0,
    )
    return pitch_obj, event_data_tuple, position_data, teamsheets_obj


IO_REGISTRY = {
    # ------------------------------------------------------------------ #
    # DFL / STS
    # ------------------------------------------------------------------ #
    "dfl": {
        "module": "floodlight.io.dfl",
        "display_name": "DFL / STS",
        "sport": "football",
        "file_inputs": {
            "filepath_mat": {
                "extensions": [".xml"],
                "required": True,
                "tooltip": "DFL/STS match info XML file describing teams, players, pitch, and periods.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
            "filepath_pos": {
                "extensions": [".xml"],
                "required": True,
                "tooltip": "DFL/STS position-data XML file containing per-frame XY tracking.",
            },
            "filepath_ev": {
                "extensions": [".xml"],
                "required": False,
                "tooltip": "Optional DFL/STS event-data XML file (passes, shots, fouls).",
            },
        },
        "loader_functions": {
            "positions": {
                "function": "read_position_data_xml",
                "args": ["filepath_pos", "filepath_mat"],
                "description": "Load XY position data from DFL XML",
            },
            "events": {
                "function": "read_event_data_xml",
                "args": ["filepath_ev", "filepath_mat"],
                "description": "Load event data from DFL XML",
            },
            "teamsheets": {
                "function": "read_teamsheets_from_mat_info_xml",
                "args": ["filepath_mat"],
                "description": "Load teamsheets from match info XML",
            },
            "pitch": {
                "function": "read_pitch_from_mat_info_xml",
                "args": ["filepath_mat"],
                "description": "Load pitch from match info XML",
            },
        },
        "outputs": ["xy", "teamsheets", "pitch", "events", "codes"],
    },
    # ------------------------------------------------------------------ #
    # Tracab / ChyronHego
    # ------------------------------------------------------------------ #
    "tracab": {
        "module": "floodlight.io.tracab",
        "display_name": "Tracab / ChyronHego",
        "sport": "football",
        "file_inputs": {
            "filepath_dat": {
                "extensions": [".dat"],
                "required": True,
                "tooltip": "Tracab/ChyronHego position-data .dat file with per-frame XY tracking.",
            },
            "filepath_metadata": {
                "extensions": [".json", ".xml"],
                "required": False,
                "description": "Optional metadata for teamsheet enrichment",
                "tooltip": "Optional Tracab metadata file (.json or .xml) used to enrich teamsheets.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
        },
        "loader_functions": {
            "positions": {
                "function": "read_position_data_dat",
                "args": ["filepath_dat", "filepath_metadata"],
                "description": "Load XY position data from Tracab .dat file",
            },
            "teamsheets_dat": {
                "function": "read_teamsheets_from_dat",
                "args": ["filepath_dat"],
                "description": "Load basic teamsheets from .dat file",
            },
            "teamsheets_meta": {
                "function": "read_teamsheets_from_meta_json",
                "args": ["filepath_metadata"],
                "description": "Load enriched teamsheets from metadata JSON",
            },
        },
        "outputs": ["xy", "teamsheets", "codes"],
    },
    # ------------------------------------------------------------------ #
    # Kinexon
    # ------------------------------------------------------------------ #
    "kinexon": {
        "module": "floodlight.io.kinexon",
        "display_name": "Kinexon",
        "sport": "football",
        "file_inputs": {
            "filepath_csv": {
                "extensions": [".csv"],
                "required": True,
                "tooltip": "Kinexon export CSV containing player positions and metadata.",
            },
        },
        "extra_params": {
            "delimiter": {
                "label": "CSV Delimiter",
                "type": "string",
                "default": ",",
            },
        },
        "loader_functions": {
            "positions": {
                "function": "read_position_data_csv",
                "args": ["filepath_csv"],
                "extra_args": {"as_dict": True},
                "description": "Load XY position data from Kinexon CSV",
            },
            "teamsheets": {
                "function": "read_teamsheets_from_csv",
                "args": ["filepath_csv"],
                "extra_args": {"as_dict": True},
                "description": "Load teamsheets from Kinexon CSV",
            },
        },
        "outputs": ["xy", "teamsheets"],
    },
    # ------------------------------------------------------------------ #
    # Opta
    # ------------------------------------------------------------------ #
    "opta": {
        "module": "floodlight.io.opta",
        "display_name": "Opta",
        "sport": "football",
        "file_inputs": {
            "filepath_f24": {
                "extensions": [".xml"],
                "required": True,
                "tooltip": "Opta F24 event-data XML file (passes, shots, defensive actions).",
            },
            "filepath_f7": {
                "extensions": [".xml"],
                "required": False,
                "description": "Optional match info for pitch and teamsheets",
                "tooltip": "Optional Opta F7 match-info XML file (teams, pitch, kickoff).",
            },
        },
        "loader_functions": {
            "events": {
                "function": "read_event_data_xml",
                "args": ["filepath_f24"],
                "description": "Load event data from Opta F24 XML",
            },
        },
        "outputs": ["events"],
    },
    # ------------------------------------------------------------------ #
    # Second Spectrum
    # ------------------------------------------------------------------ #
    "secondspectrum": {
        "module": "floodlight.io.secondspectrum",
        "display_name": "Second Spectrum",
        "sport": "football",
        "file_inputs": {
            "filepath_tracking": {
                "extensions": [".jsonl"],
                "required": True,
                "tooltip": "Second Spectrum tracking-data JSONL file with per-frame XY positions.",
            },
            "filepath_insight": {
                "extensions": [".jsonl"],
                "required": False,
                "description": "Required for events loader (filepath_insight upstream param)",
                "tooltip": "Second Spectrum insight (event) JSONL. Required only for the events loader.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
            "filepath_metadata": {
                "extensions": [".json"],
                "required": False,
                "description": "Required for events loader + optional teamsheets",
                "tooltip": "Second Spectrum metadata JSON. Required for events loader; optional for teamsheets.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
        },
        "loader_functions": {
            "positions": {
                "function": "read_position_data_jsonl",
                "args": ["filepath_tracking", "filepath_metadata"],
                "description": "Load XY position data from JSONL",
            },
            "events": {
                "function": "read_event_data_jsonl",
                "args": ["filepath_insight", "filepath_metadata"],
                "description": "Load event data from Second Spectrum JSONL (returns events_dict + pitch tuple)",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
            "teamsheets": {
                "function": "read_teamsheets_from_meta_json",
                "args": ["filepath_metadata"],
                "description": "Load teamsheets from metadata JSON",
            },
        },
        "outputs": ["xy", "teamsheets", "events"],
    },
    # ------------------------------------------------------------------ #
    # Skillcorner
    # ------------------------------------------------------------------ #
    "skillcorner": {
        "module": "floodlight.io.skillcorner",
        "display_name": "SkillCorner",
        "sport": "football",
        "file_inputs": {
            "filepath_tracking": {
                "extensions": [".json"],
                "required": True,
                "tooltip": "SkillCorner tracking-data JSON file with per-frame XY positions.",
            },
            "filepath_match": {
                "extensions": [".json"],
                "required": True,
                "tooltip": "SkillCorner match-data JSON file with teams, pitch, and metadata.",
            },
        },
        "loader_functions": {
            "positions": {
                "function": "read_position_data_json",
                "args": ["filepath_tracking", "filepath_match"],
                "description": "Load XY position data from SkillCorner JSON",
            },
        },
        "outputs": ["xy", "teamsheets", "pitch"],
    },
    # ------------------------------------------------------------------ #
    # Sportradar
    # ------------------------------------------------------------------ #
    "sportradar": {
        "module": "floodlight.io.sportradar",
        "display_name": "Sportradar",
        "sport": "football",
        "file_inputs": {
            "filepath_events": {
                "extensions": [".json"],
                "required": True,
                "tooltip": "Sportradar event-data JSON file (passes, shots, fouls).",
            },
        },
        "loader_functions": {
            "events": {
                "function": "read_event_data_json",
                "args": ["filepath_events"],
                "description": "Load event data from Sportradar JSON",
            },
        },
        "outputs": ["events"],
    },
    # ------------------------------------------------------------------ #
    # StatsPerform
    # ------------------------------------------------------------------ #
    "statsperform": {
        "module": "floodlight.io.statsperform",
        "display_name": "StatsPerform",
        "sport": "football",
        "file_inputs": {
            "filepath_tracking": {
                "extensions": [".txt"],
                "required": False,
                "tooltip": "StatsPerform tracking-data TXT file with per-frame XY positions.",
            },
            "filepath_events": {
                "extensions": [".xml"],
                "required": False,
                "tooltip": "StatsPerform event-data XML file.",
            },
            "filepath_metadata": {
                "extensions": [".json"],
                "required": False,
                "description": "Optional metadata for enriched teamsheets",
                "tooltip": "Optional StatsPerform metadata JSON for enriched teamsheets.",
            },
        },
        "loader_functions": {
            "positions": {
                "function": "read_position_data_txt",
                "args": ["filepath_tracking"],
                "description": "Load XY position data from StatsPerform TXT",
            },
            "events": {
                "function": "read_event_data_xml",
                "args": ["filepath_events"],
                "description": "Load event data from StatsPerform XML",
            },
            "teamsheets": {
                "function": "read_teamsheets_from_meta_json",
                "args": ["filepath_metadata"],
                "description": "Load teamsheets from metadata JSON",
                "disabled": True,
                "disabled_reason": "read_teamsheets_from_meta_json is not defined in floodlight.io.statsperform (it exists only in floodlight.io.tracab and floodlight.io.secondspectrum).",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
        },
        "outputs": ["xy", "teamsheets", "events"],
    },
    # ------------------------------------------------------------------ #
    # StatsBomb
    # ------------------------------------------------------------------ #
    "statsbomb": {
        "module": "floodlight.io.statsbomb",
        "display_name": "StatsBomb",
        "sport": "football",
        "file_inputs": {
            "filepath_events": {
                "extensions": [".json"],
                "required": True,
                "tooltip": "StatsBomb open-data event JSON file (one per match).",
            },
            "filepath_match": {
                "extensions": [".json"],
                "required": True,
                "description": "Required by upstream read_open_event_data_json",
                "tooltip": "StatsBomb open-data match-info JSON file. Required by the upstream loader.",  # noqa: E501 - one-entry-per-line descriptor is more readable than wrapping
            },
        },
        "loader_functions": {
            "events": {
                "function": "read_open_event_data_json",
                "args": ["filepath_events", "filepath_match"],
                "description": "Load event data from StatsBomb open data JSON",
            },
            "teamsheets": {
                "function": "read_teamsheets_from_open_event_data_json",
                "args": ["filepath_events", "filepath_match"],
                "description": "Load teamsheets from StatsBomb open event data JSON",
            },
            # Upstream floodlight.io.statsbomb exposes no read_pitch_* function;
            # materialize via Pitch.from_template so the load path produces a
            # non-None pitch (downstream tabs assume ``pitch is not None``).
            "pitch": {
                "function": "_synthetic_statsbomb_pitch",
                "args": [],
                "description": "Synthetic StatsBomb-template football pitch.",
            },
        },
        "outputs": ["events", "teamsheets", "pitch"],
    },
    # ------------------------------------------------------------------ #
    # Public Datasets (class-based loaders)
    # ------------------------------------------------------------------ #
    "eigd_h": {
        "module": "floodlight.io.datasets",
        "display_name": "EIGD-H (Public Dataset)",
        "sport": "handball",
        "dataset_class": "EIGDDataset",
        "is_dataset": True,
        "file_inputs": {},
        "loader_functions": {
            "dataset": {
                "class": "EIGDDataset",
                "description": "European Intercollegiate Games Dataset (Handball)",
            },
        },
        "outputs": ["xy", "teamsheets", "pitch", "events", "codes"],
        # Per-provider dispatcher hooks.
        "list_matches": _list_matches_eigd_h,
        "load_match": _load_match_eigd_h,
        # Verified end-to-end: ~120MB download + extraction landed 25 h5 files;
        # EIGDDataset.get returned 3 XY objects on a handball Pitch (xlim=(0,40),
        # ylim=(0,20)). The pre-download workaround in load_data._download_eigdh_to
        # bypasses upstream EIGDDataset._download_and_extract (which targets
        # DATA_DIR inside the floodlight package's .data folder, often unwritable
        # in venv installs on Windows) by fetching the zip directly into our
        # platformdirs cache_subdir before constructing EIGDDataset.
        # On cancel/failure the load worker removes the partial cache subdir.
    },
    "idsse": {
        "module": "floodlight.io.datasets",
        "display_name": "IDSSE (Public Dataset)",
        "sport": "football",
        "dataset_class": "IDSSEDataset",
        "is_dataset": True,
        "file_inputs": {},
        "loader_functions": {
            "dataset": {
                "class": "IDSSEDataset",
                "description": "Immersive Data Set for Soccer Evaluation",
            },
        },
        "outputs": ["xy", "teamsheets", "pitch"],
        # Per-provider dispatcher hooks.
        "list_matches": _list_matches_idsse,
        "load_match": _load_match_idsse,
    },
}


def visible_provider_keys() -> list[str]:
    """Return provider keys that should appear in the UI load-provider combo.

    Excludes descriptors with ``disabled=True`` (signature mismatch or broken).
    Dataset-class providers (``is_dataset=True``) are included: the dispatcher
    in ``load_data._load_dataset`` routes them via the floodlight dataset classes
    (``EIGDDataset`` / ``IDSSEDataset``).

    Paired with ``file_provider_keys`` and ``dataset_provider_keys`` which split
    this list into file-based and dataset-import providers for the split Load-tab
    UI. This function returns the union.

    Returns
    -------
    list[str]
        Ordered provider keys (insertion order of ``IO_REGISTRY``).

    Notes
    -----
    Pure function -- imports nothing from ``tabs/`` and does not require
    ``dearpygui``. Safe to call from DPG-free test code.
    """
    return [k for k, v in IO_REGISTRY.items() if not v.get("disabled")]


def file_provider_keys() -> list[str]:
    """Return visible provider keys that load local files (DFL, Kinexon, Tracab, etc.).

    A "file provider" is a visible descriptor without ``is_dataset=True``. The
    user must pick local files via a file dialog to use these providers.

    Paired with ``dataset_provider_keys`` to drive the split Load-tab UI.

    Returns
    -------
    list[str]
        Ordered provider keys (insertion order of ``IO_REGISTRY``).

    Notes
    -----
    Pure function -- DPG-free.
    """
    return [k for k, v in IO_REGISTRY.items() if not v.get("disabled") and not v.get("is_dataset")]


def dataset_provider_keys() -> list[str]:
    """Return visible provider keys that download public datasets (EIGD-H, IDSSE).

    A "dataset provider" is a visible descriptor with ``is_dataset=True``. The
    floodlight dataset class self-fetches data from a public URL so no file dialog
    is needed.

    Paired with ``file_provider_keys`` to drive the split Load-tab UI.

    Returns
    -------
    list[str]
        Ordered provider keys (insertion order of ``IO_REGISTRY``).

    Notes
    -----
    Pure function -- DPG-free.
    """
    return [k for k, v in IO_REGISTRY.items() if not v.get("disabled") and v.get("is_dataset")]


def register_io_provider(key: str, descriptor: dict) -> None:
    """Validate a descriptor, insert it into ``IO_REGISTRY``, and emit ``IO_REGISTRY_CHANGED``.

    Ordering contract (mutate-then-emit): IO_REGISTRY is updated before the
    event is emitted, so subscribers reading IO_REGISTRY inside their callback
    see the new entry immediately.

    Parameters
    ----------
    key : str
        Registry key for the new provider (e.g. ``"my_provider"``).
    descriptor : dict
        Provider descriptor; must satisfy the ``IO_REGISTRY`` schema (validated
        by ``_validate_io_descriptor`` before insertion).

    Raises
    ------
    ValueError
        On shape violation (from ``_validate_io_descriptor``; locked format:
        ``f"IO_REGISTRY descriptor for '{key}': {reason}"``).
    ValueError
        On duplicate key (locked format:
        ``f"key '{key}' already registered in IO_REGISTRY; unregister first"``).

    Notes
    -----
    Emits ``Events.IO_REGISTRY_CHANGED`` with ``key`` and ``descriptor`` as
    payload after the registry mutation.
    """
    from floodlight_gui.registry._validators import _validate_io_descriptor

    _validate_io_descriptor(key, descriptor)

    if key in IO_REGISTRY:
        raise ValueError(f"key '{key}' already registered in IO_REGISTRY; unregister first")

    IO_REGISTRY[key] = descriptor
    bus.emit(Events.IO_REGISTRY_CHANGED, key=key, descriptor=descriptor)
