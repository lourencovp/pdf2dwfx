"""Unit tests for the PDF reading helpers."""

from pathlib import Path

import pymupdf

from pdf2dwfx.model import Group, ImageShape, PathShape
from pdf2dwfx.pdf_reader import HAIRLINE_PT, _is_uniform, _jpeg_at_96dpi, concat, fmt, geometry, read_page
from synthetic import features_pdf


def test_fmt_is_compact() -> None:
    assert fmt(1.0) == "1"
    assert fmt(0.12345) == "0.123"
    assert fmt(-0.0001) == "0"
    assert fmt(1 / 3, 6) == "0.333333"


def test_geometry_untransformed() -> None:
    ops = [("M", 0, 0), ("L", 10, 0), ("C", 1, 2, 3, 4, 5, 6), ("Z",)]
    assert geometry(ops, None) == "M0,0L10,0C1,2 3,4 5,6Z"


def test_geometry_transformed() -> None:
    ops = [("M", 1, 1), ("L", 2, 1)]
    # scale 2 then shift by (10, 20)
    assert geometry(ops, (2, 0, 0, 2, 10, 20)) == "M12,22L14,22"


def test_move_only_path_draws_nothing() -> None:
    assert geometry([("M", 1, 1)], None) == ""


def test_concat_applies_first_matrix_first() -> None:
    scale, shift = (2, 0, 0, 2, 0, 0), (1, 0, 0, 1, 5, 0)
    assert concat(scale, shift) == (2, 0, 0, 2, 5, 0)  # scale, then shift
    assert concat(shift, scale) == (2, 0, 0, 2, 10, 0)  # shift, then scale


def test_uniform_scaling_detection() -> None:
    assert _is_uniform((2, 0, 0, 2, 5, 5))
    assert _is_uniform((0, 1, -1, 0, 0, 0))  # rotation
    assert _is_uniform((1, 0, 0, -1, 0, 0))  # mirror
    assert not _is_uniform((1, 0, 0, 3, 0, 0))  # stretched


def test_jpeg_marked_96dpi() -> None:
    jpeg = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False).tobytes("jpeg")
    fixed = _jpeg_at_96dpi(jpeg)
    assert fixed is not None
    assert pymupdf.Pixmap(fixed).xres == 96
    assert _jpeg_at_96dpi(b"not a jpeg") is None


def _flatten(group: Group) -> list:
    items = []
    for child in group.children:
        items.append(child)
        if isinstance(child, Group):
            items.extend(_flatten(child))
    return items


def test_features_are_read_in_drawing_order(tmp_path: Path) -> None:
    sheet = read_page(pymupdf.open(features_pdf(tmp_path / "f.pdf"))[0])
    shapes = [i for i in _flatten(sheet.root) if not isinstance(i, Group)]
    # Eight black lines, then the photo, then the red line on top of it.
    first_image = next(n for n, s in enumerate(shapes) if isinstance(s, ImageShape))
    assert first_image == 8
    assert all(s.style.stroke == "#000000" for s in shapes[:8])
    after = shapes[first_image + 1]
    assert isinstance(after, PathShape) and after.style.stroke == "#FF0000"
    assert not sheet.unsupported, sheet.unsupported


def test_image_kinds(tmp_path: Path) -> None:
    sheet = read_page(pymupdf.open(features_pdf(tmp_path / "f.pdf"))[0])
    exts = sorted(img.ext for img in sheet.images.values())
    # The high-resolution photo stays JPEG and is stored once although used
    # twice. PNG: the small transparent picture (enlarged separately for its
    # two different sizes), the enlarged low-resolution stencil and
    # the gradient picture.
    assert exts == ["jpg", "png", "png", "png", "png"]
    placements = [i for i in _flatten(sheet.root) if isinstance(i, ImageShape)]
    # photo x3 (normal, mirrored, tiny helper copy), transparent picture x2,
    # stencil, gradient
    assert len(placements) == 7


def test_low_resolution_stencil_is_enlarged(tmp_path: Path) -> None:
    sheet = read_page(pymupdf.open(features_pdf(tmp_path / "f.pdf"))[0])
    # The 8x8 stencil covers 80 pt (~7 dpi); it is enlarged to ~300 dpi.
    sizes = sorted((img.width, img.height) for img in sheet.images.values())
    assert any(w == h and w >= 300 and w % 8 == 0 for w, h in sizes)


def test_pattern_is_expanded(tmp_path: Path) -> None:
    sheet = read_page(pymupdf.open(features_pdf(tmp_path / "f.pdf"))[0])
    tiles = [g for g in _flatten(sheet.root) if isinstance(g, Group) and g.offsets]
    assert len(tiles) == 1
    # 200 x 160 pt triangle with 12 pt tiles: well over a hundred copies.
    assert len(tiles[0].offsets) > 150


def test_hairline_gets_visible_width(tmp_path: Path) -> None:
    sheet = read_page(pymupdf.open(features_pdf(tmp_path / "f.pdf"))[0])
    strokes = [p for p in _flatten(sheet.root) if isinstance(p, PathShape) and p.style.stroke]
    # The PDF line has width 0 ("thinnest possible"); XPS needs a real width.
    assert [p.style.width for p in strokes if p.style.width < 0.1] == [HAIRLINE_PT]


def test_cmyk_colour_converted(tmp_path: Path) -> None:
    sheet = read_page(pymupdf.open(features_pdf(tmp_path / "f.pdf"))[0])
    hairline = next(
        p for p in _flatten(sheet.root) if isinstance(p, PathShape) and p.style.width == HAIRLINE_PT
    )
    # CMYK magenta + yellow: clearly red, whatever the exact colour profile.
    r, g, b = (int(hairline.style.stroke[i : i + 2], 16) for i in (1, 3, 5))
    assert r > 200 and g < 80 and b < 80
