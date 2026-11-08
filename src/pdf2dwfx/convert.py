"""Top-level conversion: PDF file in, DWFx file out."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pymupdf

from .dwfx import Section, build_section, document_folder, package_parts, write_package
from .parallel import document, ordered_map, worker_count
from .pdf_reader import read_page
from .scale import PageScale

# A page's scale: given (or None), or worked out from the page by a function.
ScaleSource = PageScale | Callable[[pymupdf.Page], PageScale | None] | None


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


def convert_page(doc: pymupdf.Document, number: int, order: int, folder: str, scale: ScaleSource) -> tuple[Section, Counter[str]]:
    """Read, prepare and write one page: its sheet's files, and the PDF
    features that could not be reproduced on it."""
    page = doc[number - 1]
    sheet = read_page(page)
    sheet.scale = scale(page) if callable(scale) else scale
    return build_section(folder, order, sheet), sheet.unsupported


def _convert_page_job(job: tuple[str, int, int, str, ScaleSource]) -> tuple[Section, Counter[str]]:
    """convert_page in a worker process (see parallel.py)."""
    path, number, order, folder, scale = job
    return convert_page(document(path), number, order, folder, scale)


def convert(
    pdf_path: Path,
    dwfx_path: Path,
    pages: int | Sequence[int] | None = None,
    scales: Mapping[int, PageScale] | Callable[[pymupdf.Page], PageScale | None] | None = None,
    progress: Callable[[int, int], None] | None = None,
    workers: int | None = None,
) -> list[str]:
    """Convert PDF pages into one DWFx file with one sheet per page.

    ``pages``: page numbers counting from 1 (a single number, a list, or
    None for all pages). ``scales``: the drawing scale of each page, by
    page number, or a function that works it out from the page (see
    suggest.py); pages without one measure as paper (mm). Returns
    warnings: PDF features that could not be reproduced, totalled over all
    converted pages. ``progress(done, total)`` is called after each page
    (for a progress bar). ``workers``: how many pages to work on at the
    same time (default: one per processor core; 1 = no worker processes).
    """
    with pymupdf.open(pdf_path) as doc:
        count = doc.page_count
        if pages is None:
            numbers = list(range(1, count + 1))
        elif isinstance(pages, int):
            numbers = [pages]
        else:
            numbers = list(pages)
        for n in numbers:
            if not 1 <= n <= count:
                raise ValueError(f"page {n} does not exist (the PDF has {count} pages)")
        if not numbers:
            raise ValueError("a DWFx file needs at least one sheet")
        folder = document_folder()
        page_scales = [scales if callable(scales) else (scales or {}).get(n) for n in numbers]
        workers = workers or worker_count(len(numbers))
        if workers == 1:
            results = (convert_page(doc, n, order, folder, s) for order, (n, s) in enumerate(zip(numbers, page_scales), 1))
        else:
            jobs = [(str(pdf_path), n, order, folder, s) for order, (n, s) in enumerate(zip(numbers, page_scales), 1)]
            results = ordered_map(_convert_page_job, jobs, workers)
        sections: list[Section] = []
        totals: Counter[str] = Counter()
        hidden = "layers switched off in the PDF (left out)"
        hidden_count = 0
        for section, unsupported in results:
            sections.append(section)
            # Layers switched off are a property of the whole PDF: count them once.
            hidden_count = hidden_count or unsupported.get(hidden, 0)
            totals.update({k: v for k, v in unsupported.items() if k != hidden})
            if progress:
                progress(len(sections), len(numbers))
    write_package(dwfx_path, package_parts(folder, sections))
    if hidden_count:
        totals[hidden] = hidden_count
    return [f"{feature} ({count}x)" for feature, count in sorted(totals.items())]
