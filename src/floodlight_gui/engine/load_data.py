"""IO_REGISTRY executor: loads and normalizes provider and dataset data.

DPG-free: this module does not import dearpygui at any scope. It belongs to the
backend layer and may be imported by app.py, tabs/load/*, and headless entry points
without pulling in the GUI framework.

Layering: sits between registry/io.py (descriptors) and app.py / tabs/load/dataset_worker.py
(callers). The registries define what providers exist; this module knows how to call
them and normalize their output into the standard 4-tuple (pitch, event_data,
position_data, teamsheets).
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import logging
import time

from floodlight_gui.registry.io import IO_REGISTRY, dataset_provider_keys

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------


def extract_metadata(provider_key, loaded_data) -> dict:
    """Build the metadata dict that app.py exposes via its accessor methods.

    Parameters
    ----------
    provider_key : str
        Registry key of the provider that produced the data.
    loaded_data : tuple
        (pitch, event_data, position_data, teamsheet) from load_provider_data.

    Returns
    -------
    dict
        Keys: format_type, temporal_divisions, teams, has_possession,
        has_ballstatus, has_ball, num_halves.
    """
    if not loaded_data or len(loaded_data) < 4:
        return {
            "format_type": "unknown",
            "temporal_divisions": [],
            "teams": [],
            "has_possession": False,
            "has_ballstatus": False,
            "has_ball": False,
            "num_halves": 0,
        }

    pitch, event_data, position_data, teamsheet = loaded_data

    format_type = provider_key

    temporal_divisions = ["fullMatch"]
    teams = []
    has_possession = False
    has_ballstatus = False

    if isinstance(position_data, tuple) and len(position_data) >= 1:
        # Standard providers: (xy_dict, possession_dict, ballstatus_dict)
        xy_dict = position_data[0]
        has_possession = len(position_data) > 1 and position_data[1] is not None
        has_ballstatus = len(position_data) > 2 and position_data[2] is not None

        if isinstance(xy_dict, dict):
            first_val = next(iter(xy_dict.values()), None)
            if isinstance(first_val, dict):
                # Nested dict: {period: {team: XY}}
                temporal_divisions = list(xy_dict.keys())
                teams = list(first_val.keys())
            else:
                # Flat dict: {team: XY} (no period nesting)
                temporal_divisions = ["fullMatch"]
                teams = list(xy_dict.keys())

    elif isinstance(position_data, dict):
        # Adapted providers with flat {team: XY} position_data
        teams = list(position_data.keys())

    # Fall back to teamsheet keys for team names
    if not teams and isinstance(teamsheet, dict):
        teams = list(teamsheet.keys())

    has_ball = any(t.lower() == "ball" for t in teams)

    metadata = {
        "format_type": format_type,
        "temporal_divisions": temporal_divisions,
        "teams": teams,
        "has_possession": has_possession,
        "has_ballstatus": has_ballstatus,
        "has_ball": has_ball,
        "num_halves": len(temporal_divisions),
    }

    logger.info(
        "Metadata: provider=%s  halves=%s  teams=%s  possession=%s  ball=%s",
        provider_key,
        temporal_divisions,
        teams,
        has_possession,
        has_ball,
    )
    return metadata


def _finalize_dataset_payload(provider_key, raw_4tuple) -> dict:
    """Compute metadata and bundle it with the raw 4-tuple into a single payload dict.

    Runs on the worker thread before result_holder['done'] = True so the GUI thread
    is never blocked on extract_metadata's nested-dict iteration.

    Parameters
    ----------
    provider_key : str
        IO_REGISTRY key (e.g., 'idsse', 'eigd_h', 'dfl').
    raw_4tuple : tuple
        (pitch, event_data, position_data, teamsheet) from load_provider_data.

    Returns
    -------
    dict
        Keys: ``data`` (the raw 4-tuple) and ``metadata``. These are the only two
        keys consumed by the GUI-thread caller (``dataset_worker._dispatch_loaded`` ->
        ``app.commit_loaded``).
    """
    _t0 = time.monotonic()
    logger.info(
        "[G-06] %s _finalize_dataset_payload entry | t=%.1fs",
        provider_key,
        0.0,
    )
    metadata = extract_metadata(provider_key, raw_4tuple)
    payload = {"data": raw_4tuple, "metadata": metadata}
    logger.info(
        "[G-06] %s _finalize_dataset_payload done | t=%.1fs",
        provider_key,
        time.monotonic() - _t0,
    )
    return payload


# ---------------------------------------------------------------------------
# Generic registry-based loader
# ---------------------------------------------------------------------------

# _create_pitch_for_sport was deleted because it had zero call sites once
# pitch inference was removed from _adapt_kinexon (see below). The Create
# Pitch widget in tabs/load/pitch_section.py constructs Pitch objects
# directly via Pitch.from_template / Pitch(...).


def _adapt_kinexon(results, file_paths, extra_params):  # scan-planning-refs:allow
    """Wrap Kinexon flat Dict[str, XY] into the standard 4-tuple.

    Pitch inference is intentionally removed here. The Kinexon parser does not
    supply a Pitch object. Inferring a Pitch from extra_params['sport'] would
    violate the thin-frontend principle: the GUI would be authoring data the
    parser did not produce. If the user needs a Pitch
    attached to Kinexon data, the tabs/load/pitch_section.py 'Create Pitch'
    widget surfaces when DATA_LOADED arrives with pitch=None, prompting an
    explicit user choice (template or manual dimensions).
    See tabs/load/pitch_section.py.
    """
    xy_data = results.get("positions")
    teamsheets = results.get("teamsheets")
    position_data = (xy_data,)
    # pitch=None is intentional. The user constructs a Pitch
    # via the Create Pitch widget in tabs/load/pitch_section.py.
    return None, None, position_data, teamsheets


_ADAPTERS = {
    "kinexon": _adapt_kinexon,
}


# ---------------------------------------------------------------------------
# Dataset-class dispatcher
#
# Per-provider dispatch is driven by IO_REGISTRY descriptors via
# ``list_matches`` + ``load_match`` callables. Consumers call
# ``dataset_provider_keys()`` from ``registry.io`` to enumerate dataset
# providers (derived from descriptor ``is_dataset=True`` flags).
# Adding a new dataset provider requires only a single registry edit.
# ---------------------------------------------------------------------------

# Pre-download the EIGD-H zip into our platformdirs cache directory to bypass
# the upstream _download_and_extract path, which writes to the floodlight
# package's .data folder (often unwritable in venv installs on Windows).
_EIGDH_ZIP_URL = (
    "https://data.uni-hannover.de/dataset/8ccb364e-145f-4b28-8ff4-954b86e9b30d/"
    "resource/fd24e032-742d-4609-9052-cec310a2a563/download/eigd-h_pos.zip"
)


def _resolve_cache_root() -> str:
    """Return the absolute platformdirs cache root for floodlight-gui datasets.

    Cache lives in platformdirs.user_cache_dir, outside the venv. On Windows,
    os.path.join(DATA_DIR, absolute_path) returns the absolute path, so passing
    cache_subdir as dataset_dir_name redirects the dataset correctly without
    monkey-patching.
    """
    import os

    import platformdirs

    path = platformdirs.user_cache_dir("floodlight-gui", "floodlight-sports")
    os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Dataset match listing
# ---------------------------------------------------------------------------


def available_matches(provider_key: str) -> list[dict]:
    """Return selectable match entries for a dataset provider.

    Dispatches to ``IO_REGISTRY[provider_key]["list_matches"]``. Per-provider
    catalogue knowledge lives on the registry descriptor; this function is
    provider-agnostic. An unknown provider (or one without a list_matches hook)
    returns ``[]`` and logs a warning.

    Per-provider catalogues (defined in ``registry/io.py``):

    - ``eigd_h``: 25 entries (5 matches x 5 segments). Each ``id`` is a
      composite ``"<match_name>|<segment>"`` because EIGDDataset.get takes both.
    - ``idsse``: 2 entries: J03WMX + J03WN1.

    Each returned dict carries ``id`` + ``match_id`` (synonyms) and ``label``.

    Parameters
    ----------
    provider_key : str
        IO_REGISTRY key for a dataset provider.

    Returns
    -------
    list[dict]
        Match entry dicts with at least ``id``, ``match_id``, and ``label`` keys.
        Empty list when provider has no list_matches hook.
    """
    hook = IO_REGISTRY.get(provider_key, {}).get("list_matches")
    if hook is None:
        logger.warning("available_matches: provider %r has no list_matches hook", provider_key)
        return []
    return hook()


def _download_eigdh_to(cache_subdir, *, on_progress=None, cancel_event=None) -> bool:
    """Pre-download the EIGD-H zip into ``cache_subdir``, bypassing upstream DATA_DIR writes.

    ``EIGDDataset._download_and_extract`` writes the zip to the installed floodlight
    package's .data folder, which is often unwritable in venv installs on Windows
    (PermissionError or FileNotFoundError). By pre-populating ``cache_subdir`` with
    extracted h5 files before constructing ``EIGDDataset(dataset_dir_name=cache_subdir)``,
    the constructor's empty-dir check evaluates False and the upstream download path is
    never invoked. Calls download_from_url directly so the zip lands in the platformdirs
    cache instead.

    Parameters
    ----------
    cache_subdir : str
        Absolute path of the per-dataset cache directory (typically
        ``<platformdirs cache root>/eigddataset``). Created if missing.
    on_progress : callable or None
        Optional progress callback; called with status strings for UI display.
    cancel_event : threading.Event or None
        Optional cancel flag, checked before the network call and before extraction.

    Returns
    -------
    bool
        True if ``cache_subdir`` contains usable ``*.h5`` files at exit (cache hit or
        fresh download). False on cancel or any exception; the caller (``_cleanup_partial_cache``
        in tabs/load/dataset_worker.py) is responsible for partial-cache cleanup.
    """
    import glob
    import os

    # Cache hit: any *.h5 file already present means EIGDDataset can read it.
    if glob.glob(os.path.join(cache_subdir, "*.h5")):
        return True
    if cancel_event is not None and cancel_event.is_set():
        return False
    try:
        os.makedirs(cache_subdir, exist_ok=True)
        from floodlight.io.utils import download_from_url, extract_zip

        if on_progress:
            on_progress("Downloading EIGD-H (~120MB)...")
        data = download_from_url(_EIGDH_ZIP_URL)
        if cancel_event is not None and cancel_event.is_set():
            return False
        zip_path = os.path.join(cache_subdir, "eigd-h_pos.zip")
        with open(zip_path, "wb") as fh:
            fh.write(data)
        if on_progress:
            on_progress("Extracting EIGD-H archive...")
        extract_zip(zip_path, cache_subdir)
        with contextlib.suppress(OSError):
            os.remove(zip_path)
        return bool(glob.glob(os.path.join(cache_subdir, "*.h5")))
    except Exception as exc:  # noqa: BLE001 -- surface to caller; log exact failure mode
        logger.exception("EIGD-H pre-download failed: %s", exc)
        if on_progress:
            on_progress(f"EIGD-H download failed: {type(exc).__name__}: {exc}")
        return False


def _load_dataset(
    provider_key: str,
    *,
    on_progress=None,
    cancel_event=None,
    match_id: str | None = None,
):
    """Dispatch dataset load via ``IO_REGISTRY[provider_key]["load_match"]``.

    Each dataset descriptor owns its per-provider call convention via the
    ``load_match`` callable. This dispatcher is provider-agnostic: it resolves
    the cache subdir and delegates to the hook. Adding a new dataset provider
    requires only a single registry edit.

    Cache is redirected to platformdirs, not inside the venv, to avoid write
    failures on the installed floodlight package's .data folder. Download is
    blocking; cancel_event is best-effort (checked at construction boundaries;
    download_from_url inside floodlight is blocking and cannot be interrupted
    mid-chunk).

    Parameters
    ----------
    provider_key : str
        IO_REGISTRY key for a dataset provider (e.g., 'eigd_h', 'idsse', or any
        provider with ``is_dataset=True`` and a ``load_match`` callable).
    on_progress : callable or None
        Called with status strings for UI display (thread-safe from worker thread).
    cancel_event : threading.Event or None
        When set, the dispatcher aborts at the next check point.
    match_id : str or None
        Selected match identifier from ``available_matches(provider_key)``.
        EIGD-H expects a composite ``"<match_name>|<segment>"`` id; IDSSE expects
        a bare match id (e.g., "J03WMX"). The per-provider hook falls back to its
        documented default when match_id is None or unknown.

    Returns
    -------
    tuple or None
        (pitch, event_data, position_data, teamsheets) on success, None on
        failure or cancel.
    """
    import os

    descriptor = IO_REGISTRY.get(provider_key, {})
    hook = descriptor.get("load_match")
    if hook is None:
        logger.warning("_load_dataset: provider %r has no load_match hook", provider_key)
        return None

    cls_name = descriptor.get("dataset_class", provider_key)
    cache_root = _resolve_cache_root()
    # On Windows, os.path.join(DATA_DIR, abs_path) returns abs_path, so passing an
    # absolute path as dataset_dir_name correctly redirects the cache directory.
    cache_subdir = os.path.join(cache_root, cls_name.lower())
    os.makedirs(cache_subdir, exist_ok=True)

    if cancel_event is not None and cancel_event.is_set():
        logger.info("Dataset %s load cancelled before construction", provider_key)
        return None

    if on_progress:
        on_progress(f"Downloading {descriptor.get('display_name', provider_key)}...")

    try:
        return hook(cache_subdir, match_id, on_progress, cancel_event)
    except Exception as e:  # noqa: BLE001 -- broad catch at dataset-load boundary; surface via return value
        logger.exception("Dataset load failed for %s: %s", provider_key, e)
        return None


def _call_loader_func(module, func_desc, file_paths, extra_params):
    """Call a single loader function described by a registry entry.

    Falls back to floodlight_gui.registry.io when the function name is not found
    on the upstream module (e.g., synthetic helpers like ``_synthetic_statsbomb_pitch``
    that live in the registry module rather than in the upstream floodlight package).
    """
    fn_name = func_desc["function"]
    func = getattr(module, fn_name, None)
    if func is None:
        # Synthetic helpers defined in floodlight_gui.registry.io, not upstream
        import floodlight_gui.registry.io as _io_registry_mod

        func = getattr(_io_registry_mod, fn_name)
    args = [file_paths[k] for k in func_desc["args"]]
    kwargs = dict(func_desc.get("extra_args", {}))
    # Pass through extra_params that match function signature
    sig = inspect.signature(func)
    for param_name in sig.parameters:
        if param_name in extra_params and param_name not in kwargs:
            kwargs[param_name] = extra_params[param_name]
    return func(*args, **kwargs)


def load_provider_data(
    provider_key,
    file_paths,
    *,
    match_id: str | None = None,
    **extra_params,
):
    """Generic loader driven by IO_REGISTRY.

    Parameters
    ----------
    provider_key : str
        Key into IO_REGISTRY (e.g. "dfl", "kinexon", "tracab").
    file_paths : dict
        ``{file_input_key: filepath_string}`` matching the descriptor's
        file_inputs keys.
    match_id : str or None, keyword-only
        Selected match identifier from ``available_matches(provider_key)``;
        consumed only by the dataset dispatcher (_load_dataset). File-based
        providers ignore this argument.
    **extra_params
        Additional params forwarded to loader functions (delimiter, sport, etc.).

    Returns
    -------
    tuple or None
        (pitch, event_data, position_data, teamsheet) on success, None on failure.
    """
    try:
        descriptor = IO_REGISTRY[provider_key]

        # Route dataset providers through the dataset dispatcher.
        # Must precede the disabled guard since dataset entries are never disabled.
        if provider_key in dataset_provider_keys():
            on_progress = extra_params.pop("on_progress", None)
            cancel_event = extra_params.pop("cancel_event", None)
            return _load_dataset(
                provider_key,
                on_progress=on_progress,
                cancel_event=cancel_event,
                match_id=match_id,  # direct kwarg, NOT extra_params.pop
            )

        # Defense-in-depth: the UI already filters disabled providers via
        # file_provider_keys() / dataset_provider_keys(), but this guard protects
        # direct callers (scripts, tests, headless entry points). Must precede
        # importlib.import_module so a broken upstream module is never imported.
        if descriptor.get("disabled"):
            reason = descriptor.get("disabled_reason", "provider disabled")
            raise ValueError(f"Provider '{provider_key}' is disabled: {reason}")

        module = importlib.import_module(descriptor["module"])
        loader_funcs = descriptor["loader_functions"]

        logger.info("Loading provider: %s", provider_key)

        if provider_key in _ADAPTERS:
            # Non-standard providers: call individual functions then adapt
            results = {}
            for func_key, func_desc in loader_funcs.items():
                # Skip if required files are missing
                if not all(k in file_paths and file_paths[k] for k in func_desc["args"]):
                    logger.debug("Skipping %s: missing files", func_key)
                    continue
                results[func_key] = _call_loader_func(module, func_desc, file_paths, extra_params)
            return _ADAPTERS[provider_key](results, file_paths, extra_params)

        elif "positions" in loader_funcs:
            # Standard providers: positions function returns 5-tuple
            pos_desc = loader_funcs["positions"]
            # Guard against missing required files before building the args list.
            # Some providers (e.g., tracab, secondspectrum) mark filepath_metadata
            # as optional in the UI but the upstream function still takes it as a
            # positional arg. Without this check, a KeyError is swallowed by the
            # outer except and the UI shows only "Error loading X" with no indication
            # of which file is missing. This guard names the missing file explicitly
            # and surfaces it via on_progress for the UI status text.
            missing = [k for k in pos_desc["args"] if k not in file_paths or not file_paths[k]]
            if missing:
                msg = (
                    f"Provider '{provider_key}' positions loader missing "
                    f"required file(s): {missing}"
                )
                logger.warning(msg)
                on_progress = extra_params.get("on_progress")
                if callable(on_progress):
                    on_progress(msg)
                return None
            pos_args = [file_paths[k] for k in pos_desc["args"]]
            pos_func = getattr(module, pos_desc["function"])
            result = pos_func(*pos_args)
            xy_data, possession, ballstatus, teamsheets, pitch = result
            position_data = (xy_data, possession, ballstatus)

            # Events (optional, loaded separately)
            event_data = None
            if "events" in loader_funcs:
                ev_desc = loader_funcs["events"]
                if ev_desc.get("disabled"):
                    logger.info(
                        "Skipping disabled loader 'events' for %s: %s",
                        provider_key,
                        ev_desc.get("disabled_reason", "loader_functions-level disable"),
                    )
                elif all(k in file_paths and file_paths[k] for k in ev_desc["args"]):
                    ev_func = getattr(module, ev_desc["function"])
                    ev_args = [file_paths[k] for k in ev_desc["args"]]
                    event_data = ev_func(*ev_args)

            logger.info("Loaded %s data successfully", provider_key)
            return pitch, event_data, position_data, teamsheets

        else:
            # Event-only providers (Opta, Sportradar, StatsBomb)
            event_data = None
            teamsheets = None
            pitch = None

            if "events" in loader_funcs:
                ev_desc = loader_funcs["events"]
                if ev_desc.get("disabled"):
                    logger.info(
                        "Skipping disabled loader 'events' for %s: %s",
                        provider_key,
                        ev_desc.get("disabled_reason", "loader_functions-level disable"),
                    )
                elif all(k in file_paths and file_paths[k] for k in ev_desc["args"]):
                    event_data = _call_loader_func(module, ev_desc, file_paths, extra_params)

            if "teamsheets" in loader_funcs:
                ts_desc = loader_funcs["teamsheets"]
                if ts_desc.get("disabled"):
                    logger.info(
                        "Skipping disabled loader 'teamsheets' for %s: %s",
                        provider_key,
                        ts_desc.get("disabled_reason", "loader_functions-level disable"),
                    )
                elif all(k in file_paths and file_paths[k] for k in ts_desc["args"]):
                    teamsheets = _call_loader_func(module, ts_desc, file_paths, extra_params)

            if "pitch" in loader_funcs:
                p_desc = loader_funcs["pitch"]
                if p_desc.get("disabled"):
                    logger.info(
                        "Skipping disabled loader 'pitch' for %s: %s",
                        provider_key,
                        p_desc.get("disabled_reason", "loader_functions-level disable"),
                    )
                elif all(k in file_paths and file_paths[k] for k in p_desc["args"]):
                    pitch = _call_loader_func(module, p_desc, file_paths, extra_params)

            logger.info("Loaded %s (event-only) data", provider_key)
            return pitch, event_data, None, teamsheets

    except Exception as e:  # noqa: BLE001 -- loader failures must not crash the app; surface via return value
        logger.exception("Error loading %s: %s", provider_key, e)
        return None
