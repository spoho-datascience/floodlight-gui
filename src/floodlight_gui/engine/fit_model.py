"""MODEL_REGISTRY executor: resolve, instantiate, and fit a floodlight model.

DPG-free at module scope. Lives in the ``engine/`` layer
between ``registry/`` (descriptor source of truth) and ``tabs/model/``
(UI caller). All DPG interaction stays in the tab; this module only touches
floodlight objects and app accessors.
"""

from __future__ import annotations

import importlib
import inspect
import logging

from floodlight_gui.core.xy_access import get_xy_for_period_team
from floodlight_gui.registry.models import MODEL_REGISTRY

logger = logging.getLogger(__name__)


def _import_class(class_path):
    """Import a class from a dotted path (e.g. 'floodlight.models.kinematics.VelocityModel')."""
    module_path, class_name = class_path.rsplit(".", 1)
    return getattr(importlib.import_module(module_path), class_name)


def is_multi_team(model_key):
    """Return whether a model requires XY from multiple teams.

    Single source of truth for the multi-team dispatch decision used by
    both the UI gating logic and ``fit_model``. Centralising it here means
    a future rule change (e.g. counting only XY-typed inputs) propagates to
    both callers in one edit.

    Parameters
    ----------
    model_key : str
        Key into ``MODEL_REGISTRY``.

    Returns
    -------
    bool
        True when the descriptor declares more than one input.

    Raises
    ------
    KeyError
        If ``model_key`` is not in ``MODEL_REGISTRY``.
    """
    if model_key not in MODEL_REGISTRY:
        raise KeyError(f"Unknown model_key: {model_key!r}")
    return len(MODEL_REGISTRY[model_key]["inputs"]) > 1


def _select_teams_for_inputs(inputs, teams):
    """Resolve descriptor input keys to team names using the role-matching policy.

    Multi-team models declare inputs such as ``xy_home``/``xy_away`` (Voronoi)
    or ``xy1``/``xy2`` (NearestOpponent). Positional indexing over the raw
    ``teams`` list is unsafe because some providers return teams in an order
    that includes Ball before Home/Away, so this function applies an explicit
    role-matching policy instead.

    Policy (applied per input key, in priority order):

    1. A ``home``/``away`` hint in the input key prefers an exact
       case-insensitive match against ``teams``, falling back to the first
       substring match. When more than one team substring-matches the same
       role a debug line is emitted and the first hit is used.
    2. Ball is always excluded from the candidate list.
    3. Any input with no role hint (or whose matched team was already consumed
       by an earlier input) falls back to the first unused non-Ball team in
       ``teams`` order, emitting a debug line.

    Parameters
    ----------
    inputs : Mapping[str, dict]
        ``MODEL_REGISTRY[model_key]['inputs']``: ordered dict of input
        descriptors keyed by input name.
    teams : list[str]
        Team names from ``app_instance.get_team_names()``.

    Returns
    -------
    list[str]
        One team name per input key, in the same order ``inputs`` iterates.

    Raises
    ------
    ValueError
        If fewer non-Ball teams are available than inputs requested.
    """
    non_ball = [t for t in teams if t.lower() != "ball"]
    if len(non_ball) < len(inputs):
        raise ValueError(
            f"Multi-team model needs {len(inputs)} teams but only "
            f"{len(non_ball)} non-Ball team(s) available: {teams!r}"
        )

    def _by_role(role):
        """Return the first non-Ball team matching *role* (exact match, then substring)."""
        role_lower = role.lower()
        # Pass 1: exact case-insensitive match.
        for t in non_ball:
            if t.lower() == role_lower:
                return t
        # Pass 2: substring match (first hit). Log when more than one team
        # matches so the ambiguity is observable in logs.
        substring_hits = [t for t in non_ball if role_lower in t.lower()]
        if len(substring_hits) > 1:
            logger.debug(
                "Multiple teams substring-match role %r: %s. Using first (%r). "
                "Disambiguate via exact-match team names if this is incorrect.",
                role,
                substring_hits,
                substring_hits[0],
            )
        return substring_hits[0] if substring_hits else None

    resolved = []
    used = set()
    for input_key in inputs:
        key_lower = input_key.lower()
        chosen = None
        role_hint = None
        # Inputs hinting a role (home/away) match by name; any other input
        # (e.g. xy1/xy2) falls through to the positional fallback below.
        if "home" in key_lower:
            role_hint = "home"
            chosen = _by_role("home")
        elif "away" in key_lower:
            role_hint = "away"
            chosen = _by_role("away")

        if chosen is not None and chosen not in used:
            resolved.append(chosen)
            used.add(chosen)
        else:
            # The matched team was already consumed by a stronger hint, or no
            # hint matched at all. Fall back to the first unused non-Ball team
            # and emit a debug line so the silent downgrade is observable.
            fallback = None
            for t in non_ball:
                if t not in used:
                    fallback = t
                    break
            if fallback is not None:
                if role_hint is not None and chosen is not None and chosen in used:
                    logger.debug(
                        "Role hint %r for input %r matched team %r but it is "
                        "already assigned; falling back to positional pick %r.",
                        role_hint,
                        input_key,
                        chosen,
                        fallback,
                    )
                elif role_hint is not None and chosen is None:
                    logger.debug(
                        "Role hint %r for input %r did not match any team in "
                        "%s; falling back to positional pick %r.",
                        role_hint,
                        input_key,
                        non_ball,
                        fallback,
                    )
                resolved.append(fallback)
                used.add(fallback)
    return resolved


def fit_model(
    app_instance,
    model_key,
    half_name,
    team_name,
    ui_params=None,
    team_names=None,
):
    """Instantiate and fit a model using its MODEL_REGISTRY descriptor.

    Parameters
    ----------
    app_instance : FloodlightApp
        Live application instance used to retrieve XY data and pitch.
    model_key : str
        Key into ``MODEL_REGISTRY`` (e.g. ``"velocity"``, ``"centroid"``,
        ``"discrete_voronoi"``).
    half_name : str
        Temporal period key (e.g. ``"firstHalf"``).
    team_name : str
        Team key used for single-team models. Ignored for multi-team models
        when ``team_names`` is supplied.
    ui_params : dict, optional
        Parameter values collected from the UI widgets.
    team_names : list[str], optional
        Explicit per-input team names for multi-team models
        (``fit_xy_arity > 1``). When supplied, the user's Team A / Team B
        combo picks are honored verbatim and ``_select_teams_for_inputs`` is
        bypassed. When ``None`` (default), ``_select_teams_for_inputs``
        resolves teams from the descriptor input keys. Ignored for
        single-team models.

    Returns
    -------
    object
        Fitted model instance (floodlight model class, descriptor-specific).

    Raises
    ------
    KeyError
        If ``model_key`` is not in ``MODEL_REGISTRY``.
    TypeError
        If the descriptor declares init_params not accepted by the model
        class constructor.
    ValueError
        If ``team_names`` length does not match the number of descriptor
        inputs, or if no XY data is available for the resolved team/period.
    """
    desc = MODEL_REGISTRY[model_key]
    ModelClass = _import_class(desc["class_path"])
    ui_params = ui_params or {}

    # Filter init_params against the actual constructor signature so a
    # descriptor that drifts from the upstream class raises a clear error
    # immediately rather than a generic TypeError at fit time.
    accepted_init_params = set(inspect.signature(ModelClass.__init__).parameters) - {"self"}
    init_kw = {}
    unknown_init_params = []
    for pname, pdesc in desc.get("init_params", {}).items():
        if pname not in accepted_init_params:
            unknown_init_params.append(pname)
            continue
        if pname in ui_params:
            init_kw[pname] = ui_params[pname]
        elif pdesc.get("type") == "Pitch":
            init_kw[pname] = app_instance.pitch
        elif "default" in pdesc:
            init_kw[pname] = pdesc["default"]
    if unknown_init_params:
        raise TypeError(
            f"Model {model_key!r} descriptor declares init_params "
            f"{sorted(unknown_init_params)} not accepted by "
            f"{ModelClass.__name__}.__init__ (accepted: {sorted(accepted_init_params)})"
        )

    # XY-typed fit_params are passed positionally below; skip them here.
    fit_kw = {}
    for pname, pdesc in desc.get("fit_params", {}).items():
        if pdesc.get("type") == "XY":
            continue
        if pname in ui_params:
            fit_kw[pname] = ui_params[pname]
        elif "default" in pdesc:
            fit_kw[pname] = pdesc["default"]

    inputs = desc["inputs"]
    model = ModelClass(**init_kw)

    # Apply fit_param_coerce before the multi-/single-team split so BOTH
    # dispatch paths honor it. Descriptors that need coercion (e.g.
    # convex_hull wrapping exclude_xIDs as list-of-lists for the upstream
    # contract) declare ``fit_param_coerce`` in registry/models.py; all
    # other models omit it.
    coerce = desc.get("fit_param_coerce")
    if coerce is not None:
        fit_kw = coerce(fit_kw)

    # Use is_multi_team rather than an inline len(inputs) check so a future
    # rule change propagates to UI gating and dispatch in one edit.
    if is_multi_team(model_key):
        # When the GUI supplies explicit team_names (Team A / Team B
        # picks from the period-team selector), honor them verbatim. Fall
        # back to _select_teams_for_inputs for callers that do not yet pass
        # team_names (including headless tests).
        if team_names is not None:
            if len(team_names) != len(inputs):
                raise ValueError(
                    f"Model {model_key!r} declares {len(inputs)} inputs but "
                    f"team_names has {len(team_names)} entries: {team_names!r}"
                )
            resolved_teams = list(team_names)
        else:
            # Name-match descriptor input keys to actual team names so
            # providers that emit teams in arbitrary order cannot silently
            # feed Ball XY into xy_home.
            teams = app_instance.get_team_names()
            resolved_teams = _select_teams_for_inputs(inputs, teams)
        xy_args = []
        for input_key, t in zip(inputs, resolved_teams, strict=False):
            xy = get_xy_for_period_team(app_instance, half_name, t)
            if xy is None:
                raise ValueError(f"No XY data for {t} in {half_name} (input {input_key!r})")
            xy_args.append(xy)
        model.fit(*xy_args, **fit_kw)
    else:
        xy = get_xy_for_period_team(app_instance, half_name, team_name)
        if xy is None:
            raise ValueError(f"No XY data for {team_name} in {half_name}")
        # fit_param_coerce already applied above the multi-/single split.
        model.fit(xy, **fit_kw)

    return model
