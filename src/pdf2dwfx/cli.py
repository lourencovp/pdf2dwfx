"""Command line: ``pdf2dwfx drawing.pdf`` writes ``drawing.dwfx`` next to it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .convert import convert


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pdf2dwfx", description="Convert a vector PDF drawing into a DWFx file."
    )
    parser.add_argument("pdf", type=Path, help="the PDF file to convert")
    parser.add_argument(
        "-o", "--output", type=Path, help="DWFx file to write (default: next to the PDF)"
    )
    parser.add_argument("--page", type=int, default=1, help="page to convert (default: 1)")
    parser.add_argument("--version", action="version", version=f"pdf2dwfx {__version__}")
    args = parser.parse_args(argv)

    output: Path = args.output or args.pdf.with_suffix(".dwfx")
    try:
        convert(args.pdf, output, args.page)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Written: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
