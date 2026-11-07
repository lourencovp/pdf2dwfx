"""Top-level conversion: PDF file in, DWFx file out."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from .dwfx import write_dwfx
from .model import Group, ImageShape
from .pdf_reader import read_page, unsupported_summary


def convert(pdf_path: Path, dwfx_path: Path, page_number: int = 1) -> list[str]:
    """Convert one page (counting from 1) of a PDF into a one-sheet DWFx.

    Returns warnings: PDF features on the page that could not be reproduced.
    """
    with pymupdf.open(pdf_path) as doc:
        if not 1 <= page_number <= doc.page_count:
            raise ValueError(
                f"page {page_number} does not exist (the PDF has {doc.page_count} pages)"
            )
        sheet = read_page(doc[page_number - 1])
    write_dwfx(dwfx_path, [sheet])
    warnings = unsupported_summary(sheet)
    pictures = _count_pictures(sheet.root)
    if pictures:
        # Pictures need their own entries in Autodesk's format; coming next.
        warnings.append(f"pictures ({pictures}x)")
    return sorted(warnings)


def _count_pictures(group: Group) -> int:
    count = 0
    for child in group.children:
        if isinstance(child, ImageShape):
            count += 1
        elif isinstance(child, Group):
            count += _count_pictures(child) * len(child.offsets or [None])
    return count
