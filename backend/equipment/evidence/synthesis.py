"""Reconstruct reconciled observations and account for independent page ink.

Synthesis is a diagnostic, not another extractor and not an accuracy score. Native
PDF words can be matched exactly to reconciled values. Raster OCR is an independent
location aid but remains tentative. Bounding-box overlap alone never explains ink.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import re
import shutil
import subprocess
import time
from collections import deque
from itertools import pairwise
from pathlib import Path

import pymupdf
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from ..pdf_model import PDFModelError, visible_equipment_words


def _normalized(text):
    return re.sub(r"[^\w]+", "", str(text).casefold())


def _numeric_raw_compatible(value, unit, raw):
    """A retained wrong raw/normalized pair must not explain the selected value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    match = re.fullmatch(
        r"\s*\(?\s*([+-]?\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*\)?\s*", str(raw)
    )
    if not match:
        return False
    number, raw_unit = float(match[1]), match[2]
    units = {
        "A": ("current", 1),
        "a": ("current", 1),
        "Amp": ("current", 1),
        "Amps": ("current", 1),
        "kA": ("current", 1000),
        "ka": ("current", 1000),
        "mA": ("current", 0.001),
        "V": ("voltage", 1),
        "v": ("voltage", 1),
        "kV": ("voltage", 1000),
        "kv": ("voltage", 1000),
        "each": ("count", 1),
        "pcs": ("count", 1),
        "P": ("poles", 1),
        "p": ("poles", 1),
    }
    if raw_unit and unit:
        if (
            raw_unit not in units
            or unit not in units
            or units[raw_unit][0] != units[unit][0]
        ):
            return False
        number *= units[raw_unit][1] / units[unit][1]
    return math.isclose(number, value, rel_tol=1e-9, abs_tol=1e-9)


def _bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if (
        all(isinstance(v, (int, float)) for v in value)
        and 0 <= value[0] < value[2] <= 1
        and 0 <= value[1] < value[3] <= 1
    ):
        return list(value)
    return None


def _pixel_box(box, size):
    return tuple(round(v * size[i % 2]) for i, v in enumerate(box))


def _overlap(a, b):
    w, h = (
        max(0, min(a[2], b[2]) - max(a[0], b[0])),
        max(0, min(a[3], b[3]) - max(a[1], b[1])),
    )
    return w * h


def _area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _page(path, physical_page, max_dimension, sha256):
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as doc:
            page = doc[physical_page - 1]
            scale = max_dimension / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            try:
                visible, issues = visible_equipment_words(page, sha256)
                warning = (
                    None
                    if not issues
                    else "Native text visibility exclusions remain; excluded text is not used."
                )
            except PDFModelError:
                visible, warning = (
                    [],
                    "Bounded native visibility inspection failed; only independent raster OCR can locate text.",
                )
            words = [
                {
                    "text": w["text"],
                    "bbox": [
                        w["bbox"][0] / page.rect.width,
                        w["bbox"][1] / page.rect.height,
                        w["bbox"][2] / page.rect.width,
                        w["bbox"][3] / page.rect.height,
                    ],
                    "method": "native_pdf_text",
                    "confidence": None,
                }
                for w in visible
            ]
            return image, words, warning
    with Image.open(path) as original:
        if physical_page != 1:
            raise ValueError("Raster image input contains only physical page 1.")
        image = original.convert("RGB")
    image.thumbnail((max_dimension, max_dimension))
    return image, [], None


def _ocr(image, *, timeout):
    if not shutil.which("tesseract"):
        return [], "Tesseract is unavailable; scan text remains unexplained."
    started, best, tried = time.monotonic(), (0, []), []
    for rotation in (0, 90, 180, 270):
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0.1:
            break
        rotated = image.rotate(rotation, expand=True)
        stream = io.BytesIO()
        rotated.save(stream, format="PNG")
        try:
            process = subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "11", "tsv"],
                input=stream.getvalue(),
                capture_output=True,
                timeout=remaining,
                check=False,
            )
        except subprocess.TimeoutExpired:
            break
        tried.append(rotation)
        if process.returncode:
            continue
        words = []
        for row in csv.DictReader(
            io.StringIO(process.stdout.decode("utf-8", errors="replace")),
            delimiter="\t",
        ):
            if not row.get("text", "").strip():
                continue
            try:
                left, top, width, height = (
                    int(row[key]) for key in ("left", "top", "width", "height")
                )
                confidence = float(row["conf"])
            except (KeyError, ValueError):
                continue
            box = [
                left / rotated.width,
                top / rotated.height,
                (left + width) / rotated.width,
                (top + height) / rotated.height,
            ]
            if rotation == 90:
                box = [1 - box[3], box[0], 1 - box[1], box[2]]
            elif rotation == 180:
                box = [1 - box[2], 1 - box[3], 1 - box[0], 1 - box[1]]
            elif rotation == 270:
                box = [box[1], 1 - box[2], box[3], 1 - box[0]]
            words.append(
                {
                    "text": row["text"],
                    "bbox": box,
                    "method": "independent_tesseract_ocr",
                    "confidence": confidence,
                    "rotation_ccw": rotation,
                }
            )
        # This selects a readable orthogonal view, not a calibrated claim value.
        score = sum(
            max(0, w["confidence"] - 40) * min(len(_normalized(w["text"])), 12)
            for w in words
        )
        if score > best[0]:
            best = score, words
    if not best[1]:
        return [], "OCR failed or exhausted its budget; scan text remains unexplained."
    return (
        best[1],
        None
        if len(tried) == 4
        else "OCR orientation search exhausted its budget; surviving words remain tentative.",
    )


def _semantic_anchors(model, source_id, sha256, page):
    claims = {c["id"]: c for c in model.get("claims", [])}
    anchors = []
    resolutions = model.get("resolutions", [])
    for resolution in resolutions:
        if resolution.get("value") is None:
            continue
        for claim_id in resolution.get("supporting_claim_ids", []):
            claim = claims.get(claim_id)
            if not claim:
                continue
            ref = claim.get("source") or {}
            if (
                ref.get("source_id") != source_id
                or ref.get("sha256") != sha256
                or ref.get("page") != page
            ):
                continue
            box = _bbox(ref.get("bbox"))
            if not box:
                continue
            value = resolution["value"]
            unit = resolution.get("unit") or ""
            texts = [str(value), f"{value}{unit}", f"{value} {unit}"]
            # A claim's raw wording is usable only if its normalized value agrees
            # with the selected value. An inferred 600 A cannot explain "60 A".
            raw_compatible = _numeric_raw_compatible(
                value, unit, claim.get("raw_value", value)
            )
            if claim.get("value") == value and raw_compatible:
                texts.append(str(claim.get("raw_value", value)))
            texts = sorted(
                {_normalized(t) for t in texts if t is not None and _normalized(t)},
                key=len,
                reverse=True,
            )
            anchors.append(
                {
                    "subject_id": resolution.get(
                        "subject_id", resolution.get("entity_id")
                    ),
                    "feature": resolution.get("feature"),
                    "value": value,
                    "unit": unit,
                    "bbox": box,
                    "texts": texts,
                    "claim_id": claim_id,
                    "needs_review": resolution.get("needs_review", True),
                    "raw_numeric_candidate_rejected": not raw_compatible,
                    "localization_tight": _area(box) <= 0.05
                    and max(box[2] - box[0], box[3] - box[1]) <= 0.55,
                }
            )
    # Device identities also need to explain their visible glyphs, but source-local
    # position identifiers invented by the reader are deliberately not printed.
    for node in model.get("nodes", []):
        tag = (node.get("identifiers") or {}).get("raw_tag")
        if not tag or (node.get("metadata") or {}).get("source_local"):
            continue
        for ref in node.get("source_refs", []):
            if (
                ref.get("source_id") == source_id
                and ref.get("sha256") == sha256
                and ref.get("page") == page
                and _bbox(ref.get("bbox"))
            ):
                anchors.append(
                    {
                        "subject_id": node["id"],
                        "feature": "raw_tag",
                        "value": tag,
                        "unit": "",
                        "bbox": list(ref["bbox"]),
                        "texts": [_normalized(tag)],
                        "claim_id": None,
                        "needs_review": True,
                        "localization_tight": _area(ref["bbox"]) <= 0.05,
                    }
                )
    return anchors


def _match_words(anchors, words):
    result = []
    for anchor in anchors:
        candidates = [
            w
            for w in words
            if _overlap(w["bbox"], anchor["bbox"]) / max(_area(w["bbox"]), 1e-9) >= 0.7
        ]
        candidates.sort(key=lambda w: (round(w["bbox"][1] * 60), w["bbox"][0]))
        for index in range(len(candidates)):
            for length in range(1, min(5, len(candidates) - index) + 1):
                sequence = candidates[index : index + length]
                # A whole cell's disjoint words are not one token. Restrict each
                # matched sequence to a common text line and nearby glyphs.
                if (
                    max(w["bbox"][1] for w in sequence)
                    - min(w["bbox"][1] for w in sequence)
                    > max(w["bbox"][3] - w["bbox"][1] for w in sequence) * 0.8
                ):
                    continue
                if _normalized("".join(w["text"] for w in sequence)) in anchor["texts"]:
                    result.append(
                        {
                            **anchor,
                            "words": sequence,
                            "classification": "exact_native_text"
                            if all(w["method"] == "native_pdf_text" for w in sequence)
                            else "tentative_scan_text",
                        }
                    )
                    break
    return result


def _components(mask, *, limit):
    """Independently segment source ink before looking at any model rectangle."""
    width, height = mask.size
    data, seen = mask.tobytes(), bytearray(width * height)
    result, remaining = [], False
    for start, pixel in enumerate(data):
        if not pixel or seen[start]:
            continue
        if len(result) >= limit:
            remaining = True
            break
        queue, indices = deque([start]), []
        seen[start] = 1
        left = right = start % width
        top = bottom = start // width
        while queue:
            current = queue.popleft()
            indices.append(current)
            x, y = current % width, current // width
            left, right, top, bottom = (
                min(left, x),
                max(right, x),
                min(top, y),
                max(bottom, y),
            )
            for xx, yy in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= xx < width and 0 <= yy < height:
                    adjacent = yy * width + xx
                    if data[adjacent] and not seen[adjacent]:
                        seen[adjacent] = 1
                        queue.append(adjacent)
        result.append(
            {
                "bbox_pixels": [left, top, right + 1, bottom + 1],
                "pixels": indices,
                "bbox": [
                    left / width,
                    top / height,
                    (right + 1) / width,
                    (bottom + 1) / height,
                ],
            }
        )
    return result, remaining


def _draw_synthesis(image, anchors, model, source_id, sha256, page):
    canvas = Image.new("RGB", image.size, "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=12)
    geometry = Image.new("L", image.size, 0)
    geometry_draw = ImageDraw.Draw(geometry)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{image.width}" height="{image.height}" viewBox="0 0 {image.width} {image.height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for anchor in anchors:
        box = _pixel_box(anchor["bbox"], image.size)
        value = f"{anchor['value']} {anchor['unit']}".strip()
        draw.text((box[0], box[1]), value, font=font, fill="#162c4b")
        parts.append(
            f'<text x="{box[0]}" y="{box[1] + 12}" fill="#162c4b" font-size="12">{html.escape(value)}</text>'
        )
    for edge in model.get("edges", []):
        # Reconstruct only explicitly source-located paths. An inferred graph edge
        # cannot explain arbitrary black pixels near its endpoint boxes.
        for ref in edge.get("source_refs", []):
            path = ref.get("path")
            if (
                ref.get("source_id") != source_id
                or ref.get("sha256") != sha256
                or ref.get("page") != page
                or not isinstance(path, list)
                or len(path) < 2
            ):
                continue
            if not all(
                isinstance(p, list)
                and len(p) == 2
                and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in p)
                for p in path
            ):
                continue
            points = [
                (round(p[0] * image.width), round(p[1] * image.height)) for p in path
            ]
            draw.line(points, fill="#162c4b", width=1)
            geometry_draw.line(points, fill=255, width=1)
            points_text = " ".join(f"{x},{y}" for x, y in points)
            parts.append(
                f'<polyline points="{points_text}" fill="none" stroke="#162c4b" stroke-width="1"/>'
            )
    parts.append("</svg>")
    return canvas, geometry, "\n".join(parts)


def _canonical_model(model, source_id, page, *, max_nodes=200):
    """Generate an explicit graph projection, not a counterfeit drawing replica."""
    relevant = {
        c.get("subject_id")
        for c in model.get("claims", [])
        if c.get("source", {}).get("source_id") == source_id
        and c.get("source", {}).get("page") == page
    }
    relevant.update(
        n["id"]
        for n in model.get("nodes", [])
        if any(
            ref.get("source_id") == source_id and ref.get("page") == page
            for ref in n.get("source_refs", [])
        )
    )
    for _ in range(8):
        parents = {
            e["from"]
            for e in model.get("edges", [])
            if e.get("kind") == "contains" and e.get("to") in relevant
        }
        if parents <= relevant:
            break
        relevant |= parents
    candidates = [n for n in model.get("nodes", []) if n["id"] in relevant]
    nodes = candidates[:max_nodes]
    resolutions = model.get("resolutions", [])
    by_subject = {}
    for resolution in resolutions:
        by_subject.setdefault(
            resolution.get("subject_id", resolution.get("entity_id")), []
        ).append(resolution)
    width = 1100
    height = max(250, 90 + ((len(nodes) + 3) // 4) * 150)
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=12)
    draw.text(
        (20, 18),
        "Reconciled equipment graph — schematic projection; not a released drawing",
        font=font,
        fill="#192e48",
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="20" y="30" font-size="12">Reconciled equipment graph — schematic projection; not a released drawing</text>',
    ]
    locations = {}
    for index, node in enumerate(nodes):
        x, y = 20 + (index % 4) * 270, 60 + (index // 4) * 150
        locations[node["id"]] = (x + 125, y + 60)
        draw.rectangle((x, y, x + 250, y + 125), outline="#53677b", width=1)
        parts.append(
            f'<rect x="{x}" y="{y}" width="250" height="125" fill="none" stroke="#53677b"/>'
        )
        tag = (node.get("identifiers") or {}).get("raw_tag") or node.get("id")
        lines = [f"{node.get('kind', 'equipment')} · {tag}"]
        for resolution in by_subject.get(node["id"], [])[:5]:
            value = resolution.get("value")
            suffix = " [review]" if resolution.get("needs_review", True) else ""
            lines.append(
                f"{resolution.get('feature')}: {value if value is not None else 'unknown'} {resolution.get('unit') or ''}{suffix}"
            )
        for line_index, line in enumerate(lines):
            line = str(line)[:43]
            draw.text((x + 8, y + 8 + line_index * 18), line, fill="#192e48", font=font)
            parts.append(
                f'<text x="{x + 8}" y="{y + 20 + line_index * 18}" font-size="12">{html.escape(line)}</text>'
            )
    for edge in model.get("edges", []):
        if edge.get("from") not in locations or edge.get("to") not in locations:
            continue
        a, b = locations[edge["from"]], locations[edge["to"]]
        draw.line((a, b), fill="#577c76", width=1)
        parts.append(
            f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="#577c76"/>'
        )
    parts.append("</svg>")
    return (
        canvas,
        "\n".join(parts),
        {
            "page_nodes": len(candidates),
            "rendered_nodes": len(nodes),
            "omitted_nodes": max(0, len(candidates) - len(nodes)),
        },
    )


def _unknown_clusters(mask, *, max_regions=120):
    # Independent ink is joined only for readable crops. No region is erased or
    # called equipment merely because it sits near a reconstructed device.
    reduced = mask.copy()
    reduced.thumbnail((240, 240))
    reduced = reduced.point(lambda p: 255 if p > 10 else 0).filter(
        ImageFilter.MaxFilter(3)
    )
    components, partial = _components(reduced, limit=10000)
    components.sort(key=lambda c: len(c["pixels"]), reverse=True)
    return (
        [
            {
                "bbox": c["bbox"],
                "grid_ink_cells": len(c["pixels"]),
                "classification": "unexplained_source_ink",
                "possible_missed_equipment": True,
                "equipment_identity_established": False,
            }
            for c in components[:max_regions]
        ],
        partial or len(components) > max_regions,
        len(components),
    )


def _table_strips(ink, unexplained):
    """Propose unknown table strips from repeated ruled lines, not model boxes.

    Both axes are retained: no inference says which axis is a row or a column.
    Slight scan skew is chosen from source line concentration on a bounded image.
    These regions never increase explained-ink coverage or prove missed devices.
    """
    small = ink.copy()
    small.thumbnail((800, 800))
    # Preserve thin CAD/table rules while reducing: a one-pixel black rule can
    # occupy less than half a reduced pixel. This mask proposes regions only.
    small = small.point(lambda p: 255 if p > 20 else 0)
    best = None
    for angle in (-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2):
        candidate = small.rotate(
            angle, resample=Image.Resampling.BILINEAR, fillcolor=0
        ).point(lambda p: 255 if p > 100 else 0)
        data, width, height = candidate.tobytes(), candidate.width, candidate.height
        rows = [sum(data[y * width : (y + 1) * width]) / 255 for y in range(height)]
        columns = [sum(data[x::width]) / 255 for x in range(width)]
        score = sum((v / width) ** 2 for v in sorted(rows, reverse=True)[:12]) + sum(
            (v / height) ** 2 for v in sorted(columns, reverse=True)[:12]
        )
        if best is None or score > best[0]:
            best = score, angle, candidate
    angle, candidate = best[1], best[2].filter(ImageFilter.MaxFilter(3))
    data, width, height = candidate.tobytes(), candidate.width, candidate.height
    result = []
    for axis in (0, 1):
        axis_length, across_length = (width, height) if axis == 0 else (height, width)
        lines = []
        for position in range(axis_length):
            values = (
                data[position::width]
                if axis == 0
                else data[position * width : (position + 1) * width]
            )
            runs, start, last = [], None, None
            for index, pixel in enumerate(values):
                if not pixel:
                    continue
                if last is not None and index - last > 4:
                    runs.append((start, last + 1))
                    start = None
                if start is None:
                    start = index
                last = index
            if start is not None:
                runs.append((start, last + 1))
            if not runs:
                continue
            span = max(runs, key=lambda r: r[1] - r[0])
            if span[1] - span[0] < across_length * 0.22:
                continue
            if (
                lines
                and position - lines[-1][0] <= 3
                and abs(span[0] - lines[-1][1]) <= 5
                and abs(span[1] - lines[-1][2]) <= 5
            ):
                continue
            lines.append((position, *span))
        groups = []
        for line in lines:
            for group in groups:
                reference = group[0]
                if (
                    abs(line[1] - reference[1]) <= across_length * 0.04
                    and abs(line[2] - reference[2]) <= across_length * 0.04
                ):
                    group.append(line)
                    break
            else:
                groups.append([line])
        for group_index, group in enumerate(groups):
            if len(group) < 4:
                continue
            group.sort()
            across_start, across_end = (
                max(line[1] for line in group),
                min(line[2] for line in group),
            )
            for first, second in pairwise(group):
                if (
                    second[0] - first[0] < 4
                    or second[0] - first[0] > axis_length * 0.15
                ):
                    continue
                box = (
                    [
                        first[0] / width,
                        across_start / height,
                        second[0] / width,
                        across_end / height,
                    ]
                    if axis == 0
                    else [
                        across_start / width,
                        first[0] / height,
                        across_end / width,
                        second[0] / height,
                    ]
                )
                # Invert PIL's image-coordinate CCW rotation about the page centre.
                theta = math.radians(angle)
                points = []
                for x, y in (
                    (box[0], box[1]),
                    (box[2], box[1]),
                    (box[2], box[3]),
                    (box[0], box[3]),
                ):
                    xx, yy = (x - 0.5) * width, (y - 0.5) * height
                    points.append(
                        (
                            (math.cos(theta) * xx - math.sin(theta) * yy) / width + 0.5,
                            (math.sin(theta) * xx + math.cos(theta) * yy) / height
                            + 0.5,
                        )
                    )
                source_box = [
                    max(0, min(p[0] for p in points)),
                    max(0, min(p[1] for p in points)),
                    min(1, max(p[0] for p in points)),
                    min(1, max(p[1] for p in points)),
                ]
                pixels = _pixel_box(source_box, ink.size)
                total = sum(ink.crop(pixels).tobytes()) / 255
                unknown = sum(unexplained.crop(pixels).tobytes()) / 255
                if total and unknown / total >= 0.2:
                    result.append(
                        {
                            "bbox": source_box,
                            "classification": "unexplained_ruled_table_strip",
                            "proposal_method": "independent_parallel_source_lines",
                            "axis": axis,
                            "table_group": group_index,
                            "source_deskew_ccw": angle,
                            "unexplained_ink_fraction": unknown / total,
                            "row_or_column_identity": "not_established",
                            "possible_missed_equipment": True,
                            "equipment_identity_established": False,
                        }
                    )
    return result[:300], len(result)


def _independent_recall(regions, mask, ink, region_key, *, source_id, sha256, page):
    if not region_key:
        return {
            "recall": None,
            "status": "not_measured_no_independent_region_key",
            "denominator": None,
        }
    if region_key.get("label_source") != "independent_human" or not region_key.get(
        "frozen_before_run"
    ):
        raise ValueError(
            "Region recall needs an independently labelled key frozen before this run."
        )
    if (
        region_key.get("source_id") != source_id
        or region_key.get("sha256") != sha256
        or region_key.get("page") != page
    ):
        raise ValueError(
            "Independent region key does not identify this exact source page."
        )
    expected = [
        r
        for r in region_key.get("regions", [])
        if r.get("expected_unexplained") is True and _bbox(r.get("bbox"))
    ]
    caught, details = 0, []
    for item in expected:
        box = _pixel_box(item["bbox"], mask.size)
        total = sum(ink.crop(box).getdata()) / 255
        unexplained = sum(mask.crop(box).getdata()) / 255
        localized = any(
            _overlap(r["bbox"], item["bbox"])
            / max(
                _area(r["bbox"])
                + _area(item["bbox"])
                - _overlap(r["bbox"], item["bbox"]),
                1e-9,
            )
            >= 0.2
            for r in regions
        )
        found = total > 0 and unexplained / total >= 0.2 and localized
        caught += bool(found)
        details.append(
            {
                "id": item.get("id"),
                "caught": bool(found),
                "unexplained_ink_fraction": unexplained / total if total else None,
            }
        )
    return {
        "recall": caught / len(expected) if expected else None,
        "status": "measured_on_independent_key" if expected else "no_labelled_misses",
        "denominator": len(expected),
        "caught": caught,
        "details": details,
        "meaning": "Recall of independently labelled missing-model regions, not equipment extraction recall.",
    }


def _annotated_region_localization(regions, references, *, source_id, sha256, page):
    """Source-only row/symbol annotations measure localization, not model misses."""
    if not references:
        return {"status": "not_measured_no_independent_annotations", "by_kind": {}}
    source = next(
        (
            s
            for s in references.get("sources", [])
            if s.get("id") == source_id and s.get("page") == page
        ),
        None,
    )
    if not source or source.get("sha256") != sha256:
        raise ValueError(
            "Source region references do not bind this exact physical page."
        )
    keys = [
        r
        for r in references.get("regions", [])
        if r.get("source_id") == source_id
        and r.get("page") == page
        and _bbox(r.get("bbox"))
    ]
    used, details = set(), []
    for key in keys:
        candidates = []
        for index, region in enumerate(regions):
            overlap = _overlap(region["bbox"], key["bbox"])
            iou = overlap / max(
                _area(region["bbox"]) + _area(key["bbox"]) - overlap, 1e-9
            )
            if index not in used and iou >= 0.2:
                candidates.append((iou, index))
        match = max(candidates, default=None)
        if match:
            used.add(match[1])
        details.append(
            {
                "id": key.get("id"),
                "kind": key.get("kind", "unclassified"),
                "localized": bool(match),
                "proposal_index": match[1] if match else None,
                "intersection_over_union": match[0] if match else None,
            }
        )
    by_kind = {}
    for kind in sorted({d["kind"] for d in details}):
        items = [d for d in details if d["kind"] == kind]
        found = sum(d["localized"] for d in items)
        by_kind[kind] = {
            "reference_regions": len(items),
            "localized_regions": found,
            "localization_recall": found / len(items) if items else None,
        }
    return {
        "status": "measured_source_annotation_localization_only",
        "by_kind": by_kind,
        "reference_labelled_by": references.get("labelled_by"),
        "details": details,
        "proposal_count": len(regions),
        "one_to_one_matched_proposals": len(used),
        "proposal_precision_on_partial_reference_scope": None,
        "equipment_recall_established": False,
        "limitation": "Source-only reference regions do not say which equipment is absent from the model; partial reference scope cannot establish whole-page proposal precision.",
    }


def synthesize_and_compare(
    model,
    source_path,
    source_id,
    page,
    output_dir,
    *,
    sha256=None,
    max_dimension=1600,
    max_components=30000,
    ocr=True,
    ocr_timeout=35,
    region_key=None,
    region_references=None,
):
    """Write SVG/PNG reconstruction, pixel evidence and an honest coverage report."""
    started = time.monotonic()
    path, output = Path(source_path), Path(output_dir)
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    source_hashes = model.get("source_hashes") or {}
    expected = sha256 or source_hashes.get(source_id)
    if isinstance(expected, dict):
        expected = expected.get("sha256")
    if not expected or expected.lower() != actual_hash:
        raise ValueError("Synthesis requires the exact hash-bound source.")
    if not 200 <= max_dimension <= 3000 or not 100 <= max_components <= 100000:
        raise ValueError("Synthesis rendering and component budgets are out of range.")
    output.mkdir(parents=True, exist_ok=True)
    image, words, warning = _page(path, page, max_dimension, actual_hash)
    warnings = [warning] if warning else []
    # Scanned board packets often have selectable receipt text but raster tables.
    # A few native footer words must not suppress independent OCR of the scan.
    if len(words) < 20 and ocr:
        scanned_words, warning = _ocr(image, timeout=ocr_timeout)
        words.extend(scanned_words)
        if warning:
            warnings.append(warning)
    elif not words:
        warnings.append(
            "No native PDF words; scan text stays unexplained because OCR is disabled."
        )
    anchors = _semantic_anchors(model, source_id, actual_hash, page)
    matched = _match_words(anchors, words)
    reconstructed, geometry, svg = _draw_synthesis(
        image, anchors, model, source_id, actual_hash, page
    )
    canonical, canonical_svg, canonical_scope = _canonical_model(model, source_id, page)
    ink = image.convert("L").point(lambda p: 255 if p < 165 else 0)
    components, partial = _components(ink, limit=max_components)
    strict = bytearray(image.width * image.height)
    tentative = bytearray(len(strict))
    geometry_pixels = ImageChops.multiply(
        ink, geometry.filter(ImageFilter.MaxFilter(3))
    ).tobytes()
    classifications = []
    for component in components:
        box, pixels = component["bbox"], component["pixels"]
        classification, anchor_id = "unexplained_source_ink", None
        # Classify actual source connected components, not complete model boxes.
        # Large connected conductor/table networks cannot be explained by a word.
        for match in matched:
            if any(
                _overlap(box, word["bbox"]) / max(_area(box), 1e-9) >= 0.8
                for word in match["words"]
            ):
                classification, anchor_id = match["classification"], match["claim_id"]
                target = (
                    strict
                    if classification == "exact_native_text"
                    and not match["needs_review"]
                    and match["localization_tight"]
                    else tentative
                )
                for index in pixels:
                    target[index] = 255
                if target is tentative and classification == "exact_native_text":
                    classification = "unresolved_native_text"
                break
        if (
            classification == "unexplained_source_ink"
            and pixels
            and sum(bool(geometry_pixels[i]) for i in pixels) / len(pixels) >= 0.8
        ):
            classification = "source_located_model_geometry"
            for index in pixels:
                strict[index] = 255
        classifications.append(
            {
                "bbox": box,
                "ink_pixels": len(pixels),
                "classification": classification,
                "claim_id": anchor_id,
            }
        )
    explained = Image.frombytes("L", image.size, bytes(strict))
    tentative_mask = Image.frombytes("L", image.size, bytes(tentative))
    unexplained = ImageChops.subtract(ink, explained)
    regions, cluster_partial, region_count = _unknown_clusters(unexplained)
    table_regions, table_region_count = _table_strips(ink, unexplained)
    regions = table_regions + regions
    region_count += table_region_count
    for index, region in enumerate(regions[:50], 1):
        crop_name = f"unexplained-{index}.png"
        image.crop(_pixel_box(region["bbox"], image.size)).save(output / crop_name)
        region["crop"] = crop_name
    source_pixels = sum(ink.tobytes()) / 255
    explained_pixels = sum(explained.tobytes()) / 255
    tentative_pixels = sum(tentative_mask.tobytes()) / 255
    overlay = image.copy()
    overlay.paste(
        Image.new("RGB", image.size, (220, 40, 70)),
        mask=unexplained.point(lambda p: 130 if p else 0),
    )
    overlay.paste(
        Image.new("RGB", image.size, (30, 150, 90)),
        mask=explained.point(lambda p: 140 if p else 0),
    )
    reconstruction_diff = ImageChops.difference(image, reconstructed)
    for name, value in (
        ("source.png", image),
        ("reconstructed.png", reconstructed),
        ("canonical-model.png", canonical),
        ("unexplained-mask.png", unexplained),
        ("explained-mask.png", explained),
        ("tentative-text-mask.png", tentative_mask),
        ("overlay.png", overlay),
        ("reconstruction-diff.png", reconstruction_diff),
    ):
        value.save(output / name)
    (output / "reconstructed.svg").write_text(svg)
    (output / "canonical-model.svg").write_text(canonical_svg)
    report = {
        "schema_version": "rivet.equipment-synthesis.v1",
        "source_id": source_id,
        "sha256": actual_hash,
        "page": page,
        "pixels": list(image.size),
        "method": "independent_source_ink_components_exact_semantic_text_and_located_path_reconstruction",
        "status": "partial" if partial else "processed",
        "budgets": {
            "max_dimension": max_dimension,
            "max_components": max_components,
            "component_budget_exhausted": partial,
            "region_crop_list_truncated": cluster_partial,
        },
        "ink_pixels": int(source_pixels),
        "strictly_explained_ink_pixels": int(explained_pixels),
        "tentative_text_ink_pixels": int(tentative_pixels),
        "spatial_ink_coverage": explained_pixels / source_pixels
        if source_pixels
        else None,
        "text_words": len(words),
        "semantic_anchors": len(anchors),
        "matched_anchors": len(matched),
        "claim_anchor_diagnostics": {
            "anchor_count": len(anchors),
            "matched_claim_count": len(
                {m["claim_id"] for m in matched if m.get("claim_id")}
            ),
            "unmatched_claim_count": len(
                {a["claim_id"] for a in anchors if a.get("claim_id")}
                - {m["claim_id"] for m in matched if m.get("claim_id")}
            ),
            "raw_numeric_candidate_rejections": sum(
                a.get("raw_numeric_candidate_rejected", False) for a in anchors
            ),
            "meaning": "Unmatched may mean bad localization, wrong reading, independent OCR failure or unsupported text; never silently repaired.",
        },
        "canonical_projection": canonical_scope,
        "components": classifications,
        "unexplained_regions": regions,
        "unexplained_region_count": region_count,
        "source_table_strip_proposals": table_region_count,
        "independent_unexplained_region_recall": _independent_recall(
            regions,
            unexplained,
            ink,
            region_key,
            source_id=source_id,
            sha256=actual_hash,
            page=page,
        ),
        "independent_annotated_region_localization": _annotated_region_localization(
            regions,
            region_references,
            source_id=source_id,
            sha256=actual_hash,
            page=page,
        ),
        "equipment_extraction_recall": None,
        "engineering_verified": False,
        "requires_human_verification": True,
        "warnings": warnings,
        "limitations": [
            "Ink coverage is spatial accounting, not device recall or electrical correctness.",
            "OCR-only scan matches remain tentative and do not increase strict coverage.",
            "Layout, symbols, unmodelled labels and genuine missed devices can all produce unexplained ink.",
            "Canonical text reconstruction uses substitute fonts; raw pixel difference is diagnostic only.",
            "A matched numeric token does not independently establish its association with a device.",
        ],
        "artifacts": {
            "source": "source.png",
            "reconstruction": "reconstructed.png",
            "canonical_model": "canonical-model.png",
            "canonical_model_svg": "canonical-model.svg",
            "svg": "reconstructed.svg",
            "overlay": "overlay.png",
            "unexplained_mask": "unexplained-mask.png",
            "raw_pixel_difference": "reconstruction-diff.png",
        },
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    (output / "synthesis-report.json").write_text(json.dumps(report, indent=2))
    return report
