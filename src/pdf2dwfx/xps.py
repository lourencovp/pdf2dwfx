"""Write a :class:`Sheet` as an XPS FixedPage (the drawing part of a DWFx)."""

from __future__ import annotations

from .model import Group, PathShape, Sheet, Style
from .pdf_reader import fmt

XPS_NS = "http://schemas.microsoft.com/xps/2005/06"
# XPS measures in 1/96 inch; PDF in 1/72 inch.
PT_TO_XPS = 96 / 72


def _style_attrs(style: Style) -> str:
    attrs: list[str] = []
    if style.fill:
        attrs.append(f'Fill="{style.fill}"')
    if style.stroke:
        attrs.append(f'Stroke="{style.stroke}" StrokeThickness="{fmt(style.width)}"')
        if style.cap != "Flat":
            attrs.append(
                f'StrokeStartLineCap="{style.cap}" StrokeEndLineCap="{style.cap}" '
                f'StrokeDashCap="{style.cap}"'
            )
        if style.join != "Miter":
            attrs.append(f'StrokeLineJoin="{style.join}"')
        if style.dashes:
            # XPS dash lengths are multiples of the stroke thickness.
            dashes = list(style.dashes)
            if len(dashes) % 2:
                dashes *= 2  # XPS needs dash/gap pairs; PDF repeats odd lists
            rel = " ".join(fmt(v / style.width) for v in dashes)
            attrs.append(f'StrokeDashArray="{rel}"')
            if style.dash_offset:
                attrs.append(f'StrokeDashOffset="{fmt(style.dash_offset / style.width)}"')
    return " ".join(attrs)


def _geometry(data: str, even_odd: bool) -> str:
    # XPS defaults to even-odd filling, PDF to non-zero: "F1" selects non-zero.
    return data if even_odd else "F1 " + data


def _can_merge(style: Style) -> bool:
    """Only plain opaque strokes can share one <Path> without changing the
    picture. Merged fills could cancel where shapes overlap, and merged
    transparent strokes would no longer darken where they cross."""
    return (
        style.fill is None
        and style.stroke is not None
        and len(style.stroke) == 7  # "#RRGGBB" = opaque
    )


def _write_children(children: list[PathShape | Group], out: list[str]) -> None:
    pending: list[str] = []  # geometry waiting to be merged into one Path
    pending_key: tuple[Style, str | None] | None = None

    def flush() -> None:
        nonlocal pending, pending_key
        if pending_key is not None:
            style = pending_key[0]
            data = _geometry("".join(pending), style.even_odd)
            out.append(f'<Path Data="{data}" {_style_attrs(style)}/>')
        pending, pending_key = [], None

    for child in children:
        if isinstance(child, PathShape):
            key = (child.style, child.layer)
            if _can_merge(child.style):
                if key != pending_key:
                    flush()
                    pending_key = key
                pending.append(child.data)
                continue
            flush()
            data = _geometry(child.data, child.style.even_odd)
            out.append(f'<Path Data="{data}" {_style_attrs(child.style)}/>')
        else:
            flush()
            attrs = ""
            if child.clip:
                attrs += f' Clip="{_geometry(child.clip, child.clip_even_odd)}"'
            if child.opacity < 1:
                attrs += f' Opacity="{fmt(child.opacity)}"'
            out.append(f"<Canvas{attrs}>")
            _write_children(child.children, out)
            out.append("</Canvas>")
    flush()


def fixed_page(sheet: Sheet, canvas_name: str) -> bytes:
    """Build the FixedPage XML.

    The outer canvas carries ``canvas_name``: the DWF sheet description
    points at it (``FixedPage.fpage?<canvas_name>``), exactly as AutoCAD's
    DWFx files do. Its transform converts our point coordinates (and page
    rotation) into XPS units.
    """
    a, b, c, d, e, f = (v * PT_TO_XPS for v in sheet.transform)
    transform = ",".join(fmt(v, 10) for v in (a, b, c, d, e, f))
    out: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<FixedPage xmlns="{XPS_NS}" Width="{fmt(sheet.width_pt * PT_TO_XPS)}" '
        f'Height="{fmt(sheet.height_pt * PT_TO_XPS)}" xml:lang="und">',
        f'<Canvas Name="{canvas_name}" RenderTransform="{transform}">',
    ]
    _write_children(sheet.root.children, out)
    out.append("</Canvas></FixedPage>")
    return "".join(out).encode("utf-8")
