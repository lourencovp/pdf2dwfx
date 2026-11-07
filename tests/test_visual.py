"""Compare how the PDF and our DWFx look.

MuPDF (inside PyMuPDF) can also display XPS, and a DWFx is an XPS package.
So we render the original PDF page and our DWFx sheet to pictures and count
pixels that differ clearly. This catches misplaced, missing, mirrored or
wrongly scaled geometry without needing Design Review.

This is a fair preview of Design Review only because our pages use nothing
that Autodesk's translator ignores (test_dwfx_package checks that): no
curves, no clipping, no group transparency.
"""

from pathlib import Path

import pymupdf
import pytest

from pdf2dwfx.convert import convert
from synthetic import A1_MM, A3_MM, drawing_pdf, features_pdf

DPI = 50
# MuPDF rounds XPS page sizes to whole XPS units (1/96 inch), so the two
# pictures can differ by a pixel row/column; antialiasing adds a little more.
MAX_DIFFERENT = 0.005  # at most 0.5 % of pixels


def _difference(pdf: Path, dwfx: Path) -> float:
    a = pymupdf.open(pdf)[0].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    b = pymupdf.open(dwfx, filetype="xps")[0].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    assert abs(a.width - b.width) <= 1 and abs(a.height - b.height) <= 1
    w, h = min(a.width, b.width), min(a.height, b.height)
    sa, sb = a.samples, b.samples
    different = 0
    for y in range(h):
        ra, rb = y * a.width, y * b.width
        row_a, row_b = sa[ra : ra + w], sb[rb : rb + w]
        if row_a != row_b:
            different += sum(1 for p, q in zip(row_a, row_b) if abs(p - q) > 64)
    return different / (w * h)


def without_text(pdf: Path, out: Path) -> Path:
    """The PDF with its text removed (text is converted in milestone 3)."""
    doc = pymupdf.open(pdf)
    for page in doc:
        page.add_redact_annot(page.rect)
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE
        )
    doc.save(out)
    return out


@pytest.mark.parametrize(
    "options",
    [
        {"size_mm": A3_MM},
        {"size_mm": A1_MM},
        {"size_mm": (A3_MM[1], A3_MM[0])},  # landscape
        {"size_mm": A3_MM, "rotation": 90},
        {"size_mm": A3_MM, "rotation": 270},
        {"size_mm": A3_MM, "cropbox_mm": (10, 20, 280, 400)},
        {"size_mm": A3_MM, "rotation": 90, "cropbox_mm": (10, 20, 280, 400)},
    ],
    ids=["A3", "A1", "A3-landscape", "rot90", "rot270", "cropped", "rot90-cropped"],
)
def test_dwfx_looks_like_pdf(tmp_path: Path, options: dict) -> None:
    pdf = drawing_pdf(tmp_path / "in.pdf", **options)
    dwfx = tmp_path / "out.dwfx"
    convert(pdf, dwfx)
    assert _difference(pdf, dwfx) < MAX_DIFFERENT


@pytest.mark.parametrize("dpi", [50, 150])
def test_special_features_look_like_pdf(tmp_path: Path, dpi: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pictures (JPEG, transparent, rotated, mirrored, stencil, gradient),
    pattern fills, transparency, caps, joins, dashes, hairline, CMYK."""
    monkeypatch.setattr(__import__(__name__), "DPI", dpi)
    pdf = features_pdf(tmp_path / "in.pdf", stretched=False)
    dwfx = tmp_path / "out.dwfx"
    assert convert(pdf, dwfx) == []  # nothing reported as unsupported
    assert _difference(pdf, dwfx) < MAX_DIFFERENT


def test_stretched_line_is_reported(tmp_path: Path) -> None:
    pdf = features_pdf(tmp_path / "in.pdf", pictures=False, stretched=True)
    warnings = convert(pdf, tmp_path / "out.dwfx")
    assert warnings == ["unevenly stretched line thickness (approximated) (1x)"]


def test_private_sample_looks_like_pdf(tmp_path: Path, private_tests_dir: Path) -> None:
    """Real drawing (only on the owner's machine): everything except text
    must match the PDF exactly."""
    pdf = private_tests_dir / "pdf" / "singlepdfpage.pdf"
    if not pdf.exists():
        pytest.skip("singlepdfpage.pdf not present")
    dwfx = tmp_path / "out.dwfx"
    assert convert(pdf, dwfx) == ["text (155x)"]
    reference = without_text(pdf, tmp_path / "reference.pdf")
    assert _difference(reference, dwfx) < 0.0005
