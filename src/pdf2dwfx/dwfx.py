"""Assemble a DWFx package (a ZIP file following the OPC conventions).

A DWFx file is two things at once:

* an **XPS document** (FixedDocumentSequence -> FixedDocument -> FixedPage)
  that holds the actual graphics, and
* a **DWF document** (DWFDocumentSequence -> manifest -> one section per
  sheet) that tells Design Review about sheets, paper size and units.

Parts are linked together with OPC "relationship" files (``_rels/*.rels``).
The layout, part names and relationship types below follow DWFx files
published by AutoCAD (checked against a real AutoCAD DWFx) and the DWFx
constants in the Autodesk DWF Toolkit source (read only, nothing copied).
"""

from __future__ import annotations

import uuid
import zipfile
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import quoteattr

from . import __version__
from .model import Sheet
from .pdf_reader import fmt
from .prepare import UNITS_PER_MM, Prepared, prepare
from .w2x import w2x
from .xps import UNITS_TO_XPS, fixed_page

# --- Relationship types -----------------------------------------------------
REL_CORE_PROPS = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
REL_XPS_ROOT = "http://schemas.microsoft.com/xps/2005/06/fixedrepresentation"
REL_DWF_SEQUENCE = "http://schemas.autodesk.com/dwfx/2007/relationships/documentsequence"
REL_DWF_DOCUMENT = "http://schemas.autodesk.com/dwfx/2007/relationships/document"
REL_DWF_PROPERTIES = "http://schemas.autodesk.com/dwfx/2007/relationships/dwfproperties"
REL_DWF_SECTION = "http://schemas.autodesk.com/dwfx/2007/relationships/section"
REL_DWF_REQUIRED = "http://schemas.autodesk.com/dwfx/2007/relationships/requiredresource"
REL_DWF_W2X = "http://schemas.autodesk.com/dwfx/2007/relationships/graphics2dextensionresource"

# --- Content (MIME) types ---------------------------------------------------
DEFAULT_TYPES = {
    "rels": "application/vnd.openxmlformats-package.relationships+xml",
    "xml": "text/xml",
    "fdseq": "application/vnd.ms-package.xps-fixeddocumentsequence+xml",
    "fdoc": "application/vnd.ms-package.xps-fixeddocument+xml",
    "fpage": "application/vnd.ms-package.xps-fixedpage+xml",
    "dwfseq": "application/vnd.adsk-package.dwfx-dwfdocumentsequence+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
}
CORE_PROPS_TYPE = "application/vnd.openxmlformats-package.core-properties+xml"
# How the DWF manifest labels the XPS page that carries a sheet's graphics.
FIXEDPAGE_DWF_MIME = "application/vnd.adsk-package.dwfx-fixedpage+xml"

# Every DWF sheet section declares the "ePlot" interface with this fixed id.
EPLOT_INTERFACE_ID = "715941D4-1AC2-4545-8185-BC40E053B551"
# Name of the outer XPS canvas that holds a sheet's graphics.
GRAPHICS_ID = "dwfresource_1"

XML_DECL = '<?xml version="1.0" encoding="utf-8"?>'


def _guid() -> str:
    return str(uuid.uuid4()).upper()


def _relationships(rels: Sequence[tuple[str, str]]) -> bytes:
    """Build a .rels file from (target part, relationship type) pairs."""
    items = "".join(
        f'<Relationship Target="{target}" Id="R{i}" Type="{rel_type}"/>'
        for i, (target, rel_type) in enumerate(rels, start=1)
    )
    return (
        f"{XML_DECL}<Relationships xmlns="
        f'"http://schemas.openxmlformats.org/package/2006/relationships">{items}</Relationships>'
    ).encode("utf-8")


def _rels_path(part: str) -> str:
    """The .rels file that belongs to a part: /a/b/c.xml -> /a/b/_rels/c.xml.rels"""
    folder, _, name = part.rpartition("/")
    return f"{folder}/_rels/{name}.rels"


def _matrix_4x4() -> str:
    """Drawing-to-paper transform for the DWF sheet description.

    Drawing units are the prepared grid (micrometres); the paper is
    described in millimetres. DWF writes 2D transforms as a 4x4 matrix in
    row-vector order. Autodesk derives the page placement from this, so it
    must agree with the canvas transform in the FixedPage."""
    k = 1 / UNITS_PER_MM
    values = (k, 0, 0, 0, 0, k, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    return " ".join(fmt(v, 12) for v in values)


def _descriptor(sheet: Sheet, order: int, fpage_part: str, w2x_part: str, w2x_size: int) -> bytes:
    """The DWF sheet description: name, paper size and graphics resources.

    Like AutoCAD, the W2X companion is listed as the "2d graphics
    extension" and tied to the graphics through ePlot:Relationships."""
    w, h = fmt(sheet.width_mm, 6), fmt(sheet.height_mm, 6)
    w2x_id = _guid()
    return (
        f'<ePlot:Page xmlns:ePlot="DWF-ePlot:1.2" version="1.2" objectId="{_guid()}" '
        f'name={quoteattr(sheet.title)} plotOrder="{order}" color="128 128 128">'
        f'<ePlot:Paper units="mm" width="{w}" height="{h}" color="255 255 255" clip="0 0 {w} {h}"/>'
        "<ePlot:Resources>"
        f'<ePlot:Resource role="2d graphics extension" mime="text/xml" href="{w2x_part}" '
        f'title="W2X Resource" size="{w2x_size}" internalId="dwfresource_2" objectId="{w2x_id}"/>'
        f'<ePlot:GraphicResource role="2d streaming graphics" mime="{FIXEDPAGE_DWF_MIME}" '
        f'href="{fpage_part}?{GRAPHICS_ID}" internalId="{GRAPHICS_ID}" objectId="{_guid()}" '
        f'transform="{_matrix_4x4()}">'
        f'<ePlot:Relationships><ePlot:Relationship objectId="{w2x_id}" type="{REL_DWF_W2X}"/>'
        "</ePlot:Relationships></ePlot:GraphicResource>"
        "</ePlot:Resources></ePlot:Page>"
    ).encode("utf-8")


def build_parts(sheets: Sequence[Sheet]) -> dict[str, bytes]:
    """Return every part of the package as {part name: content}."""
    if not sheets:
        raise ValueError("a DWFx file needs at least one sheet")
    doc = f"/dwf/documents/{_guid()}"
    parts: dict[str, bytes] = {}

    # (name, descriptor, fpage, w2x, sheet, prepared)
    sections: list[tuple[str, str, str, str, Sheet, Prepared]] = []
    for order, sheet in enumerate(sheets, start=1):
        name = f"com.autodesk.dwf.ePlot_{_guid()}"
        folder = f"{doc}/sections/{name}"
        descriptor = f"{folder}/descriptor.xml"
        fpage = f"{folder}/FixedPage.fpage"
        w2x_part = f"{folder}/W2X.xml"
        prepared = prepare(sheet)
        parts[fpage] = fixed_page(prepared, GRAPHICS_ID)
        parts[w2x_part] = w2x(prepared)
        parts[descriptor] = _descriptor(sheet, order, fpage, w2x_part, len(parts[w2x_part]))
        parts[_rels_path(descriptor)] = _relationships(
            [(w2x_part, REL_DWF_REQUIRED), (w2x_part, REL_DWF_W2X)]
        )
        sections.append((name, descriptor, fpage, w2x_part, sheet, prepared))

    # --- XPS side: sequence -> document -> pages ---
    fdoc = f"{doc}/FixedDocument.fdoc"
    pages = "".join(
        f'<PageContent Source="{fpage}" Width="{fmt(pr.width_units * UNITS_TO_XPS, 6)}" '
        f'Height="{fmt(pr.height_units * UNITS_TO_XPS, 6)}"/>'
        for _, _, fpage, _, _, pr in sections
    )
    parts[fdoc] = (
        f'{XML_DECL}<FixedDocument xmlns="http://schemas.microsoft.com/xps/2005/06">'
        f"{pages}</FixedDocument>"
    ).encode("utf-8")
    parts["/FixedDocumentSequence.fdseq"] = (
        f'{XML_DECL}<FixedDocumentSequence xmlns="http://schemas.microsoft.com/xps/2005/06">'
        f'<DocumentReference Source="{fdoc}"/></FixedDocumentSequence>'
    ).encode("utf-8")

    # --- DWF side: sequence -> manifest -> sections ---
    manifest = f"{doc}/manifest.xml"
    properties = f"{doc}/DWFProperties.xml"
    section_xml = "".join(
        f'<dwf:Section type="com.autodesk.dwf.ePlot" name="{name}" title={quoteattr(s.title)}>'
        '<dwf:Source provider="pdf2dwfx"/><dwf:Toc>'
        f'<dwf:Resource role="2d streaming graphics" mime="{FIXEDPAGE_DWF_MIME}" '
        f'href="{fpage}?{GRAPHICS_ID}"/>'
        f'<dwf:Resource role="2d graphics extension" mime="text/xml" href="{w2x_part}"/>'
        f'<dwf:Resource role="descriptor" mime="text/xml" href="{descriptor}"/>'
        "</dwf:Toc></dwf:Section>"
        for name, descriptor, fpage, w2x_part, s, _ in sections
    )
    parts[manifest] = (
        f'<dwf:Manifest xmlns:dwf="DWF-Manifest:6.0" version="6.0" objectId="{_guid()}">'
        f'<dwf:Interfaces><dwf:Interface objectId="{EPLOT_INTERFACE_ID}" name="ePlot" '
        'href="http://www.autodesk.com/viewers"/></dwf:Interfaces>'
        f"<dwf:Properties/><dwf:Sections>{section_xml}</dwf:Sections></dwf:Manifest>"
    ).encode("utf-8")
    parts[_rels_path(manifest)] = _relationships(
        [(properties, REL_DWF_PROPERTIES)]
        + [(descriptor, REL_DWF_SECTION) for _, descriptor, _, _, _, _ in sections]
    )
    parts[properties] = (
        f"{XML_DECL}<DWFProperties>"
        '<Property name="SourceProductName" value="pdf2dwfx"/>'
        f'<Property name="SourceProductVersion" value="{__version__}"/>'
        '<Property name="DWFProductVendor" value="pdf2dwfx"/>'
        f'<Property name="DWFProductVersion" value="{__version__}"/>'
        '<Property name="DWFFormatVersion" value="7.00"/>'
        "</DWFProperties>"
    ).encode("utf-8")
    parts["/DWFDocumentSequence.dwfseq"] = (
        f'{XML_DECL}<DWFDocumentSequence xmlns="http://schemas.dwf.autodesk.com/dwfx/2006/11">'
        f'<ManifestReference Source="{manifest}"/></DWFDocumentSequence>'
    ).encode("utf-8")
    parts["/_rels/DWFDocumentSequence.dwfseq.rels"] = _relationships(
        [(manifest, REL_DWF_DOCUMENT)]
    )

    # --- Package root ---
    parts["/CoreProperties.xml"] = (
        f"{XML_DECL}<coreProperties "
        'xmlns="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"/>'
    ).encode("utf-8")
    parts["/_rels/.rels"] = _relationships(
        [
            ("/CoreProperties.xml", REL_CORE_PROPS),
            ("/DWFDocumentSequence.dwfseq", REL_DWF_SEQUENCE),
            ("/FixedDocumentSequence.fdseq", REL_XPS_ROOT),
        ]
    )
    defaults = "".join(
        f'<Default Extension="{ext}" ContentType="{ctype}"/>'
        for ext, ctype in DEFAULT_TYPES.items()
    )
    parts["/[Content_Types].xml"] = (
        f'{XML_DECL}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'{defaults}<Override PartName="/CoreProperties.xml" ContentType="{CORE_PROPS_TYPE}"/>'
        "</Types>"
    ).encode("utf-8")
    return parts


def write_dwfx(path: Path, sheets: Sequence[Sheet]) -> None:
    parts = build_parts(sheets)
    # The content-types file goes first, as AutoCAD and most OPC tools do.
    order = ["/[Content_Types].xml"] + [p for p in parts if p != "/[Content_Types].xml"]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in order:
            z.writestr(name.lstrip("/"), parts[name])
