"""Font helpers: Windows font names and embedding fonts in a DWFx.

Design Review draws text with the font *installed on the viewer's
computer*, chosen by name; it ignores fonts embedded in the file (verified
in Design Review: a font named "Arial" with an embedded Courier file was
drawn in Arial). So the name we give must be the Windows family name of
the font, e.g. "Arial Narrow" for the PDF font "ABCDEF+ArialNarrow-Bold".

The embedded font file is still needed: XPS requires it, and other XPS
viewers use it to draw every letter exactly.
"""

from __future__ import annotations

import re

# Suffixes Adobe/Microsoft add to PostScript font names that are not part
# of the Windows family name.
_NAME_SUFFIXES = ("PSMT", "MT", "PS")
_BOLD_WORDS = ("bold", "black", "heavy", "semibold", "demibold")
_ITALIC_WORDS = ("italic", "oblique")


def windows_font(pdf_name: str, bold: bool, italic: bool) -> tuple[str, bool, bool]:
    """(family, bold, italic) as Windows knows the font.

    "ABCDEF+ArialNarrow-Bold" -> ("Arial Narrow", True, False)
    "ArialMT" -> ("Arial", False, False);  "Arial,BoldItalic" -> ("Arial", True, True)
    """
    name = re.sub(r"^[A-Z]{6}\+", "", pdf_name)  # subset prefix
    # Family and style are separated by "-" (PostScript) or "," (TrueType).
    base, style = re.match(r"([^-,]*)[-,]?(.*)", name).groups()  # type: ignore[union-attr]
    style_lower = style.lower()
    bold = bold or any(w in style_lower for w in _BOLD_WORDS)
    italic = italic or any(w in style_lower for w in _ITALIC_WORDS)
    # Some PDFs write the style after a space: "Droid Sans Fallback Regular".
    words = base.split(" ")
    while len(words) > 1 and words[-1].lower() in ("regular", "normal", "roman", *_BOLD_WORDS, *_ITALIC_WORDS):
        last = words.pop().lower()
        bold = bold or last in _BOLD_WORDS
        italic = italic or last in _ITALIC_WORDS
    base = " ".join(words)
    for suffix in _NAME_SUFFIXES:
        if base.endswith(suffix) and len(base) > len(suffix):
            base = base[: -len(suffix)]
            break
    # "ArialNarrow" -> "Arial Narrow", "TimesNewRoman" -> "Times New Roman",
    # "SegoeUI" -> "Segoe UI": split where a lower-case letter meets a capital.
    family = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base).strip() or pdf_name
    return family, bold, italic


def truetype_names(data: bytes) -> tuple[str, str] | None:
    """(family, style) from a TrueType file's own name table, or None.

    This is exactly how Windows knows the font (name IDs 1 and 2, e.g.
    ("Arial Narrow", "Bold")), so it is more reliable than guessing from
    the PDF's font name, which may be shortened or abbreviated.
    """
    try:
        if data[:4] == b"ttcf" or len(data) < 12:
            return None
        num_tables = int.from_bytes(data[4:6], "big")
        for i in range(num_tables):
            entry = data[12 + 16 * i : 28 + 16 * i]
            if entry[:4] == b"name":
                offset = int.from_bytes(entry[8:12], "big")
                break
        else:
            return None
        count = int.from_bytes(data[offset + 2 : offset + 4], "big")
        strings = offset + int.from_bytes(data[offset + 4 : offset + 6], "big")
        found: dict[int, str] = {}
        english: set[int] = set()
        for i in range(count):
            rec = data[offset + 6 + 12 * i : offset + 18 + 12 * i]
            platform, encoding, lang, name_id, length, start = (
                int.from_bytes(rec[j : j + 2], "big") for j in range(0, 12, 2)
            )
            # Windows platform, Unicode encoding: the names Windows itself
            # uses. Fonts carry names in several languages; prefer US English
            # (0x0409), the name Windows matches fonts by.
            if platform == 3 and encoding in (0, 1) and name_id in (1, 2) and name_id not in english:
                if name_id in found and lang != 0x0409:
                    continue
                raw = data[strings + start : strings + start + length]
                found[name_id] = raw.decode("utf-16-be", errors="replace").strip()
                if lang == 0x0409:
                    english.add(name_id)
        if found.get(1):
            return found[1], found.get(2, "")
    except (IndexError, ValueError):
        pass
    return None


def is_truetype(data: bytes) -> bool:
    """True for TrueType/OpenType font files, the kind XPS can embed."""
    return data[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO", b"ttcf")


def obfuscate(data: bytes, guid: str) -> bytes:
    """Scramble a font file the way XPS requires for embedded fonts.

    The key is the 16 bytes of the GUID in the font's part name, in reverse
    order; the first 32 bytes of the font are XOR-ed with it (twice 16).
    Applying it again restores the original.
    """
    key = bytes.fromhex(guid.replace("-", ""))[::-1]
    head = bytes(b ^ key[i % 16] for i, b in enumerate(data[:32]))
    return head + data[32:]
