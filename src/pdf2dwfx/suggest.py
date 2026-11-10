"""Suggest each page's drawing scale(s) from what the PDF contains.

Two sources, best first:

1. **Scale data stored in the PDF** ("measurement viewports", /VP with
   /Measure): some CAD programs save each viewport's area and scale, the
   data Acrobat's measuring tool uses. When present it is exact.
2. **Scale texts on the page**, e.g. "SCALE: 1 : 100" in the title block
   or "FLOOR PLAN 1 | 1:100" under a drawing. The title-block scale
   (recognised by a word like SCALE next to it) becomes the sheet
   scale. A drawing titled with a different scale (a 1:5 detail on a 1:50
   sheet) gets a viewport around that drawing, found by grouping nearby
   shapes and giving each group to the nearest drawing title.

Everything is a *suggestion* for the user to check (scale.SUGGESTED);
notes explain anything doubtful, e.g. a title block that disagrees with
the drawing titles. Nothing here depends on the paper size: positions are
only compared with each other, and sizes relative to the page.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

import pymupdf
from pymupdf import mupdf

from .scale import (
    CONFIRMED, IMPERIAL, NO_SCALE, SUGGESTED, PageScale, Viewport, format_scale, imperial_scale,
)

PT_PER_MM = 72 / 25.4
METRES_PER_PT = 0.0254 / 72

# Words that announce a scale in a title block, for the ten most spoken
# languages (English, Mandarin, Hindi, Spanish, Arabic, French, Bengali,
# Portuguese, Russian, Urdu) plus Italian and German.
# Written in full only where a word has one form; the others are stems,
# because Hindi, Russian and Urdu change the ending of a word.
_WORDS = ("比例", "पैमान", "स्केल", "স্কেল", "মাপনী", "масштаб")
_RIGHT_TO_LEFT_WORDS = ("مقياس", "پيمان", "اسكيل")  # Arabic, Urdu (spelled as _SAME_LETTERS leaves them)
_KEYWORD_PATTERN = (
    r"(?<![a-z])(?:esc(?:ala)?s?|scale|[ée]ch(?:elle)?s?|scala|ma(?:ss|ß)stab)(?![a-z])"
    r"|(?<!\w)(?-i:М)(?=\.?\s*(?:1\s*:|$))"  # Russian shorthand "М 1:100" (capital only: "м" is metres)
    # Some PDFs store right-to-left text backwards, so look for that too.
    + "".join(f"|{w}" for w in (*_WORDS, *_RIGHT_TO_LEFT_WORDS, *(w[::-1] for w in _RIGHT_TO_LEFT_WORDS)))
)
_KEYWORD = re.compile(_KEYWORD_PATTERN, re.IGNORECASE)
_KEYWORD_THEN_NUMBER = re.compile(rf"(?:{_KEYWORD_PATTERN})[:.]?(?=\d)", re.IGNORECASE)
# Wide Chinese punctuation ("1：100") and Arabic letters stored in their
# joined-up shapes: the plain characters are what the patterns look for.
# Arabic and Urdu use different characters for these same letters, and which
# one a PDF stores depends on the font.
_SAME_LETTERS = str.maketrans("یىکہ", "ييكه")
_SPECIAL_FORMS = re.compile("[\uFB50-\uFDFF\uFE70-\uFEFE\uFF01-\uFF5E]+")
# "1:100", "1 : 50", "1/50", "1_50", "1-50", "1 to 50", "2:1", "1:2,5" ...
# Not part of a longer chain of numbers ("1-12-2024", "PR_1_50.dwg").
_RATIO = re.compile(
    r"(?<![\w.,/:\-–])(\d+(?:[.,]\d+)?)\s*([:/_\-–]|\bto\b)\s*(\d+(?:[.,]\d+)?)"
    r"(?![\w,]|[.:/_\-–]\d)",
    re.IGNORECASE,
)
# Scales architects and engineers actually use (the number after "1:").
_USUAL = {1, 2, 2.5, 5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 750, 1000,
          1250, 1500, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 0.5, 0.2, 0.1}

# Real-world length of one unit name, in metres (PDF measure dictionaries).
_UNIT_METRES = {
    "m": 1.0, "metre": 1.0, "meter": 1.0, "metres": 1.0, "meters": 1.0, "metro": 1.0, "metros": 1.0,
    "mm": 0.001, "cm": 0.01, "dm": 0.1, "km": 1000.0,
    "in": 0.0254, "inch": 0.0254, "inches": 0.0254, '"': 0.0254,
    "ft": 0.3048, "foot": 0.3048, "feet": 0.3048, "'": 0.3048,
    "yd": 0.9144, "mi": 1609.344,
}


@dataclass
class ScaleLabel:
    """A scale written on the page."""

    scale: float
    box: tuple[float, float, float, float]  # on the sheet, points from top-left
    size: float  # font size in points
    text: str
    title_block: bool  # announced by a word like SCALE: the sheet's scale


def nice_scale(value: float) -> float:
    """Remove tiny rounding errors: 99.9998 -> 100."""
    nearest = round(value)
    if nearest and abs(value - nearest) / value < 0.002:
        return float(nearest)
    return round(value, 4)


def _turned(page: pymupdf.Page, box) -> tuple[float, float, float, float]:
    """A box from PyMuPDF's text and shape lists -> on the sheet.

    Those lists give positions on the *unrotated* page; turn them like the
    page so they match the sheet as the user sees it.
    """
    r = pymupdf.Rect(box) * page.rotation_matrix
    r.normalize()
    o = page.rect
    return (r.x0 - o.x0, r.y0 - o.y0, r.x1 - o.x0, r.y1 - o.y0)


# --- 1. Scale data stored in the PDF -------------------------------------------


def _sheet_box(page: pymupdf.Page, pdf_box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """A box in the PDF's own coordinates -> on the sheet as the user sees it.

    Uses MuPDF's own page transform (the one used to draw the page), which
    handles page rotation and a crop box that does not start at the PDF's
    origin. (PyMuPDF's transformation_matrix/rotation_matrix pair gets the
    crop box offset wrong on rotated pages: found by the tests.)
    """
    mediabox, ctm = mupdf.FzRect(), mupdf.FzMatrix()
    mupdf.pdf_page_transform(mupdf.pdf_page_from_fz_page(page.this), mediabox, ctm)
    r = mupdf.fz_transform_rect(mupdf.FzRect(*pdf_box), ctm)
    o = mupdf.fz_bound_page(page.this)
    return (r.x0 - o.x0, r.y0 - o.y0, r.x1 - o.x0, r.y1 - o.y0)


def _measure_scale(measure: mupdf.PdfObj) -> float | None:
    """The scale (number after "1:") described by a PDF /Measure dictionary."""
    if not measure.pdf_is_dict() or measure.pdf_dict_gets("Subtype").pdf_to_name() not in ("RL", ""):
        return None
    x = measure.pdf_dict_gets("X")  # number formats: points -> real units
    if x.pdf_is_array() and x.pdf_array_len() > 0:
        first = x.pdf_array_get(0)
        factor = first.pdf_dict_gets("C").pdf_to_real()
        unit = first.pdf_dict_gets("U").pdf_to_text_string().strip().lower()
        if factor > 0 and unit in _UNIT_METRES:
            return nice_scale(factor * _UNIT_METRES[unit] / METRES_PER_PT)
    # Otherwise the human-readable ratio, e.g. "1 mm = 100 mm" or "1 in = 10 ft".
    ratio = measure.pdf_dict_gets("R").pdf_to_text_string()
    m = re.match(r"\s*([\d.]+)\s*(\S+)\s*=\s*([\d.]+)\s*(\S+)", ratio)
    if m and m.group(2).lower() in _UNIT_METRES and m.group(4).lower() in _UNIT_METRES:
        paper = float(m.group(1)) * _UNIT_METRES[m.group(2).lower()]
        real = float(m.group(3)) * _UNIT_METRES[m.group(4).lower()]
        if paper > 0 and real > 0:
            return nice_scale(real / paper)
    return None


def pdf_viewports(page: pymupdf.Page) -> list[Viewport]:
    """Measurement viewports stored in the PDF page (/VP), if any."""
    try:
        obj = mupdf.pdf_page_from_fz_page(page.this).obj()
    except Exception:  # not a PDF page (e.g. XPS input)
        return []
    vps = obj.pdf_dict_gets("VP")
    if not vps.pdf_is_array():
        return []
    found = []
    for i in range(vps.pdf_array_len()):
        vp = vps.pdf_array_get(i)
        if not vp.pdf_is_dict():
            continue
        scale = _measure_scale(vp.pdf_dict_gets("Measure"))
        bbox = vp.pdf_dict_gets("BBox")
        if scale is None or not bbox.pdf_is_array() or bbox.pdf_array_len() != 4:
            continue
        box = _sheet_box(page, tuple(bbox.pdf_array_get(j).pdf_to_real() for j in range(4)))  # type: ignore[arg-type]
        name = vp.pdf_dict_gets("Name").pdf_to_text_string()
        found.append(Viewport(box, scale, name))
    return found


# --- 2. Scale texts on the page -------------------------------------------------


def _written_scales(text: str) -> list[tuple[int, float, str]]:
    """Everything in a line of text that looks like a scale:
    (position, the number after "1:", separator). Imperial scales such as
    1/4" = 1'-0" have separator "="; their "1/4" is not also a 1:4 scale."""
    imperial = list(IMPERIAL.finditer(text))
    found = [(m.start(), imperial_scale(m), "=") for m in imperial]
    for m in _RATIO.finditer(text):
        if any(i.start() <= m.start() < i.end() for i in imperial):
            continue
        a, b = (float(g.replace(",", ".")) for g in (m.group(1), m.group(3)))
        # One side must be 1: not "21 / 28" (sheet 21 of 28) or "12:30".
        if a > 0 and b > 0 and (a == 1 or b == 1):
            found.append((m.start(), b / a, m.group(2).lower()))
    return [(start, scale, sep) for start, scale, sep in found if scale]


def _plain_text(text: str) -> str:
    """A line of the drawing in the plain form the patterns can read."""
    text = unicodedata.normalize("NFC", text)
    text = _SPECIAL_FORMS.sub(lambda m: unicodedata.normalize("NFKC", m.group()), text).translate(_SAME_LETTERS)
    # No space after the word ("比例1:100", "SCALE:1:100"): a number needs a gap before it.
    return _KEYWORD_THEN_NUMBER.sub(lambda m: m.group() + " ", text)


def _believable(scale: float, sep: str, announced: bool) -> bool:
    """Is this really a scale? Next to a word like SCALE, or written the
    imperial way, always. Otherwise only usual scales ("1:7" rarely is
    one); "1/2" is more often a fraction and "1-2" or "1_2" a range or a
    number, so "/" needs the word, and "-", "_", "to" a scale of 1:5 or less."""
    if announced or sep == "=":
        return True
    if scale not in _USUAL:
        return False
    return sep == ":" or (sep != "/" and scale >= 5)


def scale_labels(page: pymupdf.Page) -> list[ScaleLabel]:
    """All scales written on the page, without duplicates and tiny print."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if line["spans"]:
                text = "".join(s["text"] for s in line["spans"])
                size = max(s["size"] for s in line["spans"])
                lines.append((_plain_text(text), _turned(page, line["bbox"]), size))
    # Lines that are only an announcing word, e.g. "SCALE:" in a title
    # block cell, with the value in the line below or beside it.
    keyword_lines = [box for text, box, _ in lines if _KEYWORD.search(text) and not _written_scales(text)]
    found: dict[tuple, ScaleLabel] = {}
    for text, box, size in lines:
        for start, scale, sep in _written_scales(text):
            announced = bool(_KEYWORD.search(text[:start])) or any(_near_keyword(box, k) for k in keyword_lines)
            if _believable(scale, sep, announced):
                key = (round(box[0]), round(box[1]), scale)
                found.setdefault(key, ScaleLabel(nice_scale(scale), box, size, text.strip(), announced))
    labels = list(found.values())
    # Tiny print (e.g. 1 mm high view names drawn by the CAD program next
    # to each view) is not meant to be read; real titles are much larger.
    if labels:
        biggest = max(label.size for label in labels)
        labels = [label for label in labels if label.size >= 0.45 * biggest]
    return labels


def _near_keyword(box, keyword_box) -> bool:
    """Is the announcing word just above or just left of this text?"""
    height = max(box[3] - box[1], 1.0)
    kx0, ky0, kx1, ky1 = keyword_box
    # (lines may touch or overlap slightly: allow a quarter line of overlap)
    above = -0.25 * height <= box[1] - ky1 <= 3 * height and kx0 < box[2] + height and kx1 > box[0] - height
    left = abs((ky0 + ky1) / 2 - (box[1] + box[3]) / 2) <= height and -0.25 * height <= box[0] - kx1 <= 4 * height
    return above or left


# --- 3. Where each drawing is ---------------------------------------------------------


def _groups(page: pymupdf.Page, cell: float) -> list[tuple[float, float, float, float]]:
    """Boxes of groups of shapes that lie close together (a drawing each,
    roughly). Works on a coarse grid of ``cell`` points: every shape marks
    the cells it covers plus a one-cell margin, and touching cells form a
    group. Shapes as big as half the page (sheet frames) are left out:
    they would join everything into one group."""
    width, height = page.rect.width, page.rect.height
    cols, rows = int(width / cell) + 2, int(height / cell) + 2
    marked = bytearray(cols * rows)
    for _, box in page.get_bboxlog():
        x0, y0, x1, y1 = _turned(page, box)
        if x1 - x0 > width / 2 or y1 - y0 > height / 2:
            continue
        c0 = max(int(x0 / cell) - 1, 0)
        c1 = min(int(x1 / cell) + 1, cols - 1)
        r0 = max(int(y0 / cell) - 1, 0)
        r1 = min(int(y1 / cell) + 1, rows - 1)
        for r in range(r0, r1 + 1):
            marked[r * cols + c0 : r * cols + c1 + 1] = b"\x01" * (c1 - c0 + 1)
    boxes = []
    seen = bytearray(cols * rows)
    for start in range(cols * rows):
        if not marked[start] or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        c_min = r_min = 1 << 30
        c_max = r_max = -1
        while stack:
            i = stack.pop()
            r, c = divmod(i, cols)
            c_min, c_max, r_min, r_max = min(c_min, c), max(c_max, c), min(r_min, r), max(r_max, r)
            for j in (i - 1 if c else -1, i + 1 if c < cols - 1 else -1, i - cols, i + cols):
                if 0 <= j < cols * rows and marked[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        # remove the one-cell margin again
        boxes.append(((c_min + 1) * cell, (r_min + 1) * cell, c_max * cell, r_max * cell))
    return boxes


class _ClipFinder(mupdf.FzDevice2):
    """Runs through a page and notes every clipping area with how many
    shapes are drawn inside it. CAD programs clip each viewport of a
    layout to its outline, so these areas are usually exactly the drawings."""

    def __init__(self, origin: pymupdf.Rect) -> None:
        super().__init__()
        for name in (
            "fill_path", "stroke_path", "clip_path", "clip_stroke_path", "fill_text", "stroke_text",
            "clip_text", "clip_stroke_text", "fill_shade", "fill_image", "fill_image_mask",
            "clip_image_mask", "pop_clip", "begin_mask",
        ):  # fmt: skip
            getattr(self, "use_virtual_" + name)()
        self.origin = origin
        self.stack: list[list | None] = []  # open clips: [box, shapes drawn] or None
        self.found: list[tuple[tuple[float, float, float, float], int]] = []

    def _drawn(self, *args) -> None:
        for entry in self.stack:
            if entry is not None:
                entry[1] += 1

    fill_path = stroke_path = fill_text = stroke_text = fill_shade = fill_image = fill_image_mask = _drawn

    def clip_path(self, ctx, path, even_odd, ctm, scissor) -> None:
        r = mupdf.fz_bound_path(mupdf.FzPath(mupdf.ll_fz_keep_path(path)), mupdf.FzStrokeState(None), mupdf.FzMatrix(ctm))
        o = self.origin
        self.stack.append([(r.x0 - o.x0, r.y0 - o.y0, r.x1 - o.x0, r.y1 - o.y0), 0])

    def _other_clip(self, *args) -> None:
        self.stack.append(None)

    clip_stroke_path = clip_text = clip_stroke_text = clip_image_mask = begin_mask = _other_clip

    def pop_clip(self, ctx) -> None:
        if self.stack:
            entry = self.stack.pop()
            if entry is not None:
                self.found.append((entry[0], entry[1]))


def clip_areas(page: pymupdf.Page) -> list[tuple[float, float, float, float]]:
    """Clipping areas that look like drawing viewports: holding a fair
    number of shapes, neither tiny nor the whole sheet, and not inside
    another such area (hatch outlines inside a drawing are clips too)."""
    finder = _ClipFinder(page.rect)
    try:
        mupdf.fz_run_page(page.this, finder, mupdf.FzMatrix(), mupdf.FzCookie())
    finally:
        mupdf.fz_close_device(finder)
    page_area = page.rect.width * page.rect.height
    candidates = []
    for box, shapes in finder.found:
        area = (box[2] - box[0]) * (box[3] - box[1])
        if shapes >= 10 and 0.005 * page_area <= area <= 0.8 * page_area:
            candidates.append(box)
    candidates = list(dict.fromkeys(candidates))  # same box clipped twice

    def inside(a, b) -> bool:
        return a is not b and b[0] <= a[0] + 1 and b[1] <= a[1] + 1 and a[2] <= b[2] + 1 and a[3] <= b[3] + 1

    return [a for a in candidates if not any(inside(a, b) for b in candidates)]


def _distance(group, label: ScaleLabel) -> tuple[int, float]:
    """How well a title matches an area: (kind, distance), smaller is better.

    Drawing titles are usually written under the drawing, so a title just
    below the area and overlapping it sideways (kind 0) always beats any
    other title (kind 1), even a nearer one above: the area right under a
    drawing's title belongs to the drawing below it, not to that title.
    """
    gx0, gy0, gx1, gy1 = group
    lx0, ly0, lx1, ly1 = label.box
    dx = max(gx0 - lx1, lx0 - gx1, 0.0)
    dy = max(gy0 - ly1, ly0 - gy1, 0.0)
    below = ly0 >= (gy0 + gy1) / 2
    if below and dx == 0:
        return 0, dy
    return 1, (dx * dx + dy * dy) ** 0.5


def drawing_areas(
    page: pymupdf.Page, labels: list[ScaleLabel]
) -> tuple[list[tuple[float, float, float, float] | None], int]:
    """For each label, the area of the drawing it is the title of (or None),
    and how many drawing areas have no scale title at all.

    An area belongs to the nearest title just below it. Only a title that
    touches the area (within ``touch``) may be elsewhere: a drawing without
    its own scale title must not be swept into a neighbour's viewport (seen
    in practice: construction details under a 1:50 facade title).
    """
    reach = 0.35 * min(page.rect.width, page.rect.height)  # ignore far-away titles
    touch = 3 * PT_PER_MM
    areas: list[list[float] | None] = [None] * len(labels)
    regions = clip_areas(page)
    if len(regions) < 2:
        # No viewport clipping in this PDF: group nearby shapes instead.
        regions = _groups(page, 2 * PT_PER_MM)
    untitled = 0
    for group in regions:
        distances = [_distance(group, label) for label in labels]
        within = [
            i for i in range(len(labels))
            if (distances[i][0] == 0 and distances[i][1] <= reach) or distances[i][1] <= touch
        ]
        if not within:
            untitled += 1
            continue
        best = min(within, key=distances.__getitem__)
        area = areas[best]
        if area is None:
            x0, y0, x1, y1 = labels[best].box
            area = areas[best] = [x0, y0, x1, y1]
        area[0], area[1] = min(area[0], group[0]), min(area[1], group[1])
        area[2], area[3] = max(area[2], group[2]), max(area[3], group[3])
    margin = 2 * PT_PER_MM
    boxes = [
        None if a is None else (a[0] - margin, a[1] - margin, a[2] + margin, a[3] + margin)
        for a in areas
    ]
    return boxes, untitled


# --- Putting it together ------------------------------------------------------------------


def suggest(page: pymupdf.Page) -> PageScale:
    """The suggested scale(s) of one page."""
    vps = pdf_viewports(page)
    if vps:
        return _from_pdf_data(page, vps)
    labels = scale_labels(page)
    if not labels:
        return PageScale(status=NO_SCALE, notes=["no scale found on this page"])
    titles = [label for label in labels if label.title_block]
    drawings = [label for label in labels if not label.title_block]
    notes: list[str] = []
    title_scales = Counter(label.scale for label in titles)
    drawing_scales = Counter(label.scale for label in drawings)
    if len(title_scales) > 1:
        notes.append("the title block shows several scales: " + _list(title_scales))
    title_scale = title_scales.most_common(1)[0][0] if title_scales else None
    if title_scale is not None and (not drawing_scales or title_scale in drawing_scales):
        sheet_scale = title_scale
    elif drawing_scales:
        # Use the scale most drawings are titled with.
        sheet_scale = drawing_scales.most_common(1)[0][0]
        if title_scale is not None:
            notes.append(
                f"the title block says 1:{format_scale(title_scale)} but the drawings say "
                + _list(drawing_scales)
            )
    else:
        sheet_scale = title_scale
    viewports = []
    others = [label for label in drawings if label.scale != sheet_scale]
    if others:
        areas, untitled = drawing_areas(page, labels)
        if untitled:
            notes.append(
                f"{untitled} drawing area(s) without a scale title: they measure at the sheet scale, please check"
            )
        for label, area in zip(labels, areas):
            if label not in others:
                continue
            if area is None:
                notes.append(f"could not find the drawing titled {label.text!r}: please mark its area")
                continue
            viewports.append(Viewport(area, label.scale, label.text))
    found = sorted({label.scale for label in labels}, reverse=True)
    return _with_warning(PageScale(sheet_scale, viewports, SUGGESTED, "text", notes, found))


def _with_warning(result: PageScale) -> PageScale:
    """Several scale values on one page: say so first, so the user sets the
    page up (sheet scale and viewports) and confirms it."""
    if result.several_scales:
        values = ", ".join(f"1:{format_scale(v)}" for v in result.found)
        result.notes.insert(0, f"several scales on this page ({values}): please check and set up")
    return result


def _from_pdf_data(page: pymupdf.Page, vps: list[Viewport]) -> PageScale:
    """Scale data stored in the PDF: a viewport covering (almost) the whole
    sheet becomes the sheet scale, the others stay viewports."""
    area = page.rect.width * page.rect.height
    big = [v for v in vps if (v.box[2] - v.box[0]) * (v.box[3] - v.box[1]) >= 0.8 * area]
    sheet_scale = big[0].scale if len(big) == 1 else None
    rest = [v for v in vps if not (len(big) == 1 and v is big[0])]
    found = sorted({v.scale for v in vps}, reverse=True)
    return _with_warning(PageScale(sheet_scale, rest, SUGGESTED, "pdf", [], found))


def _list(scales: Counter) -> str:
    return ", ".join(f"1:{format_scale(s)}" for s, _ in scales.most_common())


def scale_plan(
    doc: pymupdf.Document,
    pages: list[int],
    sheet_scales: dict[int, float | None] | None = None,
    viewports: dict[int, list[Viewport]] | None = None,
    use_suggestions: bool = True,
) -> dict[int, PageScale]:
    """The scale of each page (numbers count from 1): what the user set,
    otherwise the suggestion.

    ``sheet_scales``: whole-sheet scales set by the user (None = paper);
    the user chose one scale for the sheet, so suggested viewports go.
    ``viewports``: viewports drawn by the user; they replace suggested ones.
    """
    sheet_scales = sheet_scales or {}
    viewports = viewports or {}
    plan: dict[int, PageScale] = {}
    for n in pages:
        guess = suggest(doc[n - 1]) if use_suggestions else PageScale()
        if n in sheet_scales or n in viewports:
            scale = sheet_scales[n] if n in sheet_scales else guess.scale
            vps = viewports.get(n, [] if n in sheet_scales else guess.viewports)
            plan[n] = PageScale(scale, list(vps), CONFIRMED, "user", [])
        else:
            plan[n] = guess
    return plan
