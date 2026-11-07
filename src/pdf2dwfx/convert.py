"""Top-level conversion: PDF file in, DWFx file out."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pymupdf

from .dwfx import write_dwfx
from .pdf_reader import read_page


def parse_pages(spec: str, page_count: int) -> list[int]:
    """Page numbers (counting from 1) from text like "3", "1-5", "4-" or "1-3,7"."""
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        first, dash, last = part.partition("-")
        start = int(first) if first else 1
        end = int(last) if last else (page_count if dash else start)
        if start > end:
            raise ValueError(f"page range {part} goes backwards")
        pages.extend(range(start, end + 1))
    return pages


def convert(pdf_path: Path, dwfx_path: Path, pages: int | Sequence[int] | None = None) -> list[str]:
    """Convert PDF pages into one DWFx file with one sheet per page.

    ``pages``: page numbers counting from 1 (a single number, a list, or
    None for all pages). Returns warnings: PDF features that could not be
    reproduced, totalled over all converted pages.
    """
    with pymupdf.open(pdf_path) as doc:
        if pages is None:
            numbers = list(range(1, doc.page_count + 1))
        elif isinstance(pages, int):
            numbers = [pages]
        else:
            numbers = list(pages)
        for n in numbers:
            if not 1 <= n <= doc.page_count:
                raise ValueError(f"page {n} does not exist (the PDF has {doc.page_count} pages)")
        sheets = [read_page(doc[n - 1]) for n in numbers]
    write_dwfx(dwfx_path, sheets)
    totals: Counter[str] = Counter()
    for sheet in sheets:
        totals.update(sheet.unsupported)
    # Layers switched off are a property of the whole PDF: count them once.
    hidden = "layers switched off in the PDF (left out)"
    if totals[hidden]:
        totals[hidden] = sheets[0].unsupported[hidden]
    else:
        del totals[hidden]
    return [f"{feature} ({count}x)" for feature, count in sorted(totals.items())]
