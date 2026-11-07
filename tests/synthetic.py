"""Generate small synthetic PDF drawings for tests (no real project data)."""

from __future__ import annotations

from pathlib import Path

import pymupdf

# ISO paper sizes in millimetres (portrait).
A1_MM = (594.0, 841.0)
A3_MM = (297.0, 420.0)
MM = 72 / 25.4  # points per millimetre


def drawing_pdf(
    path: Path,
    size_mm: tuple[float, float] = A3_MM,
    rotation: int = 0,
    cropbox_mm: tuple[float, float, float, float] | None = None,
) -> Path:
    """A page with the kinds of graphics CAD exports contain: thin and thick
    lines, dashes, curves, solid fills, a line hatch and a fill with a hole.
    The drawing is deliberately asymmetric so rotation errors show up."""
    w, h = size_mm[0] * MM, size_mm[1] * MM
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)

    # Border and an off-centre "title block".
    page.draw_rect(pymupdf.Rect(20, 20, w - 20, h - 20), color=(0, 0, 0), width=1.5)
    page.draw_rect(pymupdf.Rect(w - 220, h - 120, w - 30, h - 30), color=(0, 0, 0), width=0.7)

    # Thin "walls" and a thick line.
    for i in range(10):
        page.draw_line((60, 80 + i * 25), (60 + 20 * i + 150, 80 + i * 25), color=(0, 0, 0), width=0.25)
    page.draw_line((60, 400), (500, 420), color=(0.8, 0, 0), width=4)

    # Dashed axis.
    page.draw_line((40, 450), (w - 40, 450), color=(0, 0, 0.75), width=0.5, dashes="[6 3] 0")

    # Curves: a circle and a Bézier.
    page.draw_circle((300, 600), 60, color=(0, 0.5, 0), width=1)
    page.draw_bezier((80, 700), (150, 550), (250, 850), (400, 700), color=(0, 0, 0), width=1)

    # Solid fills with and without outline.
    page.draw_rect(pymupdf.Rect(450, 520, 560, 640), color=None, fill=(0.6, 0.6, 0.6))
    page.draw_rect(pymupdf.Rect(580, 520, 680, 640), color=(0, 0, 0), fill=(0.2, 0.4, 0.9), width=1)

    # Line hatch clipped to a region, as CAD hatches usually are. PyMuPDF has
    # no drawing call for clipping, so we append raw PDF operators
    # (PDF coordinates: origin bottom-left, y up).
    page.clean_contents()
    xref = page.get_contents()[0]
    hatch = [b"q 450 %.2f 230 120 re W n 0.3 w 0 0 0 RG" % (h - 780)]
    for i in range(-20, 40):
        x = 450 + i * 8
        hatch.append(b"%d %.2f m %d %.2f l" % (x, h - 660, x + 120, h - 780))
    hatch.append(b"S Q")
    doc.update_stream(xref, doc.xref_stream(xref) + b"\n" + b"\n".join(hatch))

    # A filled square with a square hole (even-odd).
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(150, 150, 260, 260))
    shape.draw_rect(pymupdf.Rect(180, 180, 230, 230))
    shape.finish(color=None, fill=(0, 0, 0), even_odd=True)
    shape.commit()

    if cropbox_mm is not None:
        page.set_cropbox(pymupdf.Rect(*(v * MM for v in cropbox_mm)))
    if rotation:
        page.set_rotation(rotation)
    doc.save(path)
    return path
