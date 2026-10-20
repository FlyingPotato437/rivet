"""Readable source context for drawing candidates; never changes scored boxes."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path

import pymupdf
from PIL import Image

VERSION = "rivet.drawing-source-context.v1"


def _box(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(isinstance(v, (float, int)) and math.isfinite(v) for v in value)
        and 0 <= value[0] < value[2] <= 1
        and 0 <= value[1] < value[3] <= 1
    )


def _context(boxes, *, padding=0.015, minimum_extent=0.15):
    bounds = [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]
    result = []
    for axis in (0, 1):
        middle = (bounds[axis] + bounds[axis + 2]) / 2
        extent = max(minimum_extent, bounds[axis + 2] - bounds[axis] + padding * 2)
        left = max(0, min(middle - extent / 2, 1 - extent))
        result.append((left, min(1, left + extent)))
    return [result[0][0], result[1][0], result[0][1], result[1][1]]


def add_context_crops(
    before, after, report, *, sources, output_dir, page_reports=None, minimum_pixels=320
):
    """Attach separate viewing crops from cited sections/path geometry.

    The full matched source section supplies stable context even when a tight
    interface-symbol box contains only a few pixels. Added padding and view
    rotation are explicit and never become equipment evidence or scored boxes.
    No answer key is accepted by this helper.
    """
    if not 128 <= minimum_pixels <= 1200:
        raise ValueError("Use a bounded readable crop dimension")
    output = Path(output_dir)
    if output.is_symlink():
        raise ValueError("Context output cannot be a symlink")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    result, records = deepcopy(report), []
    orientations = {
        (r["source_id"], p["page"]): p.get("rotation_clockwise", 0)
        for r in page_reports or []
        for p in r.get("pages", [])
    }
    for candidate in result.get("candidate_changes", []):
        views = {"version": VERSION, "scored_boxes_unchanged": True}
        for side, model, sid, page, original_box in (
            (
                "before",
                before,
                candidate["baseline_source_id"],
                candidate["baseline_page"],
                candidate["baseline_bbox"],
            ),
            (
                "after",
                after,
                candidate["source_id"],
                candidate["page"],
                candidate["target_bbox"],
            ),
        ):
            source = sources.get(sid)
            if not source or not _box(original_box):
                views[side] = {
                    "status": "unavailable",
                    "reason": "Source or bounded candidate reference unavailable",
                }
                continue
            path = Path(source["path"])
            expected = model.get("source_hashes", {}).get(sid)
            if (
                not expected
                or expected != source.get("sha256")
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected
            ):
                raise ValueError("Context crop source hash mismatch")
            refs = [
                ref
                for node in model["nodes"]
                if node["id"] == candidate["graph_subject_id"]
                for ref in node.get("source_refs", [])
                if ref.get("source_id") == sid
                and ref.get("sha256") == expected
                and ref.get("page") == page
                and _box(ref.get("bbox"))
            ]
            # Actual section and conductor claims bound the view, rather than a
            # reviewer key's change box or an invented enlarged object claim.
            refs.extend(
                claim["source"]
                for claim in model["claims"]
                if claim["id"] in candidate.get("supporting_claim_ids", [])
                and claim["source"].get("source_id") == sid
                and claim["source"].get("page") == page
                and _box(claim["source"].get("bbox"))
            )
            bounds = _context([original_box, *[ref["bbox"] for ref in refs]])
            with pymupdf.open(path) as document:
                physical = document[page - 1]
                area = pymupdf.Rect(
                    bounds[0] * physical.rect.width,
                    bounds[1] * physical.rect.height,
                    bounds[2] * physical.rect.width,
                    bounds[3] * physical.rect.height,
                )
                scale = min(
                    8,
                    max(4, minimum_pixels / max(0.001, min(area.width, area.height))),
                    2200 / max(area.width, area.height),
                )
                pixmap = physical.get_pixmap(
                    matrix=pymupdf.Matrix(scale, scale), clip=area, alpha=False
                )
                image = Image.frombytes(
                    "RGB", (pixmap.width, pixmap.height), pixmap.samples
                )
            rotation = orientations.get((sid, page), 0)
            if rotation not in {0, 90, 180, 270}:
                raise ValueError(
                    "Context view needs a recorded right-angle orientation"
                )
            image = image.rotate(-rotation, expand=True)
            destination = output / f"{candidate['id']}-{side}-context-v1.png"
            image.save(destination)
            destination.chmod(0o600)
            views[side] = {
                "status": "readable_context"
                if min(image.size) >= minimum_pixels
                else "source_resolution_limited",
                "path": str(destination),
                "source_id": sid,
                "sha256": expected,
                "page": page,
                "view_context_bbox": bounds,
                "original_scored_bbox": original_box,
                "view_rotation_clockwise": rotation,
                "pixel_size": list(image.size),
                "cited_context_refs": refs,
                "purpose": "Viewing context only; not a larger equipment association or proof of absence",
            }
        candidate["source_context_crops"] = views
        records.append({"candidate_id": candidate["id"], **views})
    manifest = output / "context-crop-manifest-v1.json"
    manifest.write_text(
        json.dumps({"version": VERSION, "candidates": records}, indent=2)
    )
    manifest.chmod(0o600)
    result["source_context_crop_manifest"] = str(manifest)
    return result
