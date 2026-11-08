"""Work on several pages at the same time, one page per processor core.

Pages are independent, so reading and preparing them is shared out to
worker processes (separate copies of the program; PyMuPDF cannot be used
from several threads of one process). Results come back in page order.

Workers are started fresh ("spawn") on every operating system, so the
program behaves the same on Windows, macOS and Linux, and a worker never
inherits a half-used window or open file from the app.
"""

from __future__ import annotations

import multiprocessing
import os
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TypeVar

import pymupdf

Job = TypeVar("Job")
Result = TypeVar("Result")

_documents: dict[str, pymupdf.Document] = {}  # PDFs opened by this worker


def document(path: Path | str) -> pymupdf.Document:
    """The PDF, opened once per worker process and kept open for its next pages.

    Only for use inside worker processes: they close everything when they end."""
    key = str(path)
    if key not in _documents:
        _documents[key] = pymupdf.open(path)
    return _documents[key]


def worker_count(jobs: int) -> int:
    """How many workers to use: one per core, keeping one core free so the
    computer (and the app window) stays responsive; never more than jobs."""
    return max(1, min(jobs, (os.cpu_count() or 2) - 1))


def ordered_map(work: Callable[[Job], Result], jobs: Iterable[Job], workers: int) -> Iterator[Result]:
    """``work(job)`` for every job, in order, using ``workers`` processes.

    ``work`` must be a plain module-level function (it is sent to the
    workers by name). The workers stop when the results have all been
    taken, or when the caller stops early (e.g. the user opened another PDF).
    """
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = pool.map(work, jobs)
        try:
            yield from results
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
