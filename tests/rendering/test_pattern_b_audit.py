"""Pattern B audit: every per-frame draw path is delete_item-free.

Pattern B is the rule that the playback tick path and the resize path rebuild
frames in place (via ``configure_item``), never by destroying and recreating
items with ``delete_item``. This is an AST/source-text invariant guarded
centrally here: it AST-walks the visualization-tab playback/render/timeline
modules and the overlay adapters, enumerating ``dpg.delete_item(...)`` calls in
the function bodies reachable from ``_playback_tick`` / ``update_frame``, and
asserts there are none.

``delete_item(layer, children_only=True)`` is not whitelisted for any tick-path
or resize-path function covered here; layer-clear is legitimate only in
data-load handlers (e.g. the PitchRenderer sport-change path), never in
tick-path or resize-path callees. The string check scans for the literal
"delete_item" with no carveouts. ``_render_current_frame`` is reached from both
``_playback_tick`` (tick path) and ``_check_drawlist_resize`` (resize path), so
its single audit row covers both.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Each audited function lives in its own visualization subpackage module.
_PLAYBACK_PATH = "src/floodlight_gui/tabs/visualization/playback.py"
_RENDER_LOOP_PATH = "src/floodlight_gui/tabs/visualization/render_loop.py"
_TIMELINE_PATH = "src/floodlight_gui/tabs/visualization/timeline.py"
_PITCH_RENDERER_PATH = "src/floodlight_gui/rendering/pitch_renderer.py"


def _read_module_source(rel: str) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / rel).read_text(encoding="utf-8")


def _function_body_unparsed(source: str, func_name: str) -> str | None:
    """Return the ast.unparse() of a function's body statements, or None if not found.

    ast.unparse strips comments, so a comment mentioning 'delete_item' never
    appears in the unparsed output and cannot trip the substring check.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return "\n".join(ast.unparse(n) for n in node.body)
    return None


# ============================================================================
# Tick-path functions
# ============================================================================


def test_playback_tick_body_has_no_delete_item():
    """_playback_tick direct body does not call delete_item (main tick path)."""
    source = _read_module_source(_PLAYBACK_PATH)
    body = _function_body_unparsed(source, "_playback_tick")
    assert body is not None, "_playback_tick not found in playback.py"
    assert "delete_item" not in body, (
        "_playback_tick body contains 'delete_item'; Pattern B violation on the "
        "main playback tick path"
    )


def test_render_current_frame_has_no_delete_item():
    """_render_current_frame does not call delete_item (per-frame render path)."""
    source = _read_module_source(_RENDER_LOOP_PATH)
    body = _function_body_unparsed(source, "_render_current_frame")
    assert body is not None, "_render_current_frame not found in render_loop.py"
    assert "delete_item" not in body, (
        "_render_current_frame body contains 'delete_item'; Pattern B violation on "
        "the per-frame render path"
    )


def test_redraw_frame_cursor_has_no_delete_item():
    """_redraw_frame_cursor (cursor-only redraw) does not call delete_item."""
    source = _read_module_source(_TIMELINE_PATH)
    body = _function_body_unparsed(source, "_redraw_frame_cursor")
    assert body is not None, "_redraw_frame_cursor not found in timeline.py"
    assert "delete_item" not in body, (
        "_redraw_frame_cursor body contains 'delete_item'; the cursor-only redraw "
        "must stay delete-free"
    )


# ============================================================================
# Resize-path callees
# ============================================================================


def test_check_drawlist_resize_has_no_delete_item():
    """_check_drawlist_resize (resize path, called from _playback_tick) stays
    delete_item-free; no children_only=True whitelist.
    """
    source = _read_module_source(_RENDER_LOOP_PATH)
    body = _function_body_unparsed(source, "_check_drawlist_resize")
    assert body is not None, "_check_drawlist_resize not found in render_loop.py"
    assert "delete_item" not in body, (
        "_check_drawlist_resize body contains 'delete_item'; Pattern B violation on "
        "the resize trigger path"
    )


def test_pitch_renderer_update_position_has_no_delete_item():
    """PitchRenderer.update_position (resize-path callee) stays delete_item-free.

    It is the in-place configure_item path that preserves Z-order; no
    children_only=True whitelist.
    """
    source = _read_module_source(_PITCH_RENDERER_PATH)
    body = _function_body_unparsed(source, "update_position")
    assert body is not None, "update_position not found in pitch_renderer.py"
    assert "delete_item" not in body, (
        "PitchRenderer.update_position body contains 'delete_item'; the in-place "
        "resize path must not destroy items"
    )


# ----------------------------------------------------------------------
# Overlay-adapter pipeline. Each row skips when its adapter module is
# absent, else asserts the named method body is delete_item-free.
# ----------------------------------------------------------------------

_VORONOI_ADAPTER_PATH = "src/floodlight_gui/rendering/adapters/voronoi.py"
_HULL_ADAPTER_PATH = "src/floodlight_gui/rendering/adapters/hull.py"


def _assert_adapter_method_delete_free(adapter_path: str, method_name: str) -> None:
    """Skip if the adapter file is absent, else assert delete_item not in the method body."""
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / adapter_path
    if not path.exists():
        pytest.skip(f"{adapter_path} not present; row auto-runs once the adapter ships.")
    source = _read_module_source(adapter_path)
    body = _function_body_unparsed(source, method_name)
    assert body is not None, f"{method_name} not found in {adapter_path}"
    assert "delete_item" not in body, (
        f"{method_name} body in {adapter_path} contains 'delete_item'; Pattern B violation"
    )


def test_voronoi_adapter_update_frame_has_no_delete_item():
    """VoronoiAdapter.update_frame does not call delete_item."""
    _assert_adapter_method_delete_free(_VORONOI_ADAPTER_PATH, "update_frame")


def test_voronoi_adapter_update_mapper_has_no_delete_item():
    """VoronoiAdapter.update_mapper mutates geometry via configure_item only, never
    delete_item, so Pattern B holds on the resize path.
    """
    _assert_adapter_method_delete_free(_VORONOI_ADAPTER_PATH, "update_mapper")


def test_hull_adapter_update_frame_has_no_delete_item():
    """HullAdapter.update_frame does not call delete_item."""
    _assert_adapter_method_delete_free(_HULL_ADAPTER_PATH, "update_frame")


def test_voronoi_adapter_set_visible_has_no_delete_item():
    """VoronoiAdapter.set_visible (visibility-toggle hot path) does not call delete_item."""
    _assert_adapter_method_delete_free(_VORONOI_ADAPTER_PATH, "set_visible")


def test_hull_adapter_set_visible_has_no_delete_item():
    """HullAdapter.set_visible (visibility-toggle hot path) does not call delete_item."""
    _assert_adapter_method_delete_free(_HULL_ADAPTER_PATH, "set_visible")


def test_pattern_b_audit_covers_every_registered_adapter():
    """Every adapter in OVERLAY_ADAPTER_REGISTRY has a Pattern B audit row here.

    Keeps the audit and the registry in lock-step. Skips when the registry is
    empty.
    """
    from floodlight_gui.rendering.adapters import OVERLAY_ADAPTER_REGISTRY

    if not OVERLAY_ADAPTER_REGISTRY:
        pytest.skip("OVERLAY_ADAPTER_REGISTRY empty; row auto-runs once adapters ship.")
    own_src = Path(__file__).read_text(encoding="utf-8")
    for adapter_key in OVERLAY_ADAPTER_REGISTRY:
        expected_test = f"test_{adapter_key}_adapter_update_frame_has_no_delete_item"
        assert expected_test in own_src, (
            f"adapter {adapter_key!r} in OVERLAY_ADAPTER_REGISTRY has no Pattern B "
            f"audit row. Add `def {expected_test}(...)` to this file."
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
