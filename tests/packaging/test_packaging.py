"""Build and distribution-integrity contracts for the package.

C1 The non-UI import surface stays DPG-free: importing any backend module from
   a fresh interpreter must not transitively pull ``dearpygui`` into
   ``sys.modules`` (package root, voronoi palette, help-text backend, transform
   dispatcher, matplotlib export-overlay tree).
C2 ``pyproject.toml`` names no file that has been removed from the tree.
C3 Every single-file path in ``[tool.ruff.lint.per-file-ignores]`` exists on
   disk; glob patterns are skipped.
C4 ``python -m readme_renderer README.md`` exits cleanly so PyPI renders the
   project description.

The DPG-free check runs in a fresh subprocess that imports the target and
prints ``LEAKED=<repr of sorted dearpygui keys>``. In-process it would need to
delete ``floodlight_gui`` from ``sys.modules`` (else re-import is a no-op),
which would invalidate the ``bus`` singleton conftest holds and break unrelated
tests. Only ``dearpygui`` is asserted; matplotlib leaks transitively via
``floodlight.core.pitch`` and is allowed.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
README = REPO_ROOT / "README.md"


# --------------------------------------------------------------------------- #
# Guard 1: DPG-free import surface                                            #
# --------------------------------------------------------------------------- #

# Each case is a tuple of modules to import in the subprocess. The
# export-overlays case imports all three submodules to cover that subtree.
_IMPORT_TARGETS = [
    pytest.param(
        ("floodlight_gui",),
        id="package_root",
    ),
    pytest.param(
        ("floodlight_gui.rendering.voronoi_colors",),
        id="voronoi_colors",
    ),
    pytest.param(
        ("floodlight_gui.core.help.resolve",),
        id="help_resolve",
    ),
    pytest.param(
        ("floodlight_gui.engine.apply_transforms",),
        id="apply_transforms",
    ),
    pytest.param(
        (
            "floodlight_gui.rendering.export_overlays",
            "floodlight_gui.rendering.export_overlays.voronoi",
            "floodlight_gui.rendering.export_overlays.hull",
        ),
        id="export_overlays",
    ),
]


def _build_snippet(modules: tuple[str, ...]) -> str:
    """Return source for a subprocess that imports modules and reports leaks.

    Parameters
    ----------
    modules:
        Fully-qualified module names to import in the fresh interpreter.

    Returns
    -------
    str
        Python source that imports each module then prints
        ``LEAKED=<repr of sorted dearpygui keys in sys.modules>``.
    """
    imports = "".join(f"import {m}  # noqa: F401\n" for m in modules)
    return (
        "import sys\n" + imports + "leaked = sorted(k for k in sys.modules if 'dearpygui' in k)\n"
        "print('LEAKED=' + repr(leaked))\n"
    )


@pytest.mark.parametrize("modules", _IMPORT_TARGETS)
def test_import_is_dpg_free(modules: tuple[str, ...]) -> None:
    """Importing a backend module must not pull dearpygui into sys.modules.

    Spawns a fresh interpreter that imports the target module(s) and reports
    any ``dearpygui`` keys leaked into ``sys.modules``. Asserts the subprocess
    exited cleanly and the leaked list is empty. Only ``dearpygui`` is checked;
    matplotlib leaks transitively via ``floodlight.core.pitch`` and is allowed.

    Parameters
    ----------
    modules:
        Fully-qualified module names to import in the subprocess.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _build_snippet(modules)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"Subprocess import of {modules} failed (rc={proc.returncode}).\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    leaked_line = next(
        (line for line in proc.stdout.splitlines() if line.startswith("LEAKED=")),
        None,
    )
    assert leaked_line is not None, (
        f"Subprocess did not print expected LEAKED= marker.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    # Safe eval: the only thing after 'LEAKED=' is a repr() of list[str].
    leaked = ast.literal_eval(leaked_line[len("LEAKED=") :])
    assert not leaked, (
        f"Importing {modules} pulled in dearpygui: {leaked}. "
        f"The non-UI import surface must stay DPG-free so it can be imported "
        f"without a GUI runtime."
    )


# --------------------------------------------------------------------------- #
# Guard 2: pyproject.toml omits deleted paths                                 #
# --------------------------------------------------------------------------- #

_DELETED_NAMES = ["selection_tab.py", "AGENTS.md", "TRIAGE.md"]  # scan-dead-refs:allow


@pytest.mark.parametrize("name", _DELETED_NAMES)
def test_pyproject_omits_deleted_path(name: str) -> None:  # scan-dead-refs:allow
    """A deleted filename must not appear anywhere in pyproject.toml.

    Parameters
    ----------
    name:
        Filename of a deleted artifact that must be absent from the raw
        ``pyproject.toml`` text.
    """
    raw = PYPROJECT.read_text(encoding="utf-8")
    assert name not in raw, f"pyproject.toml still references deleted file {name!r}"


# --------------------------------------------------------------------------- #
# Guard 3: per-file-ignore paths exist                                        #
# --------------------------------------------------------------------------- #


def _load_per_file_ignores() -> dict[str, list[str]]:
    """Return the ``[tool.ruff.lint.per-file-ignores]`` mapping; empty if missing.

    Tries ``tomllib`` (Python 3.11+) first, falls back to ``tomli`` (3.10).
    If neither is available, returns an empty dict so the parametrized test
    degenerates to zero cases rather than failing.

    Returns
    -------
    dict[str, list[str]]
        Mapping of path/glob to ignored rule codes.
    """
    try:
        import tomllib  # stdlib in 3.11+

        with PYPROJECT.open("rb") as f:
            data = tomllib.load(f)
    except ImportError:
        try:
            import tomli as tomllib  # 3.10 fallback

            with PYPROJECT.open("rb") as f:
                data = tomllib.load(f)
        except ImportError:
            return {}
    return data.get("tool", {}).get("ruff", {}).get("lint", {}).get("per-file-ignores", {})


@pytest.mark.parametrize("path_str", sorted(_load_per_file_ignores().keys()))
def test_per_file_ignore_path_exists(path_str: str) -> None:
    """Every per-file-ignores path must point at an existing file or be a glob.

    Single-file paths (no ``*``) must exist on disk; glob patterns (e.g.
    ``tests/**``) are skipped because file-existence is not the right check
    for them.

    Parameters
    ----------
    path_str:
        A key from ``[tool.ruff.lint.per-file-ignores]``.
    """
    if "*" in path_str:
        pytest.skip(f"glob pattern not checked for existence: {path_str}")
    full = REPO_ROOT / path_str
    assert full.exists(), (
        f"pyproject.toml [tool.ruff.lint.per-file-ignores] references "
        f"{path_str!r} but {full} does not exist on disk "
        f"-- orphaned per-file-ignore?"
    )


# --------------------------------------------------------------------------- #
# Guard 4: README renders for PyPI                                            #
# --------------------------------------------------------------------------- #


def _run_renderer() -> subprocess.CompletedProcess[str]:
    """Run ``python -m readme_renderer README.md`` from the repo root.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process with captured stdout/stderr.
    """
    return subprocess.run(
        [sys.executable, "-m", "readme_renderer", str(README)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_readme_renders() -> None:
    """python -m readme_renderer README.md must exit 0 on the current repo state.

    Pre-condition: README.md exists at repo root. Verifies PyPI Warehouse will
    render the README without errors when the package is uploaded.
    """
    assert README.exists(), (
        f"Pre-condition violation: {README} does not exist. README.md is "
        "expected to ship at repo root."
    )
    result = _run_renderer()
    assert result.returncode == 0, (
        f"readme_renderer exit {result.returncode}; expected 0 (clean render)\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
