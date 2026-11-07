"""In-between representation of a drawing sheet.

The PDF reader fills these objects and the XPS/DWFx writers consume them.
Keeping this layer separate means the writers never touch PyMuPDF, and the
reader never needs to know anything about the DWFx format.

All coordinates are in PDF points (1/72 inch), y pointing down, with the
origin at the top-left of the (unrotated) page.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Style:
    """How a path is painted. Frozen so it can be compared and hashed,
    which lets the writer merge consecutive paths that look identical."""

    fill: str | None = None  # "#RRGGBB" or "#AARRGGBB" (with transparency)
    stroke: str | None = None
    width: float = 0.0  # stroke thickness in points
    cap: str = "Flat"  # XPS names: Flat, Round, Square
    join: str = "Miter"  # XPS names: Miter, Round, Bevel
    dashes: tuple[float, ...] = ()  # dash/gap lengths in points; empty = solid
    dash_offset: float = 0.0  # in points
    even_odd: bool = False  # fill rule; False = non-zero (PDF default)


@dataclass
class PathShape:
    data: str  # path geometry in XPS mini-language, without fill rule prefix
    style: Style
    layer: str | None = None  # PDF layer (OCG) name, used from milestone 2


@dataclass
class Group:
    """Elements that share a clipping region and/or a transparency."""

    clip: str | None = None  # clip geometry (XPS mini-language), None = no clip
    clip_even_odd: bool = False
    opacity: float = 1.0
    children: list[PathShape | Group] = field(default_factory=list)


@dataclass
class Sheet:
    title: str
    width_pt: float  # paper size as seen by the user (after page rotation)
    height_pt: float
    # Maps drawing coordinates to sheet coordinates, PyMuPDF/XPS order
    # (a, b, c, d, e, f): x' = a*x + c*y + e, y' = b*x + d*y + f.
    # Needed because PyMuPDF reports geometry of rotated pages unrotated.
    transform: tuple[float, float, float, float, float, float] = (1, 0, 0, 1, 0, 0)
    root: Group = field(default_factory=Group)

    @property
    def width_mm(self) -> float:
        return self.width_pt * 25.4 / 72

    @property
    def height_mm(self) -> float:
        return self.height_pt * 25.4 / 72
