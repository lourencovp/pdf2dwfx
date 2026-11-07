"""PDF layers become Design Review layers (W2X Layer entries)."""

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf

from pdf2dwfx.convert import convert
from pdf2dwfx.pdf_reader import read_page
from synthetic import layers_pdf

NS_XPS = "{http://schemas.microsoft.com/xps/2005/06}"


def _layer_per_line(dwfx: Path) -> dict[str, str]:
    """{stroke colour: layer name in force} as Design Review would see it."""
    z = zipfile.ZipFile(dwfx)
    page = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".fpage"))))
    w2x = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith("W2X.xml"))))
    names: dict[str, str] = {}  # layer number -> name
    switch: dict[str, str] = {}  # shape name -> layer number
    for sync in w2x.iter("RenditionSync"):
        layer = sync.find("Layer")
        if layer is not None:
            if layer.get("Name") is not None:
                assert layer.get("Number") not in names, "a layer is named only once"
                names[layer.get("Number")] = layer.get("Name")
            switch[sync.get("refName")] = layer.get("Number")
    result, current = {}, None
    for path in page.iter(NS_XPS + "Path"):
        current = switch.get(path.get("Name"), current)
        if path.get("Stroke"):
            result[path.get("Stroke")] = names[current]
    return result


def test_layers_get_full_path_names(tmp_path: Path) -> None:
    dwfx = tmp_path / "out.dwfx"
    assert convert(layers_pdf(tmp_path / "in.pdf"), dwfx) == []
    assert _layer_per_line(dwfx) == {
        "#000000": "0",  # content outside any layer
        "#FF0000": "WALLS",  # used twice: named once, then by number
        "#009900": "FURNITURE / LED",
        "#0000FF": "LIGHTING / LED",  # same short name, kept separate
    }


def test_reader_records_full_layer_path(tmp_path: Path) -> None:
    sheet = read_page(pymupdf.open(layers_pdf(tmp_path / "in.pdf"))[0])
    by_colour = {
        child.style.stroke: child.layer
        for child in sheet.root.children
        if getattr(child, "style", None) is not None and child.style.stroke
    }
    assert by_colour == {
        "#000000": None,
        "#FF0000": "WALLS",
        "#009900": "FURNITURE / LED",
        "#0000FF": "LIGHTING / LED",
    }


def test_layers_switched_off_are_reported(tmp_path: Path) -> None:
    dwfx = tmp_path / "out.dwfx"
    warnings = convert(layers_pdf(tmp_path / "in.pdf", with_hidden=True), dwfx)
    assert warnings == ["layers switched off in the PDF (left out) (1x)"]
    assert "#FF00FF" not in _layer_per_line(dwfx)  # its content is not drawn
