"""Prepare a recorded sheet for Autodesk's viewers.

Design Review does not display a DWFx page directly. It translates the
page into Autodesk's own internal drawing format (W2D, the format of
classic DWF files) using the DWF Toolkit's XAML translator, and shows only
what that format can express. From the toolkit source we know it:

* stores coordinates as whole numbers,
* understands straight lines (and arcs), but not curves,
* ignores clipping regions and transparency on groups,
* fills each outline of a shape separately unless told otherwise.

So before writing, we do those things ourselves: put every point on a fine
whole-number grid, turn curves into very short straight segments, trim
shapes against their clipping regions (with the pyclipper library), apply
group transparency to each shape's own colour, and expand repeating
pattern tiles into real geometry. The result looks the same in any viewer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import pyclipper

from .model import Group, ImageShape, PathOps, PathShape, Sheet, Style

# The whole-number grid: 1 unit = 1 micrometre (0.001 mm). Far finer than
# any printer or screen, and an A0 sheet (1189 mm) is only ~1.2 million
# units, well inside the format's limit of about 2 billion.
UNITS_PER_MM = 1000
UNITS_PER_PT = UNITS_PER_MM * 25.4 / 72

# Curves become straight segments that never stray more than this from the
# true curve (in grid units, i.e. micrometres).
CURVE_TOLERANCE = 2.0

Point = tuple[int, int]
Contour = list[Point]


@dataclass
class Prim:
    """One shape ready for writing: whole-number outlines plus its style.

    For fills, every contour is a closed outline and the outlines never
    overlap (holes are separate outlines inside others), so the result is
    the same whatever fill rule a viewer uses. For strokes, ``closed`` says
    which outlines join back to their start.
    """

    kind: str  # "fill" or "stroke"
    contours: list[Contour]
    closed: list[bool]
    style: Style  # widths and dash lengths in grid units
    layer: str | None = None


@dataclass
class Prepared:
    width_units: int
    height_units: int
    prims: list[Prim] = field(default_factory=list)
    images: list[ImageShape] = field(default_factory=list)


# --- Geometry helpers ------------------------------------------------------------


def _flatten_cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
) -> list[tuple[float, float]]:
    """Points along a Bézier curve (excluding the start point), close enough
    that straight lines between them stay within CURVE_TOLERANCE."""
    # Standard bound: a cubic split into n equal steps deviates from its
    # chords by at most 3/4 * max second difference / n^2.
    ddx = max(abs(p0[0] - 2 * p1[0] + p2[0]), abs(p1[0] - 2 * p2[0] + p3[0]))
    ddy = max(abs(p0[1] - 2 * p1[1] + p2[1]), abs(p1[1] - 2 * p2[1] + p3[1]))
    dd = math.hypot(ddx, ddy)
    n = max(1, math.ceil(math.sqrt(0.75 * dd / CURVE_TOLERANCE)))
    pts = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        pts.append(
            (
                a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
            )
        )
    return pts


def to_contours(ops: PathOps, dx: float = 0.0, dy: float = 0.0) -> tuple[list[Contour], list[bool]]:
    """Path steps (points) -> whole-number outlines, curves flattened.

    ``dx, dy`` shift everything (in points), used for pattern tile copies.
    """
    k = UNITS_PER_PT
    contours: list[Contour] = []
    closed: list[bool] = []
    current: list[tuple[float, float]] = []
    start: tuple[float, float] | None = None

    def finish(is_closed: bool) -> None:
        pts: Contour = []
        for x, y in current:
            p = (round(x), round(y))
            if not pts or pts[-1] != p:  # drop repeats created by rounding
                pts.append(p)
        if is_closed and len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()
        if len(pts) >= 2:
            contours.append(pts)
            closed.append(is_closed)

    for op in ops:
        kind = op[0]
        if kind == "M":
            if len(current) > 1:
                finish(False)
            start = ((op[1] + dx) * k, (op[2] + dy) * k)
            current = [start]
        elif kind == "L":
            if not current:
                current = [start or (0.0, 0.0)]
            current.append(((op[1] + dx) * k, (op[2] + dy) * k))
        elif kind == "C":
            if not current:
                current = [start or (0.0, 0.0)]
            p1 = ((op[1] + dx) * k, (op[2] + dy) * k)
            p2 = ((op[3] + dx) * k, (op[4] + dy) * k)
            p3 = ((op[5] + dx) * k, (op[6] + dy) * k)
            current.extend(_flatten_cubic(current[-1], p1, p2, p3))
        elif kind == "Z":
            if len(current) > 1:
                finish(True)
            # After closing, drawing continues from the outline's start.
            current = [current[0]] if current else []
    if len(current) > 1:
        finish(False)
    return contours, closed


def _bbox(contours: list[Contour]) -> tuple[int, int, int, int]:
    xs = [x for c in contours for x, _ in c]
    ys = [y for c in contours for _, y in c]
    return min(xs), min(ys), max(xs), max(ys)


@dataclass
class _ClipRegion:
    """The visible area: clean, non-overlapping outlines (or empty)."""

    paths: list[Contour]
    bbox: tuple[int, int, int, int] | None
    is_rect: bool  # a single axis-aligned rectangle (allows quick checks)

    @staticmethod
    def make(paths: list[Contour]) -> _ClipRegion:
        if not paths:
            return _ClipRegion([], None, False)
        bbox = _bbox(paths)
        is_rect = (
            len(paths) == 1
            and len(paths[0]) == 4
            and all(x in (bbox[0], bbox[2]) and y in (bbox[1], bbox[3]) for x, y in paths[0])
        )
        return _ClipRegion(paths, bbox, is_rect)


def _region_from(contours: list[Contour], even_odd: bool, outer: _ClipRegion | None) -> _ClipRegion:
    """The visible area inside a new clip outline (and inside ``outer``)."""
    if not contours:
        return _ClipRegion.make([])
    pc = pyclipper.Pyclipper()
    try:
        pc.AddPaths(contours, pyclipper.PT_SUBJECT, True)
    except pyclipper.ClipperException:
        return _ClipRegion.make([])
    rule = pyclipper.PFT_EVENODD if even_odd else pyclipper.PFT_NONZERO
    if outer is None:
        paths = pc.Execute(pyclipper.CT_UNION, rule, rule)
    else:
        if not outer.paths:
            return _ClipRegion.make([])
        pc.AddPaths(outer.paths, pyclipper.PT_CLIP, True)
        paths = pc.Execute(pyclipper.CT_INTERSECTION, rule, pyclipper.PFT_NONZERO)
    return _ClipRegion.make([[tuple(p) for p in path] for path in paths])


def _relation(region: _ClipRegion | None, bbox: tuple[int, int, int, int]) -> str:
    """Quick test of a shape's box against the clip: inside, outside or cut."""
    if region is None:
        return "inside"
    if region.bbox is None:
        return "outside"
    rx0, ry0, rx1, ry1 = region.bbox
    x0, y0, x1, y1 = bbox
    if x1 < rx0 or x0 > rx1 or y1 < ry0 or y0 > ry1:
        return "outside"
    if region.is_rect and x0 >= rx0 and x1 <= rx1 and y0 >= ry0 and y1 <= ry1:
        return "inside"
    return "cut"


def _fill_contours(contours: list[Contour], even_odd: bool, region: _ClipRegion | None) -> list[Contour]:
    """Filled outlines, trimmed to the clip region and made non-overlapping."""
    relation = _relation(region, _bbox(contours))
    if relation == "outside":
        return []
    if relation == "inside" and len(contours) == 1:
        return contours if len(contours[0]) >= 3 else []
    pc = pyclipper.Pyclipper()
    try:
        pc.AddPaths(contours, pyclipper.PT_SUBJECT, True)
    except pyclipper.ClipperException:
        return []  # degenerate outline (no area)
    rule = pyclipper.PFT_EVENODD if even_odd else pyclipper.PFT_NONZERO
    if relation == "inside":
        result = pc.Execute(pyclipper.CT_UNION, rule, rule)
    else:
        pc.AddPaths(region.paths, pyclipper.PT_CLIP, True)  # type: ignore[union-attr]
        result = pc.Execute(pyclipper.CT_INTERSECTION, rule, pyclipper.PFT_NONZERO)
    return [[tuple(p) for p in path] for path in result if len(path) >= 3]


def _stroke_contours(
    contours: list[Contour], closed: list[bool], region: _ClipRegion | None
) -> tuple[list[Contour], list[bool]]:
    """Line outlines, cut where they leave the clip region."""
    relation = _relation(region, _bbox(contours))
    if relation == "outside":
        return [], []
    if relation == "inside":
        return contours, closed
    pc = pyclipper.Pyclipper()
    for contour, is_closed in zip(contours, closed):
        path = contour + [contour[0]] if is_closed else contour
        try:
            pc.AddPath(path, pyclipper.PT_SUBJECT, False)
        except pyclipper.ClipperException:
            pass
    pc.AddPaths(region.paths, pyclipper.PT_CLIP, True)  # type: ignore[union-attr]
    tree = pc.Execute2(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    pieces = [[tuple(p) for p in path] for path in pyclipper.OpenPathsFromPolyTree(tree)]
    pieces = [p for p in pieces if len(p) >= 2]
    return pieces, [False] * len(pieces)


def _with_opacity(color: str | None, opacity: float) -> str | None:
    """Fold a group's transparency into a colour ("#RRGGBB" / "#AARRGGBB")."""
    if color is None or opacity >= 1:
        return color
    alpha = int(color[1:3], 16) / 255 if len(color) == 9 else 1.0
    rgb = color[-6:]
    return f"#{round(alpha * opacity * 255):02X}{rgb}"


def _scaled_style(style: Style, opacity: float) -> Style:
    k = UNITS_PER_PT
    return replace(
        style,
        fill=_with_opacity(style.fill, opacity),
        stroke=_with_opacity(style.stroke, opacity),
        width=style.width * k,
        dashes=tuple(v * k for v in style.dashes),
        dash_offset=style.dash_offset * k,
    )


# --- Walking the drawing ---------------------------------------------------------


class _Preparer:
    def __init__(self, sheet: Sheet) -> None:
        self.sheet = sheet
        self.out = Prepared(
            width_units=round(sheet.width_pt * UNITS_PER_PT),
            height_units=round(sheet.height_pt * UNITS_PER_PT),
        )

    def walk(
        self,
        children: list,
        region: _ClipRegion | None,
        opacity: float,
        dx: float,
        dy: float,
    ) -> None:
        for child in children:
            if isinstance(child, PathShape):
                self.shape(child, region, opacity, dx, dy)
            elif isinstance(child, ImageShape):
                self.out.images.append(child)
            else:
                self.group(child, region, opacity, dx, dy)

    def group(self, group: Group, region: _ClipRegion | None, opacity: float, dx: float, dy: float) -> None:
        if group.clip is not None:
            contours, _ = to_contours(group.clip, dx, dy)
            region = _region_from(contours, group.clip_even_odd, region)
        opacity *= group.opacity
        if region is not None and region.bbox is None:
            return  # everything inside an empty clip is hidden
        for ox, oy in group.offsets or [(0.0, 0.0)]:
            self.walk(group.children, region, opacity, dx + ox, dy + oy)

    def shape(self, shape: PathShape, region: _ClipRegion | None, opacity: float, dx: float, dy: float) -> None:
        contours, closed = to_contours(shape.ops, dx, dy)
        if not contours:
            return
        style = _scaled_style(shape.style, opacity)
        if style.fill:
            filled = _fill_contours(contours, style.even_odd, region)
            if filled:
                fill_style = replace(style, stroke=None, width=0.0, dashes=(), even_odd=False)
                self.out.prims.append(Prim("fill", filled, [True] * len(filled), fill_style, shape.layer))
        if style.stroke:
            lines, line_closed = _stroke_contours(contours, closed, region)
            if lines:
                line_style = replace(style, fill=None)
                self.out.prims.append(Prim("stroke", lines, line_closed, line_style, shape.layer))


def prepare(sheet: Sheet) -> Prepared:
    p = _Preparer(sheet)
    p.walk(sheet.root.children, None, 1.0, 0.0, 0.0)
    return p.out
