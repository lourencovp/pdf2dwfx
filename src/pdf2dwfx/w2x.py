"""Write the W2X companion file of a sheet.

Autodesk's XAML translator (used by Design Review) refuses to read a DWFx
sheet without this "2d graphics extension" file. It carries information
the XPS markup cannot express. Entries refer to shapes in the page by
number (``refName`` = name prefix + shape number, see xps.py).

What we write:

* ``Units``: how grid units relate to real-world units (milestone 4 will
  set the drawing scale here),
* ``View``: the sheet area,
* ``Contour_Set`` for every filled shape with more than one outline.
  Without it the translator fills each outline on its own, so holes
  (e.g. an opening in a wall) would come out filled.
"""

from __future__ import annotations

from .pdf_reader import fmt
from .prepare import UNITS_PER_MM, Prepared
from .xps import NAME_PREFIX, shape_name


def w2x(prepared: Prepared) -> bytes:
    mm_per_unit = fmt(1 / UNITS_PER_MM, 12)
    out = [
        f'<W2X VersionMajor="7" VersionMinor="0" NamePrefix="{NAME_PREFIX}">',
        f'<Units refName="{shape_name(0)}" '
        f'Transform="{mm_per_unit},0,0,0,0,{mm_per_unit},0,0,0,0,1,0,0,0,0,1"/>',
        f'<View refName="{shape_name(0)}" '
        f'Area="0,0,{prepared.width_units},{prepared.height_units}"/>',
    ]
    for index, prim in enumerate(prepared.prims, start=1):
        if prim.kind == "fill" and len(prim.contours) > 1:
            # Count includes each outline's closing point, as the translator expects.
            total = sum(len(c) + 1 for c in prim.contours)
            out.append(
                f'<Contour_Set refName="{shape_name(index)}" '
                f'Contours="{len(prim.contours)}" Count="{total}"/>'
            )
    out.append("</W2X>")
    return "".join(out).encode("utf-8")
