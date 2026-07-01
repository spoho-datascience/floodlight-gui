"""Shared mutable state for the visualization tab subpackage.

The tab's live state is a single typed structure, ``ViewerState``, held as
the module-level singleton ``viz_state``. Every sub-module reads and writes
through ``state.viz_state`` so all readers share one instance. Sub-modules
must not re-declare these names locally.

``ViewerState`` exposes a dict-compatibility shim (``[]`` / ``.get`` /
``.setdefault`` / ``.pop`` / ``in``) because two external consumers read it
via subscript or ``.get``:

* ``floodlight_gui.rendering.adapters.voronoi`` reads
  ``viz_state["selected_half"]`` and ``viz_state["voronoi_alpha_value"]``.
* The export writers read ``state.app_instance`` and ``viz_state.get(...)``.

New code should prefer typed attribute access (``viz_state.current_frame``).

``eq=False`` is deliberate: field-wise dataclass equality would fail on
any numpy array stored in a cached field (ambiguous truth value), and a
singleton only needs identity equality.

Module-level attributes (``app_instance``, the resize cache) remain plain
module globals: they are rebound from sub-modules as ``state.x = ...``
(never via ``global``) and are not part of the per-view state shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_MISSING = object()


@dataclass(eq=False)
class ViewerState:
    """Complete mutable state of the visualization tab (one instance per session).

    Typed attribute access is preferred for new code; subscript access is
    supported via the dict-compatibility shim for external consumers (see
    module docstring).

    Attributes
    ----------
    current_frame : int
        Index of the frame currently shown on the pitch canvas.
    max_frames : int
        Total number of frames in the loaded XY for the active period/team.
    playing : bool
        True while the playback clock is running.
    play_speed : int
        Target playback rate in frames per second, clamped by the worker.
    original_fps : float or None
        Native framerate of the loaded data; used by speed-multiplier buttons
        to derive absolute rates.
    last_slider_sync_frame : int
        Frame index at which the slider was last written; guards redundant
        DPG set_value calls during playback.
    selected_half : str
        Internal period key (e.g. "firstHalf") for the active period slice.
        External consumers use this key directly via subscript.
    selected_teams : dict
        Maps team name to a boolean visibility flag.
    initialized : bool
        True after the first DATA_LOADED callback has wired up all renderer
        objects and data references.
    mapper : CoordinateMapper or None
        Pitch-to-pixel coordinate mapper; set during initialization.
    pitch_renderer : PitchRenderer or None
        DPG drawlist pitch renderer; set during initialization.
    player_renderer : PlayerRenderer or None
        DPG drawlist player renderer; set during initialization.
    active_adapters : dict[str, OverlayAdapter]
        Lazily created overlay adapters keyed by model name; populated in
        the model-fitted callback.
    voronoi_alpha_value : float
        Opacity for Voronoi cell overlays; persisted across re-fits so the
        user's last slider position survives a new model run.
    last_hover : Any
        Most recent hover hit-test result from the player renderer.
    cached_pos_data : Any
        Position array snapshot for the active period/team; refreshed on
        DATA_LOADED.
    cached_team_names : Any
        Team-name list snapshot; refreshed on DATA_LOADED.
    cached_is_single : bool
        True when only one team is loaded (single-team dataset).
    code_objects : list
        List of (name, Code) pairs for the active period; drives the
        timeline phase/possession strips.
    timeline_total_frames : int
        Frame count used to scale the timeline strip width.
    timeline_width : int or None
        Pitch-anchored pixel width of the timeline; set on each canvas
        resize and None until the first resize fires.
    """

    # --- playback / transport --------------------------------------------- #
    current_frame: int = 0
    max_frames: int = 0
    playing: bool = False
    play_speed: int = 25  # clamped FPS the worker schedules against
    original_fps: float | None = None  # native data rate; speed buttons derive from this
    last_slider_sync_frame: int = -1

    # --- period / teams --------------------------------------------------- #
    selected_half: str = "firstHalf"
    selected_teams: dict = field(default_factory=dict)
    initialized: bool = False

    # --- renderer objects (set during init) ------------------------------- #
    mapper: Any = None
    pitch_renderer: Any = None
    player_renderer: Any = None
    # dict[str, OverlayAdapter]: populated lazily in the model-fitted callback.
    active_adapters: dict = field(default_factory=dict)
    voronoi_alpha_value: float = 0.3  # persisted across re-fits

    # --- mouse ------------------------------------------------------------ #
    last_hover: Any = None

    # --- cached data references (set at init / refreshed on DATA_LOADED) --- #
    cached_pos_data: Any = None
    cached_team_names: Any = None
    cached_is_single: bool = False

    # --- timeline state --------------------------------------------------- #
    code_objects: list = field(default_factory=list)  # list[(name, Code)] for the period
    timeline_total_frames: int = 0
    timeline_width: int | None = None  # pitch-anchored width; set on resize

    # ---------------------------------------------------------------------- #
    # Dict-compatibility shim: lets external consumers use subscript /
    # .get / .setdefault / .pop / in without attribute rewrites.
    # ---------------------------------------------------------------------- #

    def __getitem__(self, key: str) -> Any:
        """Return the field value for *key* or raise KeyError if absent."""
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def __setitem__(self, key: str, value: Any) -> None:
        """Set the field *key* to *value*."""
        setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        """Return True if the state has an attribute named *key*."""
        return hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        """Return the field value for *key*, or *default* if absent."""
        return getattr(self, key, default)

    def setdefault(self, key: str, default: Any = None) -> Any:
        """Return the field value for *key*; set it to *default* first if absent."""
        if not hasattr(self, key):
            setattr(self, key, default)
            return default
        return getattr(self, key)

    def pop(self, key: str, default: Any = _MISSING) -> Any:
        """Remove and return the field *key*; raise KeyError if absent and no default given."""
        if hasattr(self, key):
            value = getattr(self, key)
            delattr(self, key)
            return value
        if default is _MISSING:
            raise KeyError(key)
        return default


# ---------------------------------------------------------------------------
# App reference: rebound via state.app_instance = app in _on_app_initialized.
# ---------------------------------------------------------------------------
app_instance = None

# ---------------------------------------------------------------------------
# Central shared-state singleton.
# ---------------------------------------------------------------------------
viz_state: ViewerState = ViewerState()

# ---------------------------------------------------------------------------
# Resize detection: list-wrapped so sub-modules rebind the inner value
# without needing a global declaration.
# ---------------------------------------------------------------------------
_last_drawlist_size: list[int] = [0, 0]
_first_resize_done: list[bool] = [False]
