"""Locate complete source schedule enclosures, without interpreting their values.

Only a hash-bound PDF and physical pages are inputs. OCR establishes a breaker
schedule title and printed column-heading families; source rules establish the
entire enclosure, including sparse rows, continuation arrows and in-table notes.
No expected values, catalog, answer key, region hints or model are consumed.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

from backend.equipment.evidence.table_reader import (
    _axis_lines,
    _deskew,
    _ocr,
    _orient,
    _render_plan,
    _Transform,
)

VERSION = "rivet.source-schedule-regions.v1"


def _word_box(word):
    left, top, width, height = word["box"]
    return [left, top, left + width, top + height]


def _union(boxes):
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _tokens(text):
    return set(re.findall(r"[A-Z]+", text.upper()))


def _titles(words, minimum):
    lines = defaultdict(list)
    for word in words:
        if word["confidence"] >= minimum:
            lines[word["line"]].append(word)
    candidates = []
    for line in lines.values():
        text = " ".join(w["text"] for w in line)
        tokens = _tokens(text)
        if "SCHEDULE" in tokens and tokens & {"BREAKER", "BREAKERS", "CIRCUIT"}:
            candidates.append(
                {
                    "text": text,
                    "bbox": _union([_word_box(w) for w in line]),
                    "minimum_ocr_confidence": min(w["confidence"] for w in line),
                }
            )
    return candidates


def _heading_families(words, minimum):
    # These are source heading concepts, not field mappings or engineering units.
    families = {
        "identity": {"DEVICE", "DEVICES", "CIRCUIT", "CKT", "DESIGNATION"},
        "frame": {"FRAME"},
        "current": {"CURRENT", "CONTINUOUS", "AMP", "AMPS", "AMPERES"},
        "interrupting": {"INTERRUPT", "INTERRUPTING", "INTERRUPTION"},
        "trip": {"TRIP"},
        "remarks": {"REMARKS", "DESCRIPTION"},
        "poles": {"POLE", "POLES"},
    }
    found = {}
    for word in words:
        if word["confidence"] < minimum:
            continue
        tokens = _tokens(word["text"])
        for family, aliases in families.items():
            if tokens & aliases:
                found.setdefault(family, []).append(word)
    return found


def _covers(line, low, high, tolerance):
    return line.start <= low + tolerance and line.end >= high - tolerance


def _rulings(image):
    binary = np.asarray(image) < 175
    return (
        _axis_lines(binary, max(40, image.width // 100)),
        _axis_lines(binary.T, max(40, image.height // 60)),
    )


def _title_bands(image, horizontal, vertical, minimum):
    """Read source-derived ruled title bands instead of tiny full-sheet text."""
    bands = []
    for top in horizontal:
        width = top.end - top.start
        if not image.width * 0.07 <= width <= image.width * 0.95:
            continue
        tolerance = max(7, width * 0.012)
        for bottom in horizontal:
            height = bottom.position - top.position
            if not (
                10 <= height <= image.height * 0.06
                and 5 <= width / height <= 50
                and abs(bottom.start - top.start) <= tolerance
                and abs(bottom.end - top.end) <= tolerance
            ):
                continue
            if not all(
                any(
                    abs(v.position - edge) <= tolerance
                    and v.start <= top.position + tolerance
                    and v.end >= bottom.position + height * 2
                    for v in vertical
                )
                for edge in (top.start, top.end)
            ):
                continue
            bands.append(
                [top.start + 4, top.position + 4, top.end - 4, bottom.position - 4]
            )
    if len(bands) > 80:
        return [], "title_band_budget_exceeded"
    titles = []
    for box in bands:
        if box[3] <= box[1]:
            continue
        crop = image.crop(tuple(round(v) for v in box))
        scale = max(1, min(3, 100 / crop.height))
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)))
        words = _ocr(crop, psm=7, timeout=10)
        for word in words:
            x, y, w, h = word["box"]
            word["box"] = [box[0] + x / scale, box[1] + y / scale, w / scale, h / scale]
        titles.extend(_titles(words, minimum))
    unique = {tuple(round(v) for v in t["bbox"]): t for t in titles}
    return list(unique.values()), None


def _enclosures(image, title, rulings=None):
    """Bind a title band to continuous side rules and their common bottom rule.

    Unlike a cell reader, this does not require bounded individual body rows.
    The bottom is derived from both outer sides, never from the last OCR word.
    """
    horizontal, vertical = rulings or _rulings(image)
    tx0, ty0, tx1, ty1 = title["bbox"]
    title_height = max(8, ty1 - ty0)
    options = []
    for top in horizontal:
        width = top.end - top.start
        tolerance = max(7, width * 0.012)
        if not (
            0 < ty0 - top.position < title_height * 3
            and _covers(top, tx0, tx1, tolerance)
            and width <= (tx1 - tx0) * 4
        ):
            continue
        bands = [
            h
            for h in horizontal
            if 0 < h.position - ty1 < title_height * 3
            and abs(h.start - top.start) <= tolerance
            and abs(h.end - top.end) <= tolerance
        ]
        for band in bands:
            lefts = [
                v
                for v in vertical
                if abs(v.position - top.start) <= tolerance
                and v.start <= top.position + tolerance
                and v.end > band.position + title_height * 3
            ]
            rights = [
                v
                for v in vertical
                if abs(v.position - top.end) <= tolerance
                and v.start <= top.position + tolerance
                and v.end > band.position + title_height * 3
            ]
            for left in lefts:
                for right in rights:
                    bottom_end = min(left.end, right.end)
                    bottom_tolerance = max(9, (bottom_end - top.position) * 0.015)
                    bottoms = [
                        h
                        for h in horizontal
                        if h.position > band.position + title_height * 3
                        and abs(h.position - left.end) <= bottom_tolerance
                        and abs(h.position - right.end) <= bottom_tolerance
                        and _covers(h, left.position, right.position, tolerance)
                    ]
                    for bottom in bottoms:
                        internals = [
                            v
                            for v in vertical
                            if left.position + tolerance
                            < v.position
                            < right.position - tolerance
                            and v.start <= band.position + tolerance
                            and v.end >= bottom.position - bottom_tolerance
                        ]
                        # Two measured interior rules establish at least three
                        # columns; unruled paragraph/title boxes remain unknown.
                        if len(internals) < 2:
                            continue
                        options.append(
                            {
                                "bbox": [
                                    left.position,
                                    top.position,
                                    right.position,
                                    bottom.position,
                                ],
                                "title_band_bottom": band.position,
                                "interior_column_rules": len(internals),
                                "outer_rules_complete": True,
                            }
                        )
    unique = {}
    for option in options:
        key = tuple(round(v) for v in option["bbox"])
        if (
            key not in unique
            or option["title_band_bottom"] < unique[key]["title_band_bottom"]
        ):
            unique[key] = option
    # Ambiguous competing enclosures are not silently resolved by area.
    return list(unique.values())


def _crop_plan(box, page_rect, requested, maximum):
    clip = pymupdf.Rect(
        box[0] * page_rect.width,
        box[1] * page_rect.height,
        box[2] * page_rect.width,
        box[3] * page_rect.height,
    )
    effective = min(float(requested), maximum * 72 / max(clip.width, clip.height))
    if effective < requested:
        effective *= 0.999
    return clip, effective


def _save_crop(path, content):
    checksum = hashlib.sha256(content).hexdigest()
    if path.exists():
        if (
            path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest() != checksum
        ):
            raise ValueError("schedule_crop_already_exists_with_different_bytes")
    else:
        with path.open("xb") as stream:
            stream.write(content)
        path.chmod(0o600)
    return checksum


def locate_schedule_regions(
    source,
    page,
    *,
    output_dir=None,
    preview_dpi=150,
    crop_dpi=360,
    max_crop_dimension=3000,
    max_pages=4,
    minimum_ocr_confidence=55.0,
):
    """Return conservative source-bound complete schedule crops, or no regions.

    Each crop is rendered directly from its original physical PDF page at
    250–360 DPI, adapting down only to meet the pixel limit. If that requires
    less than 250 DPI, no crop is emitted. `bbox` always uses the original page's
    normalized top-left coordinates, including when the page was rotated.
    OCR confidence is diagnostic, not a probability of engineering correctness.
    """
    data = source.model_dump() if hasattr(source, "model_dump") else dict(source)
    source_id = data.get("id", data.get("source_id"))
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("schedule_source_id_required")
    checksum = str(data.get("sha256", "")).lower()
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise ValueError("schedule_source_sha256_required")
    path = Path(data["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise ValueError("source_hash_mismatch")
    pages = [page] if isinstance(page, (int, bool)) else list(page)
    if not pages or len(pages) > max_pages or len(set(pages)) != len(pages):
        raise ValueError("page_budget_or_duplicate_pages")
    if not (
        100 <= preview_dpi <= 200
        and 250 <= crop_dpi <= 360
        and 500 <= max_crop_dimension <= 3000
        and 0 <= minimum_ocr_confidence <= 100
        and 1 <= max_pages <= 12
    ):
        raise ValueError("invalid_schedule_region_limits")
    target = Path(output_dir) if output_dir is not None else None
    if target and (target.is_symlink() or any(p.is_symlink() for p in target.parents)):
        raise ValueError("schedule_output_directory_symlink")
    report = {
        "version": VERSION,
        "source_id": source_id,
        "sha256": checksum,
        "regions": [],
        "pages": [],
        "requires_human_verification": True,
        "coverage_complete": False,
        "provider_model_calls": 0,
    }
    seen_regions = set()
    with pymupdf.open(path) as pdf:
        if any(
            not isinstance(p, int) or isinstance(p, bool) or not 1 <= p <= len(pdf)
            for p in pages
        ):
            raise ValueError("invalid_physical_page")
        for physical in pages:
            page = pdf[physical - 1]
            result = {"page": physical, "status": "unknown", "reasons": []}
            report["pages"].append(result)
            plan = _render_plan(page.rect, preview_dpi)
            result["preview_render_plan"] = plan
            if not plan["legibility_floor_met"]:
                result["reasons"].append("preview_below_legibility_floor")
                continue
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(
                    plan["effective_dpi"] / 72, plan["effective_dpi"] / 72
                ),
                alpha=False,
            )
            original = Image.frombytes(
                "RGB", (pix.width, pix.height), pix.samples
            ).convert("L")
            try:
                clockwise, established, scores = _orient(original)
                result.update(
                    orientation_clockwise=clockwise, orientation_scores=scores
                )
                if not established:
                    result["reasons"].append("orientation_not_established")
                    continue
                upright = original.rotate(-clockwise, expand=True)
                angle = _deskew(upright)
                image = upright.rotate(angle, expand=True, fillcolor=255)
                transform = _Transform(
                    original.size, upright.size, image.size, clockwise, angle
                )
                rulings = _rulings(image)
                titles, title_error = _title_bands(
                    image, *rulings, minimum_ocr_confidence
                )
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                result["reasons"].append(type(exc).__name__)
                continue
            result.update(deskew_ccw=angle, title_candidate_count=len(titles))
            if title_error:
                result["reasons"].append(title_error)
            if not titles:
                result["reasons"].append("breaker_schedule_title_not_established")
            if len(titles) > 6:
                result["reasons"].append("too_many_schedule_titles")
                continue
            for title in titles:
                enclosures = _enclosures(image, title, rulings)
                if len(enclosures) != 1:
                    result["reasons"].append(
                        "complete_schedule_enclosure_ambiguous_or_missing"
                    )
                    continue
                enclosure = enclosures[0]
                x0, y0, x1, y1 = enclosure["bbox"]
                header_bottom = min(
                    y1,
                    enclosure["title_band_bottom"]
                    + (title["bbox"][3] - title["bbox"][1]) * 6,
                )
                header_box = [
                    x0 + 3,
                    enclosure["title_band_bottom"] + 3,
                    x1 - 3,
                    header_bottom,
                ]
                header_image = image.crop(tuple(round(v) for v in header_box))
                header_scale = max(1, min(2, 240 / header_image.height))
                header_image = header_image.resize(
                    (
                        round(header_image.width * header_scale),
                        round(header_image.height * header_scale),
                    )
                )
                try:
                    header_words = _ocr(header_image, psm=11, timeout=20)
                except (RuntimeError, subprocess.TimeoutExpired) as exc:
                    result["reasons"].append(type(exc).__name__)
                    continue
                families = _heading_families(header_words, minimum_ocr_confidence)
                if len(families) < 2 or not (
                    families.keys()
                    & {"frame", "current", "trip", "interrupting", "poles"}
                ):
                    result["reasons"].append(
                        "supported_schedule_heading_families_not_established"
                    )
                    continue
                # A small source-space margin preserves border ink itself.
                box = transform.source_box([x0 - 3, y0 - 3, x1 + 3, y1 + 3])
                clip, effective_dpi = _crop_plan(
                    box, page.rect, crop_dpi, max_crop_dimension
                )
                if effective_dpi < 250:
                    result["reasons"].append(
                        "complete_crop_below_250_dpi_at_pixel_limit"
                    )
                    continue
                crop = page.get_pixmap(
                    matrix=pymupdf.Matrix(effective_dpi / 72, effective_dpi / 72),
                    clip=clip,
                    alpha=False,
                )
                if max(crop.width, crop.height) > max_crop_dimension:
                    result["reasons"].append("crop_pixel_limit_rounding")
                    continue
                content = crop.tobytes("png")
                identifier = hashlib.sha256(
                    f"{checksum}:{physical}:{box}".encode()
                ).hexdigest()[:24]
                if identifier in seen_regions:
                    continue
                seen_regions.add(identifier)
                crop_checksum = hashlib.sha256(content).hexdigest()
                crop_path = None
                if target is not None:
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    crop_path = target / f"page-{physical}-schedule-{identifier}.png"
                    crop_checksum = _save_crop(crop_path, content)
                title_ref = {**title, "bbox": transform.source_box(title["bbox"])}
                headings = []
                for family, words in families.items():
                    for word in words:
                        word_box = [v / header_scale for v in _word_box(word)]
                        headings.append(
                            {
                                "family": family,
                                "text": word["text"],
                                "ocr_confidence": word["confidence"],
                                "bbox": transform.source_box(
                                    [
                                        word_box[0] + header_box[0],
                                        word_box[1] + header_box[1],
                                        word_box[2] + header_box[0],
                                        word_box[3] + header_box[1],
                                    ]
                                ),
                            }
                        )
                report["regions"].append(
                    {
                        "id": "schedule-region:" + identifier,
                        "source_id": source_id,
                        "sha256": checksum,
                        "page": physical,
                        "bbox": box,
                        "png": content,
                        "coordinate_system": "normalized_top_left",
                        "kind": "breaker_schedule",
                        "requires_human_verification": True,
                        "crop": {
                            "path": str(crop_path) if crop_path else None,
                            "sha256": crop_checksum,
                            "pixel_size": [crop.width, crop.height],
                            "render_dpi": effective_dpi,
                            "requested_dpi": crop_dpi,
                            "rendered_directly_from_source": True,
                        },
                        "evidence": {
                            "title": title_ref,
                            "headings": headings,
                            "heading_families": sorted(families),
                            "outer_rules_complete": True,
                            "interior_column_rules": enclosure["interior_column_rules"],
                            "extent_policy": "Complete enclosing source border; no truncation at OCR rows. In-table arrows, remarks and notes retained.",
                        },
                    }
                )
                result["status"] = "regions_located"
    report["status"] = "regions_located" if report["regions"] else "unknown"
    report["diagnostics"] = report["pages"]
    return report
