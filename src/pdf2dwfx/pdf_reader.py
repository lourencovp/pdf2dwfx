"""Read the vector graphics of one PDF page into a :class:`Sheet`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pymupdf

from .model import Group, PathShape, Sheet, Style

# PDF line width 0 means "thinnest line the device can show". XPS would
# draw nothing at all, so we substitute a very thin real width (in points).
HAIRLINE_PT = 0.05

_CAPS = {0: "Flat", 1: "Round", 2: "Square"}
_JOINS = {0: "Miter", 1: "Round", 2: "Bevel"}


def fmt(value: float, digits: int = 3) -> str:
    """Format a number compactly. For coordinates 3 decimals of a point is
    ~0.0004 mm; scale factors need more digits (``digits``) to stay exact
    across a whole A0 sheet."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _pt(p: Any) -> str:
    return f"{fmt(p.x)},{fmt(p.y)}"


def _same_point(a: Any, b: Any) -> bool:
    return abs(a.x - b.x) < 1e-6 and abs(a.y - b.y) < 1e-6


def path_data(items: Sequence[tuple], close_path: bool) -> str:
    """Turn PyMuPDF path items into XPS path geometry ("M x,y L x,y ...")."""
    parts: list[str] = []
    current = None  # current pen position, None = must start a new figure
    for item in items:
        op = item[0]
        if op == "l":
            start, end = item[1], item[2]
            if current is None or not _same_point(current, start):
                parts.append("M" + _pt(start))
            parts.append("L" + _pt(end))
            current = end
        elif op == "c":
            start, c1, c2, end = item[1], item[2], item[3], item[4]
            if current is None or not _same_point(current, start):
                parts.append("M" + _pt(start))
            parts.append(f"C{_pt(c1)} {_pt(c2)} {_pt(end)}")
            current = end
        elif op == "re":
            r, orientation = item[1], item[2]
            x0, y0, x1, y1 = fmt(r.x0), fmt(r.y0), fmt(r.x1), fmt(r.y1)
            # Keep the PDF drawing direction: it matters for non-zero fills
            # with holes (e.g. a frame made of two rectangles).
            if orientation >= 0:
                parts.append(f"M{x0},{y1}H{x1}V{y0}H{x0}Z")
            else:
                parts.append(f"M{x0},{y1}V{y0}H{x1}V{y1}Z")
            current = None
        elif op == "qu":
            q = item[1]
            parts.append(f"M{_pt(q.ul)}L{_pt(q.ur)}L{_pt(q.lr)}L{_pt(q.ll)}Z")
            current = None
    if close_path and current is not None:
        parts.append("Z")
    return "".join(parts)


def _color(components: Sequence[float] | None, opacity: float | None) -> str | None:
    """PDF colour (gray, RGB or CMYK, values 0..1) to an XPS colour string."""
    if components is None:
        return None
    if len(components) == 1:
        r = g = b = components[0]
    elif len(components) == 3:
        r, g, b = components
    elif len(components) == 4:  # simple CMYK -> RGB, good enough for CAD colours
        c, m, y, k = components
        r, g, b = (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
    else:
        return None
    rgb = "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in (r, g, b))
    if opacity is not None and opacity < 1:
        return f"#{round(max(0.0, opacity) * 255):02X}{rgb}"
    return "#" + rgb


def _dashes(spec: str | None) -> tuple[tuple[float, ...], float]:
    """Parse PyMuPDF's dash string, e.g. "[ 3 2 ] 0" -> ((3, 2), 0)."""
    if not spec or "[" not in spec:
        return (), 0.0
    inside, _, after = spec.strip().lstrip("[").partition("]")
    values = tuple(float(v) for v in inside.split())
    if not values or not any(values):
        return (), 0.0
    offset = float(after) if after.strip() else 0.0
    return values, offset


def _shape(d: dict[str, Any]) -> PathShape | None:
    kind = d["type"]  # "f" fill, "s" stroke, "fs" both
    data = path_data(d["items"], bool(d.get("closePath")))
    if not data:
        return None
    fill = _color(d.get("fill"), d.get("fill_opacity")) if "f" in kind else None
    stroke = _color(d.get("color"), d.get("stroke_opacity")) if "s" in kind else None
    if fill is None and stroke is None:
        return None
    width = 0.0
    cap, join = "Flat", "Miter"
    dashes: tuple[float, ...] = ()
    dash_offset = 0.0
    if stroke is not None:
        width = d.get("width") or 0.0
        if width <= 0:
            width = HAIRLINE_PT
        caps = d.get("lineCap")
        if isinstance(caps, (tuple, list)) and caps:
            cap = _CAPS.get(int(caps[0]), "Flat")
        join = _JOINS.get(int(d.get("lineJoin") or 0), "Miter")
        dashes, dash_offset = _dashes(d.get("dashes"))
    style = Style(
        fill=fill,
        stroke=stroke,
        width=width,
        cap=cap,
        join=join,
        dashes=dashes,
        dash_offset=dash_offset,
        even_odd=bool(d.get("even_odd")),
    )
    return PathShape(data=data, style=style, layer=d.get("layer"))


def read_page(page: pymupdf.Page, title: str | None = None) -> Sheet:
    """Collect all vector paths of a page, keeping clipping and draw order."""
    root = Group()
    # PyMuPDF's "extended" mode reports clips and transparency groups with a
    # nesting level; everything that follows at a deeper level belongs inside.
    stack: list[tuple[int, Group]] = [(-1, root)]
    for d in page.get_drawings(extended=True):
        level = d.get("level", 0) or 0
        while stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1]
        kind = d["type"]
        if kind == "clip":
            group = Group(
                clip=path_data(d["items"], True) or None,
                clip_even_odd=bool(d.get("even_odd")),
            )
        elif kind == "group":
            group = Group(opacity=d.get("opacity") or 1.0)
        else:
            shape = _shape(d)
            if shape is not None:
                parent.children.append(shape)
            continue
        parent.children.append(group)
        stack.append((level, group))

    m = page.rotation_matrix
    return Sheet(
        title=title or page.get_label() or f"Page {page.number + 1}",
        width_pt=page.rect.width,
        height_pt=page.rect.height,
        transform=(m.a, m.b, m.c, m.d, m.e, m.f),
        root=root,
    )
