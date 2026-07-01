"""Model-tab labels and taxonomy: category order, display-name maps, and DPG tag generators.

Single source of truth for the model-key to display-name mapping and the upstream
category taxonomy. DPG-free at module scope; safe to import in tests and backend code.

Invariant: display names are globally unique across MODEL_REGISTRY so a display
string round-trips to exactly one model key.
"""

from __future__ import annotations

from floodlight_gui.registry.models import MODEL_REGISTRY

# Upstream taxonomy order. kinematics is the cold-start default.
_TAXONOMY_ORDER = ("kinematics", "geometry", "kinetics", "space")


def _categories() -> list[str]:
    """Return categories present in the registry, filtered to taxonomy order."""
    present = {desc["category"] for desc in MODEL_REGISTRY.values()}
    return [c for c in _TAXONOMY_ORDER if c in present]


CATEGORY_ORDER: list[str] = _categories()
DEFAULT_CATEGORY: str = CATEGORY_ORDER[0] if CATEGORY_ORDER else "kinematics"


def models_in_category(category: str) -> list[str]:
    """Return model keys whose descriptor category matches *category*, in registry order.

    Parameters
    ----------
    category : str
        One of the taxonomy categories, e.g. "kinematics" or "geometry".

    Returns
    -------
    list[str]
        Registry keys for models in *category*; empty list if none match.
    """
    return [k for k, d in MODEL_REGISTRY.items() if d["category"] == category]


def display_name(model_key) -> str:
    """Return the display name for a model key, falling back to the key itself.

    Parameters
    ----------
    model_key : str
        A key in ``MODEL_REGISTRY``.

    Returns
    -------
    str
        The ``display_name`` from the descriptor, or *model_key* as a string
        if the key is absent.
    """
    return MODEL_REGISTRY.get(str(model_key), {}).get("display_name", str(model_key))


def display_names_in_category(category: str) -> list[str]:
    """Return display names for all models in *category*, in registry order.

    Parameters
    ----------
    category : str
        One of the taxonomy categories.

    Returns
    -------
    list[str]
        Ordered display names for models in *category*; empty list if none match.
    """
    return [display_name(k) for k in models_in_category(category)]


def _build_display_to_key() -> dict[str, str]:
    """Build and return the display-name-to-model-key reverse map, asserting uniqueness."""
    mapping: dict[str, str] = {}
    for key, desc in MODEL_REGISTRY.items():
        name = desc["display_name"]
        assert name not in mapping, f"Duplicate model display_name: {name!r}"
        mapping[name] = key
    return mapping


_DISPLAY_TO_KEY: dict[str, str] = _build_display_to_key()


def key_for_display(display: str) -> str | None:
    """Return the model key for a display name, or None if not found.

    Parameters
    ----------
    display : str
        A display name as returned by ``display_name``.

    Returns
    -------
    str or None
        The corresponding model key, or None if *display* is not in the registry.
    """
    return _DISPLAY_TO_KEY.get(display)


def category_tab_tag(category: str) -> str:
    """Return the DPG tag for the category tab item of *category*.

    Parameters
    ----------
    category : str
        A taxonomy category name.

    Returns
    -------
    str
        DPG widget tag string.
    """
    return f"models_category_{category}_tab"


def model_combo_tag(category: str) -> str:
    """Return the DPG tag for the model-selection combo widget of *category*.

    Parameters
    ----------
    category : str
        A taxonomy category name.

    Returns
    -------
    str
        DPG widget tag string.
    """
    return f"models_model_combo_{category}"


def help_group_tag(category: str) -> str:
    """Return the DPG tag for the help-button group widget of *category*.

    Parameters
    ----------
    category : str
        A taxonomy category name.

    Returns
    -------
    str
        DPG widget tag string.
    """
    return f"models_combo_help_group_{category}"
