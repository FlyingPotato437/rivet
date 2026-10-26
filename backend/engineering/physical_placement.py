"""Bounded placement search on mapped projections, never engineering acceptance.

The native attachment X coordinate is retained after the side reflection. Each
view can slide along one explicitly named face coordinate. Interval subtraction
finds ALL overlap-free translations within the tank projection; no grid can
accidentally miss a narrow opening. Clearances and overhangs are not invented.
"""

from __future__ import annotations

import itertools
import json
import math
from copy import deepcopy
from pathlib import Path

from .cad_geometry import render_region
from .cad_snapshot import snapshot_dxf
from .comment_redesign import comments_from_pdfs, file_hash
from .physical_move import _boxes, _mapping, _one, _union, build_trial


def _free_intervals(low, high, blocked):
    if high < low:
        return []
    segments = [(low, high)]
    for a, b in sorted(blocked):
        updated = []
        for l, r in segments:
            if b <= l or a >= r:
                updated.append((l, r))
            else:
                if a > l:
                    updated.append((l, a))
                if b < r:
                    updated.append((b, r))
        segments = updated
    return [[l, r] for l, r in segments if r - l > 1e-7]


def _obstacles(boxes, view):
    groups = [{"id": h, "locators": [h]} for h in view.get("obstacle_locators", [])]
    groups += view.get("obstacle_groups", [])
    return [dict(g, bbox=_union([boxes[h] for h in g["locators"]])) for g in groups]


def search_placements(snapshot, plan, *, maximum=3):
    if type(maximum) is not int or not 1 <= maximum <= 3:
        raise ValueError("Return one to three review candidates at most")
    axes = _mapping(snapshot, plan)
    boxes = _boxes(snapshot)
    coordinates = [v.get("placement_coordinate") for v in plan["views"]]
    if any(c not in {"face_lateral", "height"} for c in coordinates) or len(
        set(coordinates)
    ) != len(coordinates):
        raise ValueError("Map distinct face-lateral and height projections explicitly")
    views = []
    for v in plan["views"]:
        old = _union([boxes[h] for h in v["handles"]])
        frame = boxes[v["frame_locator"]]
        axis = axes[v["handles"][0]]
        moved = [2 * axis - old[2], old[1], 2 * axis - old[0], old[3]]
        low, high = frame[1] - moved[1], frame[3] - moved[3]
        relevant, forbidden = [], []
        for o in _obstacles(boxes, v):
            b = o["bbox"]
            if min(moved[2], b[2]) > max(moved[0], b[0]):
                relevant.append(o)
                forbidden.append([b[1] - moved[3], b[3] - moved[1]])
        free = _free_intervals(low, high, forbidden)
        face_gaps = _free_intervals(
            frame[1], frame[3], [(o["bbox"][1], o["bbox"][3]) for o in relevant]
        )
        largest_gap = max((b - a for a, b in face_gaps), default=0)
        views.append(
            {
                "id": v["id"],
                "coordinate": v["placement_coordinate"],
                "reflected_bbox": moved,
                "frame_bbox": frame,
                "allowed_translation_range": [low, high],
                "free_translation_intervals": free,
                "face_gaps": face_gaps,
                "assembly_span": moved[3] - moved[1],
                "largest_open_span": largest_gap,
                "minimum_span_shortfall": max(0, moved[3] - moved[1] - largest_gap),
                "obstacles": relevant,
            }
        )
    # Candidate anchors maximize interval clearance first; endpoints are excluded
    # because a touching envelope is not a service-clearance recommendation.
    choices = []
    for v in views:
        options = []
        for a, b in v["free_translation_intervals"]:
            for fraction in (0.5, 0.25, 0.75):
                offset = a + (b - a) * fraction
                options.append((offset, min(offset - a, b - offset)))
        choices.append(options)
    total = math.prod(len(c) for c in choices)
    if total > 10000:
        raise ValueError("Placement combinations exceed the bounded review budget")
    ranked = []
    for combo in itertools.product(*choices):
        ranked.append(
            {
                "translations": {v["id"]: c[0] for v, c in zip(views, combo)},
                "minimum_mapped_edge_gap": min(c[1] for c in combo),
                "total_translation": sum(abs(c[0]) for c in combo),
            }
        )
    ranked.sort(key=lambda x: (-x["minimum_mapped_edge_gap"], x["total_translation"]))
    selected = []
    # Keep distinct options rather than returning three nearly identical points.
    spans = {v["id"]: v["assembly_span"] for v in views}
    for candidate in ranked:
        if all(
            any(
                abs(candidate["translations"][k] - old["translations"][k])
                >= spans[k] * 0.1
                for k in spans
            )
            for old in selected
        ):
            selected.append(dict(candidate, rank=len(selected) + 1))
        if len(selected) >= maximum:
            break
    return {
        "schema": "rivet.physical-placement-search.v1",
        "status": "candidates_require_engineer"
        if selected
        else "no_feasible_mapped_placement",
        "source_sha256": snapshot["source_sha256"],
        "views": views,
        "ranked_candidates": selected,
        "candidate_anchors_evaluated": total,
        "search_method": "continuous interval subtraction, then ranked interior anchors",
        "constraints": {
            "attachment_depth": "retain native mount depth at the reflected tank face",
            "overhang": "none beyond the mapped tank projection",
            "existing_accessories": "stationary",
            "units": "native drawing units; physical unit unconfirmed",
            "minimum_engineering_clearance": None,
            "obstacle_coverage": "explicitly mapped envelopes only",
            "projection_mapping": "manually supplied; engineer confirmation pending",
            "projection_model": "each mapped 2D view must be clear; this can reject a valid 3D stacked arrangement",
        },
        "engineering_approved": False,
        "production_released": False,
    }


def check_saved_placement(snapshot, plan, search, candidate):
    """Measure saved envelopes directly, independently of search arithmetic."""
    boxes, checks = _boxes(snapshot), []
    for v in plan["views"]:
        b = _union([boxes[h] for h in v["handles"]])
        frame = boxes[v["frame_locator"]]
        checked = next(x for x in search["views"] if x["id"] == v["id"])
        offset = candidate["translations"][v["id"]]
        observed = b[1] - checked["reflected_bbox"][1]
        overlaps = []
        for o in _obstacles(boxes, v):
            q = o["bbox"]
            if min(b[2], q[2]) > max(b[0], q[0]) and min(b[3], q[3]) > max(b[1], q[1]):
                overlaps.append(o["id"])
        passed = (
            b[2] <= frame[0] + 1e-7
            and b[1] >= frame[1] - 1e-7
            and b[3] <= frame[3] + 1e-7
            and not overlaps
            and math.isclose(observed, offset, abs_tol=1e-7, rel_tol=0)
        )
        checks.append(
            {"view": v["id"], "passed": passed, "saved_bbox": b, "overlaps": overlaps}
        )
    return {
        "passed": all(v["passed"] for v in checks),
        "views": checks,
        "engineering_approved": False,
    }


def build_search(source, plan, sources, output):
    root = Path(output)
    if root.exists():
        raise ValueError("Output must be new")
    if any(file_hash(s["path"]) != s["sha256"] for s in sources):
        raise ValueError("Comment source changed")
    request = _one(
        [c for c in comments_from_pdfs(sources) if c["id"] == plan["comment_id"]],
        "source-bound customer comment",
    )
    if request["text"] != plan["comment_text"]:
        raise ValueError("Comment text differs from mapping")
    before = snapshot_dxf(source)
    report = search_placements(before, plan)
    root.mkdir(parents=True, mode=0o700)
    report["request"] = request
    report["saved_candidates"] = []
    for c in report["ranked_candidates"]:
        revised = deepcopy(plan)
        for v in revised["views"]:
            v["translation_y"] = c["translations"][v["id"]]
        directory = root / f"candidate-{c['rank']}"
        result = build_trial(source, revised, sources, directory)
        saved = snapshot_dxf(directory / "Cabinet-relocation-UNAPPROVED.dxf")
        verification = check_saved_placement(saved, plan, report, c)
        if not verification["passed"]:
            (directory / "failed-placement.json").write_text(
                json.dumps(verification, indent=2)
            )
            raise ValueError(
                "Saved placement no longer satisfies the search constraints"
            )
        report["saved_candidates"].append(
            {
                "rank": c["rank"],
                "saved_sha256": result["saved_sha256"],
                "geometry": result["saved_geometry"],
                "placement": verification,
                "annotation_check": result["annotation_check"],
            }
        )
    report["render_counts"] = {}
    for view in plan["views"]:
        svg, counts = render_region(
            before,
            view["review_bbox"],
            width=1700,
            height=1000,
            highlight_handles=view["handles"],
        )
        (root / f"{view['id']}-source.svg").write_text(svg)
        report["render_counts"][view["id"]] = counts
    if file_hash(source) != plan["source_sha256"] or any(
        file_hash(s["path"]) != s["sha256"] for s in sources
    ):
        raise ValueError("Source drift")
    report["originals_unchanged"] = True
    (root / "search.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (root / "case-mapping.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False)
    )
    for p in root.iterdir():
        if p.is_file():
            p.chmod(0o600)
    return report
