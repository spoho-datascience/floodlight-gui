"""Resolve a registry descriptor to structured upstream-docstring help.

This module is DPG-free at module scope and must remain so.
``floodlight.*`` sub-modules are imported lazily inside
``_resolve_and_parse_cached`` via ``importlib.import_module``; no floodlight
symbol is imported at module scope. The module never raises: any broken
resolution returns a ``ParsedHelp`` with ``available=False`` instead of
propagating the exception to the caller.

Layering: ``core/help/`` (DPG-free backend). Consumed by
``tabs/_shared/help_popup.py`` (DPG layer) for in-GUI ``?`` button rendering.

Public surface:
  - ``ParsedHelp`` -- 9-field frozen dataclass
  - ``get_descriptor_help(descriptor, registry_name, container_hint="") -> ParsedHelp``
"""

from __future__ import annotations

import functools
import importlib
import inspect
import logging
from dataclasses import dataclass
from typing import Any

from floodlight_gui.core.help import _INIT_BOILERPLATE
from floodlight_gui.core.help.docstring_parser import parse_full_docstring

__all__ = ["ParsedHelp", "get_descriptor_help"]

logger = logging.getLogger(__name__)


_CANNED_UNAVAILABLE_BODY = "Upstream documentation not available."


# --------------------------------------------------------------------------- #
# Dataclass shape: ParsedHelp
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParsedHelp:
    """Structured help payload for one descriptor.

    Produced by ``get_descriptor_help`` and consumed by the help-popup renderer.
    ``body`` is always a non-empty string: either the upstream docstring or the
    canned fallback ``_CANNED_UNAVAILABLE_BODY``. ``available`` is ``True`` only
    when real upstream text was found.

    Attributes
    ----------
    title : str
        Display name from the descriptor (e.g. "Velocity Model").
    source_path : str
        Dotted import path of the resolved upstream object (e.g.
        "floodlight.models.kinematics.VelocityModel").
    body : str
        Full upstream docstring or the canned fallback. Never empty.
    returns : str or None
        Content of the upstream ``Returns`` section, if any.
    notes : str or None
        Content of the upstream ``Notes`` section, if any.
    references : str or None
        Content of the upstream ``References`` section, if any.
    examples : str or None
        Content of the upstream ``Examples`` section, if any.
    descriptor_summary : str or None
        ``description`` field from the registry descriptor (GUI-side summary).
    available : bool
        ``True`` when real upstream documentation was found.
    """

    title: str
    source_path: str
    body: str  # never empty: upstream text or canned fallback
    returns: str | None = None
    notes: str | None = None
    references: str | None = None
    examples: str | None = None
    descriptor_summary: str | None = None
    available: bool = False


# --------------------------------------------------------------------------- #
# Public entry point: get_descriptor_help
# --------------------------------------------------------------------------- #


def get_descriptor_help(
    descriptor: dict[str, Any],
    registry_name: str,
    container_hint: str = "",
) -> ParsedHelp:
    """Resolve a registry descriptor to a ``ParsedHelp``.

    Never raises. Any failure during import, attribute access, or parsing
    returns a ``ParsedHelp`` with ``available=False`` instead of propagating
    the exception.

    Resolution is a 4-tier fallback chain:

    1. Upstream class/function docstring (real text, ``available=True``).
    2. ``__init__`` or ``fit`` docstring for MODELS when ``container_hint``
       is "init" or "fit" (falls back to class doc when ``__init__``
       carries only boilerplate).
    3. IO composite: one merged body per provider (``### {loader}`` sections).
    4. Canned fallback ``_CANNED_UNAVAILABLE_BODY`` (``available=False``).

    ``container_hint`` controls which member of a class-based upstream is
    resolved for MODELS (``""`` class doc, ``"init"`` initialiser,
    ``"fit"`` fit method). For TRANSFORMS, METRICS, and XY_OPS the hint is
    ignored and the function itself is always the target.

    Parameters
    ----------
    descriptor : dict
        A single registry entry (from MODEL_REGISTRY, IO_REGISTRY, etc.).
    registry_name : str
        Registry the descriptor belongs to: "MODELS", "IO", "TRANSFORMS",
        "METRICS", or "XY_OPS".
    container_hint : str, optional
        Sub-target for MODELS: ``""`` (class), ``"init"``, or ``"fit"``.
        Ignored for all other registry types.

    Returns
    -------
    ParsedHelp
        Structured help payload. ``available=False`` and ``body`` set to the
        canned fallback when no upstream documentation could be retrieved.
    """
    # Coerce None to "" so ParsedHelp.title (annotated str) is always a str.
    title = descriptor.get("display_name") or ""
    descriptor_summary = descriptor.get("description") or None

    try:
        # IO: multi-loader composite (one ParsedHelp per provider).
        if registry_name == "IO":
            return _build_io_composite(descriptor, title, descriptor_summary)

        # XY_OPS without function_path cannot be resolved.
        if registry_name == "XY_OPS" and not descriptor.get("function_path"):
            return ParsedHelp(
                title=title,
                source_path="",
                body=_CANNED_UNAVAILABLE_BODY,
                descriptor_summary=descriptor_summary,
                available=False,
            )

        # MODELS: class-based; container_hint selects class / init / fit.
        if registry_name == "MODELS":
            class_path = descriptor.get("class_path", "")
            if not class_path:
                return ParsedHelp(
                    title=title,
                    source_path="",
                    body=_CANNED_UNAVAILABLE_BODY,
                    descriptor_summary=descriptor_summary,
                    available=False,
                )
            ph = _resolve_and_parse_cached(class_path, container_hint or "")
            return _finalize_descriptor(
                ph,
                title=title,
                descriptor_summary=descriptor_summary,
            )

        # TRANSFORMS / METRICS / XY_OPS-with-function_path: function-based.
        # container_hint is ignored for these registry types.
        function_path = descriptor.get("function_path", "")
        if not function_path:
            return ParsedHelp(
                title=title,
                source_path="",
                body=_CANNED_UNAVAILABLE_BODY,
                descriptor_summary=descriptor_summary,
                available=False,
            )
        ph = _resolve_and_parse_cached(function_path, "")
        return _finalize_descriptor(
            ph,
            title=title,
            descriptor_summary=descriptor_summary,
        )

    except Exception as err:  # noqa: BLE001 -- never-raise contract (broken resolution -> available=False)
        logger.debug(
            "help extraction failed for %s/%s: %s",
            registry_name,
            descriptor.get("display_name", "?"),
            err,
        )
        return ParsedHelp(
            title=title,
            source_path="",
            body=_CANNED_UNAVAILABLE_BODY,
            descriptor_summary=descriptor_summary,
            available=False,
        )


# --------------------------------------------------------------------------- #
# Memoized resolver: lru_cache keyed on (source_path, container_hint)
# --------------------------------------------------------------------------- #


@functools.lru_cache(maxsize=256)
def _resolve_and_parse_cached(source_path: str, container_hint: str) -> ParsedHelp:
    """Import ``source_path``, extract the docstring, and return a ``ParsedHelp``.

    Results are cached by ``(source_path, container_hint)`` so each
    upstream import and docstring parse happens at most once per session.
    With ~50 descriptors and up to 3 hint variants the cache stays well
    under its 256-entry limit. Failed resolutions are also cached so the
    outer ``get_descriptor_help`` path never retries a broken import.

    Parameters
    ----------
    source_path : str
        Dotted import path of the upstream object
        (e.g. "floodlight.models.kinematics.VelocityModel").
    container_hint : str
        Sub-target selector: ``""`` (object itself), ``"init"``, or ``"fit"``.

    Returns
    -------
    ParsedHelp
        ``title`` and ``descriptor_summary`` are left as placeholders
        (empty string / None); callers must stamp them via
        ``_finalize_descriptor``.
    """
    try:
        module_path, _, attr = source_path.rpartition(".")
        if not module_path or not attr:
            return ParsedHelp(
                title="",
                source_path=source_path,
                body=_CANNED_UNAVAILABLE_BODY,
                available=False,
            )

        # ``rpartition`` splits on the last '.', which handles
        # ``module.func`` and ``module.Class`` but not
        # ``module.Class.method`` (the import of "module.Class" as a module
        # raises ``ModuleNotFoundError``). The except block re-splits one
        # level higher and walks the remaining segments as attributes to
        # cover nested paths like ``floodlight.core.xy.XY.translate``.
        try:
            module = importlib.import_module(module_path)
            upstream: Any = module
            for part in attr.split("."):
                upstream = getattr(upstream, part)
        except (ImportError, AttributeError):
            # ``module_path`` was actually ``module.Class``; re-split so
            # ``head`` is the real importable module and the remainder
            # walks as attribute segments.
            head, _, tail = module_path.rpartition(".")
            if not head:
                raise
            module = importlib.import_module(head)
            upstream = module
            for part in (tail + "." + attr).split("."):
                upstream = getattr(upstream, part)

        # Route to the correct container target (class, init, or fit).
        target = _route_target(upstream, container_hint)

        body_text = inspect.getdoc(target)
        # When ``__init__`` returns only boilerplate, fall back to the
        # class docstring (boilerplate carries no useful information).
        if container_hint == "init" and body_text == _INIT_BOILERPLATE and target is not upstream:
            body_text = inspect.getdoc(upstream)

        # Body is binary: real upstream text or the canned fallback.
        if not body_text or body_text == _INIT_BOILERPLATE:
            body = _CANNED_UNAVAILABLE_BODY
            available = False
        else:
            body = body_text
            available = True

        # ``parse_full_docstring`` returns a 7-key dict:
        # short_description, long_description, params, returns, examples,
        # notes, references. Only the four section keys are consumed here;
        # the remaining three are intentionally ignored. Do not remove keys
        # from the parser's returned shape without auditing all consumers.
        sections = parse_full_docstring(body if available else None)

        return ParsedHelp(
            title="",  # stamped by caller via _finalize_descriptor
            source_path=source_path,
            body=body,
            returns=sections.get("returns"),
            notes=sections.get("notes"),
            references=sections.get("references"),
            examples=sections.get("examples"),
            descriptor_summary=None,  # stamped by caller via _finalize_descriptor
            available=available,
        )

    except Exception as err:  # noqa: BLE001 -- never-raise contract
        logger.debug(
            "resolve_and_parse failed for %s/%s: %s",
            source_path,
            container_hint,
            err,
        )
        return ParsedHelp(
            title="",
            source_path=source_path,
            body=_CANNED_UNAVAILABLE_BODY,
            available=False,
        )


def _route_target(upstream: Any, container_hint: str) -> Any:
    """Return the sub-target of ``upstream`` selected by ``container_hint``.

    Parameters
    ----------
    upstream : Any
        The resolved upstream class or function object.
    container_hint : str
        ``"init"`` returns ``upstream.__init__``; ``"fit"`` returns
        ``upstream.fit``; any other value (including ``""``) returns
        ``upstream`` unchanged.

    Returns
    -------
    Any
        The selected sub-target, or ``upstream`` when the hint is unknown.
    """
    if container_hint == "init":
        return getattr(upstream, "__init__", upstream)
    if container_hint == "fit":
        return getattr(upstream, "fit", upstream)
    return upstream


# --------------------------------------------------------------------------- #
# Helper: stamp per-call title + descriptor_summary onto a cached ParsedHelp
# --------------------------------------------------------------------------- #


def _finalize_descriptor(
    cached: ParsedHelp,
    *,
    title: str,
    descriptor_summary: str | None,
) -> ParsedHelp:
    """Return a copy of ``cached`` with ``title`` and ``descriptor_summary`` filled in.

    The cache stores these fields as placeholders (empty string / None) so
    the same cached body can be reused across different call sites that
    supply different display names or summaries.

    Parameters
    ----------
    cached : ParsedHelp
        The cached resolution result (``title`` and ``descriptor_summary``
        are placeholder values).
    title : str
        Descriptor display name to stamp into the copy.
    descriptor_summary : str or None
        Registry ``description`` value to stamp into the copy.

    Returns
    -------
    ParsedHelp
        A new ``ParsedHelp`` identical to ``cached`` except for the
        per-call fields.
    """
    return ParsedHelp(
        title=title,
        source_path=cached.source_path,
        body=cached.body,
        returns=cached.returns,
        notes=cached.notes,
        references=cached.references,
        examples=cached.examples,
        descriptor_summary=descriptor_summary,
        available=cached.available,
    )


# --------------------------------------------------------------------------- #
# IO multi-loader composite
# --------------------------------------------------------------------------- #


def _build_io_composite(
    descriptor: dict[str, Any],
    title: str,
    descriptor_summary: str | None,
) -> ParsedHelp:
    """Build a composite ``ParsedHelp`` from all ``loader_functions`` in an IO descriptor.

    Each loader contributes a ``### {loader_key}`` section to the merged
    body. Sections are joined in dict-insertion order. Disabled loader
    entries are skipped so they do not produce misleading
    "Upstream documentation not available." stanzas.

    Parameters
    ----------
    descriptor : dict
        An IO_REGISTRY entry with ``"module"`` and ``"loader_functions"`` keys.
    title : str
        Display name for the provider (used as ``ParsedHelp.title``).
    descriptor_summary : str or None
        Registry ``description`` value (used as ``ParsedHelp.descriptor_summary``).

    Returns
    -------
    ParsedHelp
        ``available=True`` when at least one loader resolved successfully;
        ``available=False`` when all loaders are disabled, missing, or failed.
    """
    module = descriptor.get("module", "")
    loader_functions: dict[str, dict[str, Any]] = descriptor.get("loader_functions", {}) or {}

    if not loader_functions:
        return ParsedHelp(
            title=title,
            source_path=module,
            body=_CANNED_UNAVAILABLE_BODY,
            descriptor_summary=descriptor_summary,
            available=False,
        )

    body_parts: list[str] = []
    any_available = False
    first_source_path: str | None = None

    for loader_key, loader_meta in loader_functions.items():
        # Skip disabled loaders to avoid emitting misleading "no docs" stanzas.
        if loader_meta.get("disabled"):
            continue
        # Dataset providers declare ``{"class": "..."}`` instead of
        # ``{"function": "..."}``. Accept either key so dataset descriptors
        # resolve to their upstream class docstring.
        func_name = loader_meta.get("function") or loader_meta.get("class") or ""
        if not module or not func_name:
            continue
        loader_source_path = f"{module}.{func_name}"
        if first_source_path is None:
            first_source_path = loader_source_path

        loader_ph = _resolve_and_parse_cached(loader_source_path, "")
        body_parts.append(f"### {loader_key}\n{loader_ph.body}")
        if loader_ph.available:
            any_available = True

    composite_body = "\n\n".join(body_parts) if body_parts else _CANNED_UNAVAILABLE_BODY

    return ParsedHelp(
        title=title,
        source_path=first_source_path or module,
        body=composite_body,
        descriptor_summary=descriptor_summary,
        available=any_available,
    )
