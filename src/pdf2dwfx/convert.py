"""Top-level conversion: PDF file in, DWFx file out."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from .dwfx import write_dwfx
from .pdf_reader import read_page


def convert(pdf_path: Path, dwfx_path: Path, page_number: int = 1) -> None:
    """Convert one page (counting from 1) of a PDF into a one-sheet DWFx."""
    with pymupdf.open(pdf_path) as doc:
        if not 1 <= page_number <= doc.page_count:
            raise ValueError(
                f"page {page_number} does not exist (the PDF has {doc.page_count} pages)"
            )
        sheet = read_page(doc[page_number - 1])
    write_dwfx(dwfx_path, [sheet])
