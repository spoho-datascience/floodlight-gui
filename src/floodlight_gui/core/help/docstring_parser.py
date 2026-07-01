"""Docstring text parser for the in-GUI help system.

Parses a callable's docstring (or a raw docstring string) and extracts
parameter descriptions or full structured sections. Supports NumPy/RST,
Google, and Sphinx ``:param:`` styles.

Module-level invariants: DPG-free (no ``dearpygui`` import at any scope),
stdlib-only at module scope (``docstring_parser`` is imported at module
scope because it is a pure-Python dependency with no GUI coupling). This
module lives inside ``core/``, which must remain importable without DPG.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable

import docstring_parser

__all__ = ["parse_full_docstring", "parse_param_docstring"]


# --------------------------------------------------------------------------- #
# Style D: explicit Sphinx directive  ``:param foo: <description>``
# --------------------------------------------------------------------------- #
_SPHINX_PARAM_RE = re.compile(
    r"^\s*:param\s+(?P<name>[A-Za-z_][A-Za-z_0-9]*)\s*:\s*(?P<desc>.+?)\s*$",
    re.MULTILINE,
)

# --------------------------------------------------------------------------- #
# Style A/B: numpy / RST section -- ``Parameters`` heading followed by ``----``
# --------------------------------------------------------------------------- #
# A heading line is ALL hyphens with length >= 4 (numpy convention).
_NUMPY_SECTION_HEADERS = ("Parameters", "Other Parameters")

# Param line shape inside a numpy section: ``foo : type`` or ``foo: type`` or
# ``foo`` alone. We anchor on the param name at column 0 (after dedent) followed
# by optional whitespace + colon + the rest of the line (the type annotation,
# which we discard for tooltip purposes).
_NUMPY_PARAM_LINE_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z_0-9]*)\s*(?::\s*.*)?$")

# --------------------------------------------------------------------------- #
# Style C: Google ``Args:`` (or ``Arguments:``) block --
# name + colon + same-line description
# --------------------------------------------------------------------------- #
_GOOGLE_SECTION_HEADERS = ("Args:", "Arguments:", "Parameters:")
_GOOGLE_PARAM_RE = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z_0-9]*)\s*:\s*(?P<desc>.+?)\s*$")


def _get_docstring(target: Callable | str | None) -> str | None:
    """Return a normalized docstring for a callable or a raw string.

    Uses ``inspect.getdoc`` for callables (handles indentation) and
    ``inspect.cleandoc`` for strings. Returns ``None`` when the input
    is ``None`` or the string is blank.
    """
    if target is None:
        return None
    if isinstance(target, str):
        return inspect.cleandoc(target) if target.strip() else None
    doc = inspect.getdoc(target)
    return doc if doc else None


def _first_sentence(line: str) -> str:
    """Return only the first sentence of ``line``.

    A sentence boundary is the first ``". "`` (period + space) substring. If
    the line ends with a period but no continuation, return as-is. Avoids
    splitting on common abbreviations is intentionally out of scope -- the
    upstream floodlight docstrings tested here use sentence-final periods
    consistently.
    """
    idx = line.find(". ")
    if idx == -1:
        return line
    return line[: idx + 1]


def _try_sphinx_style(doc: str, param_name: str) -> str | None:
    """Return the description for ``:param param_name: <desc>`` if present."""
    for m in _SPHINX_PARAM_RE.finditer(doc):
        if m.group("name") == param_name:
            return m.group("desc").strip()
    return None


def _try_google_style(doc: str, param_name: str) -> str | None:
    """Return the description for a Google-style ``<name>: <desc>`` entry.

    Searches inside ``Args:``, ``Arguments:``, or ``Parameters:`` blocks only.
    """
    lines = doc.splitlines()
    in_block = False
    block_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if stripped in _GOOGLE_SECTION_HEADERS:
            in_block = True
            block_indent = None
            continue
        if in_block:
            if not stripped:
                # blank line -- still in block per Google convention
                continue
            current_indent = len(line) - len(line.lstrip())
            if block_indent is None:
                block_indent = current_indent
            if current_indent < block_indent:
                # Dedent below block -- block ended
                in_block = False
                continue
            m = _GOOGLE_PARAM_RE.match(line)
            if m and m.group("name") == param_name:
                return m.group("desc").strip()
    return None


def _try_numpy_style(doc: str, param_name: str) -> str | None:
    """Return the first description line for a numpy/RST-style ``Parameters`` block."""
    lines = doc.splitlines()
    i = 0
    while i < len(lines) - 1:
        heading = lines[i].strip()
        underline = lines[i + 1].strip()
        if heading in _NUMPY_SECTION_HEADERS and set(underline) == {"-"} and len(underline) >= 4:
            # Found a numpy section. Walk forward until dedent / next section / EOF.
            section_indent: int | None = None
            j = i + 2
            while j < len(lines):
                line = lines[j]
                stripped = line.strip()
                if not stripped:
                    j += 1
                    continue
                current_indent = len(line) - len(line.lstrip())
                if section_indent is None:
                    section_indent = current_indent
                if current_indent < section_indent:
                    break  # section ended (dedent)
                # Check next-line underline to detect a new section header.
                if (
                    j + 1 < len(lines)
                    and lines[j + 1].strip()
                    and set(lines[j + 1].strip()) == {"-"}
                    and len(lines[j + 1].strip()) >= 4
                ):
                    break  # new section header reached
                # Is this a param name line at the section's own indent?
                if current_indent == section_indent:
                    m = _NUMPY_PARAM_LINE_RE.match(stripped)
                    if m and m.group("name") == param_name:
                        # The next non-blank line at deeper indent is the
                        # description's first line. Truncate at the first
                        # ". " split so multi-line descriptions don't bleed
                        # continuation fragments into the tooltip.
                        k = j + 1
                        while k < len(lines):
                            desc_line = lines[k]
                            desc_stripped = desc_line.strip()
                            if not desc_stripped:
                                k += 1
                                continue
                            desc_indent = len(desc_line) - len(desc_line.lstrip())
                            if desc_indent > current_indent:
                                return _first_sentence(desc_stripped)
                            break
                        return None
                j += 1
            # Did not find param_name in this section; continue scanning the
            # rest of the docstring (defensive against multiple Parameters
            # blocks, unlikely but harmless).
            i = j
            continue
        i += 1
    return None


def parse_full_docstring(text: str | None) -> dict:
    """Split a full docstring into structured sections via ``docstring_parser``.

    Uses ``docstring_parser.parse(text, style=docstring_parser.Style.AUTO)``
    to handle the NumPy/Google mix found in floodlight and common third-party
    targets. The library applies an internal heuristic when AUTO is ambiguous.

    Parameters
    ----------
    text : str | None
        Raw docstring text (typically ``inspect.getdoc(target)``). ``None`` or
        ``""`` returns the empty-shape dict.

    Returns
    -------
    dict
        Keys: ``short_description`` (str), ``long_description`` (str),
        ``params`` (list[docstring_parser.DocstringParam]), ``returns``
        (str | None), ``examples`` (str | None), ``notes`` (str | None),
        ``references`` (str | None). Never raises.

    Notes
    -----
    The ``params`` key is a read-only passthrough of raw
    ``docstring_parser.DocstringParam`` objects. Per-param descriptions for
    tooltip use are resolved separately via ``parse_param_docstring``.
    Do not shrink the returned shape without auditing all consumers and the
    ``tests/test_docstring_parser.py`` key-presence assertions.
    """
    if not text:
        return {
            "short_description": "",
            "long_description": "",
            "params": [],
            "returns": None,
            "examples": None,
            "notes": None,
            "references": None,
        }
    try:
        parsed = docstring_parser.parse(text, style=docstring_parser.Style.AUTO)
    except Exception:  # noqa: BLE001 -- never-raise contract: any parser error falls back below
        # The "Never raises" contract requires catching all exceptions from the
        # third-party parser (IndexError, AttributeError, ValueError on
        # pathological input can propagate from docstring_parser internals).
        # Fall back to a plain-text split rather than propagating.
        return {
            "short_description": text.splitlines()[0].strip() if text.splitlines() else "",
            "long_description": "\n".join(text.splitlines()[1:]).strip(),
            "params": [],
            "returns": None,
            "examples": None,
            "notes": None,
            "references": None,
        }

    examples_text: str | None = None
    if parsed.examples:
        examples_text = "\n".join((e.description or "") for e in parsed.examples).strip() or None

    # docstring_parser exposes free-form Notes / References as Meta entries
    # whose `args[0]` names the section. Collect their descriptions in order.
    notes_text: str | None = None
    references_text: str | None = None
    for meta in parsed.meta:
        if not getattr(meta, "args", None):
            continue
        head = meta.args[0].lower() if meta.args[0] else ""
        if head in ("note", "notes") and meta.description:
            notes_text = (
                (notes_text + "\n\n" + meta.description) if notes_text else meta.description
            )
        elif head in ("reference", "references") and meta.description:
            references_text = (
                (references_text + "\n\n" + meta.description)
                if references_text
                else meta.description
            )

    return {
        "short_description": parsed.short_description or "",
        "long_description": parsed.long_description or "",
        "params": list(parsed.params),
        "returns": parsed.returns.description if parsed.returns else None,
        "examples": examples_text,
        "notes": notes_text,
        "references": references_text,
    }


def parse_param_docstring(
    target: Callable | str | None,
    param_name: str,
) -> str | None:
    """Return the first description line for ``param_name`` in ``target``'s docstring.

    ``target`` may be:
      - a callable (class, function, method, bound method): ``inspect.getdoc``
        normalizes the docstring before parsing.
      - a docstring string: ``inspect.cleandoc`` normalizes whitespace.
      - None: returns None defensively.

    Returns the first line of the param's description (no multi-line
    concatenation) or None if the param is not found in any supported style.

    Style precedence: Sphinx ``:param:`` -> Google ``Args:`` -> Numpy/RST
    ``Parameters``. The first style that yields a match wins; the rest are
    not consulted. Real floodlight docstrings use exactly one style, so
    precedence here is a defensive tie-breaker.

    Parameters
    ----------
    target : callable or str or None
        The callable whose docstring to parse, or a raw docstring string.
    param_name : str
        The parameter name to look up.

    Returns
    -------
    str or None
        First description line for the parameter, or None if not found.
    """
    doc = _get_docstring(target)
    if not doc:
        return None
    if not param_name:
        return None

    return (
        _try_sphinx_style(doc, param_name)
        or _try_google_style(doc, param_name)
        or _try_numpy_style(doc, param_name)
    )
