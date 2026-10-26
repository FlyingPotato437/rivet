"""Source-bound mechanical projection trial; saved geometry is not design approval.

A case mapping selects a complete assembly in each view and names a native
reference frame. The bounded edit mirrors LINE/ARC/CIRCLE entities across that
frame's X centre. No inferred device identities, unit conversion or wire edits.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ezdxf.addons import iterdxf

from . import dxf_patch as raw
from .cad_geometry import render_region
from .cad_snapshot import snapshot_dxf
from .comment_redesign import comments_from_pdfs, file_hash

TYPES = {"LINE", "ARC", "CIRCLE"}


@dataclass
class Span:
    patch_start: int
    patch_end: int
    replacement_pairs: bytes


def _one(values, label):
    if len(values) != 1:
        raise ValueError(f"Expected one {label}")
    return values[0]


def _number(pairs, code, default=None):
    matches = [float(p.value.strip()) for p in pairs if p.number == code]
    if not matches and default is not None:
        return default
    value = _one(matches, f"numeric group {code}")
    if not math.isfinite(value):
        raise ValueError("Non-finite CAD coordinate")
    return value


def _boxes(snapshot):
    found = {}
    for p in snapshot["placed_paths"]:
        found[p["locator"]] = p["bbox_with_chord_error"]
    for p in snapshot["placed_lines"]:
        a, b = p["start"], p["end"]
        found[p["locator"]] = [
            min(a[0], b[0]),
            min(a[1], b[1]),
            max(a[0], b[0]),
            max(a[1], b[1]),
        ]
    return found


def _union(boxes):
    if not boxes:
        raise ValueError("No assembly geometry")
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _mapping(snapshot, plan):
    if (
        plan.get("schema") != "rivet.physical-move.v1"
        or plan.get("mode") != "engineering_trial"
    ):
        raise ValueError("Only an explicit engineering trial is supported")
    if snapshot["source_sha256"] != plan["source_sha256"]:
        raise ValueError("Mapping belongs to another native source")
    if not plan.get("missing_engineer_inputs"):
        raise ValueError("An engineering trial must retain its unresolved inputs")
    views = plan.get("views", [])
    if not 1 <= len(views) <= 8:
        raise ValueError("One to eight mapped views required")
    items = {
        t["locator"]: t for t in snapshot["placed_lines"] + snapshot["placed_paths"]
    }
    axes, seen, view_ids = {}, set(), set()
    boxes = _boxes(snapshot)
    for view in views:
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", view["id"]):
            raise ValueError("Invalid view identifier")
        if view["id"] in view_ids:
            raise ValueError("Duplicate view")
        view_ids.add(view["id"])
        frame = items.get(view["frame_locator"])
        if (
            not frame
            or frame.get("block_path")
            or frame.get("invisible")
            or frame["kind"] != "LWPOLYLINE"
            or not frame["closed"]
        ):
            raise ValueError("A closed native view reference frame is required")
        vertices = frame["native"]["vertices"]
        if len(vertices) != 4 or any(v.get("bulge", 0) for v in vertices):
            raise ValueError("Reference frame must be a four-corner rectangle")
        xs, ys = {v["x"] for v in vertices}, {v["y"] for v in vertices}
        if (
            len(xs) != 2
            or len(ys) != 2
            or len({(v["x"], v["y"]) for v in vertices}) != 4
        ):
            raise ValueError("Reference frame must be axis-aligned")
        axis = sum(xs) / 2
        handles = view["handles"]
        if (
            not handles
            or len(handles) != len(set(handles))
            or seen.intersection(handles)
        ):
            raise ValueError("Empty, duplicate or cross-view assembly selection")
        seen.update(handles)
        for h in handles:
            item = items.get(h)
            if (
                not item
                or item.get("block_path")
                or item.get("invisible")
                or item["kind"] not in TYPES
            ):
                raise ValueError("Assembly must use supported top-level native objects")
            axes[h] = axis
        if len(seen) > 500:
            raise ValueError("Assembly exceeds the bounded edit size")
        if view.get("target_side") != "negative_x":
            raise ValueError("An explicit target face is required")
        box = _union([boxes[h] for h in handles])
        if box[0] < max(xs) - 1e-5:
            raise ValueError("Assembly is not fully outside the source tank face")
        obstacles = list(view.get("obstacle_locators", []))
        for group in view.get("obstacle_groups", []):
            if not group.get("locators") or not group.get("id"):
                raise ValueError("An obstacle envelope needs identified native members")
            obstacles.extend(group["locators"])
        if any(h not in items for h in obstacles):
            raise ValueError("Missing source-bound obstacle")
        if seen.intersection(obstacles):
            raise ValueError("A moved assembly cannot be its own stationary obstacle")
    return axes


def _reactors(pairs):
    """Permit only well-formed persistent reactor groups; never strip metadata."""
    inside, found = False, []
    for pair in pairs:
        value = pair.value.strip()
        if pair.number == 102:
            if value == b"{ACAD_REACTORS" and not inside:
                inside = True
            elif value == b"}" and inside:
                inside = False
            else:
                raise ValueError("Unsupported native application data")
        elif inside:
            if pair.number != 330:
                raise ValueError("Unsupported reactor group")
            found.append(value.decode("ascii"))
    if inside or len(found) != len(set(found)):
        raise ValueError("Malformed persistent reactors")
    return found


def _dimension_dependencies(source, encoding, records, declarations):
    """Retain explicitly reviewed dimension dependencies as unresolved, not fixed.

    A mirrored edge can invalidate a dimension whose other reference stays put.
    We leave the dimension, its block, and associativity unchanged in this trial,
    exposing the exact stale annotation. Unknown reactors still prohibit editing.
    """
    touched = {}
    for record in records:
        for handle in _reactors(record.pairs):
            touched.setdefault(handle, []).append(record.handle)
    declared = {d["association_handle"]: d for d in declarations}
    if len(declared) != len(declarations) or set(declared) != set(touched):
        raise ValueError(
            "Every dimension reactor must have an explicit deferred mapping"
        )
    if not touched:
        return []
    wanted = set(touched) | {d["dimension_handle"] for d in declarations}
    entities, counts = raw._scan(
        source, encoding, wanted, retained_types={"DIMASSOC", "DIMENSION"}
    )
    by_handle = {r.handle: r for r in entities}
    if any(counts.get(h) != 1 or h not in by_handle for h in wanted):
        raise ValueError("Missing/unknown/duplicate dimension dependency")
    findings = []
    for h, selection in touched.items():
        association = by_handle[h]
        definition = declared[h]
        dimension = by_handle[definition["dimension_handle"]]
        if association.kind != "DIMASSOC" or association.scope != "OBJECTS":
            raise ValueError("Unsupported persistent reactor type")
        if dimension.kind != "DIMENSION" or dimension.scope != "ENTITIES":
            raise ValueError("Unsupported dimension dependency")
        linked = [
            p.value.strip().decode("ascii")
            for p in association.pairs
            if p.number == 330
        ]
        refs = [
            p.value.strip().decode("ascii")
            for p in association.pairs
            if p.number == 331
        ]
        if (
            dimension.handle not in linked
            or not set(selection).issubset(refs)
            or set(refs) != set(definition["reference_handles"])
            or not definition.get("reason")
        ):
            raise ValueError(
                "Dimension association does not match the reviewed dependency"
            )
        text = [
            p.value.strip().decode(encoding) for p in dimension.pairs if p.number == 1
        ]
        findings.append(
            {
                "association_handle": h,
                "dimension_handle": dimension.handle,
                "changed_reference_handles": selection,
                "reference_handles": refs,
                "display_override": text[0] if text else None,
                "stored_measurement_native_units": _number(dimension.pairs, 42, 0),
                "status": "stale_dimension_requires_revision",
                "unchanged_definition_sha256": hashlib.sha256(
                    b"".join(
                        p.code + p.value
                        for record in (association, dimension)
                        for p in record.pairs
                    )
                ).hexdigest(),
                "reason": definition["reason"],
            }
        )
    return findings


def _write_mirror(
    source, output, axes, expected_hash, deferred_dimensions=(), offsets=None
):
    """Reflect and optionally translate Y; preserve every other source byte."""
    offsets = offsets or {}
    source, output = Path(source), Path(output)
    if output.exists() or output.resolve() == source.resolve():
        raise ValueError("Physical edit output must be a new file")
    if file_hash(source) != expected_hash:
        raise ValueError("Native source changed")
    encoding, _ = raw._encoding(source)
    records, counts = raw._scan(source, encoding, set(axes), retained_types=TYPES)
    by_handle = {r.handle: r for r in records}
    dependencies = _dimension_dependencies(
        source, encoding, records, deferred_dimensions
    )
    patches = []
    for h, axis in axes.items():
        if counts.get(h) != 1 or h not in by_handle:
            raise ValueError("Missing or duplicate native assembly object")
        r = by_handle[h]
        p = r.pairs
        if r.scope != "ENTITIES" or any(v.number in {360, 1001} for v in p):
            raise ValueError(
                "Shared, associated or custom-data geometry requires a specific adapter"
            )
        if _number(p, 67, 0) != 0 or any(
            v.number == 410 and v.value.strip().lower() != b"model" for v in p
        ):
            raise ValueError("Paper-space editing is unsupported")
        if any(
            _number(p, c, d) != d
            for c, d in [(210, 0), (220, 0), (230, 1), (30, 0), (31, 0), (39, 0)]
        ):
            raise ValueError("Only planar unthickened world-XY geometry is supported")
        changes = {10: 2 * axis - _number(p, 10)}
        dy = offsets.get(h, 0)
        if not math.isfinite(dy):
            raise ValueError("Non-finite placement offset")
        y = _number(p, 20)
        if dy:
            changes[20] = y + dy
        if r.kind == "LINE":
            changes[11] = 2 * axis - _number(p, 11)
            y = _number(p, 21)
            if dy:
                changes[21] = y + dy
        else:
            if _number(p, 40) <= 0:
                raise ValueError("Invalid native radius")
            if r.kind == "ARC":
                changes[50] = (180 - _number(p, 51)) % 360
                changes[51] = (180 - _number(p, 50)) % 360
        for code, value in changes.items():
            pair = _one([v for v in p if v.number == code], f"coordinate {code}")
            patches.append(
                Span(
                    pair.start,
                    pair.end,
                    pair.code + format(value, ".17g").encode("ascii") + pair.newline,
                )
            )
    patches.sort(key=lambda v: v.patch_start)
    fd, filename = tempfile.mkstemp(prefix=".physical-", dir=output.parent)
    os.close(fd)
    temporary = Path(filename)
    try:
        copied, untouched = raw._copy_with_patches(source, temporary, patches)
        if copied != expected_hash or file_hash(source) != expected_hash:
            raise ValueError("Native original changed during edit")
        check = hashlib.sha256()
        with temporary.open("rb") as stream:
            before_cursor = after_cursor = 0
            for patch in patches:
                length = patch.patch_start - before_cursor
                stream.seek(after_cursor)
                raw._hash_stream_range(stream, check, length)
                after_cursor += length + len(patch.replacement_pairs)
                before_cursor = patch.patch_end
            stream.seek(after_cursor)
            raw._hash_stream_range(
                stream, check, temporary.stat().st_size - after_cursor
            )
        if check.hexdigest() != untouched:
            raise ValueError("Unexpected changes outside selected geometric fields")
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "preservation_verified": True,
        "untouched_bytes_sha256": untouched,
        "output_sha256": file_hash(output),
        "changed_entities": len(axes),
        "changed_numeric_fields": len(patches),
        "deferred_dimensions": dependencies,
    }


def _read_native(path, wanted):
    result = {}
    count = 0
    for entity in iterdxf.modelspace(path, types=TYPES):
        count += 1
        if entity.dxf.handle not in wanted:
            continue
        if entity.dxf.handle in result:
            raise ValueError("Duplicate saved native handle")
        result[entity.dxf.handle] = entity
    if set(result) != set(wanted):
        raise ValueError("Independent reader did not find every selected object")
    return result, count


def verify_geometry(source, saved, axes, offsets=None):
    """Independent library read; compare geometry invariants, not writer receipts."""
    before, n = _read_native(source, axes)
    after, m = _read_native(saved, axes)
    errors = []
    offsets = offsets or {}

    def close(a, b):
        return math.isclose(a, b, rel_tol=0, abs_tol=1e-7)

    for h, axis in axes.items():
        a, b = before[h], after[h]
        if a.dxftype() != b.dxftype() or a.dxf.layer != b.dxf.layer:
            errors.append(h + ": identity changed")
            continue
        if a.dxftype() == "LINE":
            pairs = [(a.dxf.start, b.dxf.start), (a.dxf.end, b.dxf.end)]
            if not close(
                a.dxf.start.distance(a.dxf.end), b.dxf.start.distance(b.dxf.end)
            ):
                errors.append(h + ": length changed")
        else:
            pairs = [(a.dxf.center, b.dxf.center)]
            if not close(a.dxf.radius, b.dxf.radius):
                errors.append(h + ": radius changed")
            if a.dxftype() == "ARC":
                # A mirror swaps the start/end of a counterclockwise arc.
                for old, new in [
                    (a.end_point, b.start_point),
                    (a.start_point, b.end_point),
                ]:
                    pairs.append((old, new))
                if not close(
                    (a.dxf.end_angle - a.dxf.start_angle) % 360,
                    (b.dxf.end_angle - b.dxf.start_angle) % 360,
                ):
                    errors.append(h + ": arc sweep changed")
        for p, q in pairs:
            if not (
                close(p.x + q.x, 2 * axis)
                and close(p.y + offsets.get(h, 0), q.y)
                and close(p.z, q.z)
            ):
                errors.append(h + ": reflection/position mismatch")
    if n != m:
        errors.append("Model-space primitive count changed")
    return {
        "passed": not errors,
        "entities_checked": len(before),
        "primitive_count_before": n,
        "primitive_count_after": m,
        "errors": errors,
        "engineering_approved": False,
    }


def check_layout(before, after, plan):
    old_boxes, boxes = _boxes(before), _boxes(after)
    views = []
    for view in plan["views"]:
        original = _union([old_boxes[h] for h in view["handles"]])
        moved = _union([boxes[h] for h in view["handles"]])
        frame = boxes[view["frame_locator"]]
        overlaps = []
        obstacles = [
            {"id": h, "locators": [h]} for h in view.get("obstacle_locators", [])
        ]
        obstacles += view.get("obstacle_groups", [])
        for obstacle in obstacles:
            b = _union([boxes[h] for h in obstacle["locators"]])
            intersection = [
                max(moved[0], b[0]),
                max(moved[1], b[1]),
                min(moved[2], b[2]),
                min(moved[3], b[3]),
            ]
            if intersection[2] > intersection[0] and intersection[3] > intersection[1]:
                overlaps.append(
                    {
                        "locator": obstacle["id"],
                        "source_locators": obstacle["locators"],
                        "intersection_native_units": intersection,
                        "width": intersection[2] - intersection[0],
                        "height": intersection[3] - intersection[1],
                    }
                )
        target = moved[2] <= frame[0] + 1e-5
        views.append(
            {
                "id": view["id"],
                "entities": len(view["handles"]),
                "before_bbox": original,
                "saved_bbox": moved,
                "target_face_reached": target,
                "projected_envelope_overlaps": overlaps,
                "obstacle_envelopes_checked": len(obstacles),
                "obstacle_coverage": "explicitly_mapped_only",
            }
        )
    return {
        "views": views,
        "mapped_target_faces_reached": all(v["target_face_reached"] for v in views),
        "projected_envelope_check_passed": not any(
            v["projected_envelope_overlaps"] for v in views
        ),
        "physical_clearance_verified": False,
        "all_views_verified": False,
        "unit_calibration_verified": False,
        "complete_collision_check": False,
    }


def build_trial(source, plan, sources, output):
    """Create an unapproved physical CAD candidate and its measured review evidence."""
    root = Path(output)
    if root.exists():
        raise ValueError("Output must be new")
    if any(file_hash(s["path"]) != s["sha256"] for s in sources):
        raise ValueError("Comment source changed")
    comments = comments_from_pdfs(sources)
    request = _one(
        [c for c in comments if c["id"] == plan["comment_id"]],
        "source-bound customer comment",
    )
    if request["text"] != plan["comment_text"]:
        raise ValueError("Comment text differs from mapping")
    before = snapshot_dxf(source)
    axes = _mapping(before, plan)
    offsets = {
        h: float(v.get("translation_y", 0)) for v in plan["views"] for h in v["handles"]
    }
    if not all(math.isfinite(v) for v in offsets.values()):
        raise ValueError("Non-finite placement offset")
    root.mkdir(parents=True, mode=0o700)
    saved = root / "Cabinet-relocation-UNAPPROVED.dxf"
    receipt = _write_mirror(
        source,
        saved,
        axes,
        plan["source_sha256"],
        plan.get("deferred_dimension_associations", []),
        offsets,
    )
    independent = verify_geometry(source, saved, axes, offsets)
    if not independent["passed"]:
        (root / "failed-check.json").write_text(json.dumps(independent, indent=2))
        raise ValueError("Independent saved geometry check failed")
    after = snapshot_dxf(saved)
    # Re-read the dependencies from the saved file, including the display override.
    encoding, _ = raw._encoding(saved)
    records, _ = raw._scan(saved, encoding, set(axes), retained_types=TYPES)
    saved_dependencies = _dimension_dependencies(
        saved, encoding, records, plan.get("deferred_dimension_associations", [])
    )
    if saved_dependencies != receipt["deferred_dimensions"]:
        raise ValueError("Native dimension dependency changed unexpectedly")
    layout = check_layout(before, after, plan)
    if file_hash(source) != plan["source_sha256"] or any(
        file_hash(s["path"]) != s["sha256"] for s in sources
    ):
        raise ValueError("Source drift")
    report = {
        "schema": "rivet.physical-move-result.v1",
        "status": "engineer_review_required",
        "request": request,
        "source_sha256": plan["source_sha256"],
        "saved_sha256": file_hash(saved),
        "write": receipt,
        "saved_geometry": independent,
        "layout": layout,
        "annotation_check": {
            "passed": not saved_dependencies,
            "preserved_dependencies_verified": True,
            "stale_dimensions": saved_dependencies,
            "all_annotations_verified": False,
        },
        "missing_engineer_inputs": plan["missing_engineer_inputs"],
        "unchanged_views": plan.get("unchanged_views", []),
        "mapping_authority": plan.get("mapping_authority", "unconfirmed"),
        "bom": {
            "supplied": any(s.get("role") == "bom" for s in sources),
            "checked": False,
            "reason": "This mechanical trial does not modify or validate a BOM; equipment identity/quantity and new mounting parts need reconciliation when a BOM is supplied.",
        },
        "engineering_approved": False,
        "production_released": False,
    }
    for view in plan["views"]:
        for state, snapshot in [("before", before), ("after", after)]:
            svg, _ = render_region(
                snapshot,
                view["review_bbox"],
                width=1700,
                height=1000,
                highlight_handles=view["handles"],
            )
            (root / f"{view['id']}-{state}.svg").write_text(svg)
    (root / "result.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (root / "case-mapping.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False)
    )
    for p in root.iterdir():
        p.chmod(0o600)
    return report
