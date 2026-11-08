"""The app window: open a PDF, check the scale of every page, convert.

All pages are shown side by side, tinted by the state of their scale:
yellow = suggested by the program, purple = no scale found (measures as
paper, mm), green = set or checked by the user. A warning sign marks
pages that show more than one scale value. Select one or more pages to
set a scale for all of them at once, or accept the suggestion.

Only window code lives here; reading scales and converting is done by
the converter core (suggest.py, convert.py), which knows nothing of the
window. Slow work (reading pages, converting) runs in a background
thread so the window stays responsive. Each thread opens the PDF itself:
PyMuPDF documents must not be shared between threads.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .convert import convert
from .scale import CONFIRMED, SUGGESTED, PageScale, confirmed, parse_scale, with_sheet_scale
from .suggest import suggest

THUMBNAIL = 220  # longest side of a page picture, in pixels
WARNING = "⚠️"

# Overlay colours (red, green, blue, opacity 0-255).
COLOURS = {
    SUGGESTED: QColor(255, 200, 0, 70),  # yellow: suggestion
    "none": QColor(150, 60, 220, 70),  # purple: no scale found
    CONFIRMED: QColor(40, 170, 60, 70),  # green: checked by the user
}
STATUS_TEXT = {
    SUGGESTED: "suggested, please check",
    "none": "no scale found: measures as paper (mm)",
    CONFIRMED: "checked by you",
}


@dataclass
class LoadedPage:
    number: int  # counting from 1
    picture: QImage
    size: tuple[float, float]  # sheet width, height in points
    scale: PageScale


def load_pages(pdf: Path) -> Iterator[LoadedPage]:
    """Picture and suggested scale of each page, one page at a time."""
    with pymupdf.open(pdf) as doc:
        for index in range(doc.page_count):
            page = doc[index]
            zoom = THUMBNAIL / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            # .copy(): the picture must own its pixels, the pixmap goes away.
            picture = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            yield LoadedPage(index + 1, picture, (page.rect.width, page.rect.height), suggest(page))


def item_text(number: int, scale: PageScale) -> str:
    """The caption under a page picture, e.g. "3 · 1:100 ⚠️"."""
    text = f"{number} · {scale.describe()}"
    return f"{text} {WARNING}" if scale.warning else text


def page_icon(page: LoadedPage, scale: PageScale) -> QPixmap:
    """The page picture tinted by status, with its viewports outlined."""
    pixmap = QPixmap.fromImage(page.picture)
    painter = QPainter(pixmap)
    painter.fillRect(pixmap.rect(), COLOURS.get(scale.status, COLOURS["none"]))
    # Viewports: areas with their own scale (box in points from top-left).
    factor = pixmap.width() / page.size[0]
    painter.setPen(QPen(QColor(0, 90, 200), 2))
    for vp in scale.viewports:
        x0, y0, x1, y1 = (c * factor for c in vp.box)
        painter.drawRect(int(x0), int(y0), int(x1 - x0), int(y1 - y0))
    painter.end()
    return pixmap


class Loader(QThread):
    """Reads pages in the background and hands them over one by one."""

    page_ready = Signal(object)  # LoadedPage
    failed = Signal(str)

    def __init__(self, pdf: Path) -> None:
        super().__init__()
        self.pdf = pdf

    def run(self) -> None:
        try:
            for page in load_pages(self.pdf):
                if self.isInterruptionRequested():
                    return
                self.page_ready.emit(page)
        except (OSError, ValueError, RuntimeError) as error:
            self.failed.emit(str(error))


class Converter(QThread):
    """Converts in the background, reporting progress."""

    progress = Signal(int, int)  # done, total
    done = Signal(list)  # warnings
    failed = Signal(str)

    def __init__(self, pdf: Path, dwfx: Path, scales: dict[int, PageScale]) -> None:
        super().__init__()
        self.pdf, self.dwfx, self.scales = pdf, dwfx, scales

    def run(self) -> None:
        try:
            warnings = convert(self.pdf, self.dwfx, sorted(self.scales), self.scales, self.progress.emit)
        except (OSError, ValueError, RuntimeError) as error:
            self.failed.emit(str(error))
        else:
            self.done.emit(warnings)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"pdf2dwfx {__version__}")
        self.resize(1200, 800)
        self.pdf: Path | None = None
        self.pages: dict[int, LoadedPage] = {}
        self.scales: dict[int, PageScale] = {}
        self.loader: Loader | None = None
        self.converter: Converter | None = None

        # Top: open a PDF.
        self.open_button = QPushButton("Open PDF…")
        self.open_button.clicked.connect(self.choose_pdf)
        self.file_label = QLabel("No PDF open")
        top = QHBoxLayout()
        top.addWidget(self.open_button)
        top.addWidget(self.file_label, 1)

        # Centre: all pages side by side.
        self.overview = QListWidget()
        self.overview.setViewMode(QListWidget.ViewMode.IconMode)
        self.overview.setIconSize(QSize(THUMBNAIL, THUMBNAIL))
        self.overview.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.overview.setMovement(QListWidget.Movement.Static)
        self.overview.setSpacing(8)
        self.overview.setWordWrap(True)
        self.overview.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # A clear frame around selected pages (the default highlight is faint).
        self.overview.setStyleSheet(
            "QListWidget::item { border: 3px solid transparent; }"
            "QListWidget::item:selected { border: 3px solid palette(highlight); }"
        )
        self.overview.itemSelectionChanged.connect(self.show_selection)

        # Right: the scale of the selected pages.
        self.selection_label = QLabel("Select one or more pages.")
        self.selection_label.setWordWrap(True)
        self.details = QLabel("")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.scale_input = QLineEdit()
        self.scale_input.setPlaceholderText('e.g. 1:100 or 1/4" = 1\'-0"')
        self.scale_input.returnPressed.connect(self.set_scale)
        self.set_button = QPushButton("Set scale for selected pages")
        self.set_button.clicked.connect(self.set_scale)
        self.accept_button = QPushButton("Accept suggestion")
        self.accept_button.clicked.connect(self.accept_suggestion)
        self.paper_button = QPushButton("No scale (measure as paper, mm)")
        self.paper_button.clicked.connect(self.set_paper)
        self.input_error = QLabel("")
        self.input_error.setStyleSheet("color: #c00")
        self.input_error.setWordWrap(True)
        legend = QLabel(
            "Yellow: suggested scale, please check\n"
            "Purple: no scale found (measures as paper, mm)\n"
            "Green: checked by you\n"
            f"{WARNING} several scales on the page: please set it up\n"
            "Blue frame: a viewport (an area with its own scale)"
        )
        legend.setWordWrap(True)
        side = QVBoxLayout()
        side.addWidget(self.selection_label)
        side.addWidget(self.details)
        side.addSpacing(12)
        side.addWidget(QLabel("Scale:"))
        side.addWidget(self.scale_input)
        side.addWidget(self.set_button)
        side.addWidget(self.input_error)
        side.addWidget(self.accept_button)
        side.addWidget(self.paper_button)
        side.addStretch(1)
        side.addWidget(legend)
        side_panel = QWidget()
        side_panel.setLayout(side)
        side_panel.setFixedWidth(300)

        middle = QHBoxLayout()
        middle.addWidget(self.overview, 1)
        middle.addWidget(side_panel)

        # Bottom: convert.
        self.progress = QProgressBar()
        self.progress.setFormat("")
        self.convert_button = QPushButton("Convert to DWFx…")
        self.convert_button.clicked.connect(self.choose_output)
        bottom = QHBoxLayout()
        bottom.addWidget(self.progress, 1)
        bottom.addWidget(self.convert_button)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addLayout(middle, 1)
        layout.addLayout(bottom)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.update_buttons()

    # Opening a PDF -------------------------------------------------------

    def choose_pdf(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF drawings (*.pdf)")
        if name:
            self.open_pdf(Path(name))

    def open_pdf(self, pdf: Path) -> None:
        self.stop_loading()
        self.pdf = pdf
        self.pages.clear()
        self.scales.clear()
        self.overview.clear()
        self.file_label.setText(f"{pdf.name}: reading pages…")
        self.progress.setRange(0, 0)  # busy, length unknown
        self.loader = Loader(pdf)
        self.loader.page_ready.connect(self.add_page)
        self.loader.failed.connect(self.loading_failed)
        self.loader.finished.connect(self.loading_finished)
        self.loader.start()
        self.update_buttons()

    def stop_loading(self) -> None:
        if self.loader is not None:
            self.loader.requestInterruption()
            self.loader.wait()
            self.loader = None

    def add_page(self, page: LoadedPage) -> None:
        self.pages[page.number] = page
        self.scales[page.number] = page.scale
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, page.number)
        self.overview.addItem(item)
        self.refresh(page.number)

    def loading_failed(self, message: str) -> None:
        self.tell("Could not open the PDF", message)

    def loading_finished(self) -> None:
        self.loader = None
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("")
        if self.pdf is not None:
            self.file_label.setText(f"{self.pdf.name}: {len(self.pages)} pages")
        self.update_buttons()

    # Pages and their scales ---------------------------------------------

    def item(self, number: int) -> QListWidgetItem:
        return self.overview.item(number - 1)

    def refresh(self, number: int) -> None:
        """Redraw one page's picture and caption after its scale changed."""
        scale = self.scales[number]
        item = self.item(number)
        item.setIcon(page_icon(self.pages[number], scale))
        item.setText(item_text(number, scale))
        item.setToolTip("\n".join([STATUS_TEXT.get(scale.status, ""), *scale.notes]))

    def selected(self) -> list[int]:
        return sorted(item.data(Qt.ItemDataRole.UserRole) for item in self.overview.selectedItems())

    def show_selection(self) -> None:
        numbers = self.selected()
        self.input_error.setText("")
        if not numbers:
            self.selection_label.setText("Select one or more pages.")
            self.details.setText("")
        elif len(numbers) == 1:
            n = numbers[0]
            scale = self.scales[n]
            self.selection_label.setText(f"Page {n}")
            lines = [scale.describe(), STATUS_TEXT.get(scale.status, "")]
            for vp in scale.viewports:
                lines.append(f"Viewport 1:{vp.scale:g}: {vp.label}")
            lines += [f"{WARNING} {note}" for note in scale.notes]
            self.details.setText("\n".join(lines))
        else:
            self.selection_label.setText(f"{len(numbers)} pages selected")
            unchecked = sum(self.scales[n].status != CONFIRMED for n in numbers)
            self.details.setText(f"{unchecked} not checked yet" if unchecked else "All checked")
        self.update_buttons()

    def change(self, make) -> None:
        """Apply ``make(old scale) -> new scale`` to every selected page."""
        for n in self.selected():
            self.scales[n] = make(self.scales[n])
            self.refresh(n)
        self.show_selection()

    def set_scale(self) -> None:
        try:
            value = parse_scale(self.scale_input.text())
        except ValueError:
            self.input_error.setText('Not a scale. Write it like 1:100, 1:50 or 1/4" = 1\'-0".')
            return
        if value is None:
            self.input_error.setText("Write a scale, or use the button for no scale.")
            return
        self.change(lambda old: with_sheet_scale(old, value))

    def set_paper(self) -> None:
        self.change(lambda old: with_sheet_scale(old, None))

    def accept_suggestion(self) -> None:
        self.change(confirmed)

    def unchecked(self) -> list[int]:
        return [n for n, s in sorted(self.scales.items()) if s.status != CONFIRMED]

    # Converting ----------------------------------------------------------

    def choose_output(self) -> None:
        if self.pdf is None:
            return
        unchecked = self.unchecked()
        if unchecked:
            answer = QMessageBox.question(
                self,
                "Scales not checked",
                f"{len(unchecked)} page(s) have scales you have not checked: "
                f"{', '.join(map(str, unchecked))}.\n\n"
                "Convert anyway, using the suggested scales?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        name, _ = QFileDialog.getSaveFileName(
            self, "Save DWFx", str(self.pdf.with_suffix(".dwfx")), "DWFx files (*.dwfx)"
        )
        if name:
            self.start_convert(Path(name))

    def start_convert(self, dwfx: Path) -> None:
        assert self.pdf is not None
        self.converter = Converter(self.pdf, dwfx, dict(self.scales))
        self.converter.progress.connect(self.show_progress)
        self.converter.done.connect(lambda warnings: self.convert_done(dwfx, warnings))
        self.converter.failed.connect(self.convert_failed)
        self.converter.finished.connect(self.convert_finished)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Converting… %p%")
        self.converter.start()
        self.update_buttons()

    def show_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def convert_done(self, dwfx: Path, warnings: list[str]) -> None:
        self.progress.setFormat("Done")
        text = f"Written: {dwfx}"
        if warnings:
            text += "\n\nNot converted (yet):\n" + "\n".join(warnings)
        self.tell("Converted", text)

    def convert_failed(self, message: str) -> None:
        self.progress.setFormat("Failed")
        self.tell("Conversion failed", message)

    def convert_finished(self) -> None:
        self.converter = None
        self.update_buttons()

    # ---------------------------------------------------------------------

    def tell(self, title: str, text: str) -> None:
        """Show a message (tests replace this to see what would be shown)."""
        QMessageBox.information(self, title, text)

    def update_buttons(self) -> None:
        busy = self.loader is not None or self.converter is not None
        has_selection = bool(self.overview.selectedItems())
        self.open_button.setEnabled(self.converter is None)
        for widget in (self.scale_input, self.set_button, self.accept_button, self.paper_button):
            widget.setEnabled(has_selection and self.converter is None)
        self.convert_button.setEnabled(self.pdf is not None and bool(self.pages) and not busy)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt's name)
        self.stop_loading()
        if self.converter is not None:
            self.converter.wait()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    """Start the app; a PDF given on the command line is opened at once."""
    argv = sys.argv if argv is None else argv
    app = QApplication.instance() or QApplication(argv)
    window = MainWindow()
    window.show()
    if len(argv) > 1:
        window.open_pdf(Path(argv[1]))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
