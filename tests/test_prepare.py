"""Tests for preparing a sheet for Autodesk's format (prepare.py)."""

import math

from pdf2dwfx.model import Group, PathShape, Sheet, Style
from pdf2dwfx.prepare import CURVE_TOLERANCE, UNITS_PER_PT, prepare, to_contours

STROKE = Style(stroke="#000000", width=1.0)
FILL = Style(fill="#000000")


def _sheet(*children) -> Sheet:
    sheet = Sheet("t", 100, 100)
    sheet.root.children.extend(children)
    return sheet


def _square(x0: float, y0: float, x1: float, y1: float) -> list[tuple]:
    return [("M", x0, y0), ("L", x1, y0), ("L", x1, y1), ("L", x0, y1), ("Z",)]


def u(points: float) -> int:
    return round(points * UNITS_PER_PT)


def test_points_land_on_whole_number_grid() -> None:
    contours, closed = to_contours([("M", 0, 0), ("L", 10, 5)])
    assert contours == [[(0, 0), (u(10), u(5))]]
    assert closed == [False]
    assert all(isinstance(v, int) for p in contours[0] for v in p)


def test_curves_become_close_straight_segments() -> None:
    # A quarter circle as a Bézier curve, radius 50 pt.
    r, k = 50, 0.5522847498
    ops = [("M", r, 0), ("C", r, r * k, r * k, r, 0, r)]
    (contour,), _ = to_contours(ops)
    assert len(contour) > 10  # many short segments
    radius = r * UNITS_PER_PT
    for x, y in contour:
        # Every point is (almost) on the true circle; the Bézier itself is
        # within 0.03 % of a circle, plus our tolerance and rounding.
        assert abs(math.hypot(x, y) - radius) < radius * 0.0003 + CURVE_TOLERANCE + 1


def test_closed_outline_is_marked_closed() -> None:
    contours, closed = to_contours(_square(0, 0, 10, 10))
    assert closed == [True]
    assert len(contours[0]) == 4  # closing point not repeated


def test_line_is_cut_at_clip_edge() -> None:
    line = PathShape([("M", 0, 50), ("L", 100, 50)], STROKE)
    clip = Group(clip=_square(20, 0, 60, 100), children=[line])
    (prim,) = prepare(_sheet(clip)).prims
    (contour,) = prim.contours
    xs = sorted(x for x, _ in contour)
    assert xs == [u(20), u(60)]


def test_line_outside_clip_disappears() -> None:
    line = PathShape([("M", 0, 90), ("L", 100, 90)], STROKE)
    clip = Group(clip=_square(0, 0, 50, 50), children=[line])
    assert prepare(_sheet(clip)).prims == []


def test_fill_is_trimmed_to_clip() -> None:
    fill = PathShape(_square(0, 0, 100, 100), FILL)
    clip = Group(clip=_square(10, 10, 30, 40), children=[fill])
    (prim,) = prepare(_sheet(clip)).prims
    (contour,) = prim.contours
    xs, ys = {x for x, _ in contour}, {y for _, y in contour}
    assert xs == {u(10), u(30)} and ys == {u(10), u(40)}


def test_nested_clips_combine() -> None:
    fill = PathShape(_square(0, 0, 100, 100), FILL)
    inner = Group(clip=_square(20, 20, 100, 100), children=[fill])
    outer = Group(clip=_square(0, 0, 50, 50), children=[inner])
    (prim,) = prepare(_sheet(outer)).prims
    xs = {x for x, _ in prim.contours[0]}
    assert xs == {u(20), u(50)}


def test_empty_clip_hides_everything() -> None:
    fill = PathShape(_square(0, 0, 100, 100), FILL)
    assert prepare(_sheet(Group(clip=[], children=[fill]))).prims == []


def test_square_with_hole_keeps_its_hole() -> None:
    ops = _square(0, 0, 100, 100) + _square(30, 30, 60, 60)
    (prim,) = prepare(_sheet(PathShape(ops, Style(fill="#000000", even_odd=True)))).prims
    assert len(prim.contours) == 2  # outer outline + hole


def test_overlapping_outlines_merge_with_nonzero_rule() -> None:
    # Two overlapping squares drawn the same way round: one filled area.
    ops = _square(0, 0, 60, 60) + _square(40, 40, 100, 100)
    (prim,) = prepare(_sheet(PathShape(ops, FILL))).prims
    assert len(prim.contours) == 1


def test_group_transparency_moves_into_colour() -> None:
    fill = PathShape(_square(0, 0, 10, 10), FILL)
    (prim,) = prepare(_sheet(Group(opacity=0.5, children=[fill]))).prims
    assert prim.style.fill == "#80000000"


def test_pattern_copies_become_real_geometry() -> None:
    tile = Group(offsets=[(0, 0), (20, 0), (40, 0)], children=[PathShape([("M", 0, 0), ("L", 10, 0)], STROKE)])
    prims = prepare(_sheet(tile)).prims
    assert [p.contours[0][0][0] for p in prims] == [0, u(20), u(40)]


def test_widths_are_in_grid_units() -> None:
    (prim,) = prepare(_sheet(PathShape([("M", 0, 0), ("L", 10, 0)], STROKE))).prims
    assert prim.style.width == UNITS_PER_PT
