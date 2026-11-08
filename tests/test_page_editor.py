"""The page screen: setting a page's scale and drawing viewports."""

import os
from pathlib import Path

import pymupdf
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pdf2dwfx.convert import convert  # noqa: E402
from pdf2dwfx.page_editor import BOTTOM, LEFT, RIGHT, SMALLEST, TOP, PageEditor, edges_near, render_page, resized  # noqa: E402
from pdf2dwfx.scale import CONFIRMED, PageScale, Viewport  # noqa: E402
from pdf2dwfx.suggest import PT_PER_MM, suggest  # noqa: E402
from synthetic import scale_pdf, scale_sheet_layout  # noqa: E402
from test_scale import NS, _paths_box, _per_unit, _w2x_and_page  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sheet(tmp_path: Path) -> Path:
    return scale_pdf(tmp_path / "sheet.pdf")


def editor_for(pdf: Path, scale: PageScale | None = None) -> PageEditor:
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        picture = render_page(page)
        size = (page.rect.width, page.rect.height)
        scale = scale or suggest(page)
    return PageEditor(1, picture, size, scale)


def test_edges_near_corners_and_sides() -> None:
    r = QRectF(10, 10, 100, 50)
    assert edges_near(r, QPointF(11, 11), 3) == {LEFT, TOP}
    assert edges_near(r, QPointF(110, 40), 3) == {RIGHT}
    assert edges_near(r, QPointF(60, 59), 3) == {BOTTOM}
    assert edges_near(r, QPointF(60, 30), 3) == set()  # inside: move, not resize
    assert edges_near(r, QPointF(200, 200), 3) == set()


def test_resize_never_turns_inside_out() -> None:
    r = QRectF(10, 10, 100, 50)
    assert resized(r, frozenset({RIGHT, BOTTOM}), QPointF(150, 90)) == QRectF(10, 10, 140, 80)
    small = resized(r, frozenset({LEFT}), QPointF(500, 30))
    assert small.width() == pytest.approx(SMALLEST) and small.right() == 110


def test_editor_starts_with_the_suggestion(app: QApplication, sheet: Path) -> None:
    editor = editor_for(sheet)
    assert editor.sheet_input.text() == "1:50"
    assert [i.scale for i in editor.items()] == [5]
    result = editor.page_scale()
    assert result.status == CONFIRMED and result.source == "user"
    assert result.scale == 50 and [v.scale for v in result.viewports] == [5]


def test_draw_move_rescale_and_delete(app: QApplication, sheet: Path) -> None:
    editor = editor_for(sheet, PageScale(100))
    editor.viewport_input.setText("1:20")
    editor.add_drawn(QRectF(100, 100, 200, 150))  # as if drawn with the mouse
    (item,) = editor.items()
    assert item.viewport().box == (100, 100, 300, 250) and item.scale == 20

    item.setPos(10, -20)  # dragged
    assert editor.page_scale().viewports[0].box == (110, 80, 310, 230)

    editor.viewport_input.setText("1:25")
    editor.change_selected_scale()
    assert editor.page_scale().viewports[0].scale == 25

    editor.delete_selected()
    assert editor.page_scale().viewports == []


def test_drawing_needs_a_scale(app: QApplication, sheet: Path) -> None:
    editor = editor_for(sheet, PageScale(100))
    editor.viewport_input.setText("")
    editor.add_drawn(QRectF(100, 100, 200, 150))
    assert editor.items() == [] and "scale" in editor.error.text()


def test_empty_page_scale_means_paper(app: QApplication, sheet: Path) -> None:
    editor = editor_for(sheet)
    editor.sheet_input.setText("")
    assert editor.page_scale().scale is None
    editor.sheet_input.setText("abc")
    editor.save()
    assert editor.result_scale is None and "not a scale" in editor.error.text()


def test_viewport_drawn_by_hand_measures_true(app: QApplication, sheet: Path, tmp_path: Path) -> None:
    """A viewport drawn in the page screen measures the detail's real size
    in Design Review (grid distance / grid units per metre)."""
    editor = editor_for(sheet, PageScale(50))
    editor.viewport_input.setText("1:5")
    # The 1:5 detail of the synthetic sheet, drawn loosely around it.
    with pymupdf.open(sheet) as doc:
        detail = suggest(doc[0]).viewports[0].box
    x0, y0, x1, y1 = detail
    editor.add_drawn(QRectF(QPointF(x0 - 3 * PT_PER_MM, y0 - 3 * PT_PER_MM), QPointF(x1 + 3 * PT_PER_MM, y1 + 3 * PT_PER_MM)))
    editor.save()
    assert editor.result_scale is not None
    out = tmp_path / "out.dwfx"
    convert(sheet, out, [1], {1: editor.result_scale})
    w2x, page = _w2x_and_page(out)
    canvases = [el for el in page.find(NS + "Canvas")[0] if el.tag == NS + "Canvas"]
    syncs = {s.get("refName"): s for s in w2x.iter("RenditionSync")}
    per_metre = {_per_unit(syncs[c.get("Name")].find("Viewport").get("Transform")) for c in canvases}
    assert len(per_metre) == 1
    x0, _, x1, _ = _paths_box([el for c in canvases for el in c])
    layout = scale_sheet_layout((420, 297))
    detail_mm = layout["detail"][2] - layout["detail"][0]
    assert abs((x1 - x0) / per_metre.pop() - detail_mm * 5 / 1000) < 0.0005  # metres


def test_enter_after_typing_does_not_close(app: QApplication, sheet: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    editor = editor_for(sheet)
    editor.show()
    for box in (editor.viewport_input, editor.sheet_input):
        box.setFocus()
        QTest.keyClick(box, Qt.Key.Key_Return)
        assert editor.isVisible() and editor.result_scale is None
    editor.close()
