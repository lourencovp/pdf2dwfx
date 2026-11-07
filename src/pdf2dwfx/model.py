"""In-between representation of a drawing sheet.

The PDF reader fills these objects and the XPS/DWFx writers consume them.
Keeping this layer separate means the writers never touch PyMuPDF, and the
reader never needs to know anything about the DWFx format.

All coordinates are in PDF points (1/72 inch) on the sheet as the user sees
it: origin at the top-left corner, y pointing down, page rotation applied.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# A 2D affine transform in PDF/XPS order (a, b, c, d, e, f):
#   x' = a*x + c*y + e,   y' = b*x + d*y + f
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


@dataclass(frozen=True)
class Style:
    """How a path is painted (immutable, so styles can be safely shared)."""

    fill: str | None = None  # "#RRGGBB" or "#AARRGGBB" (with transparency)
    stroke: str | None = None
    width: float = 0.0  # stroke thickness (in the path's own units)
    start_cap: str = "Flat"  # XPS names: Flat, Round, Square, Triangle
    end_cap: str = "Flat"
    dash_cap: str = "Flat"
    join: str = "Miter"  # XPS names: Miter, Round, Bevel
    miter_limit: float = 10.0
    dashes: tuple[float, ...] = ()  # dash/gap lengths; empty = solid line
    dash_offset: float = 0.0
    even_odd: bool = False  # fill rule; False = non-zero (PDF default)


# Path geometry: a list of steps in sheet coordinates (points):
#   ("M", x, y)                      move to
#   ("L", x, y)                      straight line to
#   ("C", x1, y1, x2, y2, x3, y3)    Bézier curve to
#   ("Z",)                           close the current outline
PathOps = list[tuple]


@dataclass
class PathShape:
    ops: PathOps
    style: Style
    layer: str | None = None  # PDF layer (OCG) name, used from milestone 2


@dataclass
class ImageShape:
    """A raster image placed on the sheet. ``transform`` maps the unit
    square (0,0)-(1,1) onto the sheet; (0,0) is the image's top-left pixel."""

    image: str  # key into Sheet.images
    transform: Matrix
    opacity: float = 1.0
    layer: str | None = None


@dataclass
class FontData:
    """A font used by text on the sheet."""

    data: bytes  # the TrueType font program embedded in the PDF
    family: str  # Windows font family name, e.g. "Arial Narrow"
    bold: bool
    italic: bool


@dataclass
class TextShape:
    """A run of text that a viewer can lay out with the font's own spacing.

    ``matrix`` (a, b, c, d) maps the font's em square (1 x 1, y down) onto
    the sheet: its length along (a, b) is the text size and direction, as in
    XPS. ``origin`` is the start of the baseline. ``glyphs`` holds the font's
    glyph numbers and ``advances`` the exact pen movement after each glyph
    (in points along the baseline), so viewers that support it can place
    every letter exactly where the PDF did.
    """

    text: str
    font: str  # key into Sheet.fonts
    origin: tuple[float, float]
    matrix: tuple[float, float, float, float]
    fill: str  # "#RRGGBB" or "#AARRGGBB"
    glyphs: list[int]
    advances: list[float]
    layer: str | None = None


@dataclass
class ImageData:
    data: bytes  # encoded file content, stored at 96 dpi
    ext: str  # "png" or "jpg"
    width: int  # pixels
    height: int


@dataclass
class Group:
    """Elements that share a clipping region and/or a transparency.

    ``offsets``: when set, the children are drawn once at each of these
    (dx, dy) positions. Used to expand repeating PDF pattern fills into
    real geometry.
    """

    # Clip outline; None = no clip, [] = empty clip (hides everything inside).
    clip: PathOps | None = None
    clip_even_odd: bool = False
    opacity: float = 1.0
    offsets: list[tuple[float, float]] | None = None
    children: list[PathShape | ImageShape | TextShape | Group] = field(default_factory=list)


@dataclass
class Sheet:
    title: str
    width_pt: float  # paper size as seen by the user (after page rotation)
    height_pt: float
    root: Group = field(default_factory=Group)
    images: dict[str, ImageData] = field(default_factory=dict)
    fonts: dict[str, FontData] = field(default_factory=dict)
    # PDF features we met but cannot (yet) reproduce, with how often. Lets
    # the program warn the user instead of silently producing a wrong sheet.
    unsupported: Counter[str] = field(default_factory=Counter)

    @property
    def width_mm(self) -> float:
        return self.width_pt * 25.4 / 72

    @property
    def height_mm(self) -> float:
        return self.height_pt * 25.4 / 72
