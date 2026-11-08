"""The window shown after a crash: the report, and what the user can do with it.

Nothing is sent by the app. "Report on GitHub" opens the user's browser on
a new GitHub issue with a short summary filled in; the user pastes the full
report (already on the clipboard) and decides whether to submit it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .crash import issue_url


class CrashDialog(QDialog):
    def __init__(self, report: str, saved: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("pdf2dwfx: something went wrong")
        self.resize(760, 520)
        self.report, self.saved = report, saved

        intro = QLabel(
            "Sorry, something unexpected went wrong. A report was saved on this computer:\n"
            f"{saved}\n\n"
            "Nothing has been sent. The report holds no drawing content, text or file names; "
            "you can read all of it below. To help fix the problem, click \"Report on GitHub\": "
            "the report is copied, and a new issue opens in your browser for you to paste it into and submit."
        )
        intro.setWordWrap(True)
        text = QPlainTextEdit(report)
        text.setReadOnly(True)
        text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        self.github_button = QPushButton("Report on GitHub")
        self.github_button.clicked.connect(self.report_on_github)
        self.copy_button = QPushButton("Copy to clipboard")
        self.copy_button.clicked.connect(self.copy)
        self.folder_button = QPushButton("Open report folder")
        self.folder_button.clicked.connect(self.open_folder)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        for button in (self.github_button, self.copy_button, self.folder_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(text, 1)
        layout.addLayout(buttons)

    def copy(self) -> None:
        QApplication.clipboard().setText(self.report)

    def report_on_github(self) -> None:
        self.copy()
        self.open_url(QUrl(issue_url(self.report)))

    def open_folder(self) -> None:
        self.open_url(QUrl.fromLocalFile(str(self.saved.parent)))

    def open_url(self, url: QUrl) -> None:
        """Hand a link or folder to the system (browser, file manager).
        Tests replace this to see what would be opened."""
        QDesktopServices.openUrl(url)


def demo() -> None:
    """Show the crash window with a pretend error, to see what users see:
    ``uv run python -m pdf2dwfx.crash_dialog``. Saves a real (harmless) report."""
    import sys

    from .crash import make_report, save_report

    app = QApplication.instance() or QApplication(sys.argv)
    try:
        raise RuntimeError("pretend crash: this is only a demonstration")
    except RuntimeError as error:
        report = make_report(error)
    CrashDialog(report, save_report(report)).exec()
    del app


if __name__ == "__main__":
    demo()
