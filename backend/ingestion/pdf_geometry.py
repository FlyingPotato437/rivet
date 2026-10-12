"""Normalize PDF evidence into the visible PDF.js page viewport.

PDF.js displays the MediaBox/CropBox intersection, then rotates it. pdfplumber
text uses a rotated MediaBox coordinate system, while annotation Rect values
remain in original PDF coordinates. Both must reach the same visible frame.
"""

from math import isfinite

from pdfplumber.utils import extract_words


def oriented_words(page):
    """Keep drawing labels readable after page or individual text rotations.

    pdfplumber's defaults read all horizontal text left-to-right and vertical
    text top-to-bottom. The glyph matrix distinguishes upside-down text and
    counter-rotated drawing labels on the same page without reversing strings.
    """
    groups = {}
    for char in page.chars:
        a, b, *_ = char.get("matrix", (1, 0, 0, 1, 0, 0))
        if abs(a) >= abs(b):
            direction = "ltr" if a >= 0 else "rtl"
        else:
            direction = "ttb" if b < 0 else "btt"
        groups.setdefault(direction, []).append(char)
    words = []
    for direction, chars in groups.items():
        horizontal = direction in ("ltr", "rtl")
        options = (
            {"line_dir": "ttb" if direction == "ltr" else "btt", "char_dir": direction}
            if horizontal
            else {
                "line_dir_rotated": "rtl" if direction == "ttb" else "ltr",
                "char_dir_rotated": direction,
            }
        )
        words.extend(extract_words(chars, **options))
    return words


def rectangle(values):
    if len(values) != 4:
        raise ValueError("Invalid PDF rectangle")
    x0, y0, x1, y1 = [float(value) for value in values]
    if not all(isfinite(value) for value in (x0, y0, x1, y1)):
        raise ValueError("Invalid PDF coordinates")
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


class PDFViewport:
    def __init__(self, page):
        self.media = rectangle(page.page_obj.mediabox)
        crop = rectangle(page.page_obj.cropbox)
        self.rotation = page.rotation % 360
        if self.rotation not in (0, 90, 180, 270):
            raise ValueError("Unsupported PDF page rotation")
        if self.media[2] <= self.media[0] or self.media[3] <= self.media[1]:
            raise ValueError("Invalid PDF page dimensions")
        visible = (
            max(self.media[0], crop[0]),
            max(self.media[1], crop[1]),
            min(self.media[2], crop[2]),
            min(self.media[3], crop[3]),
        )
        if visible[2] <= visible[0] or visible[3] <= visible[1]:
            raise ValueError("PDF crop area is outside the page")
        self.visible = visible
        self.plumber_origin = page.mediabox[:2]
        # Visible bounds within the full rotated MediaBox display coordinate frame.
        self.display = self.transform_rectangle(visible, self.media)

    def point(self, x, y, basis):
        x0, y0, x1, y1 = basis
        if self.rotation == 90:
            return y - y0, x - x0
        if self.rotation == 180:
            return x1 - x, y - y0
        if self.rotation == 270:
            return y1 - y, x1 - x
        return x - x0, y1 - y

    def transform_rectangle(self, bounds, basis):
        x0, y0, x1, y1 = rectangle(bounds)
        points = [self.point(x, y, basis) for x in (x0, x1) for y in (y0, y1)]
        return (
            min(x for x, _ in points),
            min(y for _, y in points),
            max(x for x, _ in points),
            max(y for _, y in points),
        )

    def normalize(self, bounds, coordinates="pdf"):
        if coordinates == "pdf":
            displayed = self.transform_rectangle(bounds, self.media)
        elif coordinates == "plumber":
            x0, y0, x1, y1 = rectangle(bounds)
            ox, oy = self.plumber_origin
            displayed = x0 - ox, y0 - oy, x1 - ox, y1 - oy
        else:
            raise ValueError("Unknown PDF coordinate system")
        x0, y0, x1, y1 = self.display
        width, height = x1 - x0, y1 - y0
        normalized = (
            (displayed[0] - x0) / width,
            (displayed[1] - y0) / height,
            (displayed[2] - x0) / width,
            (displayed[3] - y0) / height,
        )
        return [round(max(0, min(1, value)), 6) for value in normalized]
