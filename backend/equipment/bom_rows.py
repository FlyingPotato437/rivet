"""Bounded observations of printed BOM rows, separate from equipment facts.

An item number is not a device tag or a quantity. These readers retain exact
cells and missing identity without promoting procurement text to design state.
PDF rows require selectable visible text and an actual ruled table; scans and
unruled/ambiguous layouts remain unsupported here.
"""

import hashlib
import re
from itertools import pairwise

MAX_ROWS = 10000
MAX_WORDS = 20000
MAX_GRID_LINES = 2000
MAX_VECTOR_ITEMS = 20000
MAX_CELL_CHARS = 12000
MAX_ASSOCIATION_WORK = 2000000

ALIASES = {
    "item_number": ("item", "item number", "item no", "item no.", "line item", "no."),
    "device_tag": (
        "equipment tag",
        "equipment_tag",
        "device tag",
        "device_tag",
        "tag",
        "tag1",
    ),
    "description": (
        "description",
        "item description",
        "material description",
        "equipment description",
        "part description",
        "work description",
    ),
    "part_number": (
        "part number",
        "part no",
        "part no.",
        "catalog number",
        "catalog no",
        "catalog no.",
        "cat no",
        "p/n",
    ),
    "quantity": ("quantity", "qty", "qty.", "no required", "number required"),
    "unit": ("unit", "units", "uom", "unit of measure"),
    "supplied_by": ("supplied by", "supplier", "provided by", "furnished by"),
    "manufacturer": ("manufacturer", "mfr", "make"),
    "circuit_number": ("circuit", "circuit number", "circuit no", "ckt", "ckt no"),
    "location": ("location", "installed location"),
}


def _key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


HEADERS = {_key(label): field for field, labels in ALIASES.items() for label in labels}


def _header(value):
    return HEADERS.get(_key(value))


def _table_header(values):
    mapped = [_header(value) for value in values]
    fields = {field for field in mapped if field}
    return (
        mapped
        if (
            len(fields) >= 2
            and fields & {"description", "part_number"}
            and fields
            & {
                "item_number",
                "device_tag",
                "quantity",
                "unit",
                "supplied_by",
                "manufacturer",
                "circuit_number",
            }
        )
        else None
    )


def _warning(code, message):
    return {"code": code, "message": message}


def _row(
    source,
    cells,
    headers,
    *,
    row,
    page=None,
    sheet=None,
    bbox=None,
    table=1,
    extra_warnings=(),
):
    fields = {}
    warnings = list(extra_warnings)
    for cell in cells:
        field = _header(cell["header"])
        if not field:
            continue
        text = cell["text"]
        value = text
        if text.startswith("="):
            value = None
            warnings.append(
                _warning(
                    "formula_not_evaluated",
                    "The formula is retained as source text; its calculated value is not established.",
                )
            )
        elif field == "item_number" and re.fullmatch(r"\d+", text):
            value = int(text)
        if field in fields:
            warnings.append(
                _warning(
                    "duplicate_raw_column",
                    "Repeated columns remain separate cells; no conflicting value was selected.",
                )
            )
            fields[field] = None
        else:
            fields[field] = value
    if not any(fields.get(field) for field in ("description", "part_number")):
        return None
    # A section heading in one merged or solitary cell is not a BOM item.
    if sum(bool(cell["text"]) and bool(_header(cell["header"])) for cell in cells) < 2:
        return None
    quantity = None
    if "quantity" not in fields:
        warnings.append(
            _warning(
                "quantity_not_provided",
                "No quantity column is printed. Item numbers and units were not used as quantities.",
            )
        )
    elif fields["quantity"] is not None and re.fullmatch(
        r"\d+(?:\.\d+)?", str(fields["quantity"])
    ):
        number = float(fields["quantity"])
        quantity = int(number) if number.is_integer() else number
    else:
        warnings.append(
            _warning(
                "quantity_unknown",
                "The explicit quantity cell is empty, a formula or not a simple numeric quantity.",
            )
        )
    from .tags import TAG_FULL

    tag = fields.get("device_tag")
    device_tag = (
        tag.upper() if isinstance(tag, str) and TAG_FULL.fullmatch(tag) else None
    )
    if any(
        cell.get("merged_range") and _header(cell["header"]) == "device_tag"
        for cell in cells
    ):
        device_tag = None
    if any(
        cell.get("merged_range") and _header(cell["header"]) == "quantity"
        for cell in cells
    ):
        quantity = None
    if not device_tag:
        warnings.append(
            _warning(
                "device_identity_unknown",
                "No supported device tag is explicitly printed in a tag column. The row is not an equipment fact.",
            )
        )
    location = {"row": row, "table": table}
    if page is not None:
        location.update(page=page, bbox=bbox, coordinate_system="normalized_top_left")
    if sheet is not None:
        location["sheet"] = sheet
    identifier = hashlib.sha256(
        repr((source.id, source.sha256.lower(), location, cells)).encode()
    ).hexdigest()[:24]
    return {
        "id": identifier,
        "source_id": source.id,
        "source_name": source.name,
        "sha256": source.sha256.lower(),
        "role": source.role,
        "revision": source.revision,
        **location,
        "raw_headers": list(headers),
        "cells": cells,
        "fields": fields,
        "device_tag": device_tag,
        "quantity": quantity,
        "status": "observed_row",
        "confidence": "source_text",
        "warnings": warnings,
    }


def tabular_rows(source, rows, *, sheet, merges=()):
    """Keep actual worksheet/CSV row numbers, cells, formulas and merge gaps."""
    from openpyxl.utils import get_column_letter

    result, headers, mapped = [], None, None
    table = 0
    for rownum, values in rows:
        if rownum > MAX_ROWS or len(values) > 100:
            break
        texts = [str(value).strip() if value is not None else "" for value in values]
        if any(len(text) > MAX_CELL_CHARS for text in texts):
            raise ValueError("bom_table_cell_limit")
        candidate = _table_header(texts)
        if candidate:
            headers, mapped = texts, candidate
            table += 1
            continue
        if headers is None:
            continue
        # Do not silently inherit labels or quantities across merged rows.
        warnings = []
        cells = []
        for i, header in enumerate(headers):
            text = texts[i] if i < len(texts) else ""
            cell = {
                "column": i + 1,
                "header": header,
                "text": text,
                "cell": f"{get_column_letter(i + 1)}{rownum}",
            }
            merge = next(
                (m for m in merges if m[0] <= i + 1 <= m[2] and m[1] <= rownum <= m[3]),
                None,
            )
            if merge:
                cell["merged_range"] = (
                    f"{get_column_letter(merge[0])}{merge[1]}:{get_column_letter(merge[2])}{merge[3]}"
                )
                if mapped[i]:
                    warnings.append(
                        _warning(
                            "merged_raw_cell",
                            "A merged source cell spans columns or rows; blank cells were not forward-filled.",
                        )
                    )
            cells.append(cell)
        item = _row(
            source,
            cells,
            headers,
            row=rownum,
            sheet=sheet,
            table=table,
            extra_warnings=warnings,
        )
        if item:
            result.append(item)
    return result


def _text(words):
    """Reading order within one visible cell, preserving original glyph text."""
    if not words:
        return ""
    lines = []
    for word in sorted(words, key=lambda w: (w["bbox"][1], w["bbox"][0])):
        cy = word["center"][1]
        line = next(
            (
                line
                for line in lines
                if abs(line[0] - cy) <= max(1, word["height"] * 0.35)
            ),
            None,
        )
        if line is None:
            line = [cy, []]
            lines.append(line)
        line[1].append(word)
    text_lines = []
    for _, words_in_line in sorted(lines):
        parts, previous = [], None
        for word in sorted(words_in_line, key=lambda w: w["bbox"][0]):
            if previous and word["bbox"][0] - previous["bbox"][2] > max(
                0.4, word["height"] * 0.1
            ):
                parts.append(" ")
            parts.append(word["text"])
            previous = word
        text_lines.append("".join(parts))
    return " ".join(text_lines)


def _norm(box, page):
    return [
        round(max(0, min(1, value)), 6)
        for value in (
            box[0] / page.rect.width,
            box[1] / page.rect.height,
            box[2] / page.rect.width,
            box[3] / page.rect.height,
        )
    ]


def _grid(page):
    """Actual orthogonal strokes/thin filled rules; no table inferred from prose."""
    import pymupdf

    horizontal, vertical = [], []
    paths = page.get_drawings()
    if sum(len(path.get("items", ())) for path in paths) > MAX_VECTOR_ITEMS:
        raise ValueError("bom_table_vector_limit")

    def line(a, b):
        a, b = a * page.rotation_matrix, b * page.rotation_matrix
        if abs(a.y - b.y) <= 0.5 and abs(a.x - b.x) >= 10:
            horizontal.append(
                (float((a.y + b.y) / 2), float(min(a.x, b.x)), float(max(a.x, b.x)))
            )
        elif abs(a.x - b.x) <= 0.5 and abs(a.y - b.y) >= 10:
            vertical.append(
                (float((a.x + b.x) / 2), float(min(a.y, b.y)), float(max(a.y, b.y)))
            )

    for path in paths:
        for item in path.get("items", ()):
            if item[0] == "l" and path.get("type") in {"s", "fs"}:
                line(item[1], item[2])
            elif item[0] == "re":
                r = item[1]
                if path.get("type") in {"s", "fs"}:
                    for a, b in (
                        (r.tl, r.tr),
                        (r.tr, r.br),
                        (r.br, r.bl),
                        (r.bl, r.tl),
                    ):
                        line(a, b)
                elif path.get("type") == "f" and min(r.width, r.height) <= 3:
                    if r.width <= r.height:
                        line(
                            pymupdf.Point((r.x0 + r.x1) / 2, r.y0),
                            pymupdf.Point((r.x0 + r.x1) / 2, r.y1),
                        )
                    else:
                        line(
                            pymupdf.Point(r.x0, (r.y0 + r.y1) / 2),
                            pymupdf.Point(r.x1, (r.y0 + r.y1) / 2),
                        )
    if len(horizontal) + len(vertical) > MAX_GRID_LINES:
        raise ValueError("bom_table_grid_limit")
    return horizontal, vertical


def pdf_rows(source, page, visible_words):
    """Return source-backed rows from bounded ruled selectable-text PDF tables."""
    import pymupdf

    if len(visible_words) > MAX_WORDS:
        raise ValueError("bom_table_word_limit")
    horizontal, vertical = _grid(page)
    if not horizontal or not vertical:
        return []
    words = []
    for word in visible_words:
        b = list(pymupdf.Rect(word["bbox"]) * page.rotation_matrix)
        words.append(
            {
                "text": word["text"],
                "bbox": b,
                "center": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2],
                "height": b[3] - b[1],
            }
        )
    # A header candidate must itself contain real description/part header words.
    candidates = [
        w for w in words if _key(w["text"]) in {"description", "catalog", "part"}
    ]
    seen, result, work = set(), [], 0
    for anchor in candidates:
        work += len(horizontal) * 2 + len(vertical) + len(words)
        if work > MAX_ASSOCIATION_WORK:
            raise ValueError("bom_table_association_limit")
        cx, cy = anchor["center"]
        above = [h for h in horizontal if h[1] <= cx <= h[2] and h[0] < cy]
        below = [h for h in horizontal if h[1] <= cx <= h[2] and h[0] > cy]
        if not above or not below:
            continue
        top, bottom = max(above), min(below)
        y0, y1 = top[0], bottom[0]
        left, right = max(top[1], bottom[1]), min(top[2], bottom[2])
        if y1 - y0 > max(60, anchor["height"] * 5):
            continue
        columns = sorted(
            v for v in vertical if left - 2 <= v[0] <= right + 2 and v[1] <= cy <= v[2]
        )
        unique = []
        for v in columns:
            if not unique or v[0] - unique[-1][0] > 2:
                unique.append(v)
            elif v[2] - v[1] > unique[-1][2] - unique[-1][1]:
                unique[-1] = v
        columns = unique
        if len(columns) < 3 or len(columns) > 101:
            continue
        x = [v[0] for v in columns]
        headers = [
            _text(
                [
                    w
                    for w in words
                    if x[i] < w["center"][0] < x[i + 1] and y0 < w["center"][1] < y1
                ]
            )
            for i in range(len(x) - 1)
        ]
        if not _table_header(headers):
            continue
        signature = (tuple(round(v, 1) for v in x), round(y0, 1), round(y1, 1))
        if signature in seen:
            continue
        seen.add(signature)
        end = min(v[2] for v in columns)
        boundaries = sorted(
            {
                round(h[0], 3)
                for h in horizontal
                if y1 - 1 <= h[0] <= end + 2 and h[1] <= x[0] + 2 and h[2] >= x[-1] - 2
            }
        )
        ordinal = 0
        for a, b in pairwise(boundaries):
            if b - a <= 2:
                continue
            work += len(words) * (len(x) - 1)
            if work > MAX_ASSOCIATION_WORK:
                raise ValueError("bom_table_association_limit")
            cells = [
                {
                    "column": i + 1,
                    "header": headers[i],
                    "text": _text(
                        [
                            w
                            for w in words
                            if x[i] < w["center"][0] < x[i + 1]
                            and a < w["center"][1] < b
                        ]
                    ),
                    "bbox": _norm([x[i], a, x[i + 1], b], page),
                }
                for i in range(len(x) - 1)
            ]
            if any(len(cell["text"]) > MAX_CELL_CHARS for cell in cells):
                raise ValueError("bom_table_cell_limit")
            if _table_header([cell["text"] for cell in cells]):
                break
            ordinal += 1
            item = _row(
                source,
                cells,
                headers,
                row=ordinal,
                page=page._rivet_original_page,
                bbox=_norm([x[0], a, x[-1], b], page),
                table=len(seen),
            )
            if item:
                result.append(item)
            if len(result) >= MAX_ROWS:
                raise ValueError("bom_table_row_limit")
    return result
