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
* NOT ``Contour_Set`` with several outlines: it makes the page fail.
  Shapes with holes are cut into hole-free pieces instead (prepare.py).
"""

from __future__ import annotations

from .pdf_reader import fmt
from .prepare import UNITS_PER_MM, Prepared
from .xps import NAME_PREFIX, shape_name


def line_weight(width: float) -> int:
    """Line thickness in whole grid units (at least 1, never "0")."""
    return max(1, round(width))


def w2x(prepared: Prepared) -> bytes:
    mm_per_unit = fmt(1 / UNITS_PER_MM, 12)
    out = [
        f'<W2X VersionMajor="7" VersionMinor="0" NamePrefix="{NAME_PREFIX}">',
        f'<Units refName="{shape_name(0)}" '
        f'Transform="{mm_per_unit},0,0,0,0,{mm_per_unit},0,0,0,0,1,0,0,0,0,1"/>',
        f'<View refName="{shape_name(0)}" '
        f'Area="0,0,{prepared.width_units},{prepared.height_units}"/>',
    ]
    current_weight: int | None = None
    for index, prim in enumerate(prepared.prims, start=1):
        name = shape_name(index)
        if prim.kind == "fill":
            out.append(f'<Polygon refName="{name}"/>')
        else:
            weight = line_weight(prim.style.width)
            if weight != current_weight:
                out.append(f'<RenditionSync refName="{name}"><Line_Weight Weight="{weight}"/></RenditionSync>')
                current_weight = weight
    out.append("</W2X>")
    return "".join(out).encode("utf-8")
