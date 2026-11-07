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


@dataclass
class PathShape:
    data: str  # geometry in XPS path mini-language, without fill-rule prefix
    style: Style
    # Normally None: geometry is already in sheet coordinates. Set when a
    # stroke is distorted (stretched in one direction); then the geometry
    # stays in its own coordinates and the viewer applies this transform,
    # so line thickness is distorted exactly as in the PDF.
    transform: Matrix | None = None
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

    clip: str | None = None  # clip geometry (XPS mini-language), None = no clip
    clip_even_odd: bool = False
    opacity: float = 1.0
    offsets: list[tuple[float, float]] | None = None
    children: list[PathShape | ImageShape | Group] = field(default_factory=list)


@dataclass
class Sheet:
    title: str
    width_pt: float  # paper size as seen by the user (after page rotation)
    height_pt: float
    root: Group = field(default_factory=Group)
    images: dict[str, ImageData] = field(default_factory=dict)
    # PDF features we met but cannot (yet) reproduce, with how often. Lets
    # the program warn the user instead of silently producing a wrong sheet.
    unsupported: Counter[str] = field(default_factory=Counter)

    @property
    def width_mm(self) -> float:
        return self.width_pt * 25.4 / 72

    @property
    def height_mm(self) -> float:
        return self.height_pt * 25.4 / 72
