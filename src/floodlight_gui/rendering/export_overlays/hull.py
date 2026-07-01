"""Matplotlib ConvexHull overlay writer for the export pipeline.

Delegates to ``ConvexHullModel.plot`` via the fitted model carried in the
overlay spec. DPG-free: no ``dearpygui`` import at module scope or runtime.
No floodlight import at module scope either; the upstream class is accessed
through the spec's ``"model"`` field, which is already a fitted instance.
"""

from __future__ import annotations


def plot_hull(spec: dict, ax, *, frame: int) -> None:
    """Render team convex hulls on *ax* for one frame.

    Delegates to ``ConvexHullModel.plot`` using the fitted model and alpha
    pulled from the overlay spec. Upstream owns per-team colors; only
    ``fill=True`` and ``fill_alpha`` are forwarded from the spec.

    Artist counts are snapshotted before the upstream call so that only the
    artists added by this call have their z-order raised to 5. This keeps
    the hull visible above Voronoi fills (zorder=3) without affecting
    pre-existing axes content. Slicing via ``[n_before:]`` is robust to
    any change in the number of artists upstream adds per call.

    Parameters
    ----------
    spec : dict
        Overlay spec with keys ``"model"`` (a fitted ``ConvexHullModel``)
        and ``"alpha"`` (fill transparency). Other keys are ignored.
    ax : matplotlib.axes.Axes
        Axes to render into.
    frame : int, keyword-only
        Frame index passed as ``t`` to ``ConvexHullModel.plot``.
    """
    model = spec["model"]
    alpha = float(spec["alpha"])

    # Snapshot artist counts before the upstream call to identify new artists.
    n_patches_before = len(ax.patches)
    n_lines_before = len(ax.lines)

    model.plot(t=frame, ax=ax, fill=True, fill_alpha=alpha)

    # Raise only the new artists to zorder=5 so the hull renders above Voronoi.
    for patch in ax.patches[n_patches_before:]:
        patch.set_zorder(5)
    for line in ax.lines[n_lines_before:]:
        line.set_zorder(5)
