"""PDF text becomes real (searchable) text in the DWFx."""

import math
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf
import pytest

from pdf2dwfx.convert import convert
from pdf2dwfx.prepare import UNITS_PER_MM
from pdf2dwfx.fonts import obfuscate, windows_font
from synthetic import text_pdf

NS_XPS = "{http://schemas.microsoft.com/xps/2005/06}"


@pytest.mark.parametrize(
    "pdf_name, bold, family, is_bold, is_italic",
    [
        ("KHUMIY+ArialNarrow", False, "Arial Narrow", False, False),
        ("HSMFXB+ArialMT", False, "Arial", False, False),
        ("ZDMUEB+Arial-BoldMT", True, "Arial", True, False),
        ("LFNZJX+ArialNarrow-Bold", True, "Arial Narrow", True, False),
        ("Arial,BoldItalic", False, "Arial", True, True),
        ("TimesNewRomanPSMT", False, "Times New Roman", False, False),
        ("FBEUVA+Droid Sans Fallback Regular", False, "Droid Sans Fallback", False, False),
    ],
)
def test_windows_font_names(pdf_name: str, bold: bool, family: str, is_bold: bool, is_italic: bool) -> None:
    assert windows_font(pdf_name, bold, False) == (family, is_bold, is_italic)


def test_obfuscation_round_trip() -> None:
    data = bytes(range(256)) * 4
    guid = "B8A1C2D3-1234-5678-9ABC-DEF012345678"
    scrambled = obfuscate(data, guid)
    assert scrambled[:32] != data[:32] and scrambled[32:] == data[32:]
    assert obfuscate(scrambled, guid) == data
    # The key is the GUID's bytes in reverse order (XPS rule).
    assert scrambled[0] == data[0] ^ 0x78


def _convert(tmp_path: Path):
    pdf = text_pdf(tmp_path / "in.pdf")
    out = tmp_path / "out.dwfx"
    warnings = convert(pdf, out)
    z = zipfile.ZipFile(out)
    page = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".fpage"))))
    w2x = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith("W2X.xml"))))
    return pdf, out, warnings, z, page, w2x


def test_text_is_converted_without_warnings(tmp_path: Path) -> None:
    _, _, warnings, _, _, _ = _convert(tmp_path)
    assert warnings == []


def test_text_is_searchable(tmp_path: Path) -> None:
    pdf, out, _, _, page, _ = _convert(tmp_path)
    texts = [g.get("UnicodeString") for g in page.iter(NS_XPS + "Glyphs")]
    assert "FLOOR PLAN 1 | 1:100" in texts
    assert "Sala de estar 25,40 m2" in texts
    assert "ALCADO NORTE" in texts
    # A viewer reading the DWFx finds the same text as in the PDF.
    pdf_text = pymupdf.open(pdf)[0].get_text().split()
    dwfx_text = pymupdf.open(out, filetype="xps")[0].get_text().split()
    assert sorted(dwfx_text) == sorted(pdf_text)


def test_every_text_has_font_and_text_entries(tmp_path: Path) -> None:
    """Design Review takes the font (installed font of that name, height,
    rotation) from the W2X Font setting and the start point from the W2X
    Text entry; the letters come from the page's Glyphs."""
    _, _, _, z, page, w2x = _convert(tmp_path)
    height = int(w2x.find("View").get("Area").split(",")[3])
    texts = {t.get("refName"): t for t in w2x.iter("Text")}
    fonts = list(w2x.iter("Font"))
    assert fonts and all(f.get("Name") == "Droid Sans Fallback" for f in fonts)
    for glyphs in page.iter(NS_XPS + "Glyphs"):
        entry = texts[glyphs.get("Name")]
        x, y = (int(v) for v in entry.get("StartPoint").split(","))
        assert x == int(glyphs.get("OriginX"))
        assert y == height - int(glyphs.get("OriginY"))  # y up from the bottom
        # the embedded font file exists
        font_part = glyphs.get("FontUri")[2:]
        assert any(n.endswith(font_part) for n in z.namelist())


def test_rotated_text_gets_rotation(tmp_path: Path) -> None:
    _, _, _, _, page, w2x = _convert(tmp_path)
    rotations = [int(f.get("Rotation")) for f in w2x.iter("Font")]
    # insert_text(rotate=90) turns text 90 degrees counter-clockwise.
    assert 16384 in rotations and 0 in rotations
    rotated = [g for g in page.iter(NS_XPS + "Glyphs") if g.get("UnicodeString") == "ALCADO NORTE"][0]
    assert rotated.get("RenderTransform")


def test_text_size_matches_pdf(tmp_path: Path) -> None:
    _, _, _, _, page, w2x = _convert(tmp_path)
    title = [g for g in page.iter(NS_XPS + "Glyphs") if g.get("UnicodeString").startswith("FLOOR")][0]
    expected = 14 * UNITS_PER_MM * 25.4 / 72  # 14 pt in grid units
    assert math.isclose(float(title.get("FontRenderingEmSize")), expected, rel_tol=0.01)
    heights = {int(f.get("Height")) for f in w2x.iter("Font")}
    assert round(expected) in heights


def test_embedded_font_is_the_pdf_font(tmp_path: Path) -> None:
    _, _, _, z, page, _ = _convert(tmp_path)
    uri = next(page.iter(NS_XPS + "Glyphs")).get("FontUri")
    name = next(n for n in z.namelist() if n.endswith(uri[2:]))
    guid = name.rsplit("/", 1)[1].split(".")[0]
    font = obfuscate(z.read(name), guid)
    assert font[:4] == b"\x00\x01\x00\x00"  # a valid TrueType file once unscrambled


def test_text_on_a_layer(tmp_path: Path) -> None:
    _, _, _, _, page, w2x = _convert(tmp_path)
    names = {}
    current = None
    switch = {}
    for sync in w2x.iter("RenditionSync"):
        layer = sync.find("Layer")
        if layer is not None:
            if layer.get("Name"):
                names[layer.get("Number")] = layer.get("Name")
            switch[sync.get("refName")] = layer.get("Number")
    layer_of = {}
    for el in page.find(NS_XPS + "Canvas")[0]:
        current = switch.get(el.get("Name"), current)
        if el.tag == NS_XPS + "Glyphs":
            layer_of[el.get("UnicodeString")] = names[current]
    assert layer_of["On a layer"] == "TEXT"
    assert layer_of["FLOOR PLAN 1 | 1:100"] == "0"


def test_words_placed_by_position_stay_in_place(tmp_path: Path) -> None:
    """A gap made by moving the pen (not a space) must not shift the next
    word: it becomes spaces or a separate text item at the exact place."""
    pdf, out, _, _, _, _ = _convert(tmp_path)
    words = {w[4]: w[:2] for w in pymupdf.open(pdf)[0].get_text("words")}
    ours = {w[4]: w[:2] for w in pymupdf.open(out, filetype="xps")[0].get_text("words")}
    for word in ("COZINHA", "12m2"):
        assert math.isclose(ours[word][0], words[word][0], abs_tol=0.5)
        assert math.isclose(ours[word][1], words[word][1], abs_tol=0.5)
