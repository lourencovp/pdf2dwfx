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

import multiprocessing
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PySide6.QtCore import QSettings, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, theme
from .convert import convert
from .crash import make_report, save_report
from .crash_dialog import CrashDialog
from .page_editor import PageEditor, render_page
from .parallel import document, ordered_map, worker_count
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


# Light or dark window colours; "system" follows the computer's setting.
APPEARANCES = {
    "system": Qt.ColorScheme.Unknown,
    "light": Qt.ColorScheme.Light,
    "dark": Qt.ColorScheme.Dark,
}


def settings() -> QSettings:
    """The app's saved preferences, kept where each system keeps them
    (Windows registry, macOS preferences, a file on Linux); never sent anywhere."""
    return QSettings("pdf2dwfx", "pdf2dwfx")


def apply_appearance(name: str) -> None:
    QGuiApplication.styleHints().setColorScheme(APPEARANCES.get(name, APPEARANCES["system"]))


@dataclass
class LoadedPage:
    number: int  # counting from 1
    picture: QImage
    size: tuple[float, float]  # sheet width, height in points
    scale: PageScale


def read_page(doc: pymupdf.Document, number: int) -> tuple:
    """A small picture of the page (as plain pixels, so it can come back
    from a worker process), its size and its suggested scale."""
    page = doc[number - 1]
    zoom = THUMBNAIL / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return number, (pix.samples, pix.width, pix.height, pix.stride), (page.rect.width, page.rect.height), suggest(page)


def _read_page_job(job: tuple[str, int]) -> tuple:
    """read_page in a worker process (see parallel.py)."""
    path, number = job
    return read_page(document(path), number)


def load_pages(pdf: Path, workers: int | None = None) -> Iterator[LoadedPage]:
    """Picture and suggested scale of each page, in page order. Pages are
    read by several worker processes at once (``workers``; default one per
    processor core, 1 = none)."""
    with pymupdf.open(pdf) as doc:
        count = doc.page_count
        workers = workers or worker_count(count)
        if workers == 1:
            results = (read_page(doc, n) for n in range(1, count + 1))
        else:
            results = ordered_map(_read_page_job, [(str(pdf), n) for n in range(1, count + 1)], workers)
        for number, (samples, w, h, stride), size, scale in results:
            # .copy(): the picture must own its pixels.
            picture = QImage(samples, w, h, stride, QImage.Format.Format_RGB888).copy()
            yield LoadedPage(number, picture, size, scale)


NOTHING_SELECTED = "Click a page to see its scale. Ctrl-click or Shift-click to select several."


def legend_html() -> str:
    """What the page colours mean, with a small swatch of each colour."""
    rows = [
        (COLOURS[SUGGESTED], "Suggested scale: please check"),
        (COLOURS["none"], "No scale found: measures as paper (mm)"),
        (COLOURS[CONFIRMED], "Checked by you"),
    ]
    html = "".join(
        f'<span style="color: rgb({c.red()},{c.green()},{c.blue()}); font-size: 13pt;">■</span>&nbsp; {text}<br>'
        for c, text in rows
    )
    return (
        html
        + f"{WARNING}&nbsp; Several scales on the page<br>"
        + '<span style="color: rgb(0,90,200);">▢</span>&nbsp; Viewport: an area with its own scale'
    )


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


class Switch(QAbstractButton):
    """A small on/off switch (a rounded track with a sliding knob)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt's name)
        c = theme.current
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["primary"] if self.isChecked() else c["strong_border"]))
        p.drawRoundedRect(self.rect(), 11, 11)
        p.setBrush(QColor(c["primary_text"] if self.isChecked() else c["surface"]))
        # While held down the knob stretches, so the press is visible.
        knob = 22 if self.isDown() else 16
        x = self.width() - knob - 3 if self.isChecked() else 3
        p.drawRoundedRect(x, 3, knob, 16, 8, 8)
        p.end()


class Loader(QThread):
    """Reads pages in the background and hands them over one by one."""

    page_ready = Signal(object)  # LoadedPage
    failed = Signal(str)  # a PDF that cannot be opened: told plainly
    crashed = Signal(object)  # anything unexpected: a crash report

    def __init__(self, pdf: Path) -> None:
        super().__init__()
        self.pdf = pdf

    def run(self) -> None:
        try:
            pages = load_pages(self.pdf)
            for page in pages:
                if self.isInterruptionRequested():
                    pages.close()  # stops the worker processes
                    return
                self.page_ready.emit(page)
        except (OSError, ValueError, RuntimeError) as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.crashed.emit(error)


class Converter(QThread):
    """Converts in the background, reporting progress."""

    progress = Signal(int, int)  # done, total
    done = Signal(list)  # warnings
    failed = Signal(str)  # e.g. the file cannot be written: told plainly
    crashed = Signal(object)  # anything unexpected: a crash report

    def __init__(self, pdf: Path, dwfx: Path, scales: dict[int, PageScale]) -> None:
        super().__init__()
        self.pdf, self.dwfx, self.scales = pdf, dwfx, scales

    def run(self) -> None:
        try:
            warnings = convert(self.pdf, self.dwfx, sorted(self.scales), self.scales, self.progress.emit)
        except (OSError, ValueError, RuntimeError) as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.crashed.emit(error)
        else:
            self.done.emit(warnings)


class MainWindow(QMainWindow):
    def __init__(self, preferences: QSettings | None = None) -> None:
        super().__init__()
        self.preferences = preferences if preferences is not None else settings()
        self.setWindowTitle(f"pdf2dwfx {__version__}")
        self.resize(1200, 800)
        self.pdf: Path | None = None
        self.pages: dict[int, LoadedPage] = {}
        self.scales: dict[int, PageScale] = {}
        self.loader: Loader | None = None
        self.converter: Converter | None = None

        self.setWindowIcon(theme.icon())
        self.setAcceptDrops(True)  # a PDF can be dropped onto the window

        # Top: the app name, the open PDF, and the button to open one.
        title = QLabel("pdf2dwfx")
        title.setObjectName("title")
        self.file_label = QLabel("")
        self.file_label.setObjectName("muted")
        self.open_button = QPushButton("Open PDF…")
        self.open_button.clicked.connect(self.choose_pdf)
        top = QHBoxLayout()
        top.addWidget(title)
        top.addSpacing(12)
        top.addWidget(self.file_label, 1)
        dark_label = QLabel("Dark mode")
        dark_label.setObjectName("muted")
        self.dark_switch = Switch()
        self.dark_switch.setToolTip("Light or dark colours (remembered next time)")
        self.dark_switch.toggled.connect(lambda on: self.set_appearance("dark" if on else "light"))
        top.addWidget(dark_label)
        top.addWidget(self.dark_switch)
        top.addSpacing(16)
        top.addWidget(self.open_button)

        # Centre: a start screen until a PDF is open, then all pages side by side.
        empty_title = QLabel("Open a PDF drawing to start")
        empty_title.setObjectName("empty_title")
        empty_hint = QLabel("Click “Open PDF…” or drop a PDF here.\nEach page becomes a DWFx sheet with its drawing scale.")
        empty_hint.setObjectName("empty_hint")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_open = QPushButton("Open PDF…")
        empty_open.setObjectName("primary")
        empty_open.clicked.connect(self.choose_pdf)
        empty = QVBoxLayout()
        empty.addStretch(1)
        for widget in (empty_title, empty_hint, empty_open):
            empty.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
            empty.addSpacing(10)
        empty.addStretch(2)
        empty_page = QWidget()
        empty_page.setLayout(empty)

        self.overview = QListWidget()
        self.overview.setObjectName("overview")
        self.overview.setViewMode(QListWidget.ViewMode.IconMode)
        self.overview.setIconSize(QSize(THUMBNAIL, THUMBNAIL))
        self.overview.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.overview.setMovement(QListWidget.Movement.Static)
        self.overview.setSpacing(8)
        self.overview.setWordWrap(True)
        self.overview.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.overview.itemSelectionChanged.connect(self.show_selection)
        self.overview.itemDoubleClicked.connect(lambda item: self.open_page(item.data(Qt.ItemDataRole.UserRole)))
        self.centre = QStackedWidget()
        self.centre.addWidget(empty_page)
        self.centre.addWidget(self.overview)

        # Right: the scale of the selected pages.
        self.selection_label = QLabel("No page selected")
        self.selection_label.setObjectName("heading")
        self.selection_label.setWordWrap(True)
        self.details = QLabel(NOTHING_SELECTED)
        self.details.setObjectName("muted")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.scale_input = QLineEdit()
        self.scale_input.setPlaceholderText('e.g. 1:100 or 1/4" = 1\'-0"')
        self.scale_input.returnPressed.connect(self.set_scale)
        self.set_button = QPushButton("Set scale")
        self.set_button.setObjectName("primary")
        self.set_button.clicked.connect(self.set_scale)
        self.accept_button = QPushButton("Accept suggestion")
        self.accept_button.clicked.connect(self.accept_suggestion)
        self.paper_button = QPushButton("No scale (paper, mm)")
        self.paper_button.clicked.connect(self.set_paper)
        self.page_button = QPushButton("Set up viewports…")
        self.page_button.setToolTip("Open the page large to draw areas with their own scale (or double-click a page)")
        self.page_button.clicked.connect(lambda: self.open_page(self.selected()[0]))
        self.input_error = QLabel("")
        self.input_error.setObjectName("error")
        self.input_error.setWordWrap(True)
        self.input_error.hide()
        scale_heading = QLabel("Scale for the whole page")
        scale_heading.setObjectName("heading")
        more_heading = QLabel("More options")
        more_heading.setObjectName("heading")
        entry = QHBoxLayout()
        entry.addWidget(self.scale_input, 1)
        entry.addWidget(self.set_button)
        self.legend = QLabel(legend_html())
        self.legend.setObjectName("legend")
        self.legend.setWordWrap(True)
        side = QVBoxLayout()
        side.setContentsMargins(16, 16, 16, 16)
        side.setSpacing(8)
        side.addWidget(self.selection_label)
        side.addWidget(self.details)
        side.addSpacing(10)
        side.addWidget(scale_heading)
        side.addLayout(entry)
        side.addWidget(self.input_error)
        side.addWidget(more_heading)
        side.addWidget(self.accept_button)
        side.addWidget(self.paper_button)
        side.addWidget(self.page_button)
        side.addStretch(1)
        side.addWidget(self.legend)
        self.side_panel = side_panel = QWidget()
        side_panel.setObjectName("panel")
        side_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        side_panel.setLayout(side)
        side_panel.setFixedWidth(320)
        side_panel.hide()  # shown once a PDF is open

        middle = QHBoxLayout()
        middle.setSpacing(12)
        middle.addWidget(self.centre, 1)
        middle.addWidget(side_panel)

        # Bottom: what is happening, and convert.
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.convert_button = QPushButton("Convert to DWFx…")
        self.convert_button.setObjectName("primary")
        self.convert_button.clicked.connect(self.choose_output)
        progress_box = QVBoxLayout()
        progress_box.setSpacing(4)
        progress_box.addWidget(self.status)
        progress_box.addWidget(self.progress)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addLayout(progress_box, 1)
        bottom.addSpacing(12)
        bottom.addWidget(self.convert_button)
        self.bottom_bar = QWidget()
        self.bottom_bar.setLayout(bottom)
        self.bottom_bar.hide()  # shown once a PDF is open

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 8, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addLayout(middle, 1)
        layout.addWidget(self.bottom_bar)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        # Light or dark: what the user chose last time; until they choose,
        # follow the computer (and change along when it switches).
        self.appearance = str(self.preferences.value("appearance", "system"))
        self.show_appearance(self.appearance)
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _: self.show_appearance("system") if self.appearance == "system" else None
        )
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
        self.centre.setCurrentWidget(self.overview)
        self.side_panel.show()
        self.bottom_bar.show()
        self.file_label.setText(pdf.name)
        self.status.setText("Reading pages…")
        self.progress.setRange(0, 0)  # busy, length unknown
        self.loader = Loader(pdf)
        self.loader.page_ready.connect(self.add_page)
        self.loader.failed.connect(self.loading_failed)
        self.loader.crashed.connect(self.crashed)
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
        if self.pdf is not None:
            self.file_label.setText(f"{self.pdf.name} · {len(self.pages)} pages")
        self.show_counts()
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
        self.show_error("")
        if not numbers:
            self.selection_label.setText("No page selected")
            self.details.setText(NOTHING_SELECTED)
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
        self.show_counts()

    def set_scale(self) -> None:
        try:
            value = parse_scale(self.scale_input.text())
        except ValueError:
            self.show_error('Not a scale. Write it like 1:100, 1:50 or 1/4" = 1\'-0".')
            return
        if value is None:
            self.show_error("Write a scale, or use the button for no scale.")
            return
        self.change(lambda old: with_sheet_scale(old, value))

    def show_error(self, text: str) -> None:
        """A message under the scale box; the line only takes room when used."""
        self.input_error.setText(text)
        self.input_error.setVisible(bool(text))

    def set_paper(self) -> None:
        self.change(lambda old: with_sheet_scale(old, None))

    def accept_suggestion(self) -> None:
        self.change(confirmed)

    def page_editor(self, number: int) -> PageEditor:
        """The page screen for one page, with a sharp picture of it."""
        assert self.pdf is not None
        with pymupdf.open(self.pdf) as doc:
            picture = render_page(doc[number - 1])
        page = self.pages[number]
        return PageEditor(number, picture, page.size, self.scales[number], self)

    def open_page(self, number: int) -> None:
        # Only one thread may use PyMuPDF at a time: wait until reading is done.
        if self.loader is not None or self.converter is not None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            editor = self.page_editor(number)
        finally:
            QApplication.restoreOverrideCursor()
        if editor.exec() and editor.result_scale is not None:
            self.scales[number] = editor.result_scale
            self.refresh(number)
            self.show_selection()
            self.show_counts()

    def show_counts(self) -> None:
        """The status line: how many pages still need checking."""
        if self.loader is not None or self.converter is not None or not self.pages:
            return
        unchecked = len(self.unchecked())
        self.status.setText(f"{unchecked} of {len(self.pages)} pages not checked yet" if unchecked else "All pages checked")

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
        self.converter.crashed.connect(lambda error: self.crashed(error, [str(dwfx)]))
        self.converter.finished.connect(self.convert_finished)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText("Converting…")
        self.converter.start()
        self.update_buttons()

    def show_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.status.setText(f"Converting… page {done} of {total}")

    def convert_done(self, dwfx: Path, warnings: list[str]) -> None:
        self.status.setText(f"Saved {dwfx.name}")
        text = f"Written: {dwfx}"
        if warnings:
            text += "\n\nNot converted (yet):\n" + "\n".join(warnings)
        self.tell("Converted", text)

    def convert_failed(self, message: str) -> None:
        self.status.setText("Conversion failed")
        self.tell("Conversion failed", message)

    def convert_finished(self) -> None:
        self.converter = None
        self.update_buttons()

    # ---------------------------------------------------------------------

    # Dropping a PDF onto the window opens it.

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt's name)
        if self.dropped_pdf(event) is not None and self.converter is None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        pdf = self.dropped_pdf(event)
        if pdf is not None and self.converter is None:
            self.open_pdf(pdf)

    @staticmethod
    def dropped_pdf(event) -> Path | None:
        """The PDF being dropped, if it is exactly one PDF file."""
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        files = [Path(u.toLocalFile()) for u in urls if u.isLocalFile()]
        return files[0] if len(files) == 1 and files[0].suffix.lower() == ".pdf" else None

    def crashed(self, error: BaseException, private: list[str] | None = None) -> None:
        """Something unexpected went wrong: save a report and show it."""
        if self.converter is not None:
            self.status.setText("Conversion failed")
        try:
            report = make_report(error, self.pdf, private)
            saved = save_report(report)
        except Exception:  # never crash while reporting a crash
            return
        self.show_crash(report, saved)

    def show_crash(self, report: str, saved: Path) -> None:
        """Show the crash window (tests replace this)."""
        CrashDialog(report, saved, self).exec()

    def show_appearance(self, name: str) -> None:
        apply_appearance(name)
        theme.apply(name)
        self.dark_switch.blockSignals(True)  # showing the state is not a new choice
        self.dark_switch.setChecked(theme.is_dark(name))
        self.dark_switch.blockSignals(False)
        self.dark_switch.update()

    def set_appearance(self, name: str) -> None:
        self.appearance = name
        self.show_appearance(name)
        self.preferences.setValue("appearance", name)

    def tell(self, title: str, text: str) -> None:
        """Show a message (tests replace this to see what would be shown)."""
        QMessageBox.information(self, title, text)

    def update_buttons(self) -> None:
        busy = self.loader is not None or self.converter is not None
        has_selection = bool(self.overview.selectedItems())
        self.open_button.setEnabled(self.converter is None)
        for widget in (self.scale_input, self.set_button, self.accept_button, self.paper_button):
            widget.setEnabled(has_selection and self.converter is None)
        self.page_button.setEnabled(len(self.overview.selectedItems()) == 1 and not busy)
        self.convert_button.setEnabled(self.pdf is not None and bool(self.pages) and not busy)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt's name)
        self.stop_loading()
        if self.converter is not None:
            self.converter.wait()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    """Start the app; a PDF given on the command line is opened at once."""
    # Lets worker processes start inside the packaged app (see parallel.py).
    multiprocessing.freeze_support()
    argv = sys.argv if argv is None else argv
    app = QApplication.instance() or QApplication(argv)
    window = MainWindow()
    # Unexpected errors anywhere in the app open the crash window instead
    # of silently stopping. (Errors in background work are passed on by
    # Loader and Converter; Qt routes errors in the window to sys.excepthook.)
    sys.excepthook = lambda kind, error, tb: window.crashed(error)
    window.show()
    if len(argv) > 1:
        window.open_pdf(Path(argv[1]))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
