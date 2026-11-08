"""Drawing scales: what a sheet measures in, as real-world metres.

A sheet can have one scale for the whole sheet (best for sheets with one
drawing) and/or *viewports*: areas with their own scale (best for sheets
with several drawings, e.g. a 1:50 plan next to a 1:5 detail). Anything
outside the viewports uses the sheet scale; without a sheet scale it
measures as paper in millimetres.

Design Review shows real-world distances when the W2X file gives, for each
area, the transform from metres to the sheet's grid (see w2x.py). The only
numbers involved are the scale and the paper distance, so the result is
the same for every paper size: 10 mm on paper at 1:100 is always 1 m.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Status of a page's scale (colours in the scale window: yellow, purple, green).
SUGGESTED = "suggested"  # the program found a scale; the user has not checked it
NO_SCALE = "none"  # nothing found: the sheet measures as paper (mm)
CONFIRMED = "confirmed"  # set or checked by the user

Box = tuple[float, float, float, float]  # x0, y0, x1, y1


@dataclass
class Viewport:
    """An area of the sheet with its own scale.

    ``box`` is on the sheet in points (1/72 inch), measured from the
    sheet's top-left corner, as the user sees the sheet (after rotation).
    ``scale`` is the number after "1:" (100 means 1:100; 0.5 means 2:1).
    """

    box: Box
    scale: float
    label: str = ""  # e.g. the drawing title it was found next to


@dataclass
class PageScale:
    scale: float | None = None  # whole-sheet scale; None = paper (mm)
    viewports: list[Viewport] = field(default_factory=list)
    status: str = NO_SCALE
    source: str = ""  # where it came from: "pdf", "text" or "user"
    notes: list[str] = field(default_factory=list)  # things the user should check
    found: list[float] = field(default_factory=list)  # every scale value seen on the page

    @property
    def several_scales(self) -> bool:
        """More than one scale value on the page."""
        return len(set(self.found)) > 1

    @property
    def warning(self) -> bool:
        """Show a warning sign: several scale values on a page the user
        has not checked yet. The user sets such pages up (sheet scale and
        viewports) and confirms them."""
        return self.status == SUGGESTED and self.several_scales

    def describe(self) -> str:
        """One line for people, e.g. "1:100 + viewports 1:5"."""
        parts = [f"1:{format_scale(self.scale)}" if self.scale else "paper (mm)"]
        if self.viewports:
            scales = sorted({v.scale for v in self.viewports})
            parts.append("viewports " + ", ".join(f"1:{format_scale(s)}" for s in scales))
        return " + ".join(parts)


def format_scale(scale: float) -> str:
    """100.0 -> "100", 2.5 -> "2.5"."""
    return f"{scale:g}"


# Imperial scales: 1/4" = 1'-0" (architectural), 1" = 20' (engineering).
# A scale is only a ratio, so these become ordinary "1:n" scales: 1/4 inch
# of paper for 12 inches (1 ft) of building is 1:48. Inch and foot marks
# come in several forms (straight and typographic quotes, primes, in/ft).
_NUMBER = r"\d+(?:[.,]\d+)?(?:(?:\s+|\s*-\s*)\d+/\d+)?|\d+/\d+"
_INCH = r"(?:\"|''|″|”|\s?in\b\.?)"
_FOOT = r"(?:'|′|’|\s?ft\b\.?)"
IMPERIAL = re.compile(
    rf"(?<![\d/.])({_NUMBER})\s*{_INCH}\s*=\s*(\d+(?:[.,]\d+)?)\s*{_FOOT}"
    rf"(?:\s*-?\s*({_NUMBER})\s*{_INCH})?",
    re.IGNORECASE,
)


def _inches(text: str) -> float:
    """ "1/4" -> 0.25, "1 1/2" or "1-1/2" -> 1.5, "3" -> 3."""
    total = 0.0
    for part in re.split(r"\s+|\s*-\s*", text.strip()):
        if "/" in part:
            a, b = part.split("/")
            total += float(a) / float(b)
        elif part:
            total += float(part.replace(",", "."))
    return total


def imperial_scale(match: re.Match[str]) -> float | None:
    """The "1:n" number of an imperial scale found with IMPERIAL."""
    paper = _inches(match.group(1))
    real = float(match.group(2).replace(",", ".")) * 12
    if match.group(3):
        real += _inches(match.group(3))
    if paper <= 0 or real <= 0:
        return None
    return real / paper


def parse_scale(text: str) -> float | None:
    """The user's way of writing a scale -> the number after "1:".

    "1:100", "1/100", "1_100", "1-100", "1 to 100", "100" -> 100;  "2:1" -> 0.5;  "paper", "0", "none" -> None
    Imperial: 1/4" = 1'-0" -> 48;  1" = 20' -> 240.
    """
    imperial = IMPERIAL.fullmatch(text.strip())
    if imperial:
        value = imperial_scale(imperial)
        if value is None:
            raise ValueError(f"not a scale: {text!r}")
        return value
    t = text.strip().lower().replace(",", ".")
    t = re.sub(r"\s+to\s+", ":", t).replace(" ", "")
    if t in ("", "0", "paper", "none", "papel", "mm"):
        return None
    for sep in (":", "/", "_", "-", "–"):
        if sep in t:
            left, _, right = t.partition(sep)
            a, b = float(left), float(right)
            if a <= 0 or b <= 0:
                raise ValueError(f"not a scale: {text!r}")
            return b / a
    value = float(t)
    if value <= 0:
        raise ValueError(f"not a scale: {text!r}")
    return value
