"""Write the W2X companion file of a sheet.

Autodesk's XAML translator (used by Design Review) refuses to read a DWFx
sheet without this "2d graphics extension" file. It carries information
the XPS markup cannot express. Entries refer to shapes in the page by
number (``refName`` = name prefix + shape number, see xps.py).

Every rule below was verified by opening test files in Design Review:

* ``Units`` and ``View``: how grid units relate to real-world units
  (milestone 4 will set the drawing scale here) and the sheet area.
* ``Polygon`` for every filled shape. Without it the translator fills the
  outline as a strip of triangles, which is only right for convex shapes:
  an L-shaped fill came out with a corner cut off.
* ``Line_Weight`` (inside ``RenditionSync``) whenever the line thickness
  changes. The translator ignores the page's StrokeThickness; without
  this, every line is drawn as a hairline.
* ``Image`` (JPEG, format 8) or ``PNG_Group4_Image`` (PNG, format 12) for
  every picture, with its box in Autodesk's coordinates (y up from the
  bottom of the sheet). The picture itself is a brush in the separate
  brush list (see xps.resource_dictionary).
* ``Layer`` (inside ``RenditionSync``) whenever the layer changes: with its
  name and a number the first time, by number only afterwards. Design
  Review then lists the layers and can switch each one on and off.
* NOT ``Contour_Set`` with several outlines: it makes the page fail.
  Shapes with holes are cut into hole-free pieces instead (prepare.py).
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr

from .pdf_reader import fmt
from .prepare import UNITS_PER_MM, Prepared
from .xps import NAME_PREFIX, shape_name

# Layer for everything outside any PDF layer (and for PDFs without layers):
# "0" is the usual name of the default layer in CAD programs.
DEFAULT_LAYER = "0"


def line_weight(width: float) -> int:
    """Line thickness in whole grid units (at least 1, never "0")."""
    return max(1, round(width))


IMAGE_FORMAT_JPEG = 8  # WD_IMAGE_JPEG_EXT_OPCODE in the DWF Toolkit
IMAGE_FORMAT_PNG = 12  # WD_IMAGE_PNG_EXT_OPCODE


def w2x(prepared: Prepared, sources: dict[str, str] | None = None) -> bytes:
    """``sources`` maps picture keys to their location ("./name.png")."""
    mm_per_unit = fmt(1 / UNITS_PER_MM, 12)
    out = [
        f'<W2X VersionMajor="7" VersionMinor="0" NamePrefix="{NAME_PREFIX}">',
        f'<Units refName="{shape_name(0)}" '
        f'Transform="{mm_per_unit},0,0,0,0,{mm_per_unit},0,0,0,0,1,0,0,0,0,1"/>',
        f'<View refName="{shape_name(0)}" '
        f'Area="0,0,{prepared.width_units},{prepared.height_units}"/>',
    ]
    current_weight: int | None = None
    current_layer: str | None = None
    layer_numbers: dict[str, int] = {}
    for index, prim in enumerate(prepared.prims, start=1):
        name = shape_name(index)
        # Settings that change at this shape, written before it like AutoCAD.
        changes: list[str] = []
        layer = prim.layer or DEFAULT_LAYER
        if layer != current_layer:
            if layer in layer_numbers:
                changes.append(f'<Layer Number="{layer_numbers[layer]}"/>')
            else:
                # First use: name the layer; later switches use its number.
                layer_numbers[layer] = len(layer_numbers) + 1
                changes.append(f'<Layer Name={quoteattr(layer)} Number="{layer_numbers[layer]}"/>')
            current_layer = layer
        if prim.kind == "stroke":
            weight = line_weight(prim.style.width)
            if weight != current_weight:
                changes.insert(0, f'<Line_Weight Weight="{weight}"/>')
                current_weight = weight
        if changes:
            out.append(f'<RenditionSync refName="{name}">{"".join(changes)}</RenditionSync>')
        if prim.kind == "fill":
            out.append(f'<Polygon refName="{name}"/>')
        elif prim.kind == "image":
            # Pictures: JPEG as "Image", PNG as "PNG_Group4_Image". The box is
            # in Autodesk's coordinates, where y points up from the bottom.
            img = prim.image
            x0, y0, x1, y1 = img.box  # type: ignore[union-attr]
            h = prepared.height_units
            element, fmt_code = (
                ("Image", IMAGE_FORMAT_JPEG) if img.data.ext == "jpg" else ("PNG_Group4_Image", IMAGE_FORMAT_PNG)  # type: ignore[union-attr]
            )
            out.append(
                f'<{element} refName="{name}" Format="{fmt_code}" Ref="{(sources or {})[img.key]}" '  # type: ignore[union-attr]
                f'Width="{img.data.width}" Height="{img.data.height}" Area="{x0},{h - y1},{x1},{h - y0}"/>'  # type: ignore[union-attr]
            )
    out.append("</W2X>")
    return "".join(out).encode("utf-8")
