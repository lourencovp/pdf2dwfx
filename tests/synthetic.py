"""Generate small synthetic PDF drawings for tests (no real project data)."""

from __future__ import annotations

from pathlib import Path

import pymupdf

# ISO paper sizes in millimetres (portrait).
A1_MM = (594.0, 841.0)
A3_MM = (297.0, 420.0)
A4_MM = (210.0, 297.0)
MM = 72 / 25.4  # points per millimetre


def _append(page: pymupdf.Page, content: bytes) -> None:
    """Append raw PDF drawing operators to a page (PDF coordinates:
    origin bottom-left, y up). Used for features PyMuPDF has no call for."""
    doc = page.parent
    if not page.get_contents():
        page.draw_rect(pymupdf.Rect(0, 0, 0, 0), color=None, width=0)  # creates a content stream
    # Append to the last content stream. (Do not use page.clean_contents():
    # it deletes resources that are registered but not used yet.)
    xref = page.get_contents()[-1]
    doc.update_stream(xref, doc.xref_stream(xref) + b"\nq\n" + content + b"\nQ\n")


def _add_resource(page: pymupdf.Page, kind: str, name: str, xref: int) -> None:
    """Register an object (image, pattern...) under a name in the page's resources."""
    doc = page.parent
    # /Resources may be stored inline in the page or as a separate object.
    res_type, res_value = doc.xref_get_key(page.xref, "Resources")
    if res_type == "xref":
        owner, key = int(res_value.split()[0]), kind
    else:
        owner, key = page.xref, f"Resources/{kind}"
    sub_type, sub_value = doc.xref_get_key(owner, key)
    if sub_type == "xref":  # the sub-dictionary is itself a separate object
        owner, key = int(sub_value.split()[0]), None
        sub_type, sub_value = "dict", doc.xref_object(owner, compressed=True)
    entries = sub_value[2:-2] if sub_type == "dict" else ""
    new = f"<<{entries}/{name} {xref} 0 R>>"
    if key is None:
        doc.update_object(owner, new)
    else:
        doc.xref_set_key(owner, key, new)


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

    # Line hatch clipped to a region, as CAD hatches usually are.
    hatch = [b"450 %.2f 230 120 re W n 0.3 w 0 0 0 RG" % (h - 780)]
    for i in range(-20, 40):
        x = 450 + i * 8
        hatch.append(b"%d %.2f m %d %.2f l" % (x, h - 660, x + 120, h - 780))
    hatch.append(b"S")
    _append(page, b"\n".join(hatch))

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


def _photo(width: int, height: int, alpha: bool = False) -> pymupdf.Pixmap:
    """A small colourful picture with a clear orientation (so flips show)."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), alpha)
    for y in range(height):
        for x in range(width):
            rgb = (x * 255 // width, y * 255 // height, 255 if x < width // 4 else 60)
            pix.set_pixel(x, y, rgb + ((255 if y < height // 2 else 90),) if alpha else rgb)
    return pix


def features_pdf(path: Path, pictures: bool = True, stretched: bool = True) -> Path:
    """One A4 page using the less common PDF features the reader supports.

    ``pictures=False`` leaves out everything that becomes a picture (photos,
    stencil, gradient); ``stretched=False`` leaves out the unevenly
    stretched line, whose thickness Autodesk's format can only approximate."""
    w, h = A4_MM[0] * MM, A4_MM[1] * MM
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)

    # 1. Lines underneath a JPEG photo, then lines on top of it (draw order).
    for i in range(8):
        page.draw_line((30, 40 + i * 8), (200, 40 + i * 8), color=(0, 0, 0), width=1.5)
    jpeg = _photo(400, 300).tobytes("jpeg", jpg_quality=95)  # ~290 dpi: stays JPEG
    if pictures:
        page.insert_image(pymupdf.Rect(60, 30, 160, 105), stream=jpeg)
    page.draw_line((40, 110), (190, 30), color=(1, 0, 0), width=2)

    # 2. A transparent PNG (soft mask) over a fill, and a rotated copy.
    page.draw_rect(pymupdf.Rect(230, 30, 380, 110), color=None, fill=(0, 0.6, 0))
    png = _photo(40, 30, alpha=True).tobytes("png")
    if pictures:
        page.insert_image(pymupdf.Rect(250, 40, 330, 100), stream=png)
        page.insert_image(pymupdf.Rect(450, 30, 540, 150), stream=png, rotate=90)

    if pictures:
        # 3. A mirrored image, placed with a raw transformation.
        img_xref = page.insert_image(pymupdf.Rect(0, 0, 1, 1), stream=jpeg)
        names = [x[7] for x in page.get_images(full=True) if x[0] == img_xref]
        _append(page, b"-100 0 0 60 160 %.2f cm /%s Do" % (h - 240, names[0].encode()))

        # 4. A stencil mask: a 1-bit shape painted in one colour.
        stencil = doc.get_new_xref()
        doc.update_object(
            stencil,
            "<</Type/XObject/Subtype/Image/Width 8/Height 8/ImageMask true/BitsPerComponent 1>>",
        )
        doc.update_stream(stencil, bytes([0x18, 0x3C, 0x7E, 0xFF, 0xFF, 0x7E, 0x3C, 0x18]))
        _add_resource(page, "XObject", "Stn", stencil)
        _append(page, b"0.9 0.4 0 rg 80 0 0 80 230 %.2f cm /Stn Do" % (h - 260))

    # 5. A pattern-fill hatch (PDF tiling pattern), clipped to a triangle.
    pattern = doc.get_new_xref()
    doc.update_object(
        pattern,
        "<</Type/Pattern/PatternType 1/PaintType 1/TilingType 1"
        "/BBox[0 0 12 12]/XStep 12/YStep 12/Resources<<>>>>",
    )
    doc.update_stream(pattern, b"0.6 w 0 0 1 RG 0 0 m 12 12 l S 0 6 m 6 12 l S")
    _add_resource(page, "Pattern", "Hatch", pattern)
    _append(page, b"/Pattern cs /Hatch scn 360 %.2f m 560 %.2f l 460 %.2f l f" % (h - 380, h - 380, h - 220))

    if pictures:
        # 6. A colour gradient (shading), clipped to a circle-ish shape.
        shading = doc.get_new_xref()
        doc.update_object(
            shading,
            "<</ShadingType 2/ColorSpace/DeviceRGB/Coords[40 0 200 0]/Extend[true true]"
            "/Function<</FunctionType 2/Domain[0 1]/C0[1 1 0]/C1[0 0 1]/N 1>>>>",
        )
        _add_resource(page, "Shading", "Grad", shading)
        _append(page, b"40 %.2f 160 100 re W n /Grad sh" % (h - 400))

    # 7. Transparency: semi-transparent fills overlapping.
    gs = doc.get_new_xref()
    doc.update_object(gs, "<</Type/ExtGState/ca 0.5/CA 0.5>>")
    _add_resource(page, "ExtGState", "Half", gs)
    _append(
        page,
        b"/Half gs 1 0 0 rg 40 %.2f 100 60 re f 0 0 1 rg 90 %.2f 100 60 re f"
        % (h - 520, h - 550),
    )

    if stretched:
        # 8. A stroke stretched 3x vertically: thickness must stretch too.
        _append(page, b"1 0 0 3 0 0 cm 2 w 0 0 0 RG 300 %.2f m 380 %.2f l 340 %.2f l h S"
                % ((h - 520) / 3, (h - 520) / 3, (h - 440) / 3))

    # 9. Caps, joins and dashes on thick lines.
    _append(
        page,
        b"8 w 0 0 0 RG "
        b"0 J 0 j 420 %.2f m 470 %.2f l 520 %.2f l S "
        b"1 J 1 j 420 %.2f m 470 %.2f l 520 %.2f l S "
        b"2 J 2 j [12 6] 3 d 420 %.2f m 470 %.2f l 520 %.2f l S"
        % (h - 460, h - 500, h - 460, h - 520, h - 560, h - 520, h - 580, h - 620, h - 580),
    )

    # 10. A hairline (width 0) and a CMYK colour.
    _append(page, b"0 w 0 1 1 0 K 40 %.2f m 550 %.2f l S" % (h - 650, h - 660))

    doc.save(path)
    return path
