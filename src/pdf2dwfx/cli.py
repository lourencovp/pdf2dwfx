"""Command line: ``pdf2dwfx drawing.pdf`` writes ``drawing.dwfx`` next to it.

Scales: by default each page gets the scale the program finds on it (see
suggest.py). ``--show-scales`` lists those suggestions without
converting; ``--scale`` and ``--viewport`` set scales yourself.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
from pathlib import Path

import pymupdf

from . import __version__
from .convert import convert, parse_pages
from .crash import NEW_ISSUE, make_report, save_report
from .scale import CONFIRMED, SUGGESTED, PageScale, Viewport, format_scale, parse_scale
from .suggest import PT_PER_MM, scale_plan

_STATUS = {CONFIRMED: "set by you", SUGGESTED: "suggested", "none": "no scale found"}


def parse_sheet_scales(spec: str, page_count: int) -> dict[int, float | None]:
    """ "1-4=1:200, 17=1:50, 1=paper" -> {1: None, 2: 200, ...} (later wins)."""
    scales: dict[int, float | None] = {}
    for part in spec.split(","):
        if not part.strip():
            continue
        pages, eq, value = part.partition("=")
        if not eq:
            raise ValueError(f'"{part.strip()}": write pages=scale, e.g. "1-4=1:100"')
        scale = parse_scale(value)
        for n in parse_pages(pages, page_count):
            scales[n] = scale
    return scales


def parse_viewport(spec: str, page_count: int) -> tuple[int, Viewport]:
    """ "17:20,150,145,285=1:5" -> page 17, box in mm from the sheet's
    top-left corner (left, top, right, bottom), scale 1:5."""
    page, colon, rest = spec.partition(":")
    box, eq, value = rest.partition("=")
    numbers = box.split(",")
    if not colon or not eq or len(numbers) != 4:
        raise ValueError(f'"{spec}": write page:left,top,right,bottom=scale (mm), e.g. "17:20,150,145,285=1:5"')
    n = int(page)
    if not 1 <= n <= page_count:
        raise ValueError(f"page {n} does not exist (the PDF has {page_count} pages)")
    scale = parse_scale(value)
    if scale is None:
        raise ValueError(f'"{spec}": a viewport needs a scale')
    x0, y0, x1, y1 = (float(v) * PT_PER_MM for v in numbers)
    return n, Viewport((x0, y0, x1, y1), scale, f"Viewport 1:{format_scale(scale)}")


def _warning_sign() -> str:
    """The warning sign, or "(!)" where the console cannot show it."""
    sign = "⚠️"
    try:
        # (no console at all, e.g. the packaged app started by another program: stdout is None)
        sign.encode(getattr(sys.stdout, "encoding", None) or "ascii")
        return sign
    except (UnicodeEncodeError, LookupError):
        return "(!)"


def describe(n: int, s: PageScale) -> list[str]:
    mark = _warning_sign() if s.warning else "  "
    lines = [f"{mark}page {n:>3}: {s.describe():<32} ({_STATUS.get(s.status, s.status)})"]
    for v in s.viewports:
        x0, y0, x1, y1 = (round(c / PT_PER_MM) for c in v.box)
        lines.append(f"            viewport 1:{v.scale:g} at {x0},{y0},{x1},{y1} mm  {v.label}")
    lines += [f"            ! {note}" for note in s.notes]
    return lines


def main(argv: list[str] | None = None) -> int:
    # Lets worker processes start inside the packaged app (see parallel.py).
    multiprocessing.freeze_support()
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
    parser.add_argument(
        "--show-scales", action="store_true",
        help="only list the scale found on each page, do not convert",
    )
    parser.add_argument(
        "--scale", metavar="PAGES=SCALE",
        help='whole-sheet scales, e.g. "1-4=1:200,17=1:50,1=paper" (replaces suggestions on those pages)',
    )
    parser.add_argument(
        "--viewport", action="append", default=[], metavar="PAGE:L,T,R,B=SCALE",
        help='an area with its own scale, in mm from the sheet\'s top-left corner, '
        'e.g. "17:20,150,145,285=1:5" (repeat for more)',
    )
    parser.add_argument(
        "--no-suggest", action="store_true",
        help="do not guess scales: pages without --scale measure as paper (mm)",
    )
    parser.add_argument("--version", action="version", version=f"pdf2dwfx {__version__}")
    args = parser.parse_args(argv)
    # Drawing titles may contain letters a console cannot show (e.g. "Ç" on
    # an old Windows console): show a replacement instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    output: Path = args.output or args.pdf.with_suffix(".dwfx")
    try:
        with pymupdf.open(args.pdf) as doc:
            count = doc.page_count
            pages = parse_pages(args.pages, count) if args.pages else list(range(1, count + 1))
            for n in pages:
                if not 1 <= n <= count:
                    raise ValueError(f"page {n} does not exist (the PDF has {count} pages)")
            sheet_scales = parse_sheet_scales(args.scale, count) if args.scale else {}
            viewports: dict[int, list[Viewport]] = {}
            for spec in args.viewport:
                n, vp = parse_viewport(spec, count)
                viewports.setdefault(n, []).append(vp)
            plan = scale_plan(doc, pages, sheet_scales, viewports, not args.no_suggest)
        print("Scales:")
        for n in pages:
            print("\n".join(describe(n, plan[n])))
        if args.show_scales:
            return 0
        warnings = convert(args.pdf, output, pages, scales=plan)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Unexpected: save a crash report (nothing is sent; see crash.py).
        try:
            saved = save_report(make_report(error, args.pdf, [str(output)]))
        except Exception:
            raise error from None
        print(
            "Something unexpected went wrong. A crash report was saved here:\n"
            f"  {saved}\n"
            "It holds no drawing content or file names. To help fix it, open a new issue at\n"
            f"  {NEW_ISSUE}\n"
            "and paste the report into it.",
            file=sys.stderr,
        )
        return 2
    print(f"Written: {output}")
    several = [str(n) for n in pages if plan[n].warning]
    if several:
        print(f"{_warning_sign()} Several scales on page(s) {', '.join(several)}: please check them and set them up "
              '(--scale for the sheet, --viewport for each drawing).')
    unchecked = [n for n in pages if plan[n].status == SUGGESTED]
    if unchecked:
        print(f"Scales not yet checked by you on {len(unchecked)} page(s): please check them in Design Review.")
    if warnings:
        print("Not converted (yet): " + ", ".join(warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
