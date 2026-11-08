"""Working on several pages at once must give exactly the same file as
working on one page at a time."""

import re
import zipfile
from pathlib import Path

import pymupdf

from pdf2dwfx.convert import convert
from pdf2dwfx.suggest import suggest
from synthetic import features_pdf, layers_pdf, scale_pdf, text_pdf

GUID = re.compile(rb"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")


def mixed_pdf(tmp_path: Path) -> Path:
    """Pages of every kind: shapes and pictures, layers,
    text with fonts, a sheet with scales."""
    path = tmp_path / "mixed.pdf"
    sources = [
        features_pdf(tmp_path / "f.pdf"),
        layers_pdf(tmp_path / "l.pdf", with_hidden=True),
        text_pdf(tmp_path / "t.pdf"),
        scale_pdf(tmp_path / "s.pdf"),
    ]
    with pymupdf.open() as doc:
        for source in sources:
            with pymupdf.open(source) as part:
                doc.insert_pdf(part)
        doc.save(path)
    return path


def contents(dwfx: Path) -> list[tuple[bytes, bytes]]:
    """Every part, in order. Random IDs are replaced by their order of first
    appearance; fonts are scrambled with their ID, so only their unscrambled
    part (after the first 32 bytes) is compared."""
    seen: dict[bytes, int] = {}

    def number(match: re.Match[bytes]) -> bytes:
        return b"ID%d" % seen.setdefault(match.group(0), len(seen))

    out = []
    with zipfile.ZipFile(dwfx) as z:
        for info in z.infolist():
            data = z.read(info)
            data = data[32:] if info.filename.endswith(".odttf") else GUID.sub(number, data)
            out.append((GUID.sub(number, info.filename.encode()), data))
    return out


def test_parallel_gives_the_same_file(tmp_path: Path) -> None:
    pdf = mixed_pdf(tmp_path)
    one, many = tmp_path / "one.dwfx", tmp_path / "many.dwfx"
    steps: list[tuple[int, int]] = []
    warnings_one = convert(pdf, one, scales=suggest, workers=1)
    warnings_many = convert(pdf, many, scales=suggest, workers=3, progress=lambda d, t: steps.append((d, t)))
    assert contents(one) == contents(many)
    assert warnings_one == warnings_many
    assert steps == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_switched_off_layers_reported_once(tmp_path: Path) -> None:
    """Layers switched off belong to the whole PDF: one warning, however
    many pages (here the same page twice, on two workers)."""
    pdf = layers_pdf(tmp_path / "l.pdf", with_hidden=True)
    warnings = convert(pdf, tmp_path / "l.dwfx", [1, 1], workers=2)
    assert warnings == ["layers switched off in the PDF (left out) (1x)"]
