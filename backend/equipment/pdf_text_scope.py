"""Conservative proof that a text trace is root-page text outside any clip.

PDF vector path lists cannot locate clip starts among preceding text commands.
This bounded secondary parser reads actual q/Q, cm, W, Tr and Tm/Tj/TJ root
operators. It never walks Forms, guesses their transforms, decodes glyph outlines
or accepts text with unknown positioning. PyMuPDF remains the text/font reader.

https://pypdf.readthedocs.io/en/stable/modules/generic.html#pypdf.generic.ContentStream
"""

import io
import math

import pymupdf
from pypdf import PdfReader
from pypdf.generic import ContentStream, TextStringObject

MAX_SCOPE_BYTES = 32 * 1024 * 1024
MAX_SCOPE_OPERATORS = 100000


def unclipped_root_text(page):
    cached = getattr(page, "_rivet_root_text_scope", None)
    if cached is not None:
        return cached
    result = []
    page._rivet_root_text_scope = []
    try:
        data = page.parent.tobytes()
        if len(data) > MAX_SCOPE_BYTES:
            return result
        reader = PdfReader(io.BytesIO(data), strict=True)
        source = reader.pages[page.number]
        raw = source.get_contents()
        if raw is None or len(raw.get_data()) > 12 * 1024 * 1024:
            return result
        stream = ContentStream(raw, reader)
        if len(stream.operations) > MAX_SCOPE_OPERATORS:
            return result
        matrix, clipped, render, origin = (
            pymupdf.Matrix(1, 0, 0, 1, 0, 0),
            False,
            0,
            None,
        )
        stack = []
        for operands, operator in stream.operations:
            if operator == b"q":
                if len(stack) > 100:
                    return []
                stack.append((pymupdf.Matrix(matrix), clipped, render))
            elif operator == b"Q":
                if not stack:
                    return []
                matrix, clipped, render = stack.pop()
                origin = None
            elif operator == b"cm":
                values = [float(v) for v in operands]
                if len(values) != 6 or not all(math.isfinite(v) for v in values):
                    return []
                matrix = pymupdf.Matrix(*values) * matrix
                origin = None
            elif operator in {b"W", b"W*"}:
                clipped = True
            elif operator in {b"gs", b"BDC"}:
                # A soft mask, blend group or optional-content context needs
                # its own visibility interpreter. It cannot earn a bypass.
                clipped = True
            elif operator == b"Tr":
                render = int(operands[0])
            elif operator in {b"BT", b"ET", b"Td", b"TD", b"T*", b"'", b'"'}:
                origin = None
            elif operator == b"Tm":
                values = [float(v) for v in operands]
                if len(values) != 6 or not all(math.isfinite(v) for v in values):
                    return []
                # Only the exact trace origin is matched. No advance widths or
                # complicated text positioning are reconstructed here.
                point = (
                    pymupdf.Point(values[4], values[5])
                    * matrix
                    * page.transformation_matrix
                )
                origin = [point.x, point.y]
            elif operator in {b"Tj", b"TJ"}:
                items = operands[0] if operator == b"TJ" else operands
                if isinstance(items, (str, bytes)):
                    items = [items]
                strings = [str(v) for v in items if isinstance(v, TextStringObject)]
                if strings and origin is not None and not clipped and render in {0, 1}:
                    result.append({"text": "".join(strings), "origin": origin})
                if render >= 4:
                    clipped = True
                origin = None
        if stack:
            return []
        page._rivet_root_text_scope = result
        return result
    except Exception:  # noqa: BLE001 - uncertain secondary parsing provides no bypass.
        return []


def proven_unclipped_trace(page, span):
    chars = span.get("chars", [])
    if not chars:
        return False
    text = "".join(chr(c[0]) for c in chars)
    origin = chars[0][2]
    return any(
        text == item["text"] and math.dist(origin, item["origin"]) < 0.1
        for item in unclipped_root_text(page)
    )
