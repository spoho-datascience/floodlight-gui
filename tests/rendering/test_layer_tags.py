"""Structural invariants for the visualization drawlist layer tags.

These are AST/source-text invariants over ``render_loop.py`` (and the overlay
adapters), guarded centrally here rather than at runtime:

1. The three layer-tag string constants exist and are pairwise distinct.
2. The three layer-tag values do not collide with any other module-level
   string constant in the module.
3. ``_init_drawlist_layers`` is the first DPG-affecting call inside
   ``initialize_visualization``, before any renderer constructor.
4. ``_init_drawlist_layers`` wraps every ``add_draw_layer`` call inside a
   ``does_item_exist`` guard, so it is idempotent across data reloads.
5. Every ``dpg.draw_*`` call inside an overlay adapter attaches
   ``parent=self._parent_layer``.
6. This test module does not import dearpygui at module scope (DPG-free
   backend invariant); the checks are AST audits needing no DPG context.
"""

from __future__ import annotations

import ast
from pathlib import Path

_VIZ_TAB_PATH = "src/floodlight_gui/tabs/visualization/render_loop.py"


def _read_module_source(rel: str) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / rel).read_text(encoding="utf-8")


def _collect_string_constants(source: str) -> dict[str, str]:
    """Return ``{const_name: literal_value}`` for every module-level
    ``Assign`` of a string ``Constant``.
    """
    tree = ast.parse(source)
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                out[target.id] = node.value.value
    return out


# ============================================================================
# Three layer tag constants are pairwise distinct strings
# ============================================================================


def test_layer_tags_distinct():
    """The three layer tag constants exist and are unique strings."""
    consts = _collect_string_constants(_read_module_source(_VIZ_TAB_PATH))
    tags = (
        consts.get("_PITCH_LAYER_TAG"),
        consts.get("_PLAYER_LAYER_TAG"),
        consts.get("_OVERLAY_LAYER_TAG"),
    )
    assert all(t is not None for t in tags), f"Missing layer tag constant in render_loop.py: {tags}"
    assert len(set(tags)) == 3, f"Layer tags not pairwise distinct: {tags}"
    # Pin the exact values.
    assert consts["_PITCH_LAYER_TAG"] == "__pitch_layer", (
        f"_PITCH_LAYER_TAG must be '__pitch_layer'; got {consts['_PITCH_LAYER_TAG']!r}"
    )
    assert consts["_PLAYER_LAYER_TAG"] == "__player_layer", (
        f"_PLAYER_LAYER_TAG must be '__player_layer'; got {consts['_PLAYER_LAYER_TAG']!r}"
    )
    assert consts["_OVERLAY_LAYER_TAG"] == "__overlay_layer", (
        f"_OVERLAY_LAYER_TAG must be '__overlay_layer'; got {consts['_OVERLAY_LAYER_TAG']!r}"
    )


# ============================================================================
# Layer tags do not collide with any other module-level string
# ============================================================================


def test_layer_tags_no_collision_with_other_constants():
    """Layer tag values do not collide with any other module-level string constant
    in render_loop.py (_DRAWLIST_TAG, _TIMELINE_TAG, etc.).
    """
    consts = _collect_string_constants(_read_module_source(_VIZ_TAB_PATH))
    layer_values = {
        consts["_PITCH_LAYER_TAG"],
        consts["_PLAYER_LAYER_TAG"],
        consts["_OVERLAY_LAYER_TAG"],
    }
    other_values = {
        v
        for k, v in consts.items()
        if k not in {"_PITCH_LAYER_TAG", "_PLAYER_LAYER_TAG", "_OVERLAY_LAYER_TAG"}
    }
    overlap = layer_values & other_values
    assert not overlap, f"Layer tag value collides with another module-level constant: {overlap}"


# ============================================================================
# _init_drawlist_layers is the first call in initialize_visualization
# ============================================================================


def test_init_drawlist_layers_first_call_in_initialize_visualization():
    """_init_drawlist_layers is the first DPG-affecting call in
    initialize_visualization, before any renderer constructor.

    Scans the function body in ast.walk order; the first Call node whose name is
    one of {_init_drawlist_layers, PitchRenderer, PlayerRenderer} must be
    _init_drawlist_layers. Pre-flight no-DPG branches (status set, _stop_playback,
    _update_controls_from_data) and CoordinateMapper construction are allowed
    ahead of it. Adapters are created lazily by ``_on_model_fitted``, not here, so
    they do not participate in this ordering invariant.
    """
    source = _read_module_source(_VIZ_TAB_PATH)
    tree = ast.parse(source)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "initialize_visualization":
            target = node
            break
    assert target is not None, "initialize_visualization not found in render_loop.py"

    for stmt in ast.walk(target):
        if isinstance(stmt, ast.Call):
            callee = stmt.func
            if isinstance(callee, ast.Name):
                name = callee.id
            elif isinstance(callee, ast.Attribute):
                name = callee.attr
            else:
                name = None
            if name in {
                "_init_drawlist_layers",
                "PitchRenderer",
                "PlayerRenderer",
            }:
                assert name == "_init_drawlist_layers", (
                    f"{name}(...) called in initialize_visualization before "
                    f"_init_drawlist_layers; layer creation must precede every "
                    f"renderer constructor"
                )
                return

    raise AssertionError("initialize_visualization does not call _init_drawlist_layers")


# ============================================================================
# _init_drawlist_layers uses a does_item_exist guard (idempotency)
# ============================================================================


def test_init_drawlist_layers_uses_does_item_exist_guard():
    """_init_drawlist_layers wraps add_draw_layer in a does_item_exist check, so
    calling it twice does not raise ``SystemError: Item tag already exists``.

    The body must contain both ``does_item_exist`` and ``add_draw_layer``, and at
    least one ``If`` whose test references ``does_item_exist`` and whose body
    contains ``add_draw_layer``.
    """
    source = _read_module_source(_VIZ_TAB_PATH)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_init_drawlist_layers":
            body_text = "\n".join(ast.unparse(n) for n in node.body)
            assert "does_item_exist" in body_text, (
                "_init_drawlist_layers must use a does_item_exist guard so layer "
                "creation is idempotent"
            )
            assert "add_draw_layer" in body_text, (
                "_init_drawlist_layers must call dpg.add_draw_layer"
            )
            # Pin the guard ordering: an If node whose test references
            # does_item_exist must contain the add_draw_layer call.
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.If):  # noqa: SIM102 (AST inspector reads more clearly as outer isinstance / inner attribute check)
                    if "does_item_exist" in ast.unparse(stmt.test):
                        if_body_text = "\n".join(ast.unparse(b) for b in stmt.body)
                        if "add_draw_layer" in if_body_text:
                            return
            raise AssertionError(
                "add_draw_layer must be inside an If whose test calls "
                "does_item_exist (idempotency guard)"
            )
    raise AssertionError("_init_drawlist_layers not found in render_loop.py")


# ----------------------------------------------------------------------
# AST audit: every `dpg.draw_*(...)` call inside an overlay adapter module
# (rendering/adapters/*.py, excluding __init__.py) carries a keyword arg
# `parent=self._parent_layer`. Skips when no adapter modules are present.
# ----------------------------------------------------------------------


_ADAPTERS_DIR_REL = "src/floodlight_gui/rendering/adapters"


def _is_dpg_draw_call(node: ast.Call) -> bool:
    """Return True if node is `dpg.draw_*(...)`."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if not func.attr.startswith("draw_"):
        return False
    val = func.value
    return isinstance(val, ast.Name) and val.id == "dpg"


def _parent_kw_is_self_parent_layer(call: ast.Call) -> bool:
    """True iff `call` has `parent=` kwarg whose value is `self._parent_layer`."""
    for kw in call.keywords:
        if kw.arg != "parent":
            continue
        v = kw.value
        return (
            isinstance(v, ast.Attribute)
            and v.attr == "_parent_layer"
            and isinstance(v.value, ast.Name)
            and v.value.id == "self"
        )
    return False


def test_adapter_draw_calls_use_parent_layer():
    """Every dpg.draw_*() in an overlay adapter attaches parent=self._parent_layer.

    Skips when adapters/ holds no module other than __init__.py; otherwise pins
    the invariant for every adapter module.
    """
    import pytest

    repo_root = Path(__file__).resolve().parents[2]
    adapters_dir = repo_root / _ADAPTERS_DIR_REL
    adapter_files = sorted(f for f in adapters_dir.glob("*.py") if f.name != "__init__.py")
    if not adapter_files:
        pytest.skip("adapters/ contains no .py modules other than __init__.py")

    violations: list[tuple[str, int, str]] = []
    for src_path in adapter_files:
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_dpg_draw_call(node):  # noqa: SIM102 (AST inspector reads more clearly as outer isinstance / inner attribute check)
                if not _parent_kw_is_self_parent_layer(node):
                    func = node.func
                    attr = func.attr if isinstance(func, ast.Attribute) else "<?>"
                    violations.append((str(src_path), node.lineno, attr))

    assert not violations, (
        "dpg.draw_*() calls inside adapters/ must attach parent=self._parent_layer. "
        "Offending calls:\n  "
        + "\n  ".join(f"{p}:{ln} -> dpg.{attr}(...)" for p, ln, attr in violations)
    )


# ============================================================================
# DPG-free-backend self-guard
# ============================================================================


def test_no_module_level_dearpygui_imports():
    """This test file does not import dearpygui at module scope."""
    own_source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(own_source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "dearpygui" not in alias.name, (
                    f"module-level `import {alias.name}` (dearpygui leaked)"
                )
        elif isinstance(node, ast.ImportFrom):
            assert "dearpygui" not in (node.module or ""), (
                f"module-level `from {node.module} import ...`"
            )
