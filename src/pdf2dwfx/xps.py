"""Write a :class:`Sheet` as an XPS FixedPage (the drawing part of a DWFx)."""

from __future__ import annotations

from collections.abc import Mapping

from .model import Group, ImageShape, Matrix, PathShape, Sheet, Style
from .pdf_reader import fmt

XPS_NS = "http://schemas.microsoft.com/xps/2005/06"
# XPS measures in 1/96 inch; PDF in 1/72 inch.
PT_TO_XPS = 96 / 72


def _matrix(m: Matrix) -> str:
    return ",".join(fmt(v, 10) for v in m)


def _style_attrs(style: Style) -> str:
    attrs: list[str] = []
    if style.fill:
        attrs.append(f'Fill="{style.fill}"')
    if style.stroke:
        attrs.append(f'Stroke="{style.stroke}" StrokeThickness="{fmt(style.width, 6)}"')
        if style.start_cap != "Flat":
            attrs.append(f'StrokeStartLineCap="{style.start_cap}"')
        if style.end_cap != "Flat":
            attrs.append(f'StrokeEndLineCap="{style.end_cap}"')
        if style.join != "Miter":
            attrs.append(f'StrokeLineJoin="{style.join}"')
        elif style.miter_limit != 10:
            attrs.append(f'StrokeMiterLimit="{fmt(style.miter_limit)}"')
        if style.dashes:
            if style.dash_cap != "Flat":
                attrs.append(f'StrokeDashCap="{style.dash_cap}"')
            # XPS dash lengths are multiples of the stroke thickness.
            dashes = list(style.dashes)
            if len(dashes) % 2:
                dashes *= 2  # XPS needs dash/gap pairs; PDF repeats odd lists
            rel = " ".join(fmt(v / style.width, 6) for v in dashes)
            attrs.append(f'StrokeDashArray="{rel}"')
            if style.dash_offset:
                attrs.append(f'StrokeDashOffset="{fmt(style.dash_offset / style.width, 6)}"')
    return " ".join(attrs)


def _geometry(data: str, even_odd: bool) -> str:
    # XPS defaults to even-odd filling, PDF to non-zero: "F1" selects non-zero.
    return data if even_odd else "F1 " + data


# Note on file size: every PDF path becomes its own <Path>. Merging
# identical-looking paths into one would make files smaller, but it changes
# the picture: CAD PDFs often draw the same thin line several times, and
# viewers darken such repeated lines (anti-aliasing), while a merged path
# is drawn once. Tested on a real drawing: merging made 0.3 % of pixels
# differ, not merging 0 %. Accuracy first; revisit in the file-size milestone.


def _path(data: str, style: Style, transform: Matrix | None) -> str:
    rt = f' RenderTransform="{_matrix(transform)}"' if transform else ""
    return f'<Path Data="{_geometry(data, style.even_odd)}" {_style_attrs(style)}{rt}/>'


def _image(shape: ImageShape, uri: str, width: int, height: int) -> str:
    # A unit square, mapped onto the sheet by the image's transform and
    # filled with the whole picture.
    opacity = f' Opacity="{fmt(shape.opacity)}"' if shape.opacity < 1 else ""
    return (
        f'<Path Data="M0,0L1,0L1,1L0,1Z" RenderTransform="{_matrix(shape.transform)}"{opacity}>'
        f'<Path.Fill><ImageBrush ImageSource="{uri}" Viewbox="0,0,{width},{height}" '
        'ViewboxUnits="Absolute" Viewport="0,0,1,1" ViewportUnits="Absolute"/></Path.Fill></Path>'
    )


class _Writer:
    def __init__(self, sheet: Sheet, image_uris: Mapping[str, str]) -> None:
        self.sheet = sheet
        self.image_uris = image_uris

    def children(self, children: list, out: list[str]) -> None:
        for child in children:
            if isinstance(child, PathShape):
                out.append(_path(child.data, child.style, child.transform))
            elif isinstance(child, ImageShape):
                img = self.sheet.images[child.image]
                out.append(_image(child, self.image_uris[child.image], img.width, img.height))
            else:
                self.group(child, out)

    def group(self, group: Group, out: list[str]) -> None:
        attrs = ""
        if group.clip:
            attrs += f' Clip="{_geometry(group.clip, group.clip_even_odd)}"'
        if group.opacity < 1:
            attrs += f' Opacity="{fmt(group.opacity)}"'
        if group.offsets is None:
            out.append(f"<Canvas{attrs}>")
            self.children(group.children, out)
            out.append("</Canvas>")
            return
        # Repeated pattern tile: write its content once, then place copies.
        inner: list[str] = []
        self.children(group.children, inner)
        content = "".join(inner)
        out.append(f"<Canvas{attrs}>")
        for dx, dy in group.offsets:
            out.append(f'<Canvas RenderTransform="1,0,0,1,{fmt(dx)},{fmt(dy)}">{content}</Canvas>')
        out.append("</Canvas>")


def fixed_page(sheet: Sheet, canvas_name: str, image_uris: Mapping[str, str]) -> bytes:
    """Build the FixedPage XML.

    The outer canvas carries ``canvas_name``: the DWF sheet description
    points at it (``FixedPage.fpage?<canvas_name>``), exactly as AutoCAD's
    DWFx files do. Its transform converts our point coordinates into XPS
    units. ``image_uris`` gives the package location of each image.
    """
    k = fmt(PT_TO_XPS, 10)
    out: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<FixedPage xmlns="{XPS_NS}" Width="{fmt(sheet.width_pt * PT_TO_XPS, 6)}" '
        f'Height="{fmt(sheet.height_pt * PT_TO_XPS, 6)}" xml:lang="und">',
        f'<Canvas Name="{canvas_name}" RenderTransform="{k},0,0,{k},0,0">',
    ]
    _Writer(sheet, image_uris).children(sheet.root.children, out)
    out.append("</Canvas></FixedPage>")
    return "".join(out).encode("utf-8")
