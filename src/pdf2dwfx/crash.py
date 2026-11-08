"""Crash reports, made and kept on the user's computer only.

When something unexpected goes wrong, a plain-text report is written to a
local folder. Nothing is ever sent: the user decides whether to share it
(the app window offers to open a GitHub issue in the browser, with a short
summary filled in, for the user to complete and submit).

A report holds only: the error and where in the program it happened, the
app version, the operating system, and harmless numbers about the PDF
(page count, page sizes, number of layers). Never drawing content, text
from drawings, or file and folder names: any path or name that may appear
in an error message is blanked out before the report is written.
"""

from __future__ import annotations

import os
import platform
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from . import __version__

NEW_ISSUE = "https://github.com/lourencovp/pdf2dwfx/issues/new"


def report_folder() -> Path:
    """Where reports are kept: the usual place for an app's logs on each system."""
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return base / "pdf2dwfx" / "crash-reports"
    if sys.platform == "darwin":
        return home / "Library" / "Logs" / "pdf2dwfx"
    base = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state")
    return base / "pdf2dwfx" / "crash-reports"


def pdf_numbers(pdf: Path | None) -> list[str]:
    """Harmless facts about the PDF: page count, sizes and layer count. No names."""
    if pdf is None:
        return ["PDF: none open"]
    try:
        import pymupdf

        with pymupdf.open(pdf) as doc:
            sizes: dict[str, int] = {}
            for page in doc:
                w, h = (round(v / 72 * 25.4) for v in (page.rect.width, page.rect.height))
                sizes[f"{w} x {h} mm"] = sizes.get(f"{w} x {h} mm", 0) + 1
            layers = len(doc.get_ocgs() or {})
            return [
                f"PDF pages: {doc.page_count}",
                "PDF page sizes: " + ", ".join(f"{size} ({count}x)" for size, count in sizes.items()),
                f"PDF layers: {layers}",
            ]
    except Exception as error:  # the report must never fail because of the PDF
        return [f"PDF: could not be read ({type(error).__name__})"]


def scrub(text: str, private: list[str]) -> str:
    """Blank out anything that could name the user's files or folders.

    ``private``: paths and names known to be private (the PDF, the output).
    Also removes the user's home folder and any remaining file paths."""
    words = set()
    for item in private:
        if item:
            path = Path(item)
            words |= {str(path), path.as_posix(), path.name, path.stem}
    for word in sorted((w for w in words if len(w) > 2), key=len, reverse=True):
        text = re.sub(re.escape(word), "<file>", text, flags=re.IGNORECASE)
    home = str(Path.home())
    for form in {home, Path(home).as_posix()}:
        text = re.sub(re.escape(form), "~", text, flags=re.IGNORECASE)
    # Remaining paths in messages (e.g. 'C:\Projects\client\x.pdf' or
    # '/Users/x/client.pdf'): keep only program files (they show where the
    # error happened), which end in .py.
    return re.sub(
        r"(?:[A-Za-z]:)?[\\/](?:[^\s'\"<>:|\\/]+[\\/])+[^\s'\"<>:|]*",  # at least one folder
        lambda m: m.group(0) if m.group(0).endswith(".py") else "<path>",
        text,
    )


def make_report(error: BaseException, pdf: Path | None = None, private: list[str] | None = None) -> str:
    """The report text for an error (with the PDF's numbers, if one is open)."""
    lines = [
        f"pdf2dwfx crash report, {datetime.now().isoformat(timespec='seconds')}",
        f"App version: {__version__}",
        f"System: {platform.system()} {platform.release()} ({platform.machine()}), Python {platform.python_version()}",
        *pdf_numbers(pdf),
        "",
        "Error:",
        "".join(traceback.format_exception(error)).rstrip(),
    ]
    return scrub("\n".join(lines), [str(pdf) if pdf else "", *(private or [])])


def save_report(text: str, folder: Path | None = None) -> Path:
    """Write the report to a new file in the report folder; returns its path."""
    folder = folder or report_folder()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"crash-{datetime.now():%Y%m%d-%H%M%S-%f}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def issue_url(report: str) -> str:
    """A link that opens a new GitHub issue with a short summary filled in.

    Only the summary goes into the link (links have a length limit); the
    user pastes the full report themselves."""
    error_line = next((l for l in reversed(report.splitlines()) if l.strip()), "error")
    head = report.split("\nError:", 1)[0].splitlines()[1:]  # version, system, PDF numbers
    body = "\n".join(
        [
            "**What were you doing when it happened?**",
            "",
            "",
            "**Summary** (filled in by the app)",
            "```",
            *head,
            error_line,
            "```",
            "",
            "**Full report**: please paste it here (the app copied it to the clipboard).",
        ]
    )
    return f"{NEW_ISSUE}?{urlencode({'title': f'Crash: {error_line[:80]}', 'body': body})}"
