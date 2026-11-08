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

import hashlib
import math
from dataclasses import dataclass, field, replace

import pymupdf
import pyclipper
from pymupdf import mupdf

from .model import Group, ImageData, ImageShape, Matrix, PathOps, PathShape, Sheet, Style, TextShape

# The whole-number grid: 1 unit = 1 micrometre (0.001 mm). Far finer than
# any printer or screen, and an A0 sheet (1189 mm) is only ~1.2 million
# units, well inside the format's limit of about 2 billion.
UNITS_PER_MM = 1000
UNITS_PER_PT = UNITS_PER_MM * 25.4 / 72

# Curves become straight segments that never stray more than this from the
# true curve (in grid units, i.e. micrometres).
CURVE_TOLERANCE = 2.0

# Pictures that must be redrawn (rotated, clipped...) are limited to this
# many pixels, and Autodesk's format allows at most 65535 per side.
MAX_PICTURE_PIXELS = 16_000_000
MAX_PICTURE_SIDE = 65535

Point = tuple[int, int]
Contour = list[Point]
Box = tuple[int, int, int, int]  # x0, y0, x1, y1 in grid units (y down)


@dataclass
class PlacedImage:
    """A picture ready for Autodesk's format: an upright box on the sheet."""

    key: str  # identifies the picture data (same data -> stored once)
    data: ImageData
    box: Box


@dataclass
class PreparedText:
    """A text item on the grid (see model.TextShape; lengths in grid units)."""

    text: str
    font: str  # key into Prepared.fonts
    origin: Point
    matrix: tuple[float, float, float, float]
    glyphs: list[int]
    advances: list[float]


@dataclass
class Prim:
    """One shape ready for writing: whole-number outlines plus its style.

    For fills, there is exactly one closed outline without holes. For
    strokes, ``closed`` says which outlines join back to their start.
    For pictures, ``image`` holds the picture and ``contours`` its box.
    For text, ``text`` holds the text item (colour in ``style.fill``).
    """

    kind: str  # "fill", "stroke", "image" or "text"
    contours: list[Contour]
    closed: list[bool]
    style: Style  # widths and dash lengths in grid units
    layer: str | None = None
    image: PlacedImage | None = None
    text: PreparedText | None = None
    box: Box | None = None  # text only: the area it covers
    viewport: int | None = None  # index into Prepared.viewports, None = outside all


@dataclass
class PreparedViewport:
    """A sheet area with its own scale (see scale.py), in grid units.

    ``clip`` is ``box`` grown to cover all its shapes completely: the
    viewport's outline is also a clipping edge in the viewer, and a drawing
    that pokes out of the box a little must not be cut.
    """

    box: Box
    clip: Box
    scale: float
    label: str = ""


@dataclass
class Prepared:
    width_units: int
    height_units: int
    prims: list[Prim] = field(default_factory=list)
    fonts: dict = field(default_factory=dict)  # key -> model.FontData, fonts used by text
    scale: float | None = None  # whole-sheet scale; None = paper (mm)
    viewports: list[PreparedViewport] = field(default_factory=list)


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


def _is_rectangle(contour: Contour) -> bool:
    if len(contour) != 4:
        return False
    x0, y0, x1, y1 = _bbox([contour])
    return all(x in (x0, x1) and y in (y0, y1) for x, y in contour)


def _split_holes(outer: Contour, holes: list[Contour], depth: int = 0) -> list[Contour]:
    """Cut a filled area with holes into pieces without holes.

    Design Review cannot show a filled shape with a hole: Autodesk's
    "contour set" (several outlines in one shape) makes the page fail, and
    without it each outline is filled on its own, so holes come out filled
    (both verified in Design Review). Pieces that only touch each other
    look the same as the original area. We cut vertically through the
    first hole; that hole then opens onto the cut edge in both halves, and
    we repeat for the remaining holes.
    """
    if not holes:
        return [outer]
    if depth > 64:  # safety net for degenerate slivers; never seen in practice
        return [outer]
    xs = [x for x, _ in holes[0]]
    cut = (min(xs) + max(xs)) // 2
    x0, y0, x1, y1 = _bbox([outer])
    pieces: list[Contour] = []
    for a, b in ((x0 - 1, cut), (cut, x1 + 1)):
        half = [(a, y0 - 1), (b, y0 - 1), (b, y1 + 1), (a, y1 + 1)]
        pc = pyclipper.Pyclipper()
        pc.AddPath(outer, pyclipper.PT_SUBJECT, True)
        pc.AddPaths(holes, pyclipper.PT_SUBJECT, True)
        pc.AddPath(half, pyclipper.PT_CLIP, True)
        # Outer and holes run in opposite directions, so "non-zero" filling
        # leaves the holes empty.
        tree = pc.Execute2(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
        for node in tree.Childs:
            sub_outer = [tuple(p) for p in node.Contour]
            sub_holes = [[tuple(p) for p in h.Contour] for h in node.Childs]
            pieces.extend(_split_holes(sub_outer, sub_holes, depth + 1))
    return pieces


def _fill_pieces(contours: list[Contour], even_odd: bool, region: _ClipRegion | None) -> list[Contour]:
    """The filled area, trimmed to the clip region, as outlines that never
    overlap and have no holes (see _split_holes)."""
    relation = _relation(region, _bbox(contours))
    if relation == "outside":
        return []
    if relation == "inside" and len(contours) == 1 and _is_rectangle(contours[0]):
        return contours  # by far the most common case: skip the geometry work
    pc = pyclipper.Pyclipper()
    try:
        pc.AddPaths(contours, pyclipper.PT_SUBJECT, True)
    except pyclipper.ClipperException:
        return []  # degenerate outline (no area)
    rule = pyclipper.PFT_EVENODD if even_odd else pyclipper.PFT_NONZERO
    if relation == "inside":
        tree = pc.Execute2(pyclipper.CT_UNION, rule, rule)
    else:
        pc.AddPaths(region.paths, pyclipper.PT_CLIP, True)  # type: ignore[union-attr]
        tree = pc.Execute2(pyclipper.CT_INTERSECTION, rule, pyclipper.PFT_NONZERO)
    pieces: list[Contour] = []

    def visit(node) -> None:  # an outer outline; its children are holes
        outer = [tuple(p) for p in node.Contour]
        holes = [[tuple(p) for p in h.Contour] for h in node.Childs]
        pieces.extend(_split_holes(outer, holes))
        for hole in node.Childs:  # islands inside holes are outers again
            for island in hole.Childs:
                visit(island)

    for top in tree.Childs:
        visit(top)
    return [p for p in pieces if len(p) >= 3]


def _dash_pieces(
    contours: list[Contour], closed: list[bool], dashes: tuple[float, ...], offset: float
) -> list[Contour]:
    """Turn dashed lines into separate short line pieces.

    Design Review ignores dash patterns in DWFx pages (verified: dashed
    lines showed solid), so we draw each dash as its own piece. As in PDF,
    the pattern starts afresh at the beginning of every outline. A dash of
    length zero (a dot, drawn by round line caps) becomes a 1-unit piece.
    """
    pattern = list(dashes) * (2 if len(dashes) % 2 else 1)
    total = sum(pattern)
    if total <= 0:
        return contours
    pieces: list[Contour] = []
    for contour, is_closed in zip(contours, closed):
        pts = contour + [contour[0]] if is_closed else contour
        # Where in the pattern the outline starts (the PDF dash offset).
        pos = offset % total
        index = 0
        while pos > 0 and pos >= pattern[index]:
            pos -= pattern[index]
            index = (index + 1) % len(pattern)
        remaining = pattern[index] - pos
        on = index % 2 == 0
        current: list[tuple[float, float]] = [pts[0]] if on else []
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            length = math.hypot(bx - ax, by - ay)
            t = 0.0
            while length - t >= remaining:
                t += remaining
                p = (ax + (bx - ax) * t / length, ay + (by - ay) * t / length) if length else (ax, ay)
                if on:
                    current.append(p)
                    pieces.append(_as_piece(current))
                    current = []
                else:
                    current = [p]
                on = not on
                index = (index + 1) % len(pattern)
                remaining = pattern[index]
            remaining -= length - t
            if on:
                current.append((bx, by))
        if on and len(current) >= 2:
            pieces.append(_as_piece(current))
    return [p for p in pieces if len(p) >= 2]


def _as_piece(points: list[tuple[float, float]]) -> Contour:
    piece: Contour = []
    for x, y in points:
        p = (round(x), round(y))
        if not piece or piece[-1] != p:
            piece.append(p)
    if len(piece) == 1:  # a dot: a 1-unit piece, so its round caps draw the dot
        piece.append((piece[0][0] + 1, piece[0][1]))
    return piece


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
                self.image(child, region, opacity, dx, dy)
            elif isinstance(child, TextShape):
                self.text(child, region, opacity, dx, dy)
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

    def image(self, shape: ImageShape, region: _ClipRegion | None, opacity: float, dx: float, dy: float) -> None:
        a, b, c, d, e, f = shape.transform
        m = (a, b, c, d, e + dx, f + dy)
        placed = place_image(self.sheet.images[shape.image], shape.image, m, region, opacity * shape.opacity)
        if placed is None:
            return
        x0, y0, x1, y1 = placed.box
        # The picture's box, from its lower-left corner, like AutoCAD writes it.
        outline = [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]
        self.out.prims.append(Prim("image", [outline], [False], Style(), shape.layer, placed))

    def text(self, shape: TextShape, region: _ClipRegion | None, opacity: float, dx: float, dy: float) -> None:
        k = UNITS_PER_PT
        ox, oy = (shape.origin[0] + dx) * k, (shape.origin[1] + dy) * k
        a, b, c, d = (v * k for v in shape.matrix)
        # The text's box: along the baseline by its total advance, up by its size.
        length = sum(shape.advances) * k / max(math.hypot(a, b), 1e-9)
        corners = [(ox, oy), (ox + a * length, oy + b * length), (ox - c, oy - d), (ox + a * length - c, oy + b * length - d)]
        box = (
            round(min(x for x, _ in corners)), round(min(y for _, y in corners)),
            round(max(x for x, _ in corners)), round(max(y for _, y in corners)),
        )
        # Text cannot be cut at a clip edge; it is only left out when it lies
        # completely outside the visible area (as a PDF viewer would hide it).
        if _relation(region, box) == "outside":
            return
        self.out.fonts[shape.font] = self.sheet.fonts[shape.font]
        text = PreparedText(
            shape.text, shape.font, (round(ox), round(oy)), (a, b, c, d), shape.glyphs, [v * k for v in shape.advances]
        )
        style = Style(fill=_with_opacity(shape.fill, opacity))
        self.out.prims.append(Prim("text", [], [], style, shape.layer, text=text, box=box))

    def shape(self, shape: PathShape, region: _ClipRegion | None, opacity: float, dx: float, dy: float) -> None:
        contours, closed = to_contours(shape.ops, dx, dy)
        if not contours:
            return
        style = _scaled_style(shape.style, opacity)
        if style.fill:
            # One shape per hole-free piece: each gets its own "Polygon"
            # entry in the W2X file (see w2x.py).
            fill_style = replace(style, stroke=None, width=0.0, dashes=(), even_odd=False)
            for piece in _fill_pieces(contours, style.even_odd, region):
                self.out.prims.append(Prim("fill", [piece], [True], fill_style, shape.layer))
        if style.stroke:
            line_style = replace(style, fill=None)
            if style.dashes:
                # Dashes first, then trimming, so dashes keep their place
                # at a clip edge exactly as in the PDF.
                contours = _dash_pieces(contours, closed, style.dashes, style.dash_offset)
                closed = [False] * len(contours)
                cap = style.dash_cap
                line_style = replace(line_style, dashes=(), dash_offset=0.0, start_cap=cap, end_cap=cap)
            if not contours:
                return
            lines, line_closed = _stroke_contours(contours, closed, region)
            if lines:
                self.out.prims.append(Prim("stroke", lines, line_closed, line_style, shape.layer))


def _box_of(m: Matrix) -> tuple[float, float, float, float]:
    """Bounding box (points) of the unit square mapped by ``m``."""
    a, b, c, d, e, f = m
    xs = [e, a + e, c + e, a + c + e]
    ys = [f, b + f, d + f, b + d + f]
    return min(xs), min(ys), max(xs), max(ys)


def _redraw(img: ImageData, m: Matrix, region: _ClipRegion | None, opacity: float) -> PlacedImage | None:
    """Draw a picture upright onto a transparent canvas covering its box.

    Autodesk's format can only place a picture as an upright, unclipped
    box. So a rotated, mirrored, skewed, clipped or see-through picture is
    painted (by MuPDF, as a PDF viewer would) into a new upright picture of
    about the same resolution; areas outside it stay transparent.
    """
    a, b, c, d, _, _ = m
    side_x, side_y = math.hypot(a, b), math.hypot(c, d)
    if side_x <= 0 or side_y <= 0:
        return None
    x0, y0, x1, y1 = _box_of(m)
    if region is not None and region.bbox is not None:
        # Only the part inside the clip is visible; no need to paint more.
        rx0, ry0, rx1, ry1 = (v / UNITS_PER_PT for v in region.bbox)
        x0, y0, x1, y1 = max(x0, rx0), max(y0, ry0), min(x1, rx1), min(y1, ry1)
        if x1 <= x0 or y1 <= y0:
            return None
    k = max(img.width / side_x, img.height / side_y)  # pixels per point
    w_pt, h_pt = x1 - x0, y1 - y0
    k = min(k, math.sqrt(MAX_PICTURE_PIXELS / max(w_pt * h_pt, 1e-9)), MAX_PICTURE_SIDE / max(w_pt, h_pt))
    width, height = max(1, math.ceil(w_pt * k)), max(1, math.ceil(h_pt * k))

    pix = mupdf.FzPixmap(mupdf.FzColorspace(mupdf.FzColorspace.Fixed_RGB), width, height, mupdf.FzSeparations(), 1)
    mupdf.fz_clear_pixmap(pix)  # fully transparent
    device = mupdf.fz_new_draw_device(mupdf.FzMatrix(), pix)
    to_pixels = (k, 0.0, 0.0, k, -x0 * k, -y0 * k)
    clipped = region is not None and region.paths
    if clipped:
        path = mupdf.fz_new_path()
        s = k / UNITS_PER_PT
        for contour in region.paths:  # type: ignore[union-attr]
            (px, py), *rest = contour
            mupdf.fz_moveto(path, px * s - x0 * k, py * s - y0 * k)
            for px, py in rest:
                mupdf.fz_lineto(path, px * s - x0 * k, py * s - y0 * k)
            mupdf.fz_closepath(path)
        mupdf.fz_clip_path(device, path, 0, mupdf.FzMatrix(), mupdf.FzRect(mupdf.FzRect.Fixed_INFINITE))
    image = mupdf.fz_new_image_from_buffer(mupdf.fz_new_buffer_from_copied_data(img.data))
    placement = _concat(m, to_pixels)
    mupdf.fz_fill_image(device, image, mupdf.FzMatrix(*placement), opacity, mupdf.FzColorParams())
    if clipped:
        mupdf.fz_pop_clip(device)
    mupdf.fz_close_device(device)

    out = pymupdf.Pixmap(pix, 1)
    out.set_dpi(96, 96)
    data = ImageData(out.tobytes("png"), "png", width, height)
    box = (
        round(x0 * UNITS_PER_PT),
        round(y0 * UNITS_PER_PT),
        round(x0 * UNITS_PER_PT + width / k * UNITS_PER_PT),
        round(y0 * UNITS_PER_PT + height / k * UNITS_PER_PT),
    )
    return PlacedImage(hashlib.sha1(data.data).hexdigest(), data, box)


def _concat(m1: Matrix, m2: Matrix) -> Matrix:
    """Apply m1 first, then m2."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def place_image(
    img: ImageData, key: str, m: Matrix, region: _ClipRegion | None, opacity: float
) -> PlacedImage | None:
    """Turn a picture placement into an upright box (redrawing if needed)."""
    x0, y0, x1, y1 = _box_of(m)
    box = (round(x0 * UNITS_PER_PT), round(y0 * UNITS_PER_PT), round(x1 * UNITS_PER_PT), round(y1 * UNITS_PER_PT))
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    relation = _relation(region, box)
    if relation == "outside":
        return None
    a, b, c, d = m[:4]
    upright = abs(b) < 1e-9 and abs(c) < 1e-9 and a > 0 and d > 0
    if upright and relation == "inside" and opacity >= 1:
        return PlacedImage(key, img, box)  # use the picture as it is
    return _redraw(img, m, region if relation == "cut" else None, opacity)


def prim_box(prim: Prim) -> Box:
    if prim.box is not None:
        return prim.box
    return _bbox(prim.contours)


def _assign_viewports(out: Prepared, sheet: Sheet) -> None:
    """Put each shape in the viewport its middle lies in.

    Where viewports overlap, the smallest one wins (a detail drawn inside a
    larger plan's area belongs to the detail).
    """
    scale = sheet.scale
    if scale is None:
        return
    out.scale = scale.scale
    k = UNITS_PER_PT
    found = []
    for vp in scale.viewports:
        x0, y0, x1, y1 = vp.box
        box = (round(min(x0, x1) * k), round(min(y0, y1) * k), round(max(x0, x1) * k), round(max(y0, y1) * k))
        if box[2] > box[0] and box[3] > box[1] and vp.scale > 0:
            found.append(PreparedViewport(box, box, vp.scale, vp.label))
    if not found:
        return
    by_size = sorted(range(len(found)), key=lambda i: (found[i].box[2] - found[i].box[0]) * (found[i].box[3] - found[i].box[1]))
    clips = [list(v.box) for v in found]
    for prim in out.prims:
        bx0, by0, bx1, by1 = prim_box(prim)
        mx, my = (bx0 + bx1) / 2, (by0 + by1) / 2
        for i in by_size:
            x0, y0, x1, y1 = found[i].box
            if x0 <= mx <= x1 and y0 <= my <= y1:
                prim.viewport = i
                c = clips[i]
                c[0], c[1], c[2], c[3] = min(c[0], bx0), min(c[1], by0), max(c[2], bx1), max(c[3], by1)
                break
    for vp, c in zip(found, clips):
        # one unit of room so lines exactly on the edge are not cut
        vp.clip = (c[0] - 1, c[1] - 1, c[2] + 1, c[3] + 1)
    out.viewports = found


def prepare(sheet: Sheet) -> Prepared:
    p = _Preparer(sheet)
    p.walk(sheet.root.children, None, 1.0, 0.0, 0.0)
    _assign_viewports(p.out, sheet)
    return p.out
