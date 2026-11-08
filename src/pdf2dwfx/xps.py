"""Write a prepared sheet as an XPS FixedPage (the drawing part of a DWFx).

The markup follows the dialect that Autodesk's XAML translator (used by
Design Review) understands; see prepare.py for the background:

* every shape is a ``<Path>`` with a ``Name`` made of the W2X name prefix
  and a running number, so W2X entries can refer to it,
* coordinates are whole numbers on the prepared grid,
* path data uses only "M" (move) and "L" (line), see ``_data``,
* viewports (areas with their own scale) are named canvases, see ``layout``.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import quoteattr

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
    """Path data: "M" (move to a point) followed by "l" steps, each step
    relative to the previous point.

    Relative steps are short numbers ("l120,-35" instead of
    "L412345,287619"), which makes files much smaller and changes nothing
    in the drawing. Closed outlines step back to their first point instead
    of using "Z" (close): tested in Design Review, any path containing "Z"
    or "z" makes the whole page fail with "There was a problem displaying
    the page".
    """
    parts: list[str] = []
    for contour, closed in zip(prim.contours, prim.closed):
        points = contour + [contour[0]] if closed else contour
        (x, y), steps = points[0], []
        for nx, ny in points[1:]:
            steps.append(f"{nx - x},{ny - y}")
            x, y = nx, ny
        parts.append(f"M{points[0][0]},{points[0][1]}l{' '.join(steps)}")
    return "".join(parts)


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


# One entry of the page, in writing order (see ``layout``).
OPEN, SHAPE, CLOSE = "open", "shape", "close"


def layout(prepared: Prepared) -> list[tuple[str, str, int]]:
    """The page's elements in writing order: (what, name, index).

    ``SHAPE``: shape number ``index`` in prepared.prims (counted from 1).
    ``OPEN``: a canvas for viewport ``index`` starts; ``CLOSE``: it ends.

    Autodesk's translator numbers shapes *and* canvases in one running
    sequence (its Name/refName counter goes up for both), so the page and
    the W2X file must use exactly the same numbering: both are written
    from this list. Shapes of a viewport go inside that viewport's canvas;
    drawing order is kept by closing and reopening canvases when needed.
    """
    items: list[tuple[str, str, int]] = []
    number = 0
    current: int | None = None
    for index, prim in enumerate(prepared.prims, start=1):
        if prim.viewport != current:
            if current is not None:
                items.append((CLOSE, "", current))
            if prim.viewport is not None:
                number += 1
                items.append((OPEN, shape_name(number), prim.viewport))
            current = prim.viewport
        number += 1
        items.append((SHAPE, shape_name(number), index))
    if current is not None:
        items.append((CLOSE, "", current))
    return items


def box_data(box: tuple[int, int, int, int]) -> str:
    """A rectangle as path data (M and L only, see _data)."""
    x0, y0, x1, y1 = box
    return f"M{x0},{y0} L{x1},{y0} {x1},{y1} {x0},{y1} {x0},{y0}"


def brush_key(index: int) -> str:
    """Name of the picture brush used by shape number ``index``."""
    return f"R{index}"


def resource_dictionary(prepared: Prepared, sources: dict[str, str]) -> bytes | None:
    """The separate list of picture brushes, or None if there are no pictures.

    Autodesk's translator rejects a picture brush written inside a shape
    (it treats it as a corrupted file); like AutoCAD, each brush lives in
    this list and the shape refers to it by key. ``sources`` maps picture
    keys to their location relative to the page ("./name.png").
    """
    brushes = []
    for index, prim in enumerate(prepared.prims, start=1):
        if prim.image is None:
            continue
        img = prim.image.data
        x0, y0, x1, y1 = prim.image.box
        sx, sy = (x1 - x0) / img.width, (y1 - y0) / img.height
        brushes.append(
            f'<ImageBrush x:Key="{brush_key(index)}" ImageSource="{sources[prim.image.key]}" '
            f'Transform="{fmt(sx, 9)},0,0,{fmt(sy, 9)},{x0},{y0}" '
            f'Viewbox="0,0,{img.width},{img.height}" Viewport="0,0,{img.width},{img.height}" '
            'ViewboxUnits="Absolute" ViewportUnits="Absolute" TileMode="None"/>'
        )
    if not brushes:
        return None
    return (
        f'<ResourceDictionary xmlns="{XPS_NS}" '
        'xmlns:x="http://schemas.microsoft.com/xps/2005/06/resourcedictionary-key">'
        + "".join(brushes)
        + "</ResourceDictionary>"
    ).encode("utf-8")


def _glyphs(name: str, prim: Prim, font_uri: str) -> str:
    """A text item as XPS Glyphs.

    Design Review only uses the letters (UnicodeString); the rest lets other
    XPS viewers draw every letter exactly where the PDF put it: glyph
    numbers and advances (Indices, in hundredths of the text size) and a
    transform for rotated or stretched text.
    """
    t = prim.text
    a, b, c, d = t.matrix  # type: ignore[union-attr]
    em = math.hypot(c, d)  # text size: height of the em square
    width = math.hypot(a, b)
    x, y = t.origin  # type: ignore[union-attr]
    indices = ";".join(
        f"{gid},{fmt(adv / width * 100, 3)}" for gid, adv in zip(t.glyphs, t.advances)  # type: ignore[union-attr]
    )
    text = t.text  # type: ignore[union-attr]
    if text.startswith("{"):
        text = "{}" + text  # XPS: a leading "{}" escapes a leading brace
    transform = ""
    la, lb, lc, ld = a / em, b / em, c / em, d / em
    if abs(la - 1) > 1e-6 or abs(lb) > 1e-6 or abs(lc) > 1e-6 or abs(ld - 1) > 1e-6:
        # Rotated/stretched text: turn about the text's own origin.
        tx, ty = x - (la * x + lc * y), y - (lb * x + ld * y)
        transform = f' RenderTransform="{",".join(fmt(v, 9) for v in (la, lb, lc, ld, tx, ty))}"'
    return (
        f'<Glyphs Name="{name}" Fill="{prim.style.fill}" FontUri="{font_uri}" '
        f'FontRenderingEmSize="{fmt(em, 2)}" OriginX="{x}" OriginY="{y}" '
        f"UnicodeString={quoteattr(text)} Indices=\"{indices}\"{transform}/>"
    )


def fixed_page(
    prepared: Prepared,
    canvas_name: str,
    dictionary_source: str | None = None,
    font_uris: dict[str, str] | None = None,
) -> bytes:
    """Build the FixedPage XML.

    The outer canvas carries ``canvas_name``: the DWF sheet description
    points at it (``FixedPage.fpage?<canvas_name>``), exactly as AutoCAD's
    DWFx files do. Its transform converts grid units into XPS units.

    Autodesk viewers cut the *contents* of that named canvas out of the page
    and parse them as a stand-alone XML document (see the DWF Toolkit's
    FixedPageResourceExtractor). A stand-alone document needs exactly one
    root element that declares the XPS namespace, so -- like AutoCAD --
    everything goes inside a single inner canvas with its own ``xmlns``.

    ``dictionary_source``: location of the picture brush list, if any.
    """
    k = fmt(UNITS_TO_XPS, 15)
    w = fmt(prepared.width_units * UNITS_TO_XPS, 6)
    h = fmt(prepared.height_units * UNITS_TO_XPS, 6)
    out: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<FixedPage xmlns="{XPS_NS}" Width="{w}" Height="{h}" xml:lang="und">',
        f'<Canvas Name="{canvas_name}" RenderTransform="{k},0,0,{k},0,0">',
        f'<Canvas xmlns="{XPS_NS}">',
    ]
    if dictionary_source:
        out.append(f'<Canvas.Resources><ResourceDictionary Source="{dictionary_source}"/></Canvas.Resources>')
    for what, name, index in layout(prepared):
        if what == OPEN:
            # A viewport: its outline (Clip) tells Design Review where the
            # viewport's scale applies; the W2X file gives the scale.
            clip = prepared.viewports[index].clip
            out.append(f'<Canvas Name="{name}" Clip="{box_data(clip)}">')
            continue
        if what == CLOSE:
            out.append("</Canvas>")
            continue
        prim = prepared.prims[index - 1]
        if prim.kind == "text":
            out.append(_glyphs(name, prim, (font_uris or {})[prim.text.font]))  # type: ignore[union-attr]
            continue
        if prim.kind == "fill":
            paint = f'Fill="{prim.style.fill}"'
        elif prim.kind == "image":
            paint = f'Fill="{{StaticResource {brush_key(index)}}}"'
        else:
            paint = _stroke_attrs(prim.style)
        out.append(f'<Path Name="{name}" {paint} Data="{_data(prim)}"/>')
    out.append("</Canvas></Canvas></FixedPage>")
    return "".join(out).encode("utf-8")
