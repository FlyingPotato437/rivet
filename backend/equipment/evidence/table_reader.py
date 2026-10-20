"""Source-only ruled-table OCR candidates with measured cell locations.

This reader takes only a hash-bound PDF and physical page numbers. It does not
consume a vision transcript, answer key, region hints, catalog, or predicted
value. OCR confidence is diagnostic, not a probability of engineering truth.
All candidates on a page use the same source ancestry as other page readers.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import shutil
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageOps

from ..bom_rows import HEADERS
from .ontology import FEATURES, normalize_feature_value
from .schema import make_claim, source_ref, stable_id

VERSION = "rivet.source-ruled-table.v2"
MAX_RENDER_DIMENSION = 7000
MAX_RENDER_PIXELS = 24_000_000
MINIMUM_RENDER_DPI = 100


def _render_plan(rect, requested_dpi):
    """Fit ordinary large-format sheets within both dimension and pixel bounds.

    Coordinates are still measured against the actual rendered source page.
    A page requiring less than the declared legibility floor stays visibly
    unknown rather than silently receiving a low-resolution OCR decision.
    """
    width, height = float(rect.width), float(rect.height)
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError("invalid_source_page_dimensions")
    effective = min(
        float(requested_dpi),
        MAX_RENDER_DIMENSION * 72 / max(width, height),
        math.sqrt(MAX_RENDER_PIXELS / (width * height)) * 72,
    )
    adjusted = effective < requested_dpi
    if adjusted:
        effective *= 0.999  # Leave raster integer rounding inside the hard bounds.
    pixels = [math.ceil(width * effective / 72), math.ceil(height * effective / 72)]
    return {
        "requested_dpi": float(requested_dpi),
        "effective_dpi": effective,
        "adaptive": adjusted,
        "source_page_points": [width, height],
        "planned_pixel_size": pixels,
        "maximum_dimension": MAX_RENDER_DIMENSION,
        "maximum_pixels": MAX_RENDER_PIXELS,
        "minimum_legible_dpi": MINIMUM_RENDER_DPI,
        "legibility_floor_met": effective >= MINIMUM_RENDER_DPI,
    }


def _key(text):
    return re.sub(r"[^a-z0-9]", "", text.casefold())


# Printed electrical schedule headings, not product/catalog value substitutions.
_SCHEDULE_HEADERS = {
    "sectno": "section_no",
    "sectionno": "section_no",
    "sectionnumber": "section_no",
    "cktno": "circuit_no",
    "circuitno": "circuit_no",
    "circuitnumber": "circuit_no",
    "deviceframerating": "device_frame",
    "deviceframe": "device_frame",
    "frame": "device_frame",
    "frameamp": "frame_a",
    "frameamps": "frame_a",
    "tripamp": "trip_a",
    "tripamps": "trip_a",
    "tripa": "trip_a",
    "p": "pole_count",
    "poles": "pole_count",
    "polecount": "pole_count",
    "designation": "designation",
}


def normalize_header(text):
    """Exact punctuation/spacing normalization; never fuzzy-correct OCR letters."""
    key = _key(text)
    field = _SCHEDULE_HEADERS.get(key) or HEADERS.get(key)
    return "circuit_no" if field == "circuit_number" else field


@dataclass(frozen=True)
class _Line:
    position: float
    start: float
    end: float


@dataclass(frozen=True)
class _Transform:
    original_size: tuple[int, int]
    upright_size: tuple[int, int]
    corrected_size: tuple[int, int]
    clockwise: int
    deskew_ccw: float

    def source_box(self, box):
        """Inverse actual render transforms, with all four corners retained."""
        w, h = self.upright_size
        cw, ch = self.corrected_size
        angle = math.radians(self.deskew_ccw)
        points = []
        for x, y in (
            (box[0], box[1]),
            (box[2], box[1]),
            (box[0], box[3]),
            (box[2], box[3]),
        ):
            dx, dy = x - cw / 2, y - ch / 2
            ux = (math.cos(angle) * dx - math.sin(angle) * dy + w / 2) / w
            uy = (math.sin(angle) * dx + math.cos(angle) * dy + h / 2) / h
            if self.clockwise == 90:
                sx, sy = uy, 1 - ux
            elif self.clockwise == 180:
                sx, sy = 1 - ux, 1 - uy
            elif self.clockwise == 270:
                sx, sy = 1 - uy, ux
            else:
                sx, sy = ux, uy
            points.append((sx, sy))
        return [
            round(max(0.0, min(1.0, x)), 7)
            for x in (
                min(p[0] for p in points),
                min(p[1] for p in points),
                max(p[0] for p in points),
                max(p[1] for p in points),
            )
        ]


def _ocr(image, *, psm=6, timeout=12):
    executable = shutil.which("tesseract")
    if not executable:
        raise RuntimeError("tesseract_unavailable")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    result = subprocess.run(
        [executable, "stdin", "stdout", "--psm", str(psm), "tsv"],
        input=stream.getvalue(),
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("tesseract_failed")
    words = []
    for record in csv.DictReader(
        io.StringIO(result.stdout.decode(errors="replace")), delimiter="\t"
    ):
        text = record.get("text", "").strip()
        if not text:
            continue
        try:
            words.append(
                {
                    "text": text,
                    "confidence": float(record["conf"]),
                    "box": [int(record[k]) for k in ("left", "top", "width", "height")],
                    "line": tuple(
                        record[k] for k in ("block_num", "par_num", "line_num")
                    ),
                }
            )
        except (KeyError, ValueError):
            continue
    return words


def _orient(image):
    probe = image.copy()
    probe.thumbnail((1800, 1800))
    scores = []
    for clockwise in (0, 90, 180, 270):
        words = _ocr(probe.rotate(-clockwise, expand=True), psm=11)
        score = sum(
            max(0, w["confidence"] - 40) * min(len(_key(w["text"])), 12) for w in words
        )
        scores.append({"clockwise": clockwise, "score": round(score, 3)})
    ordered = sorted(scores, key=lambda x: x["score"], reverse=True)
    established = ordered[0]["score"] > 100 and (
        ordered[0]["score"] > ordered[1]["score"] * 1.15
    )
    return ordered[0]["clockwise"], established, scores


def _deskew(image):
    """Choose only a small scan skew from long horizontal ink projection."""
    probe = image.copy()
    probe.thumbnail((1500, 1500))
    ranked = []
    for angle in np.arange(-1.5, 1.501, 0.1):
        rotated = probe.rotate(float(angle), expand=False, fillcolor=255)
        projection = (np.asarray(rotated) < 175).sum(axis=1)
        # Long drawing lines dominate; ordinary word rows cannot set the angle.
        strong = projection[projection > probe.width * 0.25]
        ranked.append((float(np.square(strong.astype(float)).sum()), float(angle)))
    best = max(ranked)[1]
    return round(best, 2)


def _runs(values):
    edges = np.diff(np.r_[False, values, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def _long_ink(binary, length):
    """Horizontal run opening tolerant of a few scanned-line holes."""
    cumulative = np.pad(binary.astype(np.int32).cumsum(axis=1), ((0, 0), (1, 0)))
    starts = cumulative[:, length:] - cumulative[:, :-length] >= length * 0.94
    differences = np.zeros((binary.shape[0], binary.shape[1] + 1), dtype=np.int16)
    rr, cc = np.nonzero(starts)
    np.add.at(differences, (rr, cc), 1)
    np.add.at(differences, (rr, cc + length), -1)
    return differences[:, :-1].cumsum(axis=1) > 0


def _axis_lines(binary, minimum):
    opened = _long_ink(binary, minimum)
    bands = _runs(opened.sum(axis=1) >= minimum)
    result = []
    for a, b in bands:
        for start, end in _runs(opened[a:b].any(axis=0)):
            if end - start >= minimum:
                result.append(_Line((a + b - 1) / 2, start, end))
    return result


def _covered(lines, position, start, end, tolerance=5):
    overlap = max(
        (
            max(0.0, min(end, x.end) - max(start, x.start))
            for x in lines
            if abs(x.position - position) <= tolerance
        ),
        default=0.0,
    )
    return bool(overlap >= (end - start) * 0.85)


def _cluster_coords(values, tolerance=5):
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [float(np.median(g)) for g in groups]


def _grid_cells(image):
    """Find repeated ruled rectangles and their merged rectangular faces."""
    binary = np.asarray(image) < 175
    horizontal = _axis_lines(binary, max(40, image.width // 35))
    vertical = _axis_lines(binary.T, max(30, image.height // 50))
    clusters = []
    for line in sorted(horizontal, key=lambda x: -(x.end - x.start)):
        if line.end - line.start < image.width * 0.18:
            continue
        match = next(
            (
                g
                for g in clusters
                if abs(g[0].start - line.start) < 12 and abs(g[0].end - line.end) < 12
            ),
            None,
        )
        if match is None:
            clusters.append([line])
        else:
            match.append(line)
    tables = []
    for group in clusters:
        if len(group) < 5:
            continue
        x0, x1 = (
            float(np.median([x.start for x in group])),
            float(np.median([x.end for x in group])),
        )
        y0, y1 = min(x.position for x in group), max(x.position for x in group)
        if any(
            x0 >= t["bbox"][0] - 8
            and x1 <= t["bbox"][2] + 8
            and y0 >= t["bbox"][1] - 8
            and y1 <= t["bbox"][3] + 8
            for t in tables
        ):
            continue
        hs = [
            x
            for x in horizontal
            if y0 - 5 <= x.position <= y1 + 5 and x.end > x0 and x.start < x1
        ]
        vs = [
            x
            for x in vertical
            if x0 - 5 <= x.position <= x1 + 5
            and min(y1, x.end) - max(y0, x.start) > (y1 - y0) * 0.3
        ]
        xs = _cluster_coords([x0, x1, *(x.position for x in vs)])
        ys = _cluster_coords([y0, y1, *(x.position for x in hs)])
        if len(xs) < 3 or len(xs) > 61 or len(ys) > 205:
            continue
        nx, ny = len(xs) - 1, len(ys) - 1
        parent = list(range(nx * ny))

        def root(a, parent=parent):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b, parent=parent, root=root):
            parent[root(a)] = root(b)

        for j in range(ny):
            for i in range(nx):
                n = j * nx + i
                if i + 1 < nx and not _covered(vs, xs[i + 1], ys[j], ys[j + 1]):
                    union(n, n + 1)
                if j + 1 < ny and not _covered(hs, ys[j + 1], xs[i], xs[i + 1]):
                    union(n, n + nx)
        faces = defaultdict(list)
        for j in range(ny):
            for i in range(nx):
                faces[root(j * nx + i)].append((i, j))
        cells = []
        for face in faces.values():
            imin, imax = min(x[0] for x in face), max(x[0] for x in face)
            jmin, jmax = min(x[1] for x in face), max(x[1] for x in face)
            rectangular = len(face) == (imax - imin + 1) * (jmax - jmin + 1)
            box = [xs[imin], ys[jmin], xs[imax + 1], ys[jmax + 1]]
            bounded = (
                rectangular
                and _covered(hs, box[1], box[0], box[2])
                and _covered(hs, box[3], box[0], box[2])
                and _covered(vs, box[0], box[1], box[3])
                and _covered(vs, box[2], box[1], box[3])
            )
            cells.append(
                {
                    "bbox": box,
                    "grid_row": jmin + 1,
                    "grid_column": imin + 1,
                    "row_span": jmax - jmin + 1,
                    "column_span": imax - imin + 1,
                    "geometry_valid": bounded,
                }
            )
        tables.append(
            {
                "bbox": [x0, y0, x1, y1],
                "cells": cells,
                "row_boundaries": ys,
                "column_boundaries": xs,
            }
        )
    return tables


def _read_cell(image, cell):
    box = cell["bbox"]
    inset = 4
    crop_box = [
        math.ceil(box[0] + inset),
        math.ceil(box[1] + inset),
        math.floor(box[2] - inset),
        math.floor(box[3] - inset),
    ]
    if (
        crop_box[2] <= crop_box[0]
        or crop_box[3] <= crop_box[1]
        or not cell["geometry_valid"]
    ):
        return {
            **cell,
            "raw_text": "",
            "ocr_confidence": None,
            "words": [],
            "ocr_error": "unbounded_or_tiny_cell",
        }
    crop = image.crop(crop_box)
    # Do not ask OCR to turn a blank ruled cell into punctuation.
    if (np.asarray(crop) < 170).sum() < max(5, crop.width * crop.height * 0.001):
        return {**cell, "raw_text": "", "ocr_confidence": None, "words": []}
    scale = max(1.0, min(3.0, 90 / crop.height))
    crop = crop.resize((round(crop.width * scale), round(crop.height * scale)))
    crop = ImageOps.expand(crop, 12, fill=255)
    try:
        words = _ocr(crop, psm=6)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        return {
            **cell,
            "raw_text": "",
            "ocr_confidence": None,
            "words": [],
            "ocr_error": type(exc).__name__,
        }
    lines = defaultdict(list)
    for word in words:
        lines[word["line"]].append(word["text"])
        left, top, width, height = word["box"]
        word["bbox"] = [
            crop_box[0] + (left - 12) / scale,
            crop_box[1] + (top - 12) / scale,
            crop_box[0] + (left + width - 12) / scale,
            crop_box[1] + (top + height - 12) / scale,
        ]
        del word["box"], word["line"]
    return {
        **cell,
        "raw_text": "\n".join(" ".join(v) for v in lines.values()),
        "ocr_confidence": min((w["confidence"] for w in words), default=None),
        "words": words,
    }


def _normalize_candidate(field, text, confidence, *, minimum):
    if confidence is None or confidence < minimum:
        return {
            "value": None,
            "unit": FEATURES.get(field).unit if field in FEATURES else None,
            "valid": False,
            "reason": "ocr_confidence_below_threshold",
        }
    literal = text.strip()
    # Parentheses are printed annotation, not an invitation to fill digits.
    if field in {"trip_a", "frame_a"}:
        match = re.fullmatch(
            r"(?:\(\s*(\d+(?:\.\d+)?)\s*A\s*\)|(\d+(?:\.\d+)?)\s*A)",
            literal,
            re.IGNORECASE,
        )
        if not match:
            return {
                "value": None,
                "unit": "A",
                "valid": False,
                "reason": "no_unambiguous_literal_amp_value",
            }
        literal = (match[1] or match[2]) + " A"
    elif field == "pole_count":
        match = re.fullmatch(r"(\d+)\s*P?", literal, re.IGNORECASE)
        literal = match[1] if match else None
    return normalize_feature_value(field, literal)


def read_table_evidence(
    source,
    pages,
    *,
    dpi=216,
    max_pages=4,
    minimum_ocr_confidence=75.0,
    max_cells_per_page=600,
    page_timeout_seconds=120.0,
    output_dir=None,
):
    """Return raw measured tables, source-local rows, and typed candidate claims.

    `source` is SourceInput or a dict containing id/path/sha256. Candidate rows
    have no manufactured equipment identity. Physical input page numbers and
    bboxes always refer to the original rendered PDF page, before correction.
    Optional `output_dir` writes only source-derived table crops for audit.
    """
    data = source.model_dump() if hasattr(source, "model_dump") else dict(source)
    path = Path(data["path"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != data.get("sha256", "").lower():
        raise ValueError("source_hash_mismatch")
    source_ref({**data, "page": 1})
    pages = list(pages)
    if not pages or len(pages) > max_pages or len(pages) != len(set(pages)):
        raise ValueError("page_budget_or_duplicate_pages")
    if (
        not 100 <= dpi <= 360
        or not 0 <= minimum_ocr_confidence <= 100
        or not 1 <= max_cells_per_page <= 1200
        or not 10 <= page_timeout_seconds <= 600
    ):
        raise ValueError("invalid_render_or_confidence_limit")
    report = {
        "version": VERSION,
        "source_id": data.get("id", data.get("source_id")),
        "sha256": digest,
        "pages": [],
        "rows": [],
        "claims": [],
        "requires_human_verification": True,
        "coverage_complete": False,
        "independent_source_count": 1,
        "ancestry_policy": "OCR and vision of this source page share one source ancestry.",
    }
    with pymupdf.open(path) as doc:
        if any(
            not isinstance(p, int) or isinstance(p, bool) or p < 1 or p > len(doc)
            for p in pages
        ):
            raise ValueError("invalid_physical_page")
        for physical in pages:
            started = time.monotonic()
            attempted_cells = 0
            page_result = {"page": physical, "tables": [], "unknown_regions": []}
            page = doc[physical - 1]
            plan = _render_plan(page.rect, dpi)
            page_result["render_plan"] = plan
            page_result["requested_render_dpi"] = dpi
            page_result["render_dpi"] = None
            if not plan["legibility_floor_met"]:
                page_result.update(
                    status="unknown", error_type="render_below_legibility_floor"
                )
                page_result["unknown_regions"].append(
                    {
                        "bbox": [0.0, 0.0, 1.0, 1.0],
                        "reason": "The bounded source render would fall below the declared OCR legibility floor; no table OCR was attempted.",
                    }
                )
                report["pages"].append(page_result)
                continue
            effective_dpi = plan["effective_dpi"]
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(effective_dpi / 72, effective_dpi / 72),
                alpha=False,
            )
            if (
                max(pix.width, pix.height) > MAX_RENDER_DIMENSION
                or pix.width * pix.height > MAX_RENDER_PIXELS
            ):
                page_result.update(
                    status="unknown", error_type="bounded_render_plan_mismatch"
                )
                page_result["unknown_regions"].append(
                    {
                        "bbox": [0.0, 0.0, 1.0, 1.0],
                        "reason": "The rendered source exceeded its declared raster bounds; no table OCR was attempted.",
                    }
                )
                report["pages"].append(page_result)
                continue
            page_result["render_dpi"] = effective_dpi
            page_result["render_pixel_size"] = [pix.width, pix.height]
            original = Image.frombytes(
                "RGB", (pix.width, pix.height), pix.samples
            ).convert("L")
            try:
                clockwise, established, scores = _orient(original)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                page_result.update(status="unknown", error_type=type(exc).__name__)
                report["pages"].append(page_result)
                continue
            page_result.update(
                orientation_clockwise=clockwise,
                orientation_established=established,
                orientation_scores=scores,
            )
            if not established:
                page_result.update(
                    status="unknown", error_type="orientation_not_established"
                )
                report["pages"].append(page_result)
                continue
            upright = original.rotate(-clockwise, expand=True)
            angle = _deskew(upright)
            image = upright.rotate(angle, expand=True, fillcolor=255)
            transform = _Transform(
                original.size, upright.size, image.size, clockwise, angle
            )
            page_result["deskew_ccw"] = angle
            page_result.update(
                render_dpi=effective_dpi,
                source_page_size=list(original.size),
                corrected_page_size=list(image.size),
                coordinate_system="normalized_top_left",
            )
            for tnum, table in enumerate(_grid_cells(image), 1):
                if (
                    attempted_cells + len(table["cells"]) > max_cells_per_page
                    or time.monotonic() - started >= page_timeout_seconds
                ):
                    page_result["unknown_regions"].append(
                        {
                            "bbox": transform.source_box(table["bbox"]),
                            "reason": "Source table OCR cell/time budget reached.",
                        }
                    )
                    continue
                cells = []
                for cell in table["cells"]:
                    attempted_cells += 1
                    if time.monotonic() - started < page_timeout_seconds:
                        cells.append(_read_cell(image, cell))
                    else:
                        cells.append(
                            {
                                **cell,
                                "raw_text": "",
                                "ocr_confidence": None,
                                "words": [],
                                "ocr_error": "page_time_budget",
                            }
                        )
                headers = [
                    c
                    for c in cells
                    if normalize_header(c["raw_text"])
                    and c["ocr_confidence"] is not None
                    and c["ocr_confidence"] >= minimum_ocr_confidence
                ]
                # An isolated title block is not a supported equipment table.
                header_rows = defaultdict(list)
                for c in headers:
                    header_rows[c["grid_row"]].append(c)
                eligible = [r for r, hs in header_rows.items() if len(hs) >= 2]
                if not eligible:
                    continue
                first = min(eligible)
                headers = [c for c in headers if first <= c["grid_row"] <= first + 1]
                body_start = max(c["grid_row"] + c["row_span"] for c in headers)
                body = defaultdict(list)
                for c in cells:
                    if c["grid_row"] >= body_start:
                        body[c["grid_row"]].append(c)
                for body_row, (rownum, rcells) in enumerate(sorted(body.items()), 1):
                    local = f"page-{physical}-table-{tnum}-row-{rownum}"
                    subject = stable_id("source-table-position", digest, local)
                    row = {
                        "source_id": report["source_id"],
                        "sha256": digest,
                        "subject_id": subject,
                        "source_local_id": local,
                        "page": physical,
                        "table": tnum,
                        "grid_row": rownum,
                        "body_row": body_row,
                        "cells": [],
                        "fields": {},
                        "claim_ids": [],
                        "tag": None,
                        "status": "candidate_row",
                    }
                    for c in sorted(rcells, key=lambda c: c["grid_column"]):
                        match = [
                            h
                            for h in headers
                            if h["grid_column"] == c["grid_column"]
                            and h["column_span"] == c["column_span"]
                        ]
                        header = match[-1] if match else None
                        field = normalize_header(header["raw_text"]) if header else None
                        # Repeated QTY inside wire/lug schedules is not equipment count.
                        if field == "quantity" and (
                            sum(
                                normalize_header(h["raw_text"]) == "quantity"
                                for h in headers
                            )
                            > 1
                            or any(
                                "lug" in x["raw_text"].casefold()
                                for x in cells
                                if x["grid_row"] < body_start
                            )
                        ):
                            field = None
                        public = {k: v for k, v in c.items() if k != "words"}
                        public.update(
                            bbox=transform.source_box(c["bbox"]),
                            field=field,
                            header=header["raw_text"] if header else None,
                            coordinate_system="normalized_top_left",
                            word_bboxes=[
                                {**w, "bbox": transform.source_box(w["bbox"])}
                                for w in c["words"]
                            ],
                        )
                        row["cells"].append(public)
                        if field is None:
                            continue
                        normalized = _normalize_candidate(
                            field,
                            c["raw_text"],
                            c["ocr_confidence"],
                            minimum=minimum_ocr_confidence,
                        )
                        if c["row_span"] > 1:
                            normalized.update(
                                value=None,
                                valid=False,
                                reason="merged_body_cell_row_association_unknown",
                            )
                        ref = {
                            **data,
                            "page": physical,
                            "bbox": public["bbox"],
                            "coordinate_system": "normalized_top_left",
                            "table": tnum,
                            "row": rownum,
                            "column": c["grid_column"],
                            "text": c["raw_text"],
                        }
                        claim = make_claim(
                            subject,
                            field,
                            normalized["value"],
                            source=ref,
                            extractor={
                                "name": "source_ruled_table_tesseract",
                                "version": VERSION,
                            },
                            raw_value=c["raw_text"],
                            raw_confidence=c["ocr_confidence"],
                            unit=normalized["unit"],
                            normalization={
                                **normalized,
                                "threshold": minimum_ocr_confidence,
                            },
                            conditions={
                                "candidate_only": True,
                                "source_local_id": local,
                                "header_raw_text": header["raw_text"],
                                "header_bbox": transform.source_box(header["bbox"]),
                                "geometry_valid": c["geometry_valid"],
                            },
                        )
                        report["claims"].append(claim)
                        row["fields"][field] = normalized["value"]
                        row["claim_ids"].append(claim["id"])
                    if row["claim_ids"]:
                        boxes = [c["bbox"] for c in row["cells"]]
                        row["bbox"] = [
                            min(b[0] for b in boxes),
                            min(b[1] for b in boxes),
                            max(b[2] for b in boxes),
                            max(b[3] for b in boxes),
                        ]
                        row["circuit_no"] = row["fields"].get("circuit_no")
                        row["section_no"] = row["fields"].get("section_no")
                        report["rows"].append(row)
                page_result["tables"].append(
                    {
                        "table": tnum,
                        "bbox": transform.source_box(table["bbox"]),
                        "headers": [
                            {
                                "raw_text": h["raw_text"],
                                "field": normalize_header(h["raw_text"]),
                                "bbox": transform.source_box(h["bbox"]),
                                "ocr_confidence": h["ocr_confidence"],
                            }
                            for h in headers
                        ],
                        "raw_header_cells": [
                            {
                                **{
                                    k: v
                                    for k, v in h.items()
                                    if k not in {"words", "bbox"}
                                },
                                "bbox": transform.source_box(h["bbox"]),
                                "field": normalize_header(h["raw_text"])
                                if h in headers
                                else None,
                            }
                            for h in cells
                            if h["grid_row"] < body_start
                        ],
                        "geometry_cell_count": len(cells),
                        "body_row_count": len(body),
                        "ocr_complete": not any(c.get("ocr_error") for c in cells),
                    }
                )
                if output_dir:
                    target = Path(output_dir)
                    target.mkdir(parents=True, exist_ok=True)
                    image.crop(tuple(round(x) for x in table["bbox"])).save(
                        target / f"page-{physical}-table-{tnum}.png"
                    )
            page_result["status"] = (
                "candidate_tables" if page_result["tables"] else "unknown"
            )
            if not page_result["tables"]:
                page_result["unknown_regions"].append(
                    {
                        "bbox": [0.0, 0.0, 1.0, 1.0],
                        "reason": "No bounded ruled table with readable supported headers was established.",
                    }
                )
            report["pages"].append(page_result)
    report["status"] = "candidates" if report["claims"] else "unknown"
    return report
