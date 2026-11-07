"""Milestone 0 sanity checks: the project installs and its tools are usable."""

import subprocess
from pathlib import Path

import pymupdf

import pdf2dwfx

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_package_has_version() -> None:
    assert pdf2dwfx.__version__


def test_pymupdf_can_create_and_read_a_pdf() -> None:
    # Build a tiny A4 PDF in memory with one line, then read the line back.
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4 in points
    page.draw_line((100, 100), (300, 100))
    reread = pymupdf.open("pdf", doc.tobytes())
    drawings = reread[0].get_drawings()
    assert len(drawings) == 1


def test_private_drawings_are_ignored_by_git() -> None:
    # Safety net: the private folder must never be publishable.
    result = subprocess.run(
        ["git", "check-ignore", "-q", "pdf2dwfx-private-tests/example.pdf"],
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, "pdf2dwfx-private-tests/ is not git-ignored!"
