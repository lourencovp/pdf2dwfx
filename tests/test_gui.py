"""The app window, run without a screen (Qt's "offscreen" mode), as in CI."""

import os
import zipfile
from pathlib import Path

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pdf2dwfx.gui import MainWindow, item_text, load_pages  # noqa: E402
from pdf2dwfx.scale import CONFIRMED, NO_SCALE, SUGGESTED  # noqa: E402
from synthetic import drawing_pdf, scale_pdf  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def two_pages(tmp_path: Path) -> Path:
    """Page 1: a 1:50 plan with a 1:5 detail (several scales). Page 2: no scale text."""
    path = tmp_path / "two.pdf"
    with pymupdf.open(scale_pdf(tmp_path / "a.pdf")) as doc, pymupdf.open(drawing_pdf(tmp_path / "b.pdf")) as other:
        doc.insert_pdf(other)
        doc.save(path)
    return path


def opened(window: MainWindow, pdf: Path, app: QApplication) -> MainWindow:
    window.open_pdf(pdf)
    assert window.loader is not None
    window.loader.wait()
    app.processEvents()  # deliver the pages the background thread sent
    return window


def select(window: MainWindow, *numbers: int) -> None:
    window.overview.clearSelection()
    for n in numbers:
        window.item(n).setSelected(True)


def test_pages_load_with_suggestions(two_pages: Path) -> None:
    pages = list(load_pages(two_pages))
    assert [p.number for p in pages] == [1, 2]
    assert pages[0].scale.status == SUGGESTED and pages[0].scale.scale == 50
    assert pages[0].scale.warning  # 1:50 and 1:5 on one page
    assert pages[1].scale.status == NO_SCALE
    assert max(pages[0].picture.width(), pages[0].picture.height()) == 220


def test_captions_show_scale_and_warning(two_pages: Path) -> None:
    first, second = load_pages(two_pages)
    assert item_text(1, first.scale).startswith("1 · 1:50")
    assert item_text(1, first.scale).endswith("⚠️")
    assert item_text(2, second.scale) == "2 · paper (mm)"


def test_window_shows_every_page(app: QApplication, two_pages: Path) -> None:
    window = opened(MainWindow(), two_pages, app)
    assert window.overview.count() == 2
    assert window.convert_button.isEnabled()
    assert not window.set_button.isEnabled()  # nothing selected yet
    assert window.unchecked() == [1, 2]


def test_set_scale_for_several_pages(app: QApplication, two_pages: Path) -> None:
    window = opened(MainWindow(), two_pages, app)
    select(window, 1, 2)
    window.scale_input.setText("1 to 100")
    window.set_scale()
    for n in (1, 2):
        s = window.scales[n]
        assert (s.scale, s.status, s.viewports) == (100, CONFIRMED, [])
    assert window.unchecked() == []
    assert "⚠️" not in window.item(1).text()


def test_accept_keeps_suggested_viewports(app: QApplication, two_pages: Path) -> None:
    window = opened(MainWindow(), two_pages, app)
    suggested = window.scales[1]
    select(window, 1)
    window.accept_suggestion()
    s = window.scales[1]
    assert s.status == CONFIRMED and s.scale == suggested.scale
    assert [v.scale for v in s.viewports] == [v.scale for v in suggested.viewports] == [5]


def test_bad_scale_is_explained(app: QApplication, two_pages: Path) -> None:
    window = opened(MainWindow(), two_pages, app)
    select(window, 2)
    window.scale_input.setText("big")
    window.set_scale()
    assert "Not a scale" in window.input_error.text()
    assert window.scales[2].status == NO_SCALE


def test_paper_button(app: QApplication, two_pages: Path) -> None:
    window = opened(MainWindow(), two_pages, app)
    select(window, 1)
    window.set_paper()
    s = window.scales[1]
    assert (s.scale, s.status, s.viewports) == (None, CONFIRMED, [])


def test_convert_from_window(app: QApplication, two_pages: Path, tmp_path: Path) -> None:
    window = opened(MainWindow(), two_pages, app)
    select(window, 2)
    window.scale_input.setText("1:200")
    window.set_scale()
    told: list[str] = []
    window.tell = lambda title, text: told.append(text)  # no message box in tests
    out = tmp_path / "out.dwfx"
    window.start_convert(out)
    assert window.converter is not None
    window.converter.wait()
    app.processEvents()
    assert window.converter is None and window.convert_button.isEnabled()
    with zipfile.ZipFile(out) as z:
        assert sum(n.endswith("FixedPage.fpage") for n in z.namelist()) == 2
    assert told and told[0].startswith(f"Written: {out}")
    assert window.progress.value() == window.progress.maximum() == 2  # one step per page


def test_page_screen_opens_with_the_page_scale(app: QApplication, two_pages: Path) -> None:
    window = opened(MainWindow(), two_pages, app)
    select(window, 1)
    assert window.page_button.isEnabled()
    editor = window.page_editor(1)
    assert editor.sheet_input.text() == "1:50" and [i.scale for i in editor.items()] == [5]
    select(window, 1, 2)
    assert not window.page_button.isEnabled()  # one page at a time
