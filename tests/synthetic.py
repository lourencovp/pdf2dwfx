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


def layers_pdf(path: Path, with_hidden: bool = False) -> Path:
    """A page whose lines sit on PDF layers (optional content groups), the
    way CAD programs export them: a layer tree in which the short name
    "LED" exists twice (under FURNITURE and under LIGHTING), and nested
    marked content listing each element's enclosing layers."""
    w, h = A4_MM[0] * MM, A4_MM[1] * MM
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    keys = ["WALLS", "FURNITURE", "LED_1", "LIGHTING", "LED_2"] + (["HIDDEN"] if with_hidden else [])
    xref = {k: doc.add_ocg(k.split("_")[0], on=(k != "HIDDEN")) for k in keys}
    for i, k in enumerate(keys):
        _add_resource(page, "Properties", f"OC{i}", xref[k])
    tag = {k: f"/OC{i}".encode() for i, k in enumerate(keys)}
    # The layer tree as PDF viewers show it (/Order): children follow their parent.
    order = (
        f"[{xref['WALLS']} 0 R {xref['FURNITURE']} 0 R [{xref['LED_1']} 0 R] "
        f"{xref['LIGHTING']} 0 R [{xref['LED_2']} 0 R]"
        + (f" {xref['HIDDEN']} 0 R" if with_hidden else "")
        + "]"
    )
    doc.xref_set_key(doc.pdf_catalog(), "OCProperties/D/Order", order)

    def line(y: float, color: bytes) -> bytes:
        return b"%s RG 2 w 50 %.2f m 500 %.2f l S" % (color, h - y, h - y)

    content = [
        line(100, b"0 0 0"),  # outside any layer -> default layer
        b"/OC %s BDC " % tag["WALLS"] + line(200, b"1 0 0") + b" EMC",
        b"/OC %s BDC /OC %s BDC " % (tag["FURNITURE"], tag["LED_1"]) + line(300, b"0 0.6 0") + b" EMC EMC",
        b"/OC %s BDC /OC %s BDC " % (tag["LIGHTING"], tag["LED_2"]) + line(400, b"0 0 1") + b" EMC EMC",
        # An element on WALLS drawn inside a LIGHTING element: its own layer wins.
        b"/OC %s BDC /OC %s BDC " % (tag["LIGHTING"], tag["WALLS"]) + line(500, b"1 0 0") + b" EMC EMC",
    ]
    if with_hidden:
        content.append(b"/OC %s BDC " % tag["HIDDEN"] + line(600, b"1 0 1") + b" EMC")
    _append(page, b"\n".join(content))
    doc.save(path)
    return path


def text_pdf(path: Path) -> Path:
    """A page with text the way CAD programs write it, using a TrueType font
    that ships with PyMuPDF (so the test works on every system): plain
    text, rotated text, words placed by position instead of spaces, and
    text on a PDF layer."""
    w, h = A4_MM[0] * MM, A4_MM[1] * MM
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    page.insert_font(fontname="F1", fontbuffer=pymupdf.Font("cjk").buffer)
    page.insert_text((50, 100), "PLANTA PISO 1 | 1:100", fontname="F1", fontsize=14)
    page.insert_text((50, 140), "Sala de estar 25,40 m2", fontname="F1", fontsize=9, color=(0, 0, 0.8))
    page.insert_text((300, 400), "ALCADO NORTE", fontname="F1", fontsize=10, rotate=90)
    ocg = doc.add_ocg("TEXT")
    page.insert_text((50, 600), "On a layer", fontname="F1", fontsize=12, oc=ocg)
    # Words placed by position (TJ with a gap), as many CAD exports do.
    font_name = [f[4] for f in page.get_fonts() if "Droid" in f[3]][0]
    gid = pymupdf.Font("cjk")
    def hexs(s: str) -> str:
        return "".join(f"{gid.has_glyph(ord(ch)):04X}" for ch in s)
    _append(
        page,
        b"BT /%s 11 Tf 50 %.2f Td [<%s> -1500 <%s>] TJ ET"
        % (font_name.encode(), h - 200, hexs("COZINHA").encode(), hexs("12m2").encode()),
    )
    doc.subset_fonts()  # after all text is written, so every letter is kept
    doc.save(path)
    return path


# Paper sizes used to check that scales hold on every sheet size (mm).
PAPER_SIZES = {
    "A4 portrait": (210, 297),
    "A3 landscape": (420, 297),
    "A1 landscape": (841, 594),
    "A0 portrait": (841, 1189),
    "Letter": (215.9, 279.4),
    "long custom": (2000, 300),
}


def scale_sheet_layout(paper_mm: tuple[float, float]) -> dict[str, tuple[float, float, float, float]]:
    """Where things go on the scale test sheet, in mm from the top-left,
    as fractions of the paper so it fits any size."""
    w, h = paper_mm
    return {
        "plan": (0.08 * w, 0.10 * h, 0.45 * w, 0.55 * h),  # 1:50 drawing (clipped)
        "plan_title": (0.08 * w, 0.58 * h, 0, 0),
        "detail": (0.55 * w, 0.10 * h, 0.80 * w, 0.40 * h),  # 1:5 drawing (clipped)
        "detail_title": (0.55 * w, 0.43 * h, 0, 0),
        "untitled": (0.55 * w, 0.55 * h, 0.75 * w, 0.75 * h),  # no scale written
        "keyword": (0.86 * w, 0.90 * h, 0, 0),  # "ESCALA:"
        "title_scale": (0.86 * w, 0.92 * h, 0, 0),  # "1 : 50"
        "sheet_number": (0.86 * w, 0.97 * h, 0, 0),  # "3 / 28": not a scale
    }


def scale_pdf(
    path: Path,
    paper_mm: tuple[float, float] = (420, 297),
    rotate: int = 0,
    crop_offset: bool = False,
    title_block_scale: str = "1 : 50",
    measure_data: bool = False,
    clipped: bool = True,
) -> Path:
    """A sheet like an architect's layout: a 1:50 plan and a 1:5 detail,
    each clipped to its viewport (as CAD programs export them) with its
    title below it, an untitled detail, and a title block with
    "ESCALA: 1 : 50" and a sheet number "3 / 28".

    ``rotate``: the page is stored turned (PDF /Rotate) but drawn so the
    user sees the same layout. ``crop_offset``: the visible page (crop
    box) does not start at the PDF origin. ``measure_data``: also store the
    viewports' scales as PDF measurement data (/VP, /Measure).
    ``clipped=False``: no viewport clipping (some programs export so).
    """
    w_mm, h_mm = paper_mm
    margin = 30 if crop_offset else 0  # extra paper around the crop box (mm)
    doc = pymupdf.open()
    # Draw on the page as the user will see it; for a rotated page, create
    # it with swapped sides and rotate afterwards, mapping coordinates.
    page = doc.new_page(width=(w_mm + 2 * margin) * MM, height=(h_mm + 2 * margin) * MM)
    page.insert_font(fontname="F1", fontbuffer=pymupdf.Font("cjk").buffer)
    layout = scale_sheet_layout(paper_mm)

    def at(x: float, y: float) -> pymupdf.Point:
        return pymupdf.Point((x + margin) * MM, (y + margin) * MM)

    def rect(box) -> pymupdf.Rect:
        return pymupdf.Rect(at(box[0], box[1]), at(box[2], box[3]))

    text_size = max(min(w_mm, h_mm) / 40, 4.0)  # readable on any paper size
    for name in ("plan", "detail", "untitled"):
        box = rect(layout[name])
        shape = page.new_shape()
        # A small "drawing": outline, a diagonal and an inner grid of lines,
        # clipped to the viewport box like CAD viewports.
        shape.draw_rect(box)
        shape.draw_line(box.tl, box.br)
        for i in range(1, 6):
            x = box.x0 + box.width * i / 6
            shape.draw_line(pymupdf.Point(x, box.y0), pymupdf.Point(x, box.y1))
        shape.finish(color=(0, 0, 0), width=0.3)
        shape.commit()
    page.insert_text(at(*layout["plan_title"][:2]), "PLANTA PISO 1 | 1:50", fontname="F1", fontsize=text_size)
    page.insert_text(at(*layout["detail_title"][:2]), "DETALHE A 1:5", fontname="F1", fontsize=text_size)
    page.insert_text(at(*layout["keyword"][:2]), "ESCALA:", fontname="F1", fontsize=text_size * 0.5)
    page.insert_text(at(*layout["title_scale"][:2]), title_block_scale, fontname="F1", fontsize=text_size * 0.8)
    page.insert_text(at(*layout["sheet_number"][:2]), "3 / 28", fontname="F1", fontsize=text_size)
    # Clip each drawing to its viewport, as CAD exports do: wrap the
    # drawing commands in "q <box> re W n ... Q". Simplest: draw again
    # inside clips and leave the unclipped ones (same geometry).
    page_h = page.rect.height
    clips = b""
    for name in ("plan", "detail", "untitled") if clipped else ():
        r = rect(layout[name])
        clips += b"q %.3f %.3f %.3f %.3f re W n 0.2 w 0 0 0 RG " % (r.x0, page_h - r.y1, r.width, r.height)
        for i in range(12):
            y = page_h - r.y1 + r.height * (i + 0.5) / 12
            clips += b"%.3f %.3f m %.3f %.3f l S " % (r.x0 - 5, y, r.x1 + 5, y)
        clips += b"Q "
    _append(page, clips)
    doc.subset_fonts()
    doc.save(path)
    doc.close()
    doc = pymupdf.open(path)
    page = doc[0]
    # cm: from the drawing's PDF coordinates to how the page is stored.
    cm = _rotate_content(doc, rotate) if rotate else (1, 0, 0, 1, 0, 0)

    def stored(x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = cm
        return a * x + c * y + e, b * x + d * y + f

    def stored_box(x0: float, y0: float, x1: float, y1: float) -> str:
        (ax, ay), (bx, by) = stored(x0, y0), stored(x1, y1)
        return f"{min(ax, bx):.3f} {min(ay, by):.3f} {max(ax, bx):.3f} {max(ay, by):.3f}"

    if measure_data:
        vps = []
        for name, scale in (("plan", 50), ("detail", 5)):
            r = rect(layout[name])
            metres_per_pt = 0.0254 / 72 * scale
            vps.append(
                f"<< /Type /Viewport /Name ({name}) /BBox [{stored_box(r.x0, page_h - r.y1, r.x1, page_h - r.y0)}] "
                f"/Measure << /Type /Measure /Subtype /RL /R (1 mm = {scale} mm) "
                f"/X [<< /Type /NumberFormat /U (m) /C {metres_per_pt:.10f} /D 1000 >>] >> >>"
            )
        doc.xref_set_key(page.xref, "VP", "[" + " ".join(vps) + "]")
    if crop_offset:
        box = stored_box(margin * MM, margin * MM, (w_mm + margin) * MM, (h_mm + margin) * MM)
        doc.xref_set_key(page.xref, "CropBox", f"[{box}]")
    doc.save(path, incremental=True, encryption=0)
    doc.close()
    return path


def _rotate_content(doc: pymupdf.Document, rotate: int) -> tuple:
    """Turn page 1's stored content by -rotate and set /Rotate rotate, so the
    page looks the same to the user but is stored rotated (as some CAD
    programs and scanners write landscape sheets)."""
    page = doc[0]
    mb = page.mediabox
    w, h = mb.width, mb.height
    # Matrix taking the visible layout to the stored (unrotated) space.
    if rotate == 90:
        cm, new_box = (0, 1, -1, 0, h, 0), (0, 0, h, w)
    elif rotate == 270:
        cm, new_box = (0, -1, 1, 0, 0, w), (0, 0, h, w)
    elif rotate == 180:
        cm, new_box = (-1, 0, 0, -1, w, h), (0, 0, w, h)
    else:
        raise ValueError(rotate)
    xrefs = page.get_contents()
    content = b"".join(doc.xref_stream(x) for x in xrefs)
    doc.update_stream(xrefs[0], b"q %g %g %g %g %g %g cm\n" % cm + content + b"\nQ")
    for x in xrefs[1:]:
        doc.update_stream(x, b"")
    doc.xref_set_key(page.xref, "MediaBox", "[%g %g %g %g]" % new_box)
    doc.xref_set_key(page.xref, "Rotate", str(rotate))
    return cm


def fonts_pdf(path: Path) -> Path:
    """Text in fonts a DWFx cannot embed (only TrueType can be): the
    standard PDF font Helvetica (not embedded at all), an embedded Type 1
    (CFF) font, and a Type 3 font (letters drawn as little pictures), in
    several sizes, colours and a rotation, plus one TrueType line."""
    doc = pymupdf.open()
    page = doc.new_page(width=420 * MM, height=297 * MM)
    page.insert_text((60, 100), "Helvetica not embedded 1:100", fontname="helv", fontsize=24)
    page.insert_text((60, 160), "Rotated Helvetica", fontname="helv", fontsize=18, rotate=90, color=(0.8, 0, 0))
    page.insert_font(fontname="T1", fontbuffer=pymupdf.Font("Times-Roman").buffer)
    page.insert_text((60, 220), "Embedded Type 1 (CFF) font", fontname="T1", fontsize=30, color=(0, 0, 0.7))
    page.insert_font(fontname="TT", fontbuffer=pymupdf.Font("cjk").buffer)
    page.insert_text((60, 300), "TrueType stays real text", fontname="TT", fontsize=20)
    # Type 3 font: glyph "A" is a filled square, "B" a triangle.
    procs_a = doc.get_new_xref()
    doc.update_object(procs_a, "<<>>")
    doc.update_stream(procs_a, b"1000 0 0 0 800 800 d1 0 0 800 800 re f")
    procs_b = doc.get_new_xref()
    doc.update_object(procs_b, "<<>>")
    doc.update_stream(procs_b, b"1000 0 0 0 800 800 d1 0 0 m 800 0 l 400 800 l f")
    font = doc.get_new_xref()
    doc.update_object(
        font,
        "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 800 800] /FontMatrix [0.001 0 0 0.001 0 0] "
        f"/CharProcs << /A {procs_a} 0 R /B {procs_b} 0 R >> "
        "/Encoding << /Type /Encoding /Differences [65 /A /B] >> /FirstChar 65 /LastChar 66 "
        "/Widths [1000 1000] /ToUnicode null >>",
    )
    _add_resource(page, "Font", "F3", font)
    _append(page, b"BT /F3 40 Tf 0 0.5 0 rg 60 %.2f Td (ABAB) Tj ET" % (297 * MM - 420))
    doc.save(path)
    return path
