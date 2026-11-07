"""Write a prepared sheet as an XPS FixedPage (the drawing part of a DWFx).

The markup follows the dialect that Autodesk's XAML translator (used by
Design Review) understands; see prepare.py for the background:

* every shape is a ``<Path>`` with a ``Name`` made of the W2X name prefix
  and a running number, so W2X entries can refer to it,
* coordinates are whole numbers on the prepared grid,
* path data uses only "M" (move), "L" (line) and "Z" (close).
"""

from __future__ import annotations

from .pdf_reader import fmt
from .prepare import UNITS_PER_MM, Prepared, Prim
from .model import Style

XPS_NS = "http://schemas.microsoft.com/xps/2005/06"
# XPS measures in 1/96 inch.
UNITS_TO_XPS = 96 / (25.4 * UNITS_PER_MM)
# Shape names are NAME_PREFIX + number. Number 0 is reserved for the
# sheet-wide W2X entries (units, view); shapes start at 1.
NAME_PREFIX = "N_"


def _data(prim: Prim) -> str:
    """Path data with only "M" and "L".

    Closed outlines repeat their first point at the end instead of using
    the "Z" (close) command: tested in Design Review, any path containing
    "Z" or "z" makes the whole page fail with "There was a problem
    displaying the page". Repeating the point draws the same shape.
    """
    parts: list[str] = []
    for contour, closed in zip(prim.contours, prim.closed):
        points = contour + [contour[0]] if closed else contour
        x0, y0 = points[0]
        rest = " ".join(f"{x},{y}" for x, y in points[1:])
        parts.append(f"M{x0},{y0} L{rest}")
    return " ".join(parts)


def _stroke_attrs(style: Style) -> str:
    attrs = [f'Stroke="{style.stroke}" StrokeThickness="{fmt(style.width, 1)}"']
    if style.start_cap != "Flat":
        attrs.append(f'StrokeStartLineCap="{style.start_cap}"')
    if style.end_cap != "Flat":
        attrs.append(f'StrokeEndLineCap="{style.end_cap}"')
    if style.join != "Miter":
        attrs.append(f'StrokeLineJoin="{style.join}"')
    elif style.miter_limit != 10:
        attrs.append(f'StrokeMiterLimit="{fmt(style.miter_limit)}"')
    if style.dashes and style.width > 0:
        if style.dash_cap != "Flat":
            attrs.append(f'StrokeDashCap="{style.dash_cap}"')
        # XPS dash lengths are multiples of the stroke thickness.
        dashes = list(style.dashes)
        if len(dashes) % 2:
            dashes *= 2  # XPS needs dash/gap pairs; PDF repeats odd lists
        attrs.append(f'StrokeDashArray="{" ".join(fmt(v / style.width, 6) for v in dashes)}"')
        if style.dash_offset:
            attrs.append(f'StrokeDashOffset="{fmt(style.dash_offset / style.width, 6)}"')
    return " ".join(attrs)


def shape_name(index: int) -> str:
    return f"{NAME_PREFIX}{index}"


def fixed_page(prepared: Prepared, canvas_name: str) -> bytes:
    """Build the FixedPage XML.

    The outer canvas carries ``canvas_name``: the DWF sheet description
    points at it (``FixedPage.fpage?<canvas_name>``), exactly as AutoCAD's
    DWFx files do. Its transform converts grid units into XPS units.

    Autodesk viewers cut the *contents* of that named canvas out of the page
    and parse them as a stand-alone XML document (see the DWF Toolkit's
    FixedPageResourceExtractor). A stand-alone document needs exactly one
    root element that declares the XPS namespace, so -- like AutoCAD --
    everything goes inside a single inner canvas with its own ``xmlns``.
    """
    k = fmt(UNITS_TO_XPS, 12)
    w = fmt(prepared.width_units * UNITS_TO_XPS, 6)
    h = fmt(prepared.height_units * UNITS_TO_XPS, 6)
    out: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<FixedPage xmlns="{XPS_NS}" Width="{w}" Height="{h}" xml:lang="und">',
        f'<Canvas Name="{canvas_name}" RenderTransform="{k},0,0,{k},0,0">',
        f'<Canvas xmlns="{XPS_NS}">',
    ]
    for index, prim in enumerate(prepared.prims, start=1):
        if prim.kind == "fill":
            paint = f'Fill="{prim.style.fill}"'
        else:
            paint = _stroke_attrs(prim.style)
        out.append(f'<Path Name="{shape_name(index)}" {paint} Data="{_data(prim)}"/>')
    out.append("</Canvas></Canvas></FixedPage>")
    return "".join(out).encode("utf-8")
