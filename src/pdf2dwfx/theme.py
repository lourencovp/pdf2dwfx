"""The app's look: one font (Inter, bundled), grayscale colours, light and dark.

All colours live in the two palettes below; the style sheet is written from
them, so light and dark always match. The coloured page markers (yellow,
purple, green) are not here: they carry meaning (see gui.py).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import QApplication

ASSETS = Path(__file__).resolve().parent / "assets"
FONT = "Inter"

LIGHT = {
    "window": "#F5F5F5",  # behind everything
    "surface": "#FFFFFF",  # panels, inputs, page cards
    "sunken": "#ECECEC",  # the page overview
    "border": "#E0E0E0",
    "strong_border": "#C8C8C8",
    "text": "#1F1F1F",
    "muted": "#6E6E6E",
    "hover": "#F0F0F0",
    "pressed": "#DCDCDC",  # a button while it is held down
    "primary": "#262626",  # main buttons
    "primary_hover": "#3D3D3D",
    "primary_pressed": "#000000",
    "primary_text": "#FFFFFF",
    "selection": "#262626",  # frame around selected pages
    "error": "#B3261E",
}
DARK = {
    "window": "#1E1E1E",
    "surface": "#262626",
    "sunken": "#181818",
    "border": "#353535",
    "strong_border": "#4A4A4A",
    "text": "#ECECEC",
    "muted": "#A0A0A0",
    "hover": "#2E2E2E",
    "pressed": "#404040",
    "primary": "#ECECEC",
    "primary_hover": "#D4D4D4",
    "primary_pressed": "#B0B0B0",
    "primary_text": "#1E1E1E",
    "selection": "#ECECEC",
    "error": "#F2B8B5",
}


def icon() -> QIcon:
    return QIcon(str(ASSETS / "icon.svg"))


_fonts_loaded = False


def load_fonts() -> None:
    """Make the bundled font available to the app (once)."""
    global _fonts_loaded
    if not _fonts_loaded:
        for file in sorted((ASSETS / "fonts").glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(file))
        _fonts_loaded = True


def is_dark(appearance: str) -> bool:
    """Dark colours for "dark", or for "system" when the computer is set to dark."""
    if appearance == "system":
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    return appearance == "dark"


def style_sheet(c: dict[str, str]) -> str:
    return f"""
    QWidget {{ color: {c['text']}; font-family: "{FONT}"; font-size: 10pt; }}
    QMainWindow, QDialog {{ background: {c['window']}; }}
    QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px 6px; }}

    QMenuBar {{ background: {c['window']}; padding: 2px 6px; }}
    QMenuBar::item {{ padding: 4px 10px; border-radius: 6px; }}
    QMenuBar::item:selected {{ background: {c['hover']}; }}
    QMenu {{ background: {c['surface']}; border: 1px solid {c['border']}; padding: 4px; border-radius: 8px; }}
    QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {c['hover']}; }}

    QLabel#title {{ font-size: 13pt; font-weight: 600; }}
    QLabel#heading {{ font-weight: 600; margin-top: 6px; }}
    QLabel#muted, QLabel#legend {{ color: {c['muted']}; }}
    QLabel#error {{ color: {c['error']}; }}
    QLabel#empty_title {{ font-size: 16pt; font-weight: 600; }}
    QLabel#empty_hint {{ color: {c['muted']}; }}

    QWidget#panel {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
    QWidget#panel QLabel {{ background: transparent; }}

    QPushButton {{
        background: {c['surface']}; border: 1px solid {c['strong_border']};
        border-radius: 8px; padding: 7px 14px;
    }}
    QPushButton:hover {{ background: {c['hover']}; }}
    QPushButton:pressed {{ background: {c['pressed']}; padding: 8px 14px 6px 14px; }}
    QPushButton:checked {{ background: {c['hover']}; border-color: {c['selection']}; }}
    QPushButton:disabled {{ color: {c['muted']}; border-color: {c['border']}; }}
    QPushButton#primary {{
        background: {c['primary']}; color: {c['primary_text']}; border: none; font-weight: 500;
    }}
    QPushButton#primary:hover {{ background: {c['primary_hover']}; }}
    QPushButton#primary:pressed {{ background: {c['primary_pressed']}; padding: 8px 14px 6px 14px; }}
    QPushButton#primary:disabled {{ background: {c['border']}; color: {c['muted']}; }}

    QLineEdit, QPlainTextEdit {{
        background: {c['surface']}; border: 1px solid {c['strong_border']};
        border-radius: 8px; padding: 6px 8px; selection-background-color: {c['strong_border']};
    }}
    QLineEdit:focus {{ border-color: {c['selection']}; }}
    QLineEdit:disabled {{ color: {c['muted']}; border-color: {c['border']}; }}

    QListWidget#overview {{ background: {c['sunken']}; border: none; border-radius: 12px; padding: 8px; }}
    QListWidget#overview::item {{
        background: {c['surface']}; border: 2px solid transparent; border-radius: 10px;
        padding: 8px; color: {c['text']};
    }}
    QListWidget#overview::item:hover {{ border-color: {c['strong_border']}; }}
    QListWidget#overview::item:selected {{ border-color: {c['selection']}; background: {c['surface']}; }}

    QGraphicsView {{ background: {c['sunken']}; border: none; border-radius: 12px; }}

    QProgressBar {{ background: {c['border']}; border: none; border-radius: 2px; max-height: 4px; }}
    QProgressBar::chunk {{ background: {c['primary']}; border-radius: 2px; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['strong_border']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {c['strong_border']}; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """


def palette(c: dict[str, str]) -> QPalette:
    """Colours for the parts a style sheet does not reach (e.g. file dialogs)."""
    p = QPalette()
    for role, key in (
        (QPalette.ColorRole.Window, "window"), (QPalette.ColorRole.Base, "surface"),
        (QPalette.ColorRole.AlternateBase, "hover"), (QPalette.ColorRole.Button, "surface"),
        (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.WindowText, "text"),
        (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.PlaceholderText, "muted"),
        (QPalette.ColorRole.Highlight, "strong_border"), (QPalette.ColorRole.HighlightedText, "text"),
        (QPalette.ColorRole.ToolTipBase, "surface"), (QPalette.ColorRole.ToolTipText, "text"),
    ):
        p.setColor(role, QColor(c[key]))
    return p


current: dict[str, str] = LIGHT  # the colours in use (for parts drawn by hand)


def apply(appearance: str) -> dict[str, str]:
    """Give the whole app the light or dark look; returns the colours used."""
    global current
    app = QApplication.instance()
    colours = current = DARK if is_dark(appearance) else LIGHT
    if app is not None:
        load_fonts()
        app.setStyle("Fusion")  # draws the same on every system, so the style sheet looks the same
        app.setPalette(palette(colours))
        app.setStyleSheet(style_sheet(colours))
        app.setFont(QFont(FONT, 10))
    return colours
