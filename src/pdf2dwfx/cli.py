"""Command line: ``pdf2dwfx drawing.pdf`` writes ``drawing.dwfx`` next to it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
import pymupdf

from .convert import convert, parse_pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pdf2dwfx", description="Convert a vector PDF drawing into a DWFx file."
    )
    parser.add_argument("pdf", type=Path, help="the PDF file to convert")
    parser.add_argument(
        "-o", "--output", type=Path, help="DWFx file to write (default: next to the PDF)"
    )
    parser.add_argument(
        "--pages",
        help='pages to convert, e.g. "3", "1-5" or "1-3,7" (default: all pages)',
    )
    parser.add_argument("--version", action="version", version=f"pdf2dwfx {__version__}")
    args = parser.parse_args(argv)

    output: Path = args.output or args.pdf.with_suffix(".dwfx")
    try:
        pages = None
        if args.pages:
            with pymupdf.open(args.pdf) as doc:
                pages = parse_pages(args.pages, doc.page_count)
        warnings = convert(args.pdf, output, pages)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Written: {output}")
    if warnings:
        print("Not converted (yet): " + ", ".join(warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
