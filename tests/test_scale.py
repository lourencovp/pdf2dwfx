"""Drawing scales: suggestions from the PDF, and true real-world sizes in
the DWFx on every paper size, page rotation and crop box."""

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf
import pytest

from pdf2dwfx.convert import convert
from pdf2dwfx.scale import CONFIRMED, NO_SCALE, SUGGESTED, PageScale, Viewport, parse_scale
from pdf2dwfx.suggest import scale_labels, suggest
from pdf2dwfx.w2x import units_transform
from synthetic import MM, PAPER_SIZES, scale_pdf, scale_sheet_layout, text_pdf

NS = "{http://schemas.microsoft.com/xps/2005/06}"


@pytest.mark.parametrize(
    "text, scale",
    [("1:100", 100), ("1 : 50", 50), ("1/20", 20), ("200", 200), ("2:1", 0.5), ("1:2,5", 2.5),
     ("paper", None), ("0", None)],
)
def test_parse_scale(text: str, scale: float | None) -> None:
    assert parse_scale(text) == scale


def test_parse_scale_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        parse_scale("1:0")
    with pytest.raises(ValueError):
        parse_scale("abc")


def test_units_do_not_depend_on_paper() -> None:
    """1 m at 1:100 is 10 mm of paper = 10 000 grid units (1 unit = 1 um)."""
    assert units_transform(100).startswith("10000,0,0,0,0,10000,")
    assert units_transform(5).startswith("200000,0,0,0,0,200000,")
    assert units_transform(None).startswith("1000,0,0,0,0,1000,")  # paper mm


def _inside(box, outer, tol=0.5) -> bool:
    return (outer[0] - tol <= box[0] and outer[1] - tol <= box[1]
            and box[2] <= outer[2] + tol and box[3] <= outer[3] + tol)


def _pt(box_mm):
    return tuple(v * MM for v in box_mm)


CASES = [(name, size, 0, False) for name, size in PAPER_SIZES.items()] + [
    ("A3 rotated 90", (420, 297), 90, False),
    ("A3 rotated 270", (420, 297), 270, False),
    ("A1 rotated 180, offset crop box", (841, 594), 180, True),
    ("A4 offset crop box", (210, 297), 0, True),
]


@pytest.mark.parametrize("name, paper, rotate, crop", CASES, ids=[c[0] for c in CASES])
def test_suggestion_on_any_sheet(tmp_path: Path, name: str, paper, rotate: int, crop: bool) -> None:
    pdf = scale_pdf(tmp_path / "s.pdf", paper, rotate=rotate, crop_offset=crop)
    page = pymupdf.open(pdf)[0]
    assert round(page.rect.width / MM) == round(paper[0])  # the sheet as the user sees it
    s = suggest(page)
    assert s.status == SUGGESTED and s.source == "text"
    assert s.scale == 50  # from the title block, agreeing with the plan title
    assert [v.scale for v in s.viewports] == [5]  # the 1:5 detail
    layout = scale_sheet_layout(paper)
    vp = s.viewports[0].box
    assert _inside(_pt(layout["detail"]), vp)
    assert not _inside(_pt(layout["plan"]), vp)
    # the untitled drawing is not swept into the 1:5 viewport, and the user is told
    assert not _inside(_pt(layout["untitled"]), vp)
    assert any("without a scale title" in n for n in s.notes)


@pytest.mark.parametrize("paper, rotate", [((420, 297), 0), ((841, 594), 90), ((210, 297), 270)])
def test_drawings_found_without_viewport_clipping(tmp_path: Path, paper, rotate: int) -> None:
    """Without clipped viewports in the PDF, nearby shapes are grouped
    into drawings instead."""
    pdf = scale_pdf(tmp_path / "s.pdf", paper, rotate=rotate, clipped=False)
    s = suggest(pymupdf.open(pdf)[0])
    assert s.scale == 50 and [v.scale for v in s.viewports] == [5]
    layout = scale_sheet_layout(paper)
    vp = s.viewports[0].box
    assert _inside(_pt(layout["detail"]), vp)
    assert not _inside(_pt(layout["plan"]), vp) and not _inside(_pt(layout["untitled"]), vp)


@pytest.mark.parametrize("rotate, crop", [(0, False), (90, False), (270, True)])
def test_scale_data_stored_in_pdf_wins(tmp_path: Path, rotate: int, crop: bool) -> None:
    pdf = scale_pdf(tmp_path / "s.pdf", (420, 297), rotate=rotate, crop_offset=crop,
                    measure_data=True, title_block_scale="1 : 200")
    s = suggest(pymupdf.open(pdf)[0])
    assert s.source == "pdf"
    assert sorted(v.scale for v in s.viewports) == [5, 50]
    layout = scale_sheet_layout((420, 297))
    boxes = {v.label: v.box for v in s.viewports}
    for name in ("plan", "detail"):
        expected = _pt(layout[name])
        assert all(abs(a - b) < 0.5 for a, b in zip(boxes[name], expected)), name


def test_scale_texts() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    font = pymupdf.Font("cjk")
    page.insert_font(fontname="F1", fontbuffer=font.buffer)
    texts = [
        ((20, 40), "FOLHA 21 / 28", 12),  # sheet number, not a scale
        ((20, 80), "parede 1/2 tijolo", 12),  # a fraction
        ((20, 120), "ESC. 1/20", 12),  # announced: "/" allowed
        ((20, 160), "CORTE AA' 1:50", 12),
        ((20, 200), "nota 1:7", 12),  # unusual scale, not announced
        ((20, 240), "VISTA 1:100", 3),  # tiny print
        ((20, 280), "ESCALA", 6),
        ((20, 292), "1 : 100", 10),  # value under the word ESCALA
    ]
    for pos, text, size in texts:
        page.insert_text(pos, text, fontname="F1", fontsize=size)
    found = {(label.scale, label.title_block) for label in scale_labels(page)}
    assert found == {(20, True), (50, False), (100, True)}


def test_title_block_disagreeing_with_drawings(tmp_path: Path) -> None:
    pdf = scale_pdf(tmp_path / "s.pdf", title_block_scale="1 : 100")
    s = suggest(pymupdf.open(pdf)[0])
    assert s.scale == 50  # the plan says 1:50: drawings are what gets measured
    assert any("title block says 1:100" in n for n in s.notes)


def test_page_without_scale(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page()
    s = suggest(doc[0])
    assert s.status == NO_SCALE and s.scale is None and not s.viewports


# --- the DWFx carries true real-world sizes -----------------------------------


def _w2x_and_page(dwfx: Path):
    z = zipfile.ZipFile(dwfx)
    w2x = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith("W2X.xml"))))
    page = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".fpage"))))
    return w2x, page


def _per_unit(transform: str) -> float:
    return float(transform.split(",")[0])


def _paths_box(elements) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for el in elements:
        if el.tag == NS + "Path":
            nums = [int(v) for v in re.findall(r"-?\d+", el.get("Data"))]
            xs += nums[0::2]
            ys += nums[1::2]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.mark.parametrize("name, paper, rotate, crop", CASES, ids=[c[0] for c in CASES])
def test_measured_sizes_are_true(tmp_path: Path, name: str, paper, rotate: int, crop: bool) -> None:
    """What Design Review's measure tool computes: grid distance divided by
    the area's grid units per metre. Must give the real size of each
    drawing whatever the paper size, rotation or crop box."""
    pdf = scale_pdf(tmp_path / "s.pdf", paper, rotate=rotate, crop_offset=crop)
    out = tmp_path / "s.dwfx"
    convert(pdf, out, scales=suggest)
    w2x, page = _w2x_and_page(out)
    layout = scale_sheet_layout(paper)
    # Sheet scale 1:50 for the plan: real width = paper width x 50.
    sheet_per_metre = _per_unit(w2x.find("Units").get("Transform"))
    assert w2x.find("Units").get("Label") == "m"
    content = page.find(NS + "Canvas")[0]
    # The 1:5 detail: drawn in several runs (outline first, clipped lines
    # later in the PDF), so possibly several canvases, all with its scale.
    canvases = [el for el in content if el.tag == NS + "Canvas"]
    assert canvases
    outside = [el for el in content if el.tag == NS + "Path"]
    plan_mm = layout["plan"][2] - layout["plan"][0]
    plan_paths = [el for el in outside if _paths_box([el])[0] < (layout["plan"][2] - 1) * 1000]
    x0, _, x1, _ = _paths_box(plan_paths)
    assert abs((x1 - x0) / sheet_per_metre - plan_mm * 50 / 1000) < 0.001  # metres
    # Viewport scale 1:5 for the detail.
    syncs = {s.get("refName"): s for s in w2x.iter("RenditionSync")}
    viewports = [syncs[c.get("Name")].find("Viewport") for c in canvases]
    assert {v.get("Label") for v in viewports} == {"m"}
    assert len({v.get("Transform") for v in viewports}) == 1
    detail_per_metre = _per_unit(viewports[0].get("Transform"))
    x0, _, x1, _ = _paths_box([el for c in canvases for el in c])
    detail_mm = layout["detail"][2] - layout["detail"][0]
    assert abs((x1 - x0) / detail_per_metre - detail_mm * 5 / 1000) < 0.0005


def test_confirmed_scale_overrides(tmp_path: Path) -> None:
    pdf = text_pdf(tmp_path / "t.pdf")
    out = tmp_path / "t.dwfx"
    convert(pdf, out, scales={1: PageScale(scale=20, status=CONFIRMED, source="user")})
    w2x, page = _w2x_and_page(out)
    assert _per_unit(w2x.find("Units").get("Transform")) == 50000  # 1 m = 50 mm at 1:20


def test_without_scale_the_sheet_measures_paper_mm(tmp_path: Path) -> None:
    pdf = text_pdf(tmp_path / "t.pdf")
    out = tmp_path / "t.dwfx"
    convert(pdf, out)
    w2x, _ = _w2x_and_page(out)
    units = w2x.find("Units")
    assert units.get("Label") == "mm" and _per_unit(units.get("Transform")) == 1000


def test_drawing_order_kept_across_viewports(tmp_path: Path) -> None:
    """Shapes keep their PDF drawing order even when viewports interleave:
    canvases close and reopen, with the same numbering in page and W2X."""
    pdf = scale_pdf(tmp_path / "s.pdf")
    layout = scale_sheet_layout((420, 297))
    vp = Viewport(_pt(layout["detail"]), 5, "detail")
    out = tmp_path / "s.dwfx"
    convert(pdf, out, scales={1: PageScale(scale=50, viewports=[vp], status=CONFIRMED)})
    w2x, page = _w2x_and_page(out)
    names = []
    for el in page.iter():
        if el.get("Name", "").startswith("N_"):
            names.append(int(el.get("Name")[2:]))
    assert names == list(range(1, len(names) + 1))  # one running sequence
    canvas_names = {el.get("Name") for el in page.iter(NS + "Canvas") if el.get("Clip")}
    viewport_refs = {s.get("refName") for s in w2x.iter("RenditionSync") if s.find("Viewport") is not None and s.find("Viewport").get("Name")}
    assert canvas_names == viewport_refs and len(canvas_names) >= 2  # detail drawn in two runs


def test_private_sample_scales(private_tests_dir: Path) -> None:
    """Suggestions on the owner's real drawing set (only numbers here: no
    drawing content goes into the public repository)."""
    pdfs = sorted((private_tests_dir / "pdf").glob("*28*.pdf"))
    if not pdfs:
        pytest.skip("28-page sample not available")
    doc = pymupdf.open(pdfs[0])
    expected = {
        1: (None, []), 4: (200, []), 7: (100, []), 15: (50, []),
        17: (50, [5]), 18: (50, [5]), 21: (50, []), 23: (100, [50, 50]), 26: (100, []),
    }
    for n, (scale, viewports) in expected.items():
        s = suggest(doc[n - 1])
        assert (s.scale, sorted(v.scale for v in s.viewports)) == (scale, viewports), n
    assert suggest(doc[20]).notes  # title block disagrees with the drawings



@pytest.mark.parametrize(
    "text, scale",
    [('1/4" = 1\'-0"', 48), ('1/8"=1\'-0"', 96), ('3/16" = 1\'-0"', 64), ('1-1/2" = 1\'-0"', 8),
     ('1 1/2" = 1\'-0"', 8), ('3" = 1\'-0"', 4), ('1" = 20\'', 240), ('1" = 20\'-0"', 240),
     ("1/4 in = 1 ft", 48), ("1/4″ = 1′-0″", 48), ("1/4” = 1’-0”", 48)],
)
def test_imperial_scales(text: str, scale: float) -> None:
    """Imperial scales are ratios too: 1/4 inch per foot is 1:48."""
    assert parse_scale(text) == scale


@pytest.mark.parametrize("paper", [(279.4, 215.9), (914.4, 609.6)], ids=["Letter landscape", "ARCH D"])
def test_imperial_sheet(tmp_path: Path, paper) -> None:
    """A US-style sheet: scales written in feet and inches, measured in metres."""
    pdf = scale_pdf(
        tmp_path / "s.pdf", paper, plan_title='FLOOR PLAN 1/8" = 1\'-0"',
        detail_title='DETAIL A 3" = 1\'-0"', keyword="SCALE:", title_block_scale='1/8" = 1\'-0"',
    )
    s = suggest(pymupdf.open(pdf)[0])
    assert s.scale == 96 and [v.scale for v in s.viewports] == [4]
    out = tmp_path / "s.dwfx"
    convert(pdf, out, scales=suggest)
    w2x, _ = _w2x_and_page(out)
    # 1 m at 1:96 = 1000/96 mm of paper; 1 ft (0.3048 m) = 1/8 inch exactly.
    per_metre = _per_unit(w2x.find("Units").get("Transform"))
    assert abs(0.3048 * per_metre - 3175) < 0.01  # 1/8 inch = 3.175 mm = 3175 grid units


def test_several_scales_warning(tmp_path: Path) -> None:
    s = suggest(pymupdf.open(scale_pdf(tmp_path / "s.pdf"))[0])
    assert s.warning and s.several_scales
    assert s.notes[0].startswith("several scales on this page (1:50, 1:5)")
    # once the user has set the page up, no more warning
    s.status = CONFIRMED
    assert not s.warning


def test_one_scale_no_warning(tmp_path: Path) -> None:
    pdf = scale_pdf(tmp_path / "s.pdf", detail_title="DETALHE A 1:50")
    s = suggest(pymupdf.open(pdf)[0])
    assert s.scale == 50 and not s.viewports and not s.warning


@pytest.mark.parametrize("text", ["1:50", "1 : 50", "1/50", "1_50", "1-50", "1 - 50", "1 to 50", "1 TO 50", "1–50"])
def test_scale_symbols_typed_by_user(text: str) -> None:
    assert parse_scale(text) == 50


@pytest.mark.parametrize(
    "text, found",
    [
        ("PLANTA 1:50", 50), ("PLANTA 1_50", 50), ("PLANTA 1-50", 50), ("PLANTA 1 to 50", 50),
        ("CORTE 1 - 20", 20), ("DETALHE 1–5", 5),
        # not scales: dates, ranges, file names, references
        ("DATA 1-12-2024", None), ("NOTAS 1-2", None), ("PR2017_PL_1_50.dwg", None),
        ("IS1-50", None), ("FOLHA 1_2", None), ("ver 1 to 3", None),
    ],
)
def test_scale_symbols_on_the_page(text: str, found: float | None) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    page.insert_font(fontname="F1", fontbuffer=pymupdf.Font("cjk").buffer)
    page.insert_text((20, 100), text, fontname="F1", fontsize=12)
    scales = [label.scale for label in scale_labels(page)]
    assert scales == ([found] if found else [])
