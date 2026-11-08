"""Check a built app: convert a three-page test drawing with it.

    uv run python packaging/smoke_test.py COMMAND...

COMMAND starts the built app, possibly inside a tool that blocks the
network (the release workflow does that on every system). This script
adds "--cli test.pdf -o test.dwfx" and checks the result: the conversion
must work fully offline, using several worker processes.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from synthetic import features_pdf, scale_pdf, text_pdf  # noqa: E402


def main(command: list[str]) -> int:
    folder = Path(tempfile.mkdtemp())
    pdf, dwfx = folder / "test.pdf", folder / "test.dwfx"
    with pymupdf.open() as doc:
        for source in (features_pdf(folder / "f.pdf"), scale_pdf(folder / "s.pdf"), text_pdf(folder / "t.pdf")):
            with pymupdf.open(source) as part:
                doc.insert_pdf(part)
        doc.save(pdf)
    result = subprocess.run([*command, "--cli", str(pdf), "-o", str(dwfx)], timeout=600)
    if result.returncode != 0:
        print(f"FAILED: the app ended with code {result.returncode}")
        return 1
    with zipfile.ZipFile(dwfx) as z:
        sheets = sum(name.endswith(".fpage") for name in z.namelist())
    if sheets != 3:
        print(f"FAILED: expected 3 sheets, found {sheets}")
        return 1
    print(f"OK: converted 3 pages ({dwfx.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
