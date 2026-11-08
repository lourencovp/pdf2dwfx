"""Read everything drawn on one PDF page into a :class:`Sheet`.

How it works: MuPDF (the engine inside PyMuPDF) draws a page by calling a
"device" once per operation: fill this path, stroke that path, set a clip,
place an image, and so on, in exact painting order. Normally the device is
a screen or a picture. Here we plug in our own device, ``_Recorder``, which
writes each operation down instead of painting it.

This is more faithful than PyMuPDF's convenience function
``Page.get_drawings()``, which merges and drops some paths and does not
report where images fall in the painting order.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from typing import Any

import pymupdf
from pymupdf import mupdf

from .fonts import is_truetype, truetype_names, windows_font
from .model import (
    FontData,
    Group,
    ImageData,
    ImageShape,
    Matrix,
    PathShape,
    Sheet,
    Style,
    TextShape,
)

# PDF line width 0 means "thinnest line the device can show". XPS would
# draw nothing at all, so we substitute a very thin real width (in points).
HAIRLINE_PT = 0.05
# Colour gradients ("shadings") are turned into pictures at this resolution.
SHADING_DPI = 150
MAX_SHADING_PIXELS = 16_000_000  # lower the resolution for huge gradients
# Pictures shown at less than MIN_CRISP_DPI on paper are enlarged (pixels
# repeated) to about TARGET_CRISP_DPI, so viewers do not blur them; see
# upscale_factor(). The pixel limit keeps one picture from growing huge.
MIN_CRISP_DPI = 150
TARGET_CRISP_DPI = 300
MAX_UPSCALED_PIXELS = 16_000_000
# Joins nested PDF layer names into one Design Review layer name.
LAYER_SEPARATOR = " / "
# Safety limit when expanding repeating pattern fills into geometry.
MAX_PATTERN_TILES = 100_000

_CAPS = {0: "Flat", 1: "Round", 2: "Square", 3: "Triangle"}
_JOINS = {0: "Miter", 1: "Round", 2: "Bevel", 3: "Miter"}


def fmt(value: float, digits: int = 3) -> str:
    """Format a number compactly. For coordinates 3 decimals of a point is
    ~0.0004 mm; scale factors need more digits (``digits``) to stay exact
    across a whole A0 sheet."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def concat(m1: Matrix, m2: Matrix) -> Matrix:
    """Apply m1 first, then m2."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _is_uniform(m: Matrix) -> bool:
    """True if the transform scales equally in all directions (it may rotate
    or mirror). Then a stroke's thickness can simply be scaled."""
    a, b, c, d = m[:4]
    sx, sy = math.hypot(a, b), math.hypot(c, d)
    return abs(a * c + b * d) <= 1e-9 * max(sx * sy, 1e-30) and abs(sx - sy) <= 1e-9 * max(sx, sy)


def _scale(m: Matrix) -> float:
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))


# --- Path geometry -----------------------------------------------------------


class _PathWalker(mupdf.FzPathWalker2):
    """Receives the segments of one path from MuPDF. Curves of other kinds
    (quadratic, rectangles...) are converted by MuPDF into these four."""

    def __init__(self) -> None:
        super().__init__()
        self.use_virtual_moveto()
        self.use_virtual_lineto()
        self.use_virtual_curveto()
        self.use_virtual_closepath()
        self.ops: list[tuple] = []

    def moveto(self, ctx: Any, x: float, y: float) -> None:
        self.ops.append(("M", x, y))

    def lineto(self, ctx: Any, x: float, y: float) -> None:
        self.ops.append(("L", x, y))

    def curveto(self, ctx: Any, x1: float, y1: float, x2: float, y2: float, x3: float, y3: float) -> None:
        self.ops.append(("C", x1, y1, x2, y2, x3, y3))

    def closepath(self, ctx: Any) -> None:
        self.ops.append(("Z",))


def _walk(path: Any) -> list[tuple]:
    return _walk_fz(mupdf.FzPath(mupdf.ll_fz_keep_path(path)))


def _walk_fz(path: mupdf.FzPath) -> list[tuple]:
    walker = _PathWalker()
    mupdf.fz_walk_path(path, walker, walker.m_internal)
    return walker.ops


def transform_ops(ops: list[tuple], m: Matrix) -> list[tuple]:
    """Path segments with every point moved by the transform ``m``."""
    a, b, c, d, e, f = m
    out: list[tuple] = []
    for op in ops:
        if op[0] == "Z":
            out.append(op)
            continue
        pts: list[float] = []
        for i in range(1, len(op), 2):
            x, y = op[i], op[i + 1]
            pts.append(a * x + c * y + e)
            pts.append(b * x + d * y + f)
        out.append((op[0], *pts))
    return out


def _recolour(group: Group, fill: str) -> None:
    """Paint everything in a group in one colour (Type 3 letters take the
    text colour)."""
    for child in group.children:
        if isinstance(child, Group):
            _recolour(child, fill)
        elif isinstance(child, PathShape):
            style = child.style
            child.style = replace(style, fill=fill if style.fill else None, stroke=fill if style.stroke else None)


def draws_something(ops: list[tuple]) -> bool:
    """A path made only of "move to" steps draws nothing."""
    return any(op[0] in "LC" for op in ops)


# --- Colours -------------------------------------------------------------------


class _ColorConverter:
    """Converts any PDF colour (gray, RGB, CMYK, spot, ICC...) to RGB using
    MuPDF's own colour management, the same one used to display the PDF."""

    def __init__(self) -> None:
        self._rgb = mupdf.FzColorspace(mupdf.FzColorspace.Fixed_RGB)
        self._params = mupdf.FzColorParams()

    def rgb(self, colorspace: Any, color: Any) -> tuple[float, float, float]:
        out = mupdf.ll_fz_convert_color(
            colorspace, color, self._rgb.m_internal, None, self._params.internal()
        )
        return out[0], out[1], out[2]

    def xps(self, colorspace: Any, color: Any, alpha: float) -> str:
        rgb = "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in self.rgb(colorspace, color))
        if alpha < 1:
            return f"#{round(max(0.0, alpha) * 255):02X}{rgb}"
        return "#" + rgb


# --- Images --------------------------------------------------------------------

_JFIF_96DPI = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x60\x00\x60\x00\x00"


def _jpeg_at_96dpi(data: bytes) -> bytes | None:
    """Mark a JPEG as 96 dpi, the resolution XPS assumes when sizing images.

    Returns None when that cannot be done safely (then the caller re-encodes
    the image as PNG instead)."""
    if data[:2] != b"\xff\xd8":
        return None
    if data[2:4] == b"\xff\xe0" and data[6:11] == b"JFIF\x00":
        # Existing JFIF header: overwrite units and densities in place.
        return data[:13] + b"\x01\x00\x60\x00\x60" + data[18:]
    if b"Adobe" in data[:4096]:
        # Adobe JPEGs may store RGB directly; adding a JFIF header would make
        # viewers misread the colours.
        return None
    return data[:2] + _JFIF_96DPI + data[2:]


def _pixmap_from_image(image: Any) -> mupdf.FzPixmap:
    pix, _w, _h = mupdf.fz_get_pixmap_from_image(
        image,
        mupdf.FzIrect(pymupdf.FZ_MIN_INF_RECT, pymupdf.FZ_MIN_INF_RECT, pymupdf.FZ_MAX_INF_RECT, pymupdf.FZ_MAX_INF_RECT),
        mupdf.FzMatrix(image.w(), 0, 0, image.h(), 0, 0),
    )
    return pix


def upscale_factor(image: Any, ctm: Any) -> int:
    """How much to enlarge a low-resolution picture before storing it.

    A PDF viewer shows a small picture stretched over a large area with
    crisp square pixels (unless the PDF asks for smoothing), while XPS
    viewers always smooth it into a blur. Enlarging such a picture
    ourselves, by repeating pixels, keeps it crisp in any viewer."""
    if image.interpolate:
        return 1  # the PDF itself asks for smoothing
    width_pt = math.hypot(ctm.a, ctm.b)
    height_pt = math.hypot(ctm.c, ctm.d)
    if width_pt <= 0 or height_pt <= 0:
        return 1
    dpi = min(image.w / (width_pt / 72), image.h / (height_pt / 72))
    if dpi >= MIN_CRISP_DPI:
        return 1
    factor = math.ceil(TARGET_CRISP_DPI / max(dpi, 1e-6))
    # Never let one picture grow beyond MAX_UPSCALED_PIXELS.
    limit = math.floor(math.sqrt(MAX_UPSCALED_PIXELS / max(image.w * image.h, 1)))
    return max(1, min(factor, limit))


def _enlarge(pix: pymupdf.Pixmap, factor: int) -> pymupdf.Pixmap:
    """Enlarge by repeating each pixel ``factor`` times in both directions."""
    if factor <= 1:
        return pix
    n, w = pix.n, pix.width
    stride = pix.stride
    samples = pix.samples
    rows = []
    for y in range(pix.height):
        row = samples[y * stride : y * stride + w * n]
        wide = b"".join(row[i : i + n] * factor for i in range(0, w * n, n))
        rows.append(wide * factor)
    big = pymupdf.Pixmap(
        pix.colorspace, pymupdf.IRect(0, 0, w * factor, pix.height * factor), bool(pix.alpha)
    )
    big.samples_mv[:] = b"".join(rows)
    return big


def _png(pix: pymupdf.Pixmap) -> ImageData:
    pix.set_dpi(96, 96)
    return ImageData(pix.tobytes("png"), "png", pix.width, pix.height)


def _single_channel(fz_pix: mupdf.FzPixmap) -> pymupdf.Pixmap:
    """A mask as a one-channel picture (what PyMuPDF expects for masks).
    MuPDF may hand masks over as gray pictures or as alpha-only pictures."""
    pix = pymupdf.Pixmap(fz_pix, fz_pix.alpha())
    if pix.n == 1:
        return pix
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    return pymupdf.Pixmap(pymupdf.csGRAY, pix)


def _to_rgb_or_gray(pix: pymupdf.Pixmap) -> pymupdf.Pixmap:
    if pix.colorspace is not None and pix.colorspace.n in (1, 3):
        return pix
    return pymupdf.Pixmap(pymupdf.csRGB, pix)


def encode_image(image: Any, enlarge: int = 1) -> ImageData:
    """Encode an image for XPS, keeping JPEGs as they are where possible
    (no quality loss, no size growth) and using lossless PNG otherwise.
    ``enlarge``: see upscale_factor()."""
    img = mupdf.FzImage(mupdf.ll_fz_keep_image(image))
    raw = img.m_internal
    has_mask = raw.mask is not None
    cbuf = mupdf.ll_fz_compressed_image_buffer(raw)
    if (
        enlarge == 1
        and cbuf is not None
        and cbuf.params.type == mupdf.FZ_IMAGE_JPEG
        and not has_mask
        and not raw.use_colorkey
        and not raw.use_decode
        and mupdf.fz_colorspace_n(img.colorspace()) in (1, 3)
    ):
        data = mupdf.fz_buffer_extract_copy(mupdf.FzBuffer(mupdf.ll_fz_keep_buffer(cbuf.buffer)))
        jpeg = _jpeg_at_96dpi(bytes(data))
        if jpeg is not None:
            return ImageData(jpeg, "jpg", img.w(), img.h())

    pix = pymupdf.Pixmap(_pixmap_from_image(img), 1)  # keep any alpha it has
    if pix.alpha:
        # Colour-key transparency: convert colours, keep the alpha channel.
        base = _to_rgb_or_gray(pymupdf.Pixmap(pix, 0))
        alpha = pymupdf.Pixmap(None, pix)  # alpha channel only
        pix = pymupdf.Pixmap(base, alpha)
    else:
        pix = _to_rgb_or_gray(pix)
    if has_mask:
        # Soft mask: a second image holding the transparency of each pixel.
        mask_img = mupdf.FzImage(mupdf.ll_fz_keep_image(raw.mask))
        mask = _single_channel(_pixmap_from_image(mask_img))
        if (mask.width, mask.height) != (pix.width, pix.height):
            # PDF allows a mask of different resolution; resample it.
            mask = pymupdf.Pixmap(mask, pix.width, pix.height, None)
        pix = pymupdf.Pixmap(pymupdf.Pixmap(pix, 0) if pix.alpha else pix, mask)
    return _png(_enlarge(pix, enlarge))


def encode_stencil(image: Any, rgb: tuple[float, float, float], enlarge: int = 1) -> ImageData:
    """A stencil mask image: a 1-bit shape painted in a single colour."""
    img = mupdf.FzImage(mupdf.ll_fz_keep_image(image))
    shape = _single_channel(_pixmap_from_image(img))
    base = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, shape.width, shape.height), False)
    base.set_rect(base.irect, tuple(round(v * 255) for v in rgb))
    return _png(_enlarge(pymupdf.Pixmap(base, shape), enlarge))


# --- The recording device -------------------------------------------------------


def _address(obj: Any) -> int:
    """Memory address of a MuPDF object, to recognise the same object twice."""
    return int(getattr(obj, "this", obj))


def _m(ctm: Any) -> Matrix:
    return (ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f)


class _Recorder(mupdf.FzDevice2):
    def __init__(self, sheet: Sheet, page_transform: Matrix, layer_tree: "LayerTree") -> None:
        super().__init__()
        for name in (
            "fill_path", "stroke_path", "clip_path", "clip_stroke_path",
            "fill_text", "stroke_text", "clip_text", "clip_stroke_text", "ignore_text",
            "fill_shade", "fill_image", "fill_image_mask", "clip_image_mask",
            "pop_clip", "begin_mask", "end_mask", "begin_group", "end_group",
            "begin_tile", "end_tile", "begin_layer", "end_layer",
        ):  # fmt: skip
            getattr(self, "use_virtual_" + name)()
        self.sheet = sheet
        self.page_transform = page_transform  # MuPDF page space -> sheet
        self.colors = _ColorConverter()
        # Open groups; operations are added to the last one. Entries of
        # ``kinds`` say what opened each group, so the matching "end"
        # operation closes the right one.
        self.stack: list[Group] = [sheet.root]
        self.kinds: list[str] = ["root"]
        self.tiles: list[tuple[Any, Any, float, float, Matrix]] = []
        self.layers: list[str] = []
        self.layer_tree = layer_tree
        self.layer_cache: dict[tuple[str, ...], str] = {}
        self.font_keys: dict[int, str | None] = {}  # MuPDF font -> sheet.fonts key
        self.clip_boxes: dict[int, pymupdf.Rect] = {}  # id(clip group) -> bounding box
        # Open "clip by image" groups: id(group) -> [mask address, used?]
        self.image_clips: dict[int, list] = {}

    # -- helpers --

    @property
    def layer(self) -> str | None:
        """The current PDF layer as its full path, e.g. "02_INT DESIGN / LIGHTING / LED"."""
        names = tuple(n for n in self.layers if n)
        if not names:
            return None
        if names not in self.layer_cache:
            self.layer_cache[names] = LAYER_SEPARATOR.join(self.layer_tree.resolve(names))
        return self.layer_cache[names]

    def _sheet_matrix(self, ctm: Any) -> Matrix:
        return concat(_m(ctm), self.page_transform)

    def _add(self, element: PathShape | ImageShape | Group) -> None:
        self.stack[-1].children.append(element)

    def _push(self, group: Group, kind: str, attach: bool = True) -> None:
        if attach:
            self._add(group)
        self.stack.append(group)
        self.kinds.append(kind)

    def _pop(self, kinds: tuple[str, ...]) -> Group | None:
        """Close the innermost group opened by one of ``kinds``."""
        for i in range(len(self.kinds) - 1, 0, -1):
            if self.kinds[i] in kinds:
                group = self.stack[i]
                del self.stack[i:], self.kinds[i:]
                return group
        return None

    def _note(self, feature: str) -> None:
        self.sheet.unsupported[feature] += 1

    def _store_image(self, data: ImageData) -> str:
        key = hashlib.sha1(data.data).hexdigest()
        self.sheet.images.setdefault(key, data)
        return key

    def _place_image(self, key: str, ctm: Any, alpha: float) -> None:
        self._add(ImageShape(key, self._sheet_matrix(ctm), alpha, self.layer))

    # -- paths --

    def fill_path(self, ctx: Any, path: Any, even_odd: int, ctm: Any, cs: Any, color: Any, alpha: float, cp: Any) -> None:
        ops = transform_ops(_walk(path), self._sheet_matrix(ctm))
        if draws_something(ops):
            style = Style(fill=self.colors.xps(cs, color, alpha), even_odd=bool(even_odd))
            self._add(PathShape(ops, style, layer=self.layer))

    def stroke_path(self, ctx: Any, path: Any, stroke: Any, ctm: Any, cs: Any, color: Any, alpha: float, cp: Any) -> None:
        m = self._sheet_matrix(ctm)
        ops = transform_ops(_walk(path), m)
        if not draws_something(ops):
            return
        # Line thickness is scaled with the drawing. Autodesk's format has
        # no way to stretch a line's thickness unevenly, so for a stretched
        # drawing (rare) we use the average scale and report it.
        factor = _scale(m)
        if not _is_uniform(m):
            self._note("unevenly stretched line thickness (approximated)")
        width = stroke.linewidth * factor
        if width <= 0:
            width = HAIRLINE_PT
        dashes = tuple(
            mupdf.floats_getitem(stroke.dash_list, i) * factor for i in range(stroke.dash_len)
        )
        if not any(dashes):
            dashes = ()
        style = Style(
            stroke=self.colors.xps(cs, color, alpha),
            width=width,
            start_cap=_CAPS.get(stroke.start_cap, "Flat"),
            end_cap=_CAPS.get(stroke.end_cap, "Flat"),
            dash_cap=_CAPS.get(stroke.dash_cap, "Flat"),
            join=_JOINS.get(stroke.linejoin, "Miter"),
            miter_limit=max(1.0, stroke.miterlimit),
            dashes=dashes,
            dash_offset=stroke.dash_phase * factor if dashes else 0.0,
        )
        self._add(PathShape(ops, style, self.layer))

    # -- clipping --

    def clip_path(self, ctx: Any, path: Any, even_odd: int, ctm: Any, scissor: Any) -> None:
        ops = transform_ops(_walk(path), self._sheet_matrix(ctm))
        # An empty clip region (no drawing steps) hides everything inside it.
        group = Group(clip=ops if draws_something(ops) else [], clip_even_odd=bool(even_odd))
        # Remember the clip's bounding box (MuPDF's "scissor"), so gradient
        # pictures only need to cover the visible part.
        e, f = self.page_transform[4], self.page_transform[5]
        self.clip_boxes[id(group)] = pymupdf.Rect(scissor.x0 + e, scissor.y0 + f, scissor.x1 + e, scissor.y1 + f)
        self._push(group, "clip")

    def clip_stroke_path(self, ctx: Any, path: Any, stroke: Any, ctm: Any, scissor: Any) -> None:
        self._note("clipping by a line outline")
        self._push(Group(), "clip")

    def clip_text(self, ctx: Any, text: Any, ctm: Any, scissor: Any) -> None:
        self._note("clipping by text")
        self._push(Group(), "clip")

    def clip_stroke_text(self, ctx: Any, text: Any, stroke: Any, ctm: Any, scissor: Any) -> None:
        self._note("clipping by text")
        self._push(Group(), "clip")

    def clip_image_mask(self, ctx: Any, image: Any, ctm: Any, scissor: Any) -> None:
        # MuPDF draws a picture with a transparency mask as "clip by the
        # mask, draw the picture, end clip". We already build the
        # transparency into the picture itself (see encode_image), so that
        # common case needs no clip. Anything else is reported as missing.
        group = Group()
        self.image_clips[id(group)] = [_address(image), False]
        self._push(group, "clip")

    def pop_clip(self, ctx: Any) -> None:
        group = self._pop(("clip", "mask"))
        if group is not None:
            self.clip_boxes.pop(id(group), None)
        if group is not None and id(group) in self.image_clips:
            _, used = self.image_clips.pop(id(group))
            if not used:
                self._note("clipping by an image")

    # -- text (converted in milestone 3) --

    def fill_text(self, ctx: Any, text: Any, ctm: Any, cs: Any, color: Any, alpha: float, cp: Any) -> None:
        fill = self.colors.xps(cs, color, alpha)
        m = self._sheet_matrix(ctm)
        span = text.head
        while span:
            self._text_span(mupdf.FzTextSpan(span), m, fill, ctm)
            span = span.next

    def stroke_text(self, ctx: Any, text: Any, stroke: Any, ctm: Any, cs: Any, color: Any, alpha: float, cp: Any) -> None:
        # Outlined letters: Autodesk's text has no outline mode, so draw them
        # as normal (filled) text in the outline colour.
        self._note("outlined text (drawn as normal text)")
        self.fill_text(ctx, text, ctm, cs, color, alpha, cp)

    def _font(self, font: mupdf.FzFont) -> str | None:
        """Key of a font in sheet.fonts (registered on first use), or None if
        its file cannot be embedded in a DWFx (only TrueType/OpenType can)."""
        address = font.m_internal_value()
        if address in self.font_keys:
            return self.font_keys[address]
        key = None
        buffer = font.m_internal.buffer
        if buffer is not None:
            data = bytes(mupdf.fz_buffer_extract_copy(mupdf.FzBuffer(mupdf.ll_fz_keep_buffer(buffer))))
            if is_truetype(data):
                bold, italic = bool(mupdf.fz_font_is_bold(font)), bool(mupdf.fz_font_is_italic(font))
                names = truetype_names(data)
                if names:  # the font file's own Windows name: most reliable
                    family, style = names
                    bold = bold or "bold" in style.lower()
                    italic = italic or "italic" in style.lower() or "oblique" in style.lower()
                else:  # otherwise derive it from the PDF's font name
                    family, bold, italic = windows_font(mupdf.fz_font_name(font), bold, italic)
                key = hashlib.sha1(data).hexdigest()
                self.sheet.fonts.setdefault(key, FontData(data, family, bold, italic))
        self.font_keys[address] = key
        return key

    def _text_outlines(self, span: mupdf.FzTextSpan, m: Matrix, fill: str, ctm: Any) -> None:
        """Draw letters as filled shapes, for fonts a DWFx cannot embed
        (Type 1/CFF fonts, the standard PDF fonts like Helvetica when not
        embedded, Type 3 fonts). Looks exactly like the PDF; the letters
        are then shapes, not searchable text."""
        font = span.font()
        trm = span.trm()
        ops: list[tuple] = []
        is_type3 = bool(font.m_internal.t3procs)
        for i in range(span.m_internal.len):
            item = span.items(i)
            if item.gid < 0:
                continue
            glyph_m = mupdf.FzMatrix(trm.a, trm.b, trm.c, trm.d, item.x, item.y)
            if is_type3:
                # Type 3 letters are little drawings: run them through this
                # device (in the text colour) like any other drawing.
                group = Group()
                self._push(group, "type3")
                mupdf.fz_run_t3_glyph(font, item.gid, mupdf.fz_concat(glyph_m, mupdf.FzMatrix(ctm)), self)
                self._pop(("type3",))
                _recolour(group, fill)
                continue
            outline = mupdf.fz_outline_glyph(font, item.gid, glyph_m)
            if outline.m_internal:
                ops += _walk_fz(outline)
        ops = transform_ops(ops, m)
        if draws_something(ops):
            self._add(PathShape(ops, Style(fill=fill), layer=self.layer))

    def _text_span(self, span: mupdf.FzTextSpan, m: Matrix, fill: str, ctm: Any = None) -> None:
        """Turn one run of letters into text items a viewer lays out itself.

        Design Review places letters using the font's own spacing (it keeps
        no per-letter positions). So letters stay in one item only while
        the PDF puts each where the font would; where the PDF moves a letter
        by hand, a gap of about whole spaces becomes spaces, and anything
        else starts a new item at the letter's exact position.
        """
        font = span.font()
        key = self._font(font)
        count = span.m_internal.len
        if key is None:
            # Only TrueType fonts can go into a DWFx: draw the letters as shapes.
            self._note("text in fonts that cannot be embedded (drawn as shapes, not searchable)")
            self._text_outlines(span, m, fill, ctm)
            return
        trm = span.trm()
        a, b, c, d = concat((trm.a, trm.b, trm.c, trm.d, 0, 0), m)[:4]
        # PDF glyph space has y up; XPS em squares have y down.
        matrix = (a, b, -c, -d)
        size_x, size_y = math.hypot(a, b), math.hypot(c, d)
        if size_x <= 0 or size_y <= 0:
            return
        if a * (-d) - b * (-c) <= 0 or abs(a * -c + b * -d) > 1e-6 * size_x * size_y:
            self._note("mirrored or slanted text (drawn upright)")
        ux, uy = a / size_x, b / size_x  # baseline direction
        space_gid = mupdf.fz_encode_character(font, 32)
        space = mupdf.fz_advance_glyph(font, space_gid, 0) * size_x if space_gid > 0 else 0.28 * size_x
        e, f = m[4], m[5]
        ma, mb, mc, md = m[:4]

        run: dict | None = None  # the text item being built

        def finish() -> None:
            nonlocal run
            if run and run["text"].strip():
                run["advances"].append(run["expected"])
                self._add(TextShape(run["text"], key, run["origin"], matrix, fill, run["glyphs"], run["advances"], self.layer))
            run = None

        for i in range(count):
            item = span.items(i)
            if item.gid < 0:
                continue  # a character with no glyph (part of a ligature)
            if item.ucs <= 0:
                self._note("characters with unknown meaning (left out)")
                finish()
                continue
            px = ma * item.x + mc * item.y + e
            py = mb * item.x + md * item.y + f
            expected = mupdf.fz_advance_glyph(font, item.gid, 0) * size_x
            if run is not None:
                dx, dy = px - run["last"][0], py - run["last"][1]
                along = dx * ux + dy * uy  # actual pen movement along the baseline
                across = dy * ux - dx * uy
                gap = along - run["expected"]
                if abs(across) > 0.05 * size_y or abs(gap) > 0.1 * size_x:
                    spaces = round(gap / space) if space > 0 and abs(across) <= 0.05 * size_y else 0
                    if spaces >= 1 and abs(gap - spaces * space) <= 0.3 * space and item.ucs != 32:
                        # Words placed by position instead of with spaces.
                        run["advances"].append(run["expected"])
                        for _ in range(spaces):
                            run["text"] += " "
                            run["glyphs"].append(space_gid if space_gid > 0 else 0)
                            run["advances"].append(gap / spaces)
                    else:
                        finish()
                else:
                    run["advances"].append(along)
            if run is None:
                run = {"text": "", "origin": (px, py), "glyphs": [], "advances": []}
            run["text"] += chr(item.ucs)
            run["glyphs"].append(item.gid)
            run["last"] = (px, py)
            run["expected"] = expected
        finish()

    def ignore_text(self, ctx: Any, text: Any, ctm: Any) -> None:
        pass  # invisible text (e.g. for searching); nothing to draw

    # -- images and gradients --

    def fill_image(self, ctx: Any, image: Any, ctm: Any, alpha: float, cp: Any) -> None:
        mask = image.mask
        if mask is not None and id(self.stack[-1]) in self.image_clips:
            entry = self.image_clips[id(self.stack[-1])]
            if entry[0] == _address(mask):
                entry[1] = True  # the clip is this picture's own mask
        data = encode_image(image, upscale_factor(image, ctm))
        self._place_image(self._store_image(data), ctm, alpha)

    def fill_image_mask(self, ctx: Any, image: Any, ctm: Any, cs: Any, color: Any, alpha: float, cp: Any) -> None:
        rgb = self.colors.rgb(cs, color)
        data = encode_stencil(image, rgb, upscale_factor(image, ctm))
        self._place_image(self._store_image(data), ctm, alpha)

    def fill_shade(self, ctx: Any, shade: Any, ctm: Any, alpha: float, cp: Any) -> None:
        """Gradients have no exact XPS equivalent in general, so we paint
        the gradient into a picture covering the area it fills."""
        sheet_area = pymupdf.Rect(0, 0, self.sheet.width_pt, self.sheet.height_pt)
        m = self._sheet_matrix(ctm)
        bounds = mupdf.fz_bound_shade(mupdf.FzShade(mupdf.ll_fz_keep_shade(shade)), mupdf.FzMatrix(*m))
        area = pymupdf.Rect(bounds.x0, bounds.y0, bounds.x1, bounds.y1) & sheet_area
        for group in self.stack:  # only the part inside all open clips is visible
            if id(group) in self.clip_boxes:
                area &= self.clip_boxes[id(group)]
        if area.is_empty:
            return
        k = SHADING_DPI / 72
        if area.width * area.height * k * k > MAX_SHADING_PIXELS:
            k = math.sqrt(MAX_SHADING_PIXELS / (area.width * area.height))
        w, h = max(1, math.ceil(area.width * k)), max(1, math.ceil(area.height * k))
        pix = mupdf.FzPixmap(mupdf.FzColorspace(mupdf.FzColorspace.Fixed_RGB), w, h, mupdf.FzSeparations(), 1)
        mupdf.fz_clear_pixmap(pix)
        to_pixels = concat(m, (k, 0, 0, k, -area.x0 * k, -area.y0 * k))
        mupdf.fz_paint_shade(
            mupdf.FzShade(mupdf.ll_fz_keep_shade(shade)),
            mupdf.FzColorspace(),
            mupdf.FzMatrix(*to_pixels),
            pix,
            mupdf.FzColorParams(),
            mupdf.FzIrect(0, 0, w, h),
            mupdf.FzOverprint(),
        )
        key = self._store_image(_png(pymupdf.Pixmap(pix, 1)))
        placement = (area.width, 0.0, 0.0, area.height, area.x0, area.y0)
        self._add(ImageShape(key, placement, alpha, self.layer))

    # -- transparency --

    def begin_group(self, ctx: Any, area: Any, cs: Any, isolated: int, knockout: int, blendmode: int, alpha: float) -> None:
        if blendmode != 0:  # XPS can only paint "normal" on top
            self._note("transparency blend modes")
        self._push(Group(opacity=alpha), "group")

    def end_group(self, ctx: Any) -> None:
        self._pop(("group",))

    def begin_mask(self, ctx: Any, area: Any, luminosity: int, cs: Any, bc: Any, cp: Any) -> None:
        # The mask's own drawing must not appear on the sheet: collect it
        # into a group that is never attached.
        self._note("soft masks (transparency masks)")
        self._push(Group(), "maskdef", attach=False)

    def end_mask(self, ctx: Any, *args: Any) -> None:
        self._pop(("maskdef",))
        # Content drawn through the mask follows, until the next pop_clip.
        # We draw it without the mask.
        self._push(Group(), "mask")

    # -- repeating patterns (e.g. hatches stored as PDF tiling patterns) --

    def begin_tile(self, ctx: Any, area: Any, view: Any, xstep: float, ystep: float, ctm: Any, tile_id: int, doc_id: int) -> int:
        self.tiles.append((area, view, xstep, ystep, _m(ctm)))
        self._push(Group(), "tile", attach=False)
        return 0  # 0 = "please send the tile content"

    def end_tile(self, ctx: Any) -> None:
        tile = self._pop(("tile",))
        area, view, xstep, ystep, ctm = self.tiles.pop()
        if tile is None or not tile.children or not xstep or not ystep:
            return
        offsets = _tile_offsets(area, view, xstep, ystep, ctm)
        if len(offsets) > MAX_PATTERN_TILES:
            self._note("pattern fills too dense to expand")
            offsets = offsets[:MAX_PATTERN_TILES]
        # Tile content arrives already positioned for the first tile, in
        # MuPDF page space; offsets are in page space too. Convert offsets
        # to sheet space (linear part of the page transform only).
        a, b, c, d = self.page_transform[:4]
        tile.offsets = [(a * dx + c * dy, b * dx + d * dy) for dx, dy in offsets]
        self._add(tile)

    # -- layers --

    def begin_layer(self, ctx: Any, name: str) -> None:
        self.layers.append(name or "")

    def end_layer(self, ctx: Any) -> None:
        if self.layers:
            self.layers.pop()


class LayerTree:
    """The PDF's own layer tree (as PDF viewers show it), used to name layers.

    CAD programs nest layers, and the same short name often appears under
    different parents ("LED" under both "KITCHEN" and "LIGHTING"). Design
    Review shows a flat list, so each layer is named by its full path in
    the tree (owner's choice).

    While drawing, MuPDF only tells us the names of the layers that enclose
    a drawing element, innermost last, e.g. ("01_VISUALARQ", "Slabs",
    "00_DOCUMENTATION", "HATCH", "White") for a hatch on layer White that
    belongs to a slab. An element can be on only one Design Review layer;
    we choose its own (innermost) layer: the tree path that matches the
    end of that list, here "00_DOCUMENTATION / HATCH / White".
    """

    def __init__(self, ui_configs: list[dict]) -> None:
        self.paths_by_name: dict[str, list[tuple[str, ...]]] = {}
        parents: list[str] = []
        for item in ui_configs:
            depth = item.get("depth", 0)
            parents = parents[:depth]
            path = (*parents, item["text"])
            if item.get("type") != "label":  # labels are headings, not layers
                self.paths_by_name.setdefault(item["text"], []).append(path)
            parents.append(item["text"])

    def resolve(self, enclosing: tuple[str, ...]) -> tuple[str, ...]:
        candidates = self.paths_by_name.get(enclosing[-1], [])
        # Best: a tree path that is exactly the end of the enclosing list.
        tails = [c for c in candidates if enclosing[-len(c) :] == c]
        if tails:
            return max(tails, key=len)
        # Next: a tree path whose layers all enclose the element, in order.
        for c in sorted(candidates, key=len, reverse=True):
            it = iter(enclosing)
            if all(name in it for name in c):
                return c
        if len(candidates) == 1:
            return candidates[0]
        return (enclosing[-1],)  # no tree in the PDF: the layer's own name


def _tile_offsets(area: Any, view: Any, xstep: float, ystep: float, ctm: Matrix) -> list[tuple[float, float]]:
    """Positions (in page space) of every tile copy needed to cover ``area``.

    MuPDF gives ``area`` (the region to fill) and ``view`` (one tile's
    extent) in pattern space; copies are spaced by xstep/ystep in pattern
    space, which ``ctm`` maps to page space."""
    a, b, c, d = ctm[:4]
    sx, sy = abs(xstep), abs(ystep)
    i0 = math.floor((area.x0 - view.x1) / sx)
    i1 = math.ceil((area.x1 - view.x0) / sx)
    j0 = math.floor((area.y0 - view.y1) / sy)
    j1 = math.ceil((area.y1 - view.y0) / sy)
    if (i1 - i0 + 1) * (j1 - j0 + 1) > 4 * MAX_PATTERN_TILES:
        i1 = i0 + min(i1 - i0, 2 * int(math.sqrt(MAX_PATTERN_TILES)))
        j1 = j0 + min(j1 - j0, 2 * int(math.sqrt(MAX_PATTERN_TILES)))
    offsets = []
    for j in range(j0, j1 + 1):
        for i in range(i0, i1 + 1):
            px, py = i * sx, j * sy
            offsets.append((a * px + c * py, b * px + d * py))
    return offsets


def read_page(page: pymupdf.Page, title: str | None = None) -> Sheet:
    """Record everything MuPDF draws for this page (as a PDF viewer shows it)."""
    bounds = mupdf.fz_bound_page(page.this)
    sheet = Sheet(
        title=title or page.get_label() or f"Page {page.number + 1}",
        width_pt=bounds.x1 - bounds.x0,
        height_pt=bounds.y1 - bounds.y0,
    )
    # MuPDF's page space already has the page rotation applied and y going
    # down; we only move the origin to the sheet's top-left corner.
    page_transform: Matrix = (1.0, 0.0, 0.0, 1.0, -bounds.x0, -bounds.y0)
    device = _Recorder(sheet, page_transform, LayerTree(page.parent.layer_ui_configs()))
    try:
        mupdf.fz_run_page(page.this, device, mupdf.FzMatrix(), mupdf.FzCookie())
    finally:
        mupdf.fz_close_device(device)
    # Layers switched off in the PDF are not drawn (as in a PDF viewer), so
    # they cannot be switched on in Design Review. Say so instead of
    # silently leaving them out.
    hidden = sum(1 for ocg in page.parent.get_ocgs().values() if not ocg["on"])
    if hidden:
        sheet.unsupported["layers switched off in the PDF (left out)"] += hidden
    return sheet
