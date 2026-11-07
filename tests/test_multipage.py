"""Several PDF pages become several sheets in one DWFx file."""

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf
import pytest

from pdf2dwfx.cli import main
from pdf2dwfx.convert import convert, parse_pages
from synthetic import A1_MM, A3_MM, A4_MM, drawing_pdf, layers_pdf

NS_EPLOT = "{DWF-ePlot:1.2}"
NS_XPS = "{http://schemas.microsoft.com/xps/2005/06}"


def _three_pages(tmp_path: Path) -> Path:
    """A3 portrait, A1 portrait and a layered A4 page."""
    out = pymupdf.open()
    for i, make in enumerate(
        (lambda p: drawing_pdf(p, size_mm=A3_MM), lambda p: drawing_pdf(p, size_mm=A1_MM), layers_pdf)
    ):
        out.insert_pdf(pymupdf.open(make(tmp_path / f"part{i}.pdf")))
    path = tmp_path / "three.pdf"
    out.save(path)
    return path


def test_parse_pages() -> None:
    assert parse_pages("3", 10) == [3]
    assert parse_pages("1-3,7", 10) == [1, 2, 3, 7]
    assert parse_pages("8-", 10) == [8, 9, 10]
    with pytest.raises(ValueError):
        parse_pages("5-2", 10)


def test_every_page_becomes_a_sheet(tmp_path: Path) -> None:
    out = tmp_path / "out.dwfx"
    convert(_three_pages(tmp_path), out)
    z = zipfile.ZipFile(out)
    descriptors = sorted(n for n in z.namelist() if n.endswith("descriptor.xml"))
    papers = []
    for name in descriptors:
        root = ET.fromstring(z.read(name))
        paper = root.find(NS_EPLOT + "Paper")
        papers.append((int(root.get("plotOrder")), round(float(paper.get("width"))), round(float(paper.get("height")))))
    assert sorted(papers) == [(1, 297, 420), (2, 594, 841), (3, 210, 297)]
    # Each sheet has its own page and W2X companion, and the XPS side lists
    # the pages in the same order.
    assert sum(n.endswith(".fpage") for n in z.namelist()) == 3
    assert sum(n.endswith("W2X.xml") for n in z.namelist()) == 3
    fdoc = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".fdoc"))))
    assert len(list(fdoc.iter(NS_XPS + "PageContent"))) == 3


def test_selected_pages_only(tmp_path: Path) -> None:
    out = tmp_path / "out.dwfx"
    convert(_three_pages(tmp_path), out, [3, 1])
    z = zipfile.ZipFile(out)
    widths = []
    for name in z.namelist():
        if name.endswith("descriptor.xml"):
            root = ET.fromstring(z.read(name))
            widths.append((int(root.get("plotOrder")), round(float(root.find(NS_EPLOT + "Paper").get("width")))))
    assert sorted(widths) == [(1, 210), (2, 297)]  # in the order asked for


def test_multipage_looks_like_pdf(tmp_path: Path) -> None:
    pdf = _three_pages(tmp_path)
    out = tmp_path / "out.dwfx"
    convert(pdf, out)
    sheets, pages = pymupdf.open(out, filetype="xps"), pymupdf.open(pdf)
    assert sheets.page_count == 3
    for sheet, page in zip(sheets, pages):
        a = page.get_pixmap(dpi=30, colorspace=pymupdf.csGRAY)
        b = sheet.get_pixmap(dpi=30, colorspace=pymupdf.csGRAY)
        w, h = min(a.width, b.width), min(a.height, b.height)
        diff = sum(
            1 for y in range(h) for p, q in zip(a.samples[y * a.width : y * a.width + w], b.samples[y * b.width : y * b.width + w])
            if abs(p - q) > 64
        )
        assert diff / (w * h) < 0.005


def test_cli_pages_option(tmp_path: Path) -> None:
    pdf = _three_pages(tmp_path)
    assert main([str(pdf), "--pages", "2-3", "-o", str(tmp_path / "x.dwfx")]) == 0
    z = zipfile.ZipFile(tmp_path / "x.dwfx")
    assert sum(n.endswith(".fpage") for n in z.namelist()) == 2
