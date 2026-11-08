"""Write the W2X companion file of a sheet.

Autodesk's XAML translator (used by Design Review) refuses to read a DWFx
sheet without this "2d graphics extension" file. It carries information
the XPS markup cannot express. Entries refer to shapes in the page by
number (``refName`` = name prefix + shape number, see xps.py).

Every rule below was verified by opening test files in Design Review:

* ``Units`` and ``View``: how real-world units relate to grid units
  (the sheet's drawing scale, or paper millimetres) and the sheet area.
* ``Viewport`` (inside ``RenditionSync``) for each viewport canvas: its
  own drawing scale, as a transform from metres to grid units. This is
  how AutoCAD stores several scales on one sheet (checked in AutoCAD's
  own DWF files and in the DWF Toolkit source).
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

import math
from xml.sax.saxutils import quoteattr

from .pdf_reader import fmt
from .prepare import UNITS_PER_MM, Prepared
from .xps import CLOSE, NAME_PREFIX, OPEN, layout, shape_name

# Layer for everything outside any PDF layer (and for PDFs without layers):
# "0" is the usual name of the default layer in CAD programs.
DEFAULT_LAYER = "0"


def line_weight(width: float) -> int:
    """Line thickness in whole grid units (at least 1, never "0")."""
    return max(1, round(width))


IMAGE_FORMAT_JPEG = 8  # WD_IMAGE_JPEG_EXT_OPCODE in the DWF Toolkit
IMAGE_FORMAT_PNG = 12  # WD_IMAGE_PNG_EXT_OPCODE


def _font_setting(prim, prepared: Prepared, font_uris: dict[str, str]) -> str:
    """The W2X Font setting for a text item.

    Design Review draws the text with the *installed* font of this name, at
    this height, turned by this rotation (verified); the embedded font file
    (FontUri) is only listed. Rotation is in 1/65536 of a full turn,
    counter-clockwise. WidthScale (1024 = normal) covers stretched text.
    """
    t = prim.text
    font = prepared.fonts[t.font]
    a, b, c, d = t.matrix
    height = max(1, round(math.hypot(c, d)))
    angle = math.degrees(math.atan2(-b, a)) % 360  # page y points down
    rotation = round(angle / 360 * 65536) % 65536
    width_scale = round(1024 * math.hypot(a, b) / math.hypot(c, d))
    extra = f' WidthScale="{width_scale}"' if abs(width_scale - 1024) > 10 else ""
    return (
        f"<Font Name={quoteattr(font.family)} FontUri=\"{font_uris[t.font]}\" "
        f'Bold="{int(font.bold)}" Italic="{int(font.italic)}" Underline="0" '
        f'Height="{height}" Rotation="{rotation}"{extra}/>'
    )


def units_transform(scale: float | None, x0: float = 0, y0: float = 0) -> str:
    """W2X transform from real-world units to grid units, as 16 numbers.

    With a scale: metres; 1 m is 1000 mm / scale on paper. Without: paper
    millimetres. The real-world origin goes to grid point (x0, y0). Only
    the scale matters for distances, so this holds for any paper size.
    """
    if scale:
        per_unit = UNITS_PER_MM * 1000 / scale  # grid units per metre
    else:
        per_unit = UNITS_PER_MM  # grid units per millimetre
    s = fmt(per_unit, 9)
    return f"{s},0,0,0,0,{s},0,0,0,0,1,0,{fmt(x0, 3)},{fmt(y0, 3)},0,1"


def w2x(
    prepared: Prepared,
    sources: dict[str, str] | None = None,
    font_uris: dict[str, str] | None = None,
) -> bytes:
    """``sources`` maps picture keys and ``font_uris`` font keys to their
    location relative to the page ("./name.png")."""
    h = prepared.height_units
    if prepared.scale:
        page_units = f'Label="m" Transform="{units_transform(prepared.scale)}"'
    else:
        page_units = f'Label="mm" Transform="{units_transform(None)}"'
    out = [
        f'<W2X VersionMajor="7" VersionMinor="0" NamePrefix="{NAME_PREFIX}">',
        f'<Units refName="{shape_name(0)}" {page_units}/>',
        f'<View refName="{shape_name(0)}" '
        f'Area="0,0,{prepared.width_units},{prepared.height_units}"/>',
    ]
    current_weight: int | None = None
    current_font: str | None = None
    current_layer: str | None = None
    layer_numbers: dict[str, int] = {}
    left_viewport = False
    for what, name, index in layout(prepared):
        if what == OPEN:
            vp = prepared.viewports[index]
            # Real-world origin at the viewport's lower-left corner (W2X y is up).
            x0, _, _, y1 = vp.box
            label = vp.label or f"Viewport {index + 1}"
            out.append(
                f'<RenditionSync refName="{name}"><Viewport Name={quoteattr(label)} Label="m" '
                f'Transform="{units_transform(vp.scale, x0, h - y1)}"/></RenditionSync>'
            )
            continue
        if what == CLOSE:
            left_viewport = True
            continue
        prim = prepared.prims[index - 1]
        # Settings that change at this shape, written before it like AutoCAD.
        changes: list[str] = []
        if left_viewport:
            # Back outside all viewports: an outline-less viewport ends the
            # last one (as the DWF Toolkit writes it), so the sheet scale
            # applies again.
            changes.append('<Viewport Name="" End="true"/>')
            left_viewport = False
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
        if prim.kind == "text":
            font = _font_setting(prim, prepared, font_uris or {})
            if font != current_font:
                changes.append(font)
                current_font = font
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
        elif prim.kind == "text":
            # The start of the baseline, in Autodesk's coordinates (y up).
            x, y = prim.text.origin  # type: ignore[union-attr]
            out.append(f'<Text refName="{name}" StartPoint="{x},{prepared.height_units - y}"/>')
    out.append("</W2X>")
    return "".join(out).encode("utf-8")
