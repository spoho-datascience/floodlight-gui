"""Behavioral contracts for ``floodlight_gui.rendering.coordinate_mapper``.

``CoordinateMapper`` is pure math: given a pitch (xlim/ylim) and a drawlist
size, it computes a uniform scale and pixel offsets that fit the pitch into
the drawlist with aspect-ratio-preserving letterboxing, and maps points
between pitch units and drawlist pixels. The only collaborator is a Pitch
exposing ``.xlim`` / ``.ylim``; a tiny stub stands in for it.

The geometry is fully determined, so most assertions are exact values from
the published formula. Round-trip is asserted within float tolerance.

Behavioral contracts guarded here
---------------------------------
pitch_to_pixel / pixel_to_pitch
  C1  Round-trip pitch -> pixel -> pitch recovers the original point within
      float tolerance, across several real pitch coordinate systems.
  C2  The y-axis is flipped: a higher pitch-y maps to a lower pixel-y, while
      x is not flipped.

letterboxing / offsets
  C3  A pitch whose aspect ratio differs from the drawlist is centered: the
      offset on the constrained axis exceeds the raw padding, and the two
      letterbox bars on that axis are equal.
  C4  The uniform scale equals the smaller of the per-axis scales, so the
      rendered pitch never exceeds the available area on either axis.

properties
  C5  ``pitch_origin_px`` is the pixel of (xlim[0], ylim[1]) and
      ``pitch_end_px`` the pixel of (xlim[1], ylim[0]); together they span
      the rendered pitch box inside the padded, centered area.

scale_distance
  C6  ``scale_distance`` multiplies a pitch-unit length by the current scale,
      matching the pixel span of the same length measured via pitch_to_pixel.

update
  C7  A partial ``update`` mutates only the supplied dimensions and leaves the
      others unchanged.
  C8  Enlarging the pitch within the same drawlist lowers the scale (a bigger
      pitch is drawn smaller to still fit).

aspect ratio
  C9  The uniform scale (pixels per pitch unit) is identical on x and y across
      different viewport sizes, so pitch aspect ratio is preserved.

guard
  C10 A drawlist no larger than twice the padding does not zero-divide; the
      ``max(1.0, ...)`` floor keeps the scale finite and positive.
"""

from __future__ import annotations

import pytest

from floodlight_gui.rendering.coordinate_mapper import CoordinateMapper


class _StubPitch:
    """Minimal Pitch stand-in exposing only ``xlim`` and ``ylim``.

    Parameters
    ----------
    xlim : tuple[float, float]
        (x_min, x_max) bounds of the pitch coordinate system.
    ylim : tuple[float, float]
        (y_min, y_max) bounds of the pitch coordinate system.
    """

    def __init__(self, xlim, ylim):
        self.xlim = xlim
        self.ylim = ylim


# Real coordinate systems the app actually produces.
DFL = ((-52.5, 52.5), (-34.0, 34.0))  # centered football
FOOTPRINT = ((0.0, 105.0), (0.0, 68.0))  # 0-based football footprint
EIGD = ((0.0, 40.0), (0.0, 20.0))  # EIGD handball


@pytest.mark.parametrize(
    "xlim, ylim, point",
    [
        (*[*DFL], (10.0, -22.0)),
        (*[*FOOTPRINT], (3.0, 65.0)),
        (*[*EIGD], (37.5, 1.25)),
    ],
)
def test_round_trip_recovers_point(xlim, ylim, point):
    """C1: pitch -> pixel -> pitch returns the original point within tolerance."""
    mapper = CoordinateMapper(_StubPitch(xlim, ylim), 800, 600)
    px, py = mapper.pitch_to_pixel(*point)
    x_back, y_back = mapper.pixel_to_pitch(px, py)
    assert x_back == pytest.approx(point[0], abs=1e-9)
    assert y_back == pytest.approx(point[1], abs=1e-9)


def test_y_flip_high_pitch_y_is_low_pixel_y():
    """C2: increasing pitch-y lowers pixel-y; increasing pitch-x raises pixel-x."""
    mapper = CoordinateMapper(_StubPitch(*DFL), 800, 600)
    px_low_y, py_low_y = mapper.pitch_to_pixel(0.0, -30.0)
    px_high_y, py_high_y = mapper.pitch_to_pixel(0.0, 30.0)
    # Same x in, same pixel-x out (x is not flipped).
    assert px_low_y == pytest.approx(px_high_y)
    # Higher pitch-y -> smaller pixel-y.
    assert py_high_y < py_low_y

    px_left, _ = mapper.pitch_to_pixel(-50.0, 0.0)
    px_right, _ = mapper.pitch_to_pixel(50.0, 0.0)
    assert px_right > px_left


def test_letterboxing_centers_on_constrained_axis():
    """C3: a wide pitch in a square-ish drawlist gets equal top/bottom bars.

    The DFL pitch is wider (105 x 68, ratio ~1.54) than the 800 x 600 draw
    area, so width binds the scale and the spare vertical space splits into
    two equal letterbox bars. The vertical offset then exceeds raw padding.
    """
    mapper = CoordinateMapper(_StubPitch(*DFL), 800, 600, padding=40)
    top = mapper.pitch_pixel_top
    bottom_bar = mapper.drawlist_height - mapper.pitch_pixel_bottom
    # Two letterbox bars are equal (centered).
    assert top == pytest.approx(bottom_bar)
    # Centering pushed the pitch origin below the raw padding line.
    assert top > 40


def test_scale_is_min_of_per_axis_scales():
    """C4: the uniform scale never overflows either axis of the padded area."""
    mapper = CoordinateMapper(_StubPitch(*DFL), 800, 600, padding=40)
    available_w = 800 - 2 * 40
    available_h = 600 - 2 * 40
    rendered_w = (DFL[0][1] - DFL[0][0]) * mapper.scale_distance(1.0)
    rendered_h = (DFL[1][1] - DFL[1][0]) * mapper.scale_distance(1.0)
    assert rendered_w <= available_w + 1e-9
    assert rendered_h <= available_h + 1e-9
    # And it touches the binding axis (width here).
    assert rendered_w == pytest.approx(available_w)


def test_origin_and_end_properties_span_rendered_box():
    """C5: origin_px maps (xmin, ymax) and end_px maps (xmax, ymin)."""
    mapper = CoordinateMapper(_StubPitch(*FOOTPRINT), 800, 600, padding=40)
    assert mapper.pitch_origin_px == mapper.pitch_to_pixel(0.0, 68.0)
    assert mapper.pitch_end_px == mapper.pitch_to_pixel(105.0, 0.0)
    ox, oy = mapper.pitch_origin_px
    ex, ey = mapper.pitch_end_px
    # Origin is the top-left of the rendered box; end is the bottom-right.
    assert ox < ex
    assert oy < ey


def test_scale_distance_matches_pixel_span():
    """C6: scale_distance(d) equals the pixel span of length d on the pitch."""
    mapper = CoordinateMapper(_StubPitch(*EIGD), 800, 600)
    span = mapper.pitch_to_pixel(10.0, 0.0)[0] - mapper.pitch_to_pixel(0.0, 0.0)[0]
    assert mapper.scale_distance(10.0) == pytest.approx(span)


def test_partial_update_mutates_only_supplied_dimension():
    """C7: update(drawlist_width=...) leaves height and pitch untouched."""
    pitch = _StubPitch(*DFL)
    mapper = CoordinateMapper(pitch, 800, 600)
    mapper.update(drawlist_width=1200)
    assert mapper.drawlist_width == 1200
    assert mapper.drawlist_height == 600
    assert mapper.pitch is pitch


def test_enlarging_pitch_lowers_scale():
    """C8: a bigger pitch in the same drawlist is drawn at a smaller scale."""
    mapper = CoordinateMapper(_StubPitch(*EIGD), 800, 600)
    small_scale = mapper.scale_distance(1.0)
    mapper.update(pitch=_StubPitch(*DFL))
    big_scale = mapper.scale_distance(1.0)
    assert big_scale < small_scale


@pytest.mark.parametrize("width, height", [(800, 600), (1600, 400), (300, 1000)])
def test_aspect_ratio_preserved_across_viewports(width, height):
    """C9: pixels-per-unit are equal on x and y regardless of viewport size."""
    mapper = CoordinateMapper(_StubPitch(*DFL), width, height)
    p0 = mapper.pitch_to_pixel(0.0, 0.0)
    x_per_unit = mapper.pitch_to_pixel(1.0, 0.0)[0] - p0[0]
    # y is flipped, so a +1 unit step lowers pixel-y; take magnitude.
    y_per_unit = p0[1] - mapper.pitch_to_pixel(0.0, 1.0)[1]
    assert x_per_unit == pytest.approx(y_per_unit)


@pytest.mark.parametrize("width, height", [(80, 80), (40, 600), (1, 1)])
def test_tiny_drawlist_does_not_zero_divide(width, height):
    """C10: a drawlist <= 2*padding stays finite via the max(1.0, ...) floor."""
    mapper = CoordinateMapper(_StubPitch(*DFL), width, height, padding=40)
    scale = mapper.scale_distance(1.0)
    assert scale > 0.0
    # Round-trip still well-defined under the clamped scale.
    x_back, y_back = mapper.pixel_to_pitch(*mapper.pitch_to_pixel(5.0, 5.0))
    assert x_back == pytest.approx(5.0, abs=1e-6)
    assert y_back == pytest.approx(5.0, abs=1e-6)
