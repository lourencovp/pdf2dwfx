"""The page screen: one page shown large, to set up its scales.

The user sets a scale for the page (used outside viewports; empty = paper,
mm) and draws, moves, resizes and deletes *viewports*: rectangles around
drawings that have their own scale (e.g. a 1:5 detail on a 1:50 sheet).

The scene is measured in PDF points from the sheet's top-left corner, the
same units as Viewport.box, so no conversion is needed when reading the
rectangles back. A shape belongs to the viewport its middle lies in (see
prepare.py), so rectangles need not be drawn exactly.
"""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .scale import PageScale, Viewport, format_scale, parse_scale, with_viewports

PICTURE = 2400  # longest side of the page picture, in pixels (sharp enough to zoom in)
GRIP = 8  # how close (screen pixels) to an edge the mouse must be to resize
SMALLEST = 5.0  # smallest viewport side, in points (about 2 mm)

LEFT, TOP, RIGHT, BOTTOM = "left", "top", "right", "bottom"


def render_page(page: pymupdf.Page) -> QImage:
    zoom = PICTURE / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()


def edges_near(rect: QRectF, point: QPointF, reach: float) -> frozenset[str]:
    """Which edges of ``rect`` are within ``reach`` of ``point`` (a corner gives two)."""
    if not rect.adjusted(-reach, -reach, reach, reach).contains(point):
        return frozenset()
    found = set()
    if abs(point.x() - rect.left()) <= reach:
        found.add(LEFT)
    elif abs(point.x() - rect.right()) <= reach:
        found.add(RIGHT)
    if abs(point.y() - rect.top()) <= reach:
        found.add(TOP)
    elif abs(point.y() - rect.bottom()) <= reach:
        found.add(BOTTOM)
    return frozenset(found)


def resized(rect: QRectF, edges: frozenset[str], point: QPointF) -> QRectF:
    """``rect`` with the grabbed ``edges`` moved to ``point``, never smaller
    than SMALLEST and never turned inside out."""
    r = QRectF(rect)
    if LEFT in edges:
        r.setLeft(min(point.x(), r.right() - SMALLEST))
    if RIGHT in edges:
        r.setRight(max(point.x(), r.left() + SMALLEST))
    if TOP in edges:
        r.setTop(min(point.y(), r.bottom() - SMALLEST))
    if BOTTOM in edges:
        r.setBottom(max(point.y(), r.top() + SMALLEST))
    return r


_CURSORS = {
    frozenset({LEFT}): Qt.CursorShape.SizeHorCursor,
    frozenset({RIGHT}): Qt.CursorShape.SizeHorCursor,
    frozenset({TOP}): Qt.CursorShape.SizeVerCursor,
    frozenset({BOTTOM}): Qt.CursorShape.SizeVerCursor,
    frozenset({LEFT, TOP}): Qt.CursorShape.SizeFDiagCursor,
    frozenset({RIGHT, BOTTOM}): Qt.CursorShape.SizeFDiagCursor,
    frozenset({RIGHT, TOP}): Qt.CursorShape.SizeBDiagCursor,
    frozenset({LEFT, BOTTOM}): Qt.CursorShape.SizeBDiagCursor,
}


class ViewportItem(QGraphicsRectItem):
    """A viewport on the page: drag inside to move it, drag an edge or
    corner to resize it, click to select it."""

    def __init__(self, viewport: Viewport) -> None:
        x0, y0, x1, y1 = viewport.box
        super().__init__(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
        self.scale = viewport.scale
        self.label = viewport.label
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setBrush(QBrush(QColor(0, 90, 200, 30)))
        self.caption = QGraphicsSimpleTextItem(self)
        # The caption keeps its size on screen however far the user zooms.
        self.caption.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.caption.setBrush(QColor(0, 70, 170))
        self._grabbed: frozenset[str] = frozenset()
        self.update_look()

    def update_look(self) -> None:
        selected = self.isSelected()
        pen = QPen(QColor(230, 90, 0) if selected else QColor(0, 90, 200), 2)
        pen.setCosmetic(True)  # 2 screen pixels wide at any zoom
        self.setPen(pen)
        self.caption.setText(f"1:{format_scale(self.scale)}")
        self.caption.setPos(self.rect().topLeft())

    def viewport(self) -> Viewport:
        r = self.mapRectToScene(self.rect()).normalized()
        return Viewport((r.left(), r.top(), r.right(), r.bottom()), self.scale, self.label)

    def _reach(self) -> float:
        """GRIP screen pixels, in page points at the current zoom."""
        views = self.scene().views() if self.scene() else []
        return GRIP / views[0].transform().m11() if views else GRIP

    def itemChange(self, change, value):  # noqa: N802 (Qt's name)
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update_look()
        return super().itemChange(change, value)

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        edges = edges_near(self.rect(), event.pos(), self._reach())
        self.setCursor(QCursor(_CURSORS.get(edges, Qt.CursorShape.SizeAllCursor)))
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._grabbed = edges_near(self.rect(), event.pos(), self._reach())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._grabbed:
            self.prepareGeometryChange()
            self.setRect(resized(self.rect(), self._grabbed, event.pos()))
            self.update_look()
        else:
            super().mouseMoveEvent(event)  # moves the whole viewport

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._grabbed = frozenset()
        super().mouseReleaseEvent(event)


class PageView(QGraphicsView):
    """The page, zoomed with the mouse wheel. In drawing mode, dragging
    draws a new viewport; otherwise dragging the page pans it."""

    drawn = Signal(QRectF)  # a new rectangle, in page points

    def __init__(self, scene: QGraphicsScene) -> None:
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.drawing = False
        self._start: QPointF | None = None
        self._rubber: QGraphicsRectItem | None = None
        self.set_drawing(False)

    def set_drawing(self, on: bool) -> None:
        self.drawing = on
        self.setDragMode(QGraphicsView.DragMode.NoDrag if on else QGraphicsView.DragMode.ScrollHandDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.OpenHandCursor)

    def fit(self) -> None:
        self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.drawing and event.button() == Qt.MouseButton.LeftButton:
            self._start = self.mapToScene(event.position().toPoint())
            pen = QPen(QColor(230, 90, 0), 2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._rubber = self.scene().addRect(QRectF(self._start, self._start), pen)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._rubber is not None and self._start is not None:
            point = self.mapToScene(event.position().toPoint())
            self._rubber.setRect(QRectF(self._start, point).normalized())
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._rubber is not None:
            rect = self._rubber.rect()
            self.scene().removeItem(self._rubber)
            self._rubber = self._start = None
            if rect.width() >= SMALLEST and rect.height() >= SMALLEST:
                self.drawn.emit(rect)
        else:
            super().mouseReleaseEvent(event)


class PageEditor(QDialog):
    """Set up one page's scales. After OK, ``result_scale`` holds the page's
    new scale, marked as checked by the user."""

    def __init__(self, number: int, picture: QImage, size: tuple[float, float], scale: PageScale, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Page {number}: scales")
        self.resize(1300, 850)
        self.original = scale
        self.result_scale: PageScale | None = None

        # The page picture, scaled so the scene is in points.
        self.scene = QGraphicsScene(self)
        page_item = self.scene.addPixmap(QPixmap.fromImage(picture))
        page_item.setScale(size[0] / picture.width())
        page_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        page_item.setZValue(-1)
        self.scene.setSceneRect(QRectF(0, 0, size[0], size[1]))
        self.view = PageView(self.scene)
        self.view.drawn.connect(self.add_drawn)
        self.scene.selectionChanged.connect(self.show_selected)

        # Right: the page scale and the viewports.
        self.sheet_input = QLineEdit(f"1:{format_scale(scale.scale)}" if scale.scale else "")
        self.sheet_input.setPlaceholderText("empty = no scale (paper, mm)")
        self.draw_button = QPushButton("Draw a new viewport")
        self.draw_button.setCheckable(True)
        self.draw_button.toggled.connect(self.view.set_drawing)
        self.viewport_input = QLineEdit()
        self.viewport_input.setPlaceholderText("e.g. 1:5")
        self.viewport_input.textEdited.connect(self.change_selected_scale)
        self.delete_button = QPushButton("Delete selected viewport")
        self.delete_button.clicked.connect(self.delete_selected)
        self.fit_button = QPushButton("Show whole page")
        self.fit_button.clicked.connect(self.view.fit)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #c00")
        self.error.setWordWrap(True)
        notes = QLabel("\n".join(f"⚠️ {n}" for n in scale.notes))
        notes.setWordWrap(True)
        help_text = QLabel(
            "Mouse wheel: zoom. Drag the page: move around.\n"
            "Viewports: click to select, drag to move, drag an edge or corner to resize.\n"
            "Lines belong to the viewport their middle lies in, so the rectangle need not be exact."
        )
        help_text.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save (marks the page checked)")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)

        side = QVBoxLayout()
        side.addWidget(QLabel("Scale of the page (outside viewports):"))
        side.addWidget(self.sheet_input)
        side.addSpacing(16)
        side.addWidget(self.draw_button)
        side.addWidget(QLabel("Scale of the new or selected viewport:"))
        side.addWidget(self.viewport_input)
        side.addWidget(self.delete_button)
        side.addWidget(self.error)
        side.addSpacing(16)
        side.addWidget(notes)
        side.addStretch(1)
        side.addWidget(help_text)
        side.addWidget(self.fit_button)
        side.addWidget(buttons)
        panel = QWidget()
        panel.setLayout(side)
        panel.setFixedWidth(320)
        layout = QHBoxLayout(self)
        layout.addWidget(self.view, 1)
        layout.addWidget(panel)

        for vp in scale.viewports:
            self.scene.addItem(ViewportItem(vp))
        self.show_selected()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.view.fit()  # the window has its size now

    def items(self) -> list[ViewportItem]:
        return [i for i in self.scene.items(Qt.SortOrder.AscendingOrder) if isinstance(i, ViewportItem)]

    def selected_item(self) -> ViewportItem | None:
        chosen = [i for i in self.scene.selectedItems() if isinstance(i, ViewportItem)]
        return chosen[0] if chosen else None

    def show_selected(self) -> None:
        item = self.selected_item()
        self.delete_button.setEnabled(item is not None)
        if item is not None:
            self.viewport_input.setText(f"1:{format_scale(item.scale)}")

    def _viewport_scale(self) -> float | None:
        """The scale typed for viewports, or None (with a message) if it is not one."""
        try:
            value = parse_scale(self.viewport_input.text())
        except ValueError:
            value = None
        if value is None:
            self.error.setText("Type the viewport's scale first, e.g. 1:5.")
        else:
            self.error.setText("")
        return value

    def add_drawn(self, rect: QRectF) -> None:
        self.draw_button.setChecked(False)
        scale = self._viewport_scale()
        if scale is None:
            return
        self.add_viewport(Viewport((rect.left(), rect.top(), rect.right(), rect.bottom()), scale, f"Viewport 1:{format_scale(scale)}"))

    def add_viewport(self, viewport: Viewport) -> ViewportItem:
        item = ViewportItem(viewport)
        self.scene.addItem(item)
        self.scene.clearSelection()
        item.setSelected(True)
        return item

    def change_selected_scale(self) -> None:
        item = self.selected_item()
        if item is None:
            return
        try:
            value = parse_scale(self.viewport_input.text())
        except ValueError:
            return  # still typing
        if value is not None:
            item.scale = value
            item.label = f"Viewport 1:{format_scale(value)}"
            item.update_look()

    def delete_selected(self) -> None:
        item = self.selected_item()
        if item is not None:
            self.scene.removeItem(item)
            self.show_selected()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return  # Enter after typing a scale must not close the page screen
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not self.viewport_input.hasFocus() and not self.sheet_input.hasFocus():
            self.delete_selected()
        else:
            super().keyPressEvent(event)

    def page_scale(self) -> PageScale:
        """The page as set up now; ValueError if the page scale is not a scale."""
        sheet = parse_scale(self.sheet_input.text())
        return with_viewports(self.original, sheet, [i.viewport() for i in self.items()])

    def save(self) -> None:
        try:
            self.result_scale = self.page_scale()
        except ValueError:
            self.error.setText("The page scale is not a scale. Write it like 1:100, or leave it empty for paper.")
            return
        self.accept()
