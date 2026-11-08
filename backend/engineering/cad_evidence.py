"""Evidence from placed CAD cells, never a fabricated production BOM or netlist.

The native file's rectangles establish cell boundaries. Missing headers and
ambiguous cells remain unknown; proximity and matching tag text establish only
review candidates, not physical component identity or electrical connectivity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import defaultdict
from itertools import pairwise

from ezdxf.tools.text import plain_mtext

VERSION = "rivet.cad-evidence.v1"


def text(value):
    return " ".join(plain_mtext(value).replace("^J", " ").split())


def _id(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:24]


def anchor(item, sha):
    return {
        "source_sha256": sha,
        "locator": item["locator"],
        "handle": item["handle"],
        "layout": item.get("layout", "Model"),
        "layer": item.get("layer"),
        "block_path": item.get("block_path", []),
        "point": [item["x"], item["y"]] if "x" in item else None,
    }


def _rectangles(snapshot):
    rectangles = []
    for path in snapshot.get("placed_paths", []):
        points = path["points"]
        if path["kind"] != "LWPOLYLINE" or len(points) != 5:
            continue
        x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
        y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
        tol = max(x1 - x0, y1 - y0) * 1e-6
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            continue
        corners = {
            (round((p[0] - x0) / (x1 - x0)), round((p[1] - y0) / (y1 - y0)))
            for p in points
        }
        if corners != {(0, 0), (0, 1), (1, 0), (1, 1)}:
            continue
        if any(
            min(abs(p[0] - x0), abs(p[0] - x1)) > tol
            or min(abs(p[1] - y0), abs(p[1] - y1)) > tol
            for p in points
        ):
            continue
        if any(
            abs(a[0] - b[0]) > tol and abs(a[1] - b[1]) > tol
            for a, b in pairwise(points)
        ):
            continue
        if math.dist(points[0], points[-1]) > tol:
            continue
        rectangles.append(
            {
                "bbox": [x0, y0, x1, y1],
                "geometry_locator": path["locator"],
                "layout": path.get("layout", "Model"),
            }
        )
    return rectangles


def _cell(rect, placed, sha):
    x0, y0, x1, y1 = rect["bbox"]
    items = [
        t
        for t in placed
        if t.get("layout", "Model") == rect["layout"]
        and x0 < t["x"] < x1
        and y0 < t["y"] < y1
        and text(t["text"])
    ]
    items.sort(key=lambda t: (-t["y"], t["x"], t["locator"]))
    explicit = len(items) == 1 and abs(items[0].get("text_rotation", 0)) < 1e-5
    return {
        **rect,
        "value": text(items[0]["text"]) if explicit else None,
        "status": "observed" if explicit else "blank" if not items else "ambiguous",
        "confidence": "native_cell" if explicit else "unknown",
        "evidence": [{**anchor(t, sha), "text": text(t["text"])} for t in items],
    }


HEADERS = {
    "no": "item",
    "no.": "item",
    "symbol": "symbol",
    "name": "description",
    "type": "type",
    "qty": "quantity",
    "qty.": "quantity",
    "quantity": "quantity",
    "part number": "part_number",
    "catalog": "part_number",
}


def extract_schedules(snapshot):
    """Contiguous rectangular tables with explicit No/Symbol/Name headers."""
    deadline = time.monotonic() + 10

    def check_budget():
        if time.monotonic() > deadline:
            raise ValueError("native_schedule_time_budget")

    sha, placed = snapshot["source_sha256"], snapshot["placed_text"]
    rects = _rectangles(snapshot)
    if len(rects) > 25_000:
        raise ValueError("native_schedule_geometry_limit")
    schedules, seen = [], set()
    for title in placed:
        check_budget()
        if text(title["text"]).casefold() not in {"no", "no."}:
            continue
        containers = [
            r
            for r in rects
            if r["layout"] == title.get("layout", "Model")
            and r["bbox"][0] < title["x"] < r["bbox"][2]
            and r["bbox"][1] < title["y"] < r["bbox"][3]
        ]
        # Outer sheet/table frames can also contain the header. The smallest
        # box is the cell; coincident boxes remain ambiguous.
        if containers:
            area = lambda r: (
                (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1])
            )
            smallest = min(map(area, containers))
            containers = [
                r for r in containers if math.isclose(area(r), smallest, rel_tol=1e-6)
            ]
        if len(containers) != 1:
            continue
        first = containers[0]
        x0, y0, _x1, y1 = first["bbox"]
        tol = (y1 - y0) * 1e-4
        row = [first]
        # Follow adjacent cells; never infer missing column widths.
        for _ in range(24):
            candidates = [
                r
                for r in rects
                if r["layout"] == first["layout"]
                and abs(r["bbox"][0] - row[-1]["bbox"][2]) < tol
                and abs(r["bbox"][1] - y0) < tol
                and abs(r["bbox"][3] - y1) < tol
            ]
            if len(candidates) != 1:
                break
            row.append(candidates[0])
        header = [_cell(r, placed, sha) for r in row]
        fields = [
            HEADERS.get((c["value"] or "").casefold(), f"unlabelled_{i + 1}")
            for i, c in enumerate(header)
        ]
        if not {"item", "symbol", "description"} <= set(fields) or len(fields) != len(
            set(fields)
        ):
            continue
        if first["geometry_locator"] in seen:
            continue
        seen.add(first["geometry_locator"])
        rows, top = [], y0
        for _ in range(500):
            check_budget()
            next_row = []
            for col in row:
                candidates = [
                    r
                    for r in rects
                    if r["layout"] == first["layout"]
                    and abs(r["bbox"][0] - col["bbox"][0]) < tol
                    and abs(r["bbox"][2] - col["bbox"][2]) < tol
                    and abs(r["bbox"][3] - top) < tol
                ]
                if len(candidates) != 1:
                    break
                next_row.append(candidates[0])
            if (
                len(next_row) != len(row)
                or len({round(r["bbox"][1], 5) for r in next_row}) != 1
            ):
                break
            cells = {f: _cell(r, placed, sha) for f, r in zip(fields, next_row)}
            rows.append(
                {
                    "id": _id(sha, first["geometry_locator"], len(rows) + 1),
                    "row": len(rows) + 1,
                    "cells": cells,
                }
            )
            top = next_row[0]["bbox"][1]
        if rows:
            schedules.append(
                {
                    "id": _id(sha, first["geometry_locator"]),
                    "source_sha256": sha,
                    "layout": first["layout"],
                    "bbox": [x0, top, row[-1]["bbox"][2], y1],
                    "columns": [
                        {"field": f, "header": h} for f, h in zip(fields, header)
                    ],
                    "rows": rows,
                    "scope": "Drawing component schedule; not a manufacturer BOM export.",
                    "warnings": [
                        "Columns without a printed header retain raw values; their meaning is unknown."
                    ]
                    if any(f.startswith("unlabelled_") for f in fields)
                    else [],
                }
            )
    return schedules


def explicit_tags(value):
    """Expand only finite, unambiguous printed lists/ranges. Never expand 'n'."""
    if not value:
        return []
    match = re.fullmatch(r"([A-Z]+)(\d+)-\1(\d+)", value)
    if match:
        lo, hi = int(match[2]), int(match[3])
        if 0 <= hi - lo <= 100 and str(lo) == match[2] and str(hi) == match[3]:
            return [f"{match[1]}{i}" for i in range(lo, hi + 1)]
        return []
    parts = re.split(r"[\s,、;]+", value)
    if all(re.fullmatch(r"(?:\d+)?[A-Z]+\d*", p) for p in parts):
        return list(dict.fromkeys(parts))
    return []


def equipment_evidence(snapshot):
    schedules = extract_schedules(snapshot)
    by_tag = defaultdict(list)
    for item in snapshot["placed_text"]:
        by_tag[text(item["text"])].append(item)
    devices = []
    for schedule in schedules:
        for row in schedule["rows"]:
            symbols = row["cells"]["symbol"]
            for tag in explicit_tags(symbols["value"]):
                own = {
                    e["locator"] for c in row["cells"].values() for e in c["evidence"]
                }
                references = [
                    {
                        **anchor(t, snapshot["source_sha256"]),
                        "relation": "same_tag_candidate",
                    }
                    for t in by_tag[tag]
                    if t["locator"] not in own
                ]
                devices.append(
                    {
                        "tag": tag,
                        "schedule_id": schedule["id"],
                        "row_id": row["id"],
                        "description": row["cells"]["description"]["value"],
                        "type": row["cells"].get("type", {}).get("value"),
                        "evidence": symbols["evidence"],
                        "other_occurrences": references,
                        "physical_identity_verified": False,
                    }
                )
    return {
        "schema": VERSION,
        "source_sha256": snapshot["source_sha256"],
        "schedules": schedules,
        "device_candidates": devices,
        "counts": {
            "schedules": len(schedules),
            "schedule_rows": sum(len(s["rows"]) for s in schedules),
            "explicit_tag_candidates": len(devices),
            "cross_reference_candidates": sum(
                len(d["other_occurrences"]) for d in devices
            ),
        },
        "drawing_coverage_complete": snapshot["coverage_complete"],
        "paper_space_coverage_complete": snapshot.get("layout_coverage_complete", True),
        "paper_space_entity_counts": snapshot.get("paper_space_entity_counts", {}),
        "omissions": snapshot["omissions"],
        "production_bom_verified": False,
        "electrical_connectivity_verified": False,
    }


def read_streamed_schedule(source):
    """Bridge large CAD schedules into the existing equipment evidence reader.

    No electrical field facts are inferred from loose text. Grouped row tags
    are retained verbatim, not assigned one row's quantity to every device.
    """
    from .cad_snapshot import snapshot_dxf

    snapshot = snapshot_dxf(source.path)
    if snapshot["source_sha256"] != source.sha256.lower():
        raise ValueError("source_hash_mismatch")
    evidence = equipment_evidence(snapshot)
    rows = []
    for table in evidence["schedules"]:
        for row in table["rows"]:
            cells = row["cells"]
            tags = explicit_tags(cells["symbol"]["value"])
            quantity = cells.get("quantity", {}).get("value")
            quantity = (
                int(quantity) if quantity and re.fullmatch(r"\d+", quantity) else None
            )
            rows.append(
                {
                    "id": row["id"],
                    "source_id": source.id,
                    "source_name": source.name,
                    "sha256": source.sha256,
                    "role": source.role,
                    "revision": source.revision,
                    "sheet": table["layout"],
                    "row": row["row"],
                    "table_id": table["id"],
                    "device_tag": tags[0] if len(tags) == 1 else None,
                    "raw_tag": cells["symbol"]["value"],
                    "candidate_tags": tags,
                    "quantity": quantity,
                    "raw_headers": [
                        c["header"]["value"] or "" for c in table["columns"]
                    ],
                    "cells": [
                        {
                            "column": i + 1,
                            "header": c["header"]["value"] or "",
                            "text": cells[c["field"]]["value"],
                            "status": cells[c["field"]]["status"],
                            "evidence": cells[c["field"]]["evidence"],
                        }
                        for i, c in enumerate(table["columns"])
                    ],
                    "fields": {
                        k: cells[k]["value"]
                        for k in ("description", "type", "part_number")
                        if cells.get(k, {}).get("value")
                    },
                    "status": "observed_row",
                    "confidence": "native_cell",
                    "warnings": list(table["warnings"]),
                }
            )
    return evidence, rows


def verify_saved_drawing(before, after, edits):
    """Independent parser comparison after serialization, including block fanout.

    The byte-preserving patcher separately checks *all* untouched source bytes.
    This checker measures the supported placed representation only.
    """

    def index(snapshot, key):
        rows = snapshot.get(key, [])
        locators = [r["locator"] for r in rows]
        if len(locators) != len(set(locators)):
            raise ValueError("Non-unique native locators prevent independent recheck")
        return {r["locator"]: r for r in rows}

    expected = {e["handle"].upper(): e for e in edits}
    a, b = index(before, "placed_text"), index(after, "placed_text")
    errors, outcomes = [], []
    if set(a) != set(b):
        errors.append("Placed text was added or removed unexpectedly")
    for locator in sorted(set(a) & set(b)):
        old, new = a[locator], b[locator]
        target = expected.get(old["handle"].upper())
        if {k: v for k, v in old.items() if k != "text"} != {
            k: v for k, v in new.items() if k != "text"
        }:
            errors.append(f"Text placement changed: {locator}")
        if target:
            good = text(old["text"]) == text(target["expected_text"]) and text(
                new["text"]
            ) == text(target["replacement_text"])
            outcomes.append(
                {
                    "edit_id": target["id"],
                    "locator": locator,
                    "handle": old["handle"],
                    "before": text(old["text"]),
                    "after": text(new["text"]),
                    "verified": good,
                    "source": anchor(old, before["source_sha256"]),
                    "saved": anchor(new, after["source_sha256"]),
                }
            )
            if not good:
                errors.append(f"Saved value does not match requested change: {locator}")
        elif old["text"] != new["text"]:
            errors.append(f"Unplanned text change: {locator}")
    found = {o["edit_id"] for o in outcomes}
    if found != {e["id"] for e in edits}:
        errors.append("Not every planned edit has a saved placement")
    for key in ("placed_lines", "placed_paths", "placed_inserts"):
        if index(before, key) != index(after, key):
            errors.append(f"Unplanned native geometry change: {key}")
    for key in (
        "paper_space_placed_text",
        "paper_space_placed_lines",
        "paper_space_placed_paths",
        "paper_space_placed_inserts",
    ):
        if index(before, key) != index(after, key):
            errors.append(f"Unplanned paper-space change: {key}")
    if (
        before["header"] != after["header"]
        or before["omissions"] != after["omissions"]
        or before["coverage_complete"] != after["coverage_complete"]
        or before.get("paper_space_entity_counts", {})
        != after.get("paper_space_entity_counts", {})
        or before.get("non_model_entity_counts", {})
        != after.get("non_model_entity_counts", {})
        or before.get("layout_coverage_complete", True)
        != after.get("layout_coverage_complete", True)
    ):
        errors.append("Native units or layout extraction coverage changed")
    placement_gaps = {
        k: v
        for k, v in before["omissions"].items()
        if any(
            word in k
            for word in (
                "cap",
                "budget",
                "incomplete",
                "insert",
                "xref",
                "block_cycle",
                "missing_block",
                "nonplanar_text",
                "extrusion",
                "base_z",
            )
        )
    }
    placement_complete = not placement_gaps and not before.get(
        "non_model_entity_counts"
    )
    if any(e.get("allow_shared_definition") for e in edits) and not placement_complete:
        errors.append(
            "A shared definition may have unobserved placements; engineer review is required"
        )
    return {
        "schema": "rivet.saved-cad-check.v1",
        "passed": not errors,
        "source_sha256": before["source_sha256"],
        "saved_sha256": after["source_sha256"],
        "expected_edits": len(edits),
        "verified_edits": len({o["edit_id"] for o in outcomes if o["verified"]}),
        "placements_checked": len(outcomes),
        "placements": outcomes,
        "errors": errors,
        "supported_geometry_unchanged": not any("geometry" in e for e in errors),
        "coverage_complete": after["coverage_complete"],
        "paper_space_coverage_complete": after.get("layout_coverage_complete", True),
        "paper_space_entities_preserved": not any(
            "paper-space" in error for error in errors
        ),
        "shared_definition_placement_coverage_verified": placement_complete,
        "engineering_correctness_verified": False,
    }
