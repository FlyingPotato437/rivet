"""Bounded drawing arithmetic, with native cells and an independent saved check.

This is not transformer design. A supported single-phase current table can be
made arithmetically consistent with its own explicit voltage and MVA inputs.
Conflicting capacity values block that column, not silently choose a value.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from itertools import pairwise

from .cad_evidence import _cell, anchor, text


def _one(items, reason):
    if len(items) != 1:
        raise ValueError(reason)
    return items[0]


def _segments(snapshot, layout):
    segments = []
    for item in snapshot.get("placed_lines", []):
        if item.get("layout", "Model") == layout:
            segments.append((item["start"], item["end"], item["locator"]))
    for item in snapshot.get("placed_paths", []):
        if item.get("layout", "Model") == layout and item["kind"] == "LWPOLYLINE":
            segments.extend(
                (a, b, item["locator"]) for a, b in pairwise(item["points"])
            )
    if len(segments) > 100_000:
        raise ValueError("Drawing calculation geometry budget exceeded")
    return segments


def _grid_cell(snapshot, item, segments):
    """Require four native straight boundaries; text proximity alone is insufficient."""
    x, y = item["x"], item["y"]
    tolerance = max(item.get("text_height", 1), 1e-3) * 1e-4
    vertical, horizontal = [], []
    for a, b, locator in segments:
        if abs(a[0] - b[0]) <= tolerance and min(a[1], b[1]) < y < max(a[1], b[1]):
            vertical.append((a[0], min(a[1], b[1]), max(a[1], b[1]), locator))
        if abs(a[1] - b[1]) <= tolerance and min(a[0], b[0]) < x < max(a[0], b[0]):
            horizontal.append((a[1], min(a[0], b[0]), max(a[0], b[0]), locator))
    try:
        x0, x1 = (
            max(v[0] for v in vertical if v[0] < x),
            min(v[0] for v in vertical if v[0] > x),
        )
        y0, y1 = (
            max(v[0] for v in horizontal if v[0] < y),
            min(v[0] for v in horizontal if v[0] > y),
        )
    except ValueError as exc:
        raise ValueError("A numeric value has no closed native table cell") from exc
    boundaries = []
    for value, candidates, low, high in (
        (x0, vertical, y0, y1),
        (x1, vertical, y0, y1),
        (y0, horizontal, x0, x1),
        (y1, horizontal, x0, x1),
    ):
        matches = [
            v
            for v in candidates
            if abs(v[0] - value) <= tolerance
            and v[1] <= low + tolerance
            and v[2] >= high - tolerance
        ]
        if not matches:
            raise ValueError("Native table boundary does not span the complete cell")
        boundaries.extend(v[3] for v in matches)
    cell = _cell(
        {
            "bbox": [x0, y0, x1, y1],
            "geometry_locator": sorted(set(boundaries)),
            "layout": item.get("layout", "Model"),
        },
        snapshot["placed_text"],
        snapshot["source_sha256"],
    )
    if (
        cell["status"] != "observed"
        or cell["evidence"][0]["locator"] != item["locator"]
    ):
        raise ValueError("A numeric table cell is ambiguous")
    return cell


def _literal(item, sha):
    return {**anchor(item, sha), "text": text(item["text"])}


def _analyse(snapshot, voltage_comment):
    requested = re.findall(r"\d+(?:\.\d+)?", voltage_comment["text"])
    if not 2 <= len(requested) <= 10 or len(set(requested)) != len(requested):
        raise ValueError("The requested voltage list is not unique and finite")
    placed, sha = snapshot["placed_text"], snapshot["source_sha256"]
    values = [
        _one(
            [t for t in placed if text(t["text"]) == v],
            "Requested voltages have missing or duplicate CAD occurrences",
        )
        for v in requested
    ]
    layout = values[0].get("layout", "Model")
    if any(t.get("layout", "Model") != layout or t.get("block_path") for t in values):
        raise ValueError(
            "Shared or cross-layout current tables require an explicit mapping"
        )
    segments = _segments(snapshot, layout)
    cells = [_grid_cell(snapshot, t, segments) for t in values]
    x0, _, x1, _ = cells[0]["bbox"]
    unit = x1 - x0
    if any(
        abs(c["bbox"][0] - x0) > unit * 1e-4 or abs(c["bbox"][2] - x1) > unit * 1e-4
        for c in cells
    ):
        raise ValueError("Requested voltages do not share one native column")
    if [t["y"] for t in values] != sorted((t["y"] for t in values), reverse=True):
        raise ValueError("Voltage row order differs from the customer's request")
    if any(abs(a["bbox"][1] - b["bbox"][3]) > unit * 1e-4 for a, b in pairwise(cells)):
        raise ValueError("Voltage rows are not contiguous")
    top = cells[0]["bbox"][3]
    region = [
        t
        for t in placed
        if t.get("layout", "Model") == layout
        and x0 - 15 * unit < t["x"] < x1 + 5 * unit
        and top - 25 * unit < t["y"] < top + 5 * unit
    ]
    header = _one(
        [
            t
            for t in region
            if x0 < t["x"] < x1
            and top < t["y"] < top + unit
            and text(t["text"]) == "VOLTS(V)"
        ],
        "Missing explicit voltage units",
    )
    phase = _one(
        [t for t in region if text(t["text"]).casefold() == "number of phases"],
        "Missing unique phase count label",
    )

    def labelled_value(label):
        # Nameplate field labels may sit outside their boxed value. Require
        # one aligned value, its complete native box and bounded label gap.
        value = _one(
            [
                t
                for t in region
                if label["x"] < t["x"] < label["x"] + 6 * unit
                and abs(t["y"] - label["y"]) < max(label.get("text_height", 1), 1e-3)
            ],
            "Missing unique aligned nameplate field",
        )
        cell = _grid_cell(snapshot, value, segments)
        if (
            not label["x"] < cell["bbox"][0] < label["x"] + 5 * unit
            or not cell["bbox"][1] < label["y"] < cell["bbox"][3]
        ):
            raise ValueError("Nameplate field is not aligned with its boxed value")
        return value

    phase_value = labelled_value(phase)
    if text(phase_value["text"]) != "1":
        raise ValueError("Only explicitly single-phase tables are supported")
    capacity_label = _one(
        [t for t in region if text(t["text"]).casefold() == "tertiary capacity"],
        "Missing explicit tertiary capacity record",
    )
    capacity_value = labelled_value(capacity_label)
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)*)\s*MVA(?:@(.+))?",
        text(capacity_value["text"]),
        re.IGNORECASE,
    )
    if not match:
        raise ValueError("Capacity record is not an explicit MVA list")
    capacity_numbers = [Decimal(v) for v in match[1].split("/")]
    if len(capacity_numbers) < 2 or any(a >= b for a, b in pairwise(capacity_numbers)):
        raise ValueError("Capacity stages must be explicitly increasing")
    # Only the two end ratings of the explicitly printed capacity list are
    # supported here; intermediate stages need their own table binding.
    ratings = [
        t
        for t in region
        if x1 < t["x"] < x1 + 3 * unit
        and top < t["y"] < top + unit
        and re.fullmatch(r"\d+(?:\.\d+)?\s*MVA", text(t["text"]), re.IGNORECASE)
    ]
    ratings.sort(key=lambda t: t["x"])
    if len(ratings) != 2:
        raise ValueError("The current table must have two unambiguous MVA headers")
    amp_header = _one(
        [
            t
            for t in region
            if ratings[0]["x"] < t["x"] < ratings[-1]["x"]
            and ratings[0]["y"] < t["y"] < top + unit
            and text(t["text"]) == "AMPS(A)"
        ],
        "Missing explicit current units",
    )
    observations, blocked = [], []
    for index, rating in enumerate(ratings):
        rating_cell = _grid_cell(snapshot, rating, segments)
        rating_value = Decimal(re.match(r"\d+(?:\.\d+)?", text(rating["text"]))[0])
        governing = capacity_numbers[0 if index == 0 else -1]
        if rating_value != governing:
            blocked.append(
                {
                    "code": "capacity_conflict",
                    "table_header": _literal(rating, sha),
                    "capacity_record": _literal(capacity_value, sha),
                    "detail": f"Printed header {rating_value} MVA differs from the capacity record's {governing} MVA. This column is unchanged; an engineer must establish the governing value.",
                }
            )
            continue
        if not Decimal(0) < rating_value <= Decimal(1000):
            raise ValueError(
                "Current-table rating is outside the bounded supported range"
            )
        for voltage, cell in zip(values, cells):
            low, high = cell["bbox"][1], cell["bbox"][3]
            target = _one(
                [
                    t
                    for t in region
                    if rating_cell["bbox"][0] < t["x"] < rating_cell["bbox"][2]
                    and low < t["y"] < high
                ],
                "Current row is missing or ambiguous",
            )
            target_cell = _grid_cell(snapshot, target, segments)
            if any(
                abs(a - b) > unit * 1e-4
                for a, b in zip(
                    target_cell["bbox"],
                    [rating_cell["bbox"][0], low, rating_cell["bbox"][2], high],
                )
            ):
                raise ValueError(
                    "Current row does not align with voltage and rating cells"
                )
            if target.get("block_path") or not re.fullmatch(
                r"\d+", text(target["text"])
            ):
                raise ValueError(
                    "Only unshared whole-ampere current cells can be corrected"
                )
            v = Decimal(text(voltage["text"]))
            if not 0 < v <= 1_000_000:
                raise ValueError("Invalid table voltage")
            replacement = str(
                (rating_value * 1_000_000 / v).quantize(
                    Decimal(1), rounding=ROUND_HALF_UP
                )
            )
            observations.append(
                {
                    "target": target,
                    "replacement": replacement,
                    "current_cell": target_cell,
                    "voltage": _literal(voltage, sha),
                    "rating": _literal(rating, sha),
                    "phase_count": _literal(phase_value, sha),
                    "capacity_record": _literal(capacity_value, sha),
                    "unit_headers": [_literal(header, sha), _literal(amp_header, sha)],
                    "formula": "I(A) = S(MVA) * 1000000 / V(V); single phase; nearest whole ampere, half up",
                    "review_bbox": [
                        x0 - 2 * unit,
                        cells[-1]["bbox"][1] - unit,
                        ratings[-1]["x"] + 2 * unit,
                        top + 2 * unit,
                    ],
                }
            )
    return observations, blocked, values


def propose_current_corrections(comments, snapshot):
    audits, edits = [], []
    for comment in comments:
        if not re.fullmatch(
            r"update amp values to reflect correct voltages[.!]?",
            " ".join(comment["text"].split()),
            re.IGNORECASE,
        ):
            continue
        companions = [
            c
            for c in comments
            if c.get("source_document_id") == comment.get("source_document_id")
            and c.get("page") == comment.get("page")
            and re.fullmatch(
                r"(?:\s*\d+(?:\.\d+)?\s*V\s*){2,10}", c["text"], re.IGNORECASE
            )
        ]
        try:
            voltage_comment = _one(
                companions, "No unique source-bound voltage-list comment"
            )
            observations, blocked, values = _analyse(snapshot, voltage_comment)
        except ValueError as exc:
            audits.append(
                {
                    "comment_id": comment["id"],
                    "status": "blocked",
                    "reason": str(exc),
                    "engineering_verified": False,
                }
            )
            continue
        audit = {
            "comment_id": comment["id"],
            "status": "partial_draft" if blocked else "draft_prepared",
            "blocked_columns": blocked,
            "calculations": [],
            "engineering_verified": False,
        }
        for row in observations:
            t = row.pop("target")
            calc = {
                **row,
                "target": _literal(t, snapshot["source_sha256"]),
                "before": text(t["text"]),
            }
            audit["calculations"].append(calc)
            if calc["before"] == row["replacement"]:
                continue
            identifier = hashlib.sha256(
                json.dumps([comment["id"], t["locator"], row["replacement"]]).encode()
            ).hexdigest()[:24]
            edit = {
                "id": identifier,
                "comment_id": comment["id"],
                "handle": t["handle"],
                "entity_type": t["kind"],
                "expected_text": t["text"],
                "replacement_text": row["replacement"],
                "allow_shared_definition": False,
                "label": "Recalculate documented tertiary current",
                "kind": "cad_calculation",
                "source_locator": t["locator"],
                "x": t["x"],
                "y": t["y"],
                "review_bbox": row["review_bbox"],
                "scope_note": "Draft table arithmetic only, using matching printed MVA ratings and explicit phase count. No winding, protection or physical equipment change is approved.",
                "calculation": calc,
            }
            edits.append(edit)
            comment["change_ids"].append(identifier)
        voltage_comment.update(
            status="already_present",
            explanation="All requested voltage values already occur in the matched native table, in the requested row order. These are existing manufacturer values, not Rivet edits.",
            evidence_handles=[t["handle"] for t in values],
        )
        comment["explanation"] = (
            f"Prepared {sum(e['comment_id'] == comment['id'] for e in edits)} current-cell corrections using the explicit single-phase formula and matching printed capacity. "
            + (
                "A conflicting MVA header leaves the other column unchanged; engineer confirmation is required."
                if blocked
                else "The saved arithmetic is rechecked; transformer design and rating selection still require engineer approval."
            )
        )
        # Keep engineering approval separate from completing arithmetic.
        comment["status"] = "needs_information"
        audits.append(audit)
    return edits, audits


def verify_saved_calculations(saved, audits):
    """Recompute from saved inputs with exact fractions, not proposed outputs."""
    by_locator = {}
    for item in saved["placed_text"]:
        if item["locator"] in by_locator:
            raise ValueError("Duplicate saved calculation locator")
        by_locator[item["locator"]] = item
    checks, errors = [], []
    for audit in audits:
        for calc in audit.get("calculations", []):
            try:
                inputs = {}
                for key in ("voltage", "rating", "phase_count", "capacity_record"):
                    evidence = calc[key]
                    value = text(by_locator[evidence["locator"]]["text"])
                    if value != evidence["text"]:
                        raise ValueError("A calculation input changed")
                    inputs[key] = value
                if inputs["phase_count"] != "1":
                    raise ValueError("Saved phase count is not single phase")
                amount = (
                    Fraction(
                        re.sub(
                            r"\s*MVA$", "", inputs["rating"], flags=re.IGNORECASE
                        ).strip()
                    )
                    * 1_000_000
                    / Fraction(inputs["voltage"])
                )
                rounded = (2 * amount.numerator + amount.denominator) // (
                    2 * amount.denominator
                )
                value = text(by_locator[calc["target"]["locator"]]["text"])
                if value != str(rounded):
                    raise ValueError(
                        "Saved current does not equal independently recomputed amperes"
                    )
                checks.append(
                    {
                        "locator": calc["target"]["locator"],
                        "saved_current_a": value,
                        "voltage_v": inputs["voltage"],
                        "rating": inputs["rating"],
                        "verified": True,
                    }
                )
            except (KeyError, ValueError, ZeroDivisionError) as exc:
                errors.append(str(exc))
    return {
        "passed": not errors,
        "cells_checked": len(checks),
        "checks": checks,
        "errors": errors,
        "engineering_verified": False,
    }
