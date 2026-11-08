"""Build the installable app for the system this runs on.

    uv run python packaging/build.py

Windows: dist/release/pdf2dwfx-windows.exe  (one file, nothing to install)
macOS:   dist/release/pdf2dwfx-macos.dmg    (pdf2dwfx.app inside)
Linux:   dist/release/pdf2dwfx-linux.AppImage  (needs appimagetool, see below)

Run by GitHub Actions for every release (see .github/workflows/release.yml);
also works locally on each system.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
RELEASE = DIST / "release"
NAME = "pdf2dwfx"


def run(*command: str | Path) -> None:
    print("+", " ".join(str(c) for c in command), flush=True)
    subprocess.run([str(c) for c in command], check=True)


def make_icon(path: Path, size: int = 256) -> Path:
    """A plain placeholder icon (a sheet with a folded corner), drawn here
    so no picture file needs to be kept in the repository."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPolygonF

    app = QGuiApplication.instance() or QGuiApplication([])
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 16
    sheet = QPolygonF([QPointF(3 * s, 1 * s), QPointF(10 * s, 1 * s), QPointF(13 * s, 4 * s), QPointF(13 * s, 15 * s), QPointF(3 * s, 15 * s)])
    p.setBrush(QColor(255, 255, 255))
    p.setPen(QColor(0, 70, 170))
    p.drawPolygon(sheet)
    p.setBrush(QColor(0, 90, 200))
    p.drawPolygon(QPolygonF([QPointF(10 * s, 1 * s), QPointF(13 * s, 4 * s), QPointF(10 * s, 4 * s)]))
    p.drawRect(int(5 * s), int(7 * s), int(6 * s), int(5 * s))
    p.end()
    image.save(str(path))
    # Windows and macOS want their own icon formats; Qt writes both.
    for suffix in (".ico", ".icns"):
        image.save(str(path.with_suffix(suffix)))
    del app
    return path


def pyinstaller(icon: Path, onefile: bool) -> None:
    run(
        sys.executable, "-m", "PyInstaller", ROOT / "packaging" / "entry.py",
        "--name", NAME,
        "--windowed",  # no console window
        "--onefile" if onefile else "--onedir",
        "--noconfirm", "--clean",
        "--icon", icon.with_suffix({"win32": ".ico", "darwin": ".icns"}.get(sys.platform, ".png")),
        "--copy-metadata", NAME,  # the app shows its version number
        "--distpath", DIST, "--workpath", ROOT / "build", "--specpath", ROOT / "build",
    )


def main() -> None:
    RELEASE.mkdir(parents=True, exist_ok=True)
    work = ROOT / "build"
    work.mkdir(exist_ok=True)
    icon = make_icon(work / "icon.png")

    if sys.platform == "win32":
        pyinstaller(icon, onefile=True)
        shutil.copy2(DIST / f"{NAME}.exe", RELEASE / f"{NAME}-windows.exe")

    elif sys.platform == "darwin":
        pyinstaller(icon, onefile=False)  # makes dist/pdf2dwfx.app
        dmg = RELEASE / f"{NAME}-macos.dmg"
        dmg.unlink(missing_ok=True)
        run("hdiutil", "create", "-volname", NAME, "-srcfolder", DIST / f"{NAME}.app", "-ov", "-format", "UDZO", dmg)

    else:
        pyinstaller(icon, onefile=False)  # makes dist/pdf2dwfx/
        appdir = work / f"{NAME}.AppDir"
        shutil.rmtree(appdir, ignore_errors=True)
        shutil.copytree(DIST / NAME, appdir / "usr" / "bin")
        shutil.copy2(icon, appdir / f"{NAME}.png")
        (appdir / f"{NAME}.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=pdf2dwfx\n"
            f"Exec={NAME} %f\nIcon={NAME}\nCategories=Graphics;\nMimeType=application/pdf;\n",
            encoding="utf-8",
        )
        apprun = appdir / "AppRun"
        apprun.write_text('#!/bin/sh\nexec "$(dirname "$(readlink -f "$0")")/usr/bin/pdf2dwfx" "$@"\n', encoding="utf-8")
        apprun.chmod(0o755)
        # appimagetool: path in APPIMAGETOOL (the release workflow downloads it).
        tool = os.environ.get("APPIMAGETOOL", "appimagetool")
        env_tool = {**os.environ, "ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"}
        out = RELEASE / f"{NAME}-linux.AppImage"
        print("+", tool, appdir, out, flush=True)
        subprocess.run([tool, str(appdir), str(out)], check=True, env=env_tool)

    for file in sorted(RELEASE.iterdir()):
        print(f"built {file} ({file.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
