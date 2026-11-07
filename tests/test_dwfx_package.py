"""Check the structure of the DWFx package against the rules of the format.

These checks mirror what a viewer does when opening the file: follow the
relationships from the package root down to each sheet, and make sure every
part exists, is valid XML and has a declared content type.
"""

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from pdf2dwfx.convert import convert
from synthetic import A1_MM, A3_MM, drawing_pdf, features_pdf

NS_RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
NS_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
NS_XPS = "{http://schemas.microsoft.com/xps/2005/06}"
NS_EPLOT = "{DWF-ePlot:1.2}"


def _convert(tmp_path: Path, **kwargs: object) -> zipfile.ZipFile:
    pdf = drawing_pdf(tmp_path / "in.pdf", **kwargs)  # type: ignore[arg-type]
    out = tmp_path / "out.dwfx"
    convert(pdf, out)
    return zipfile.ZipFile(out)


def _rels(z: zipfile.ZipFile, name: str) -> dict[str, str]:
    """{relationship type: target} for a .rels part."""
    root = ET.fromstring(z.read(name))
    return {r.get("Type"): r.get("Target") for r in root.iter(NS_RELS + "Relationship")}


def test_every_part_is_xml_with_a_content_type(tmp_path: Path) -> None:
    z = _convert(tmp_path)
    names = z.namelist()
    assert names[0] == "[Content_Types].xml"
    ct = ET.fromstring(z.read("[Content_Types].xml"))
    defaults = {d.get("Extension") for d in ct.iter(NS_CT + "Default")}
    overrides = {o.get("PartName") for o in ct.iter(NS_CT + "Override")}
    for name in names:
        ET.fromstring(z.read(name))  # raises if not well-formed
        if name != "[Content_Types].xml":
            assert name.rsplit(".", 1)[-1] in defaults or "/" + name in overrides, name


def test_every_relationship_target_exists(tmp_path: Path) -> None:
    z = _convert(tmp_path)
    names = set(z.namelist())
    for name in names:
        if name.endswith(".rels"):
            for target in _rels(z, name).values():
                assert target.lstrip("/") in names, f"{name} -> {target}"


def test_relationship_chain_reaches_the_sheet(tmp_path: Path) -> None:
    z = _convert(tmp_path)
    root = _rels(z, "_rels/.rels")
    # XPS side
    fdseq = root["http://schemas.microsoft.com/xps/2005/06/fixedrepresentation"]
    fdoc = ET.fromstring(z.read(fdseq.lstrip("/"))).find(NS_XPS + "DocumentReference").get("Source")
    page_ref = ET.fromstring(z.read(fdoc.lstrip("/"))).find(NS_XPS + "PageContent")
    fpage = page_ref.get("Source")
    # DWF side
    dwfseq = root["http://schemas.autodesk.com/dwfx/2007/relationships/documentsequence"]
    seq_rels = _rels(z, "_rels/" + dwfseq.lstrip("/") + ".rels")
    manifest = seq_rels["http://schemas.autodesk.com/dwfx/2007/relationships/document"]
    folder, _, file = manifest.lstrip("/").rpartition("/")
    section_rels = ET.fromstring(z.read(f"{folder}/_rels/{file}.rels"))
    descriptors = [
        r.get("Target")
        for r in section_rels.iter(NS_RELS + "Relationship")
        if r.get("Type").endswith("/section")
    ]
    assert len(descriptors) == 1
    descriptor = ET.fromstring(z.read(descriptors[0].lstrip("/")))
    graphic = descriptor.find(f"{NS_EPLOT}Resources/{NS_EPLOT}GraphicResource")
    # The sheet description must point at the very page the XPS side shows,
    # and at the named canvas inside it.
    href_part, _, canvas = graphic.get("href").partition("?")
    assert href_part == fpage
    page = ET.fromstring(z.read(fpage.lstrip("/")))
    assert page.find(NS_XPS + "Canvas").get("Name") == canvas


@pytest.mark.parametrize("size", [A1_MM, A3_MM])
def test_paper_size_is_preserved(tmp_path: Path, size: tuple[float, float]) -> None:
    z = _convert(tmp_path, size_mm=size)
    descriptor = next(n for n in z.namelist() if n.endswith("descriptor.xml"))
    paper = ET.fromstring(z.read(descriptor)).find(NS_EPLOT + "Paper")
    assert paper.get("units") == "mm"
    assert float(paper.get("width")) == pytest.approx(size[0], abs=0.01)
    assert float(paper.get("height")) == pytest.approx(size[1], abs=0.01)
    fpage = next(n for n in z.namelist() if n.endswith(".fpage"))
    page = ET.fromstring(z.read(fpage))
    assert float(page.get("Width")) == pytest.approx(size[0] / 25.4 * 96, abs=0.01)
    assert float(page.get("Height")) == pytest.approx(size[1] / 25.4 * 96, abs=0.01)


def test_rotated_page_swaps_paper_size(tmp_path: Path) -> None:
    z = _convert(tmp_path, size_mm=A3_MM, rotation=90)
    descriptor = next(n for n in z.namelist() if n.endswith("descriptor.xml"))
    paper = ET.fromstring(z.read(descriptor)).find(NS_EPLOT + "Paper")
    assert float(paper.get("width")) == pytest.approx(A3_MM[1], abs=0.01)
    assert float(paper.get("height")) == pytest.approx(A3_MM[0], abs=0.01)


@pytest.mark.parametrize("make", [drawing_pdf, features_pdf], ids=["drawing", "features"])
def test_graphics_extract_as_standalone_xml(tmp_path: Path, make) -> None:
    """Autodesk viewers cut the contents of the canvas named in the sheet
    description out of the page and parse them as a separate XML document
    (DWF Toolkit, FixedPageResourceExtractor). That piece must be valid XML
    on its own: one root element that declares the XPS namespace. When it
    was not, Design Review said "There was a problem displaying the page"."""
    pdf = make(tmp_path / "in.pdf")
    convert(pdf, tmp_path / "out.dwfx")
    z = zipfile.ZipFile(tmp_path / "out.dwfx")
    page = z.read(next(n for n in z.namelist() if n.endswith(".fpage"))).decode("utf-8")
    start = page.index('<Canvas Name="dwfresource_1"')
    inner_start = page.index(">", start) + 1
    inner_end = page.rindex("</Canvas>")  # closing tag of the named canvas
    piece = page[inner_start:inner_end]
    root = ET.fromstring(piece)  # raises if not one well-formed element
    assert root.tag == NS_XPS + "Canvas"


def _part(z: zipfile.ZipFile, suffix: str) -> str:
    return next(n for n in z.namelist() if n.endswith(suffix))


def test_w2x_companion_is_linked(tmp_path: Path) -> None:
    """Design Review refuses to show a sheet without its W2X companion
    ("2d graphics extension"), linked the way AutoCAD links it."""
    z = _convert(tmp_path)
    w2x = "/" + _part(z, "W2X.xml")
    root = ET.fromstring(z.read(w2x.lstrip("/")))
    assert root.tag == "W2X" and root.get("NamePrefix")
    # manifest lists it
    manifest = ET.fromstring(z.read(_part(z, "manifest.xml")))
    roles = {r.get("href"): r.get("role") for r in manifest.iter("{DWF-Manifest:6.0}Resource")}
    assert roles[w2x] == "2d graphics extension"
    # descriptor lists it and ties it to the graphics
    descriptor = ET.fromstring(z.read(_part(z, "descriptor.xml")))
    resource = next(
        r for r in descriptor.iter(NS_EPLOT + "Resource") if r.get("href") == w2x
    )
    link = descriptor.find(f".//{NS_EPLOT}GraphicResource/{NS_EPLOT}Relationships/{NS_EPLOT}Relationship")
    assert link.get("objectId") == resource.get("objectId")
    assert link.get("type").endswith("/graphics2dextensionresource")
    # and the descriptor's .rels points at it
    rels = _rels(z, _part(z, "_rels/descriptor.xml.rels"))
    assert w2x in rels.values()


@pytest.mark.parametrize("make", [drawing_pdf, features_pdf], ids=["drawing", "features"])
def test_page_uses_only_what_autodesk_understands(tmp_path: Path, make) -> None:
    """Autodesk's XAML translator (DWF Toolkit, XamlPathGeometry) reads only
    straight-line path commands with whole-number coordinates, and ignores
    clipping and group transparency. Anything else would be lost or garbled
    in Design Review."""
    convert(make(tmp_path / "in.pdf"), tmp_path / "out.dwfx")
    z = zipfile.ZipFile(tmp_path / "out.dwfx")
    page = ET.fromstring(z.read(_part(z, ".fpage")))
    named = page.find(NS_XPS + "Canvas")
    (inner,) = list(named)
    names = []
    for el in inner:
        assert el.tag == NS_XPS + "Path"  # no nested canvases (clips, groups)
        data = el.get("Data")
        assert re.fullmatch(r"(M-?\d+,-?\d+ L(-?\d+,-?\d+ ?)+( Z)? ?)+", data), data[:80]
        names.append(el.get("Name"))
    # Shapes are numbered 1, 2, 3... so W2X entries can refer to them.
    assert names == [f"N_{i}" for i in range(1, len(names) + 1)]


def test_contour_sets_match_shapes(tmp_path: Path) -> None:
    """A filled shape with several outlines (e.g. with a hole) needs a W2X
    Contour_Set entry with the right counts, or holes come out filled."""
    pdf = drawing_pdf(tmp_path / "in.pdf")  # contains a square with a hole
    convert(pdf, tmp_path / "out.dwfx")
    z = zipfile.ZipFile(tmp_path / "out.dwfx")
    paths = {
        el.get("Name"): el.get("Data")
        for el in ET.fromstring(z.read(_part(z, ".fpage"))).iter(NS_XPS + "Path")
    }
    sets = list(ET.fromstring(z.read(_part(z, "W2X.xml"))).iter("Contour_Set"))
    assert sets, "the square with a hole should produce a contour set"
    for entry in sets:
        data = paths[entry.get("refName")]
        outlines = data.split("M")[1:]
        assert int(entry.get("Contours")) == len(outlines)
        points = sum(len(re.findall(r"-?\d+,-?\d+", o)) + 1 for o in outlines)
        assert int(entry.get("Count")) == points


def test_part_names_are_plain_ascii(tmp_path: Path) -> None:
    # OPC part names must be valid URIs; we only ever use safe characters.
    z = _convert(tmp_path)
    for name in z.namelist():
        assert re.fullmatch(r"[A-Za-z0-9_./\[\]-]+", name), name
