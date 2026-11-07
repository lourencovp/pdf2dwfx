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
from dataclasses import dataclass, field
import zipfile
from collections.abc import Sequence
from pathlib import Path
from xml.sax.saxutils import quoteattr

from . import __version__
from .model import Sheet
from .pdf_reader import fmt
from .prepare import UNITS_PER_MM, Prepared, prepare
from .w2x import w2x
from .xps import UNITS_TO_XPS, fixed_page, resource_dictionary

# --- Relationship types -----------------------------------------------------
REL_CORE_PROPS = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
REL_XPS_ROOT = "http://schemas.microsoft.com/xps/2005/06/fixedrepresentation"
REL_DWF_SEQUENCE = "http://schemas.autodesk.com/dwfx/2007/relationships/documentsequence"
REL_DWF_DOCUMENT = "http://schemas.autodesk.com/dwfx/2007/relationships/document"
REL_DWF_PROPERTIES = "http://schemas.autodesk.com/dwfx/2007/relationships/dwfproperties"
REL_DWF_SECTION = "http://schemas.autodesk.com/dwfx/2007/relationships/section"
REL_DWF_REQUIRED = "http://schemas.autodesk.com/dwfx/2007/relationships/requiredresource"
REL_DWF_W2X = "http://schemas.autodesk.com/dwfx/2007/relationships/graphics2dextensionresource"
REL_DWF_DICTIONARY = "http://schemas.autodesk.com/dwfx/2007/relationships/graphics2ddictionaryresource"
REL_DWF_RASTER = "http://schemas.autodesk.com/dwfx/2007/relationships/rasterreferenceresource"
REL_XPS_REQUIRED = "http://schemas.microsoft.com/xps/2005/06/required-resource"

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
DICTIONARY_TYPE = "application/vnd.ms-package.xps-resourcedictionary+xml"
IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg"}
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


@dataclass
class _Resource:
    """A file belonging to a sheet besides its page (W2X, pictures...)."""

    part: str
    role: str  # DWF role name, e.g. "2d graphics extension"
    mime: str
    relationship: str  # how the sheet's graphics refer to it
    element: str = "Resource"  # "ImageResource" for pictures
    object_id: str = field(default_factory=lambda: _guid())


def _descriptor(sheet: Sheet, order: int, fpage_part: str, resources: list[_Resource]) -> bytes:
    """The DWF sheet description: name, paper size and graphics resources.

    Like AutoCAD, every extra file (W2X companion, picture brush list,
    pictures) is listed as a resource and tied to the graphics through
    ePlot:Relationships."""
    w, h = fmt(sheet.width_mm, 6), fmt(sheet.height_mm, 6)
    listed = "".join(
        f'<ePlot:{r.element} role="{r.role}" mime="{r.mime}" href="{r.part}" objectId="{r.object_id}"/>'
        for r in resources
    )
    links = "".join(
        f'<ePlot:Relationship objectId="{r.object_id}" type="{r.relationship}"/>' for r in resources
    )
    return (
        f'<ePlot:Page xmlns:ePlot="DWF-ePlot:1.2" version="1.2" objectId="{_guid()}" '
        f'name={quoteattr(sheet.title)} plotOrder="{order}" color="128 128 128">'
        f'<ePlot:Paper units="mm" width="{w}" height="{h}" color="255 255 255" clip="0 0 {w} {h}"/>'
        f"<ePlot:Resources>{listed}"
        f'<ePlot:GraphicResource role="2d streaming graphics" mime="{FIXEDPAGE_DWF_MIME}" '
        f'href="{fpage_part}?{GRAPHICS_ID}" internalId="{GRAPHICS_ID}" objectId="{_guid()}" '
        f'transform="{_matrix_4x4()}">'
        f"<ePlot:Relationships>{links}</ePlot:Relationships></ePlot:GraphicResource>"
        "</ePlot:Resources></ePlot:Page>"
    ).encode("utf-8")


def build_parts(sheets: Sequence[Sheet]) -> dict[str, bytes]:
    """Return every part of the package as {part name: content}."""
    if not sheets:
        raise ValueError("a DWFx file needs at least one sheet")
    doc = f"/dwf/documents/{_guid()}"
    parts: dict[str, bytes] = {}

    overrides: dict[str, str] = {"/CoreProperties.xml": CORE_PROPS_TYPE}
    # (name, descriptor, fpage, resources, sheet, prepared)
    sections: list[tuple[str, str, str, list[_Resource], Sheet, Prepared]] = []
    for order, sheet in enumerate(sheets, start=1):
        name = f"com.autodesk.dwf.ePlot_{_guid()}"
        folder = f"{doc}/sections/{name}"
        descriptor = f"{folder}/descriptor.xml"
        fpage = f"{folder}/FixedPage.fpage"
        w2x_part = f"{folder}/W2X.xml"
        prepared = prepare(sheet)

        # Pictures: each distinct picture stored once, next to the page.
        sources: dict[str, str] = {}  # picture key -> "./file" (relative to the page)
        image_parts: list[tuple[str, str]] = []  # (part, ext)
        for prim in prepared.prims:
            if prim.image is not None and prim.image.key not in sources:
                file = f"{_guid()}.{prim.image.data.ext}"
                parts[f"{folder}/{file}"] = prim.image.data.data
                sources[prim.image.key] = f"./{file}"
                image_parts.append((f"{folder}/{file}", prim.image.data.ext))
        dictionary = resource_dictionary(prepared, sources)
        dictionary_part = f"{folder}/Dictionary.xml" if dictionary else None

        parts[fpage] = fixed_page(prepared, GRAPHICS_ID, "./Dictionary.xml" if dictionary else None)
        parts[w2x_part] = w2x(prepared, sources)
        resources = [_Resource(w2x_part, "2d graphics extension", "text/xml", REL_DWF_W2X)]
        if dictionary_part:
            parts[dictionary_part] = dictionary  # type: ignore[assignment]
            overrides[dictionary_part] = DICTIONARY_TYPE
            resources.append(_Resource(dictionary_part, "2d graphics dictionary", DICTIONARY_TYPE, REL_DWF_DICTIONARY))
            # XPS: the page needs its brush list and pictures as resources.
            parts[_rels_path(fpage)] = _relationships(
                [(dictionary_part, REL_XPS_REQUIRED)] + [(part, REL_XPS_REQUIRED) for part, _ in image_parts]
            )
        for part, ext in image_parts:
            resources.append(_Resource(part, "raster reference", IMAGE_MIME[ext], REL_DWF_RASTER, "ImageResource"))
        parts[descriptor] = _descriptor(sheet, order, fpage, resources)
        parts[_rels_path(descriptor)] = _relationships(
            [rel for r in resources for rel in ((r.part, REL_DWF_REQUIRED), (r.part, r.relationship))]
        )
        sections.append((name, descriptor, fpage, resources, sheet, prepared))

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
        + "".join(f'<dwf:Resource role="{r.role}" mime="{r.mime}" href="{r.part}"/>' for r in res)
        + f'<dwf:Resource role="descriptor" mime="text/xml" href="{descriptor}"/>'
        "</dwf:Toc></dwf:Section>"
        for name, descriptor, fpage, res, s, _ in sections
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
        f"{defaults}"
        + "".join(f'<Override PartName="{part}" ContentType="{ctype}"/>' for part, ctype in overrides.items())
        + "</Types>"
    ).encode("utf-8")
    return parts


def write_dwfx(path: Path, sheets: Sequence[Sheet]) -> None:
    parts = build_parts(sheets)
    # The content-types file goes first, as AutoCAD and most OPC tools do.
    order = ["/[Content_Types].xml"] + [p for p in parts if p != "/[Content_Types].xml"]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in order:
            z.writestr(name.lstrip("/"), parts[name])
