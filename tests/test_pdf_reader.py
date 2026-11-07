"""Unit tests for turning PDF path data into XPS geometry and styles."""

import pymupdf

from pdf2dwfx.pdf_reader import _color, _dashes, fmt, path_data

P = pymupdf.Point


def test_fmt_is_compact() -> None:
    assert fmt(1.0) == "1"
    assert fmt(0.12345) == "0.123"
    assert fmt(-0.0001) == "0"
    assert fmt(1 / 3, 6) == "0.333333"


def test_connected_lines_share_one_figure() -> None:
    items = [("l", P(0, 0), P(10, 0)), ("l", P(10, 0), P(10, 5))]
    assert path_data(items, close_path=True) == "M0,0L10,0L10,5Z"


def test_disconnected_lines_start_new_figures() -> None:
    items = [("l", P(0, 0), P(10, 0)), ("l", P(0, 5), P(10, 5))]
    assert path_data(items, close_path=False) == "M0,0L10,0M0,5L10,5"


def test_curve() -> None:
    items = [("c", P(0, 0), P(1, 2), P(3, 4), P(5, 0))]
    assert path_data(items, close_path=False) == "M0,0C1,2 3,4 5,0"


def test_rectangle_is_closed() -> None:
    items = [("re", pymupdf.Rect(1, 2, 3, 4), 1)]
    assert path_data(items, close_path=False) == "M1,4H3V2H1Z"


def test_colours() -> None:
    assert _color((1, 0, 0), 1.0) == "#FF0000"
    assert _color((0.5,), None) == "#808080"  # gray
    assert _color((0, 0, 0, 1), 1.0) == "#000000"  # CMYK black
    assert _color((0, 0, 1), 0.5) == "#800000FF"  # transparent
    assert _color(None, 1.0) is None


def test_dashes() -> None:
    assert _dashes("[ 6 3 ] 0") == ((6.0, 3.0), 0.0)
    assert _dashes("[] 0") == ((), 0.0)
    assert _dashes(None) == ((), 0.0)
