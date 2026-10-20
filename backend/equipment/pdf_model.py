"""Evidence-backed, bounded vector-PDF equipment graph.

Only visible selectable labels, actual closed symbol outlines and actual vector
contacts establish observations. PDF coordinates are points on a schematic,
never physical millimetres. Device internals, cross-sheet links and unfamiliar
symbols stay unknown. This is a reading adapter, not a PDF-to-editable-CAD claim.

PyMuPDF contracts checked against the official Page / Functions documentation:
https://pymupdf.readthedocs.io/en/latest/page.html#Page.get_drawings
https://pymupdf.readthedocs.io/en/latest/functions.html#Page.get_texttrace
https://pymupdf.readthedocs.io/en/latest/recipes-optional-content.html
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter, OrderedDict, defaultdict
from copy import deepcopy
from itertools import pairwise
from pathlib import Path
from threading import RLock

import pymupdf

from .pdf_batches import (
    PDF_MAX_BYTES,
    PDF_MAX_PAGES,
    document_batches,
    original_page,
    original_pages,
)
from .revision_sheets import sheet_identity
from .schemas import SourceInput
from .tags import SPACED_PREFIX, TAG_BODY, ambiguous_tag_styles

SCHEMA_VERSION = "pdf-equipment-1"
MAX_BYTES = PDF_MAX_BYTES
MAX_PAGES = PDF_MAX_PAGES
MAX_PAGE_CONTENT = 12 * 1024 * 1024
MAX_PATHS = 60000
# Dense CAD PDFs often contain tens of thousands of filled outline-font items,
# but only hundreds of conductor strokes. Bound raw parsing independently from
# the much tighter conductor / association / intersection work limits below.
MAX_ITEMS = 300000
MAX_SEGMENTS = 12000
MAX_WORDS = 40000
MAX_PAIR_TESTS = 2000000
MAX_SPATIAL_BUCKET_ITEMS = 1000000
MAX_ASSOCIATION_WORK = 2000000
MAX_ISSUES = 200
MAX_DOCUMENT_GRAPH_RECORDS = 200000
TOLERANCE = 0.2  # PDF points: export rounding, not an engineering clearance.
TAG = re.compile("(?:" + TAG_BODY + r"|[A-Z]{1,8}\d{1,5}[A-Z]?(?:[-_.]\d{1,5})?)")
PORT = re.compile(r"[A-Z0-9][A-Z0-9_+.-]{0,12}")
_CACHE = OrderedDict()
_CACHE_LOCK = RLock()


class PDFModelError(ValueError):
    pass


class _BBoxIndex:
    """Exact candidate pruning; every intersecting original remains eligible.

    Filled outline fonts can contribute tens of thousands of tiny opaque fills.
    Testing every word and conductor against every glyph was quadratic. Large
    fills use a separate list, so a page-sized opaque cover is never omitted.
    """

    def __init__(self, records, *, cell=32):
        self.records = records
        self.cell = cell
        self.buckets = defaultdict(list)
        self.large = []
        count = 0
        for index, record in enumerate(records):
            box = record["bbox"]
            xs, ys = self._cells(box)
            if len(xs) * len(ys) > 256:
                self.large.append(index)
                continue
            count += len(xs) * len(ys)
            if count > MAX_SPATIAL_BUCKET_ITEMS:
                raise PDFModelError(
                    "Opaque-fill spatial indexing exceeds the bounded work limit."
                )
            for x in xs:
                for y in ys:
                    self.buckets[x, y].append(index)

    def _cells(self, box):
        return (
            range(math.floor(box[0] / self.cell), math.floor(box[2] / self.cell) + 1),
            range(math.floor(box[1] / self.cell), math.floor(box[3] / self.cell) + 1),
        )

    def intersecting(self, box):
        xs, ys = self._cells(box)
        if len(xs) * len(ys) > 4096:
            candidates = range(len(self.records))
        else:
            candidates = set(self.large)
            for x in xs:
                for y in ys:
                    candidates.update(self.buckets.get((x, y), ()))
        return [
            self.records[i]
            for i in candidates
            if self.records[i]["bbox"][0] <= box[2]
            and self.records[i]["bbox"][2] >= box[0]
            and self.records[i]["bbox"][1] <= box[3]
            and self.records[i]["bbox"][3] >= box[1]
        ]


def _id(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:24]


def _xy(point):
    values = [float(point[0]), float(point[1])]
    if not all(math.isfinite(v) for v in values):
        raise PDFModelError(
            "Nonfinite vector coordinates cannot establish equipment geometry."
        )
    return [round(v, 4) for v in values]


def _box(points):
    return [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]


def _area(box):
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _overlap(a, b):
    return (
        min(a[2], b[2]) - max(a[0], b[0]) > TOLERANCE
        and min(a[3], b[3]) - max(a[1], b[1]) > TOLERANCE
    )


def _segment_in_box(a, b, box):
    return (
        abs(a[1] - b[1]) <= TOLERANCE
        and box[1] + TOLERANCE < a[1] < box[3] - TOLERANCE
        and min(max(a[0], b[0]), box[2]) - max(min(a[0], b[0]), box[0]) > TOLERANCE
    ) or (
        abs(a[0] - b[0]) <= TOLERANCE
        and box[0] + TOLERANCE < a[0] < box[2] - TOLERANCE
        and min(max(a[1], b[1]), box[3]) - max(min(a[1], b[1]), box[1]) > TOLERANCE
    )


def _inside(point, box, strict=False):
    margin = TOLERANCE if strict else -TOLERANCE
    return (
        box[0] + margin < point[0] < box[2] - margin
        and box[1] + margin < point[1] < box[3] - margin
    )


def _distance_to_box(point, box):
    return math.hypot(
        max(box[0] - point[0], 0, point[0] - box[2]),
        max(box[1] - point[1], 0, point[1] - box[3]),
    )


def _close(a, b):
    return math.dist(a, b) <= TOLERANCE


def _on(point, a, b):
    return (
        abs(math.dist(a, point) + math.dist(point, b) - math.dist(a, b))
        <= TOLERANCE / 4
    )


def _orthogonal(a, b):
    return not _close(a, b) and (
        abs(a[0] - b[0]) <= TOLERANCE or abs(a[1] - b[1]) <= TOLERANCE
    )


def _intersection(a, b, c, d):
    av, cv = abs(a[0] - b[0]) <= TOLERANCE, abs(c[0] - d[0]) <= TOLERANCE
    if av != cv:
        point = [a[0], c[1]] if av else [c[0], a[1]]
        return (
            ("point", point) if _on(point, a, b) and _on(point, c, d) else (None, None)
        )
    axis, fixed = (1, 0) if av else (0, 1)
    if abs(a[fixed] - c[fixed]) > TOLERANCE:
        return None, None
    lo, hi = (
        max(min(a[axis], b[axis]), min(c[axis], d[axis])),
        min(max(a[axis], b[axis]), max(c[axis], d[axis])),
    )
    if hi < lo - TOLERANCE:
        return None, None
    return ("overlap" if hi - lo > TOLERANCE else "point"), (
        [a[0], lo] if av else [lo, a[1]]
    )


def _visual(page, point):
    converted = pymupdf.Point(*point) * page.rotation_matrix
    return [round(converted.x, 4), round(converted.y, 4)]


def _anchor(page, checksum, bbox, kind, **extra):
    visual = pymupdf.Rect(bbox) * page.rotation_matrix
    box = [round(v, 4) for v in visual]
    width, height = page.rect.width, page.rect.height
    normalized = [
        round(max(0, min(1, value / (width if i % 2 == 0 else height))), 6)
        for i, value in enumerate(box)
    ]
    return {
        "source_id": None,
        "sha256": checksum,
        "page": original_page(page),
        "kind": kind,
        "bbox": box,
        "normalized_bbox": normalized,
        "coordinate_system": "visible-page-points",
        **extra,
    }


def _issue(code, reason, evidence=None, **extra):
    return {
        "code": code,
        "status": "unknown",
        "reason": reason,
        "evidence": evidence or [],
        **extra,
    }


def _layer_visible(layer, layers):
    return not layer or layers.get(layer) is True


def _rectangle(points):
    box = _box(points)
    corners = {(round(box[i], 3), round(box[j], 3)) for i in (0, 2) for j in (1, 3)}
    return (
        len(points) == 4
        and len(corners) == 4
        and {(round(p[0], 3), round(p[1], 3)) for p in points} == corners
    )


def _round_polygon(points):
    """Recognize actual closed, convex line approximations of a circle.

    Older CAD exporters flatten circles to many short lines, sometimes sharing
    a PDF path with unrelated strokes. A square, open arc, glyph or arbitrary
    path bbox cannot establish a circular terminal marker.
    """
    if len(points) < 9 or not _close(points[0], points[-1]):
        return None
    points = points[:-1]
    box = _box(points)
    width, height = box[2] - box[0], box[3] - box[1]
    if min(width, height) <= 0.2 or abs(width - height) > max(0.15, width * 0.08):
        return None
    center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
    radius = (width + height) / 4
    if any(
        abs(math.dist(p, center) - radius) > max(0.15, radius * 0.09) for p in points
    ):
        return None
    angles = [math.atan2(p[1] - center[1], p[0] - center[0]) for p in points]
    turns = [
        math.atan2(math.sin(b - a), math.cos(b - a))
        for a, b in pairwise([*angles, angles[0]])
    ]
    if (
        any(abs(t) > math.pi / 2 for t in turns)
        or abs(abs(sum(turns)) - math.tau) > 0.02
    ):
        return None
    if min(turns) < -0.01 < max(turns):
        return None
    return box, center, radius


def _line_chains(items):
    chains, chain = [], []
    for item_index, item in enumerate(items):
        if item[0] != "l":
            if chain:
                chains.append(chain)
            chain = []
            continue
        a, b = _xy(item[1]), _xy(item[2])
        if chain and not _close(chain[-1][2], a):
            chains.append(chain)
            chain = []
        chain.append((item_index, a, b))
        if len(chain) > 2 and _close(chain[0][1], b):
            chains.append(chain)
            chain = []
    if chain:
        chains.append(chain)
    return chains


def _junction_fill(path):
    color = path.get("fill")
    return (
        color is not None
        and path.get("fill_opacity", 1) == 1
        and sum(color) / max(1, len(color)) < 0.45
    )


def _split_filled_dots(paths, layers):
    """Recover circles exported as two explicitly filled radial half-polygons.

    Both actual polygon boundaries must close, share the same centre and join
    into a circular perimeter. Nearby black marks or a matching bbox alone do
    not prove a junction.
    """
    candidates = defaultdict(list)
    for index, path in enumerate(paths):
        items = path.get("items", [])
        box = path.get("rect")
        if (
            not _junction_fill(path)
            or not _layer_visible(path.get("layer"), layers)
            or not box
            or max(box.width, box.height) > 8
            or len(items) < 8
            or not all(x[0] == "l" for x in items)
        ):
            continue
        chain = _line_chains(items)
        if len(chain) != 1 or len(chain[0]) != len(items):
            continue
        points = [chain[0][0][1], *[c[2] for c in chain[0]]]
        if not _close(points[0], points[-1]):
            continue
        center = points[0]
        arc = points[1:-1]
        if len(arc) < 6 or math.dist(arc[0], arc[-1]) < 0.5:
            continue
        midpoint = [(arc[0][i] + arc[-1][i]) / 2 for i in range(2)]
        if not _close(center, midpoint):
            continue
        candidates[tuple(round(v, 2) for v in center)].append((index, center, arc))
    result = {}
    for entries in candidates.values():
        if len(entries) != 2:
            continue
        left, right = entries
        left_path, right_path = paths[left[0]], paths[right[0]]
        if (
            abs(left[0] - right[0]) != 1
            or left_path.get("level", 0) != right_path.get("level", 0)
            or left_path.get("layer") != right_path.get("layer")
        ):
            continue
        if not (_close(left[2][-1], right[2][0]) and _close(right[2][-1], left[2][0])):
            continue
        points = [*left[2], *right[2][1:]]
        if not _close(points[0], points[-1]):
            points.append(points[0])
        circle = _round_polygon(points)
        if circle is None:
            continue
        box, center, radius = circle
        if radius > 4:
            continue
        ids = [f"{i}:{j}" for i, _, _ in entries for j in range(len(paths[i]["items"]))]
        for i, _, _ in entries:
            result[i] = {
                "kind": "circle",
                "bbox": box,
                "center": center,
                "radius": radius,
                "ids": ids,
                "path_index": i,
                "seqno": paths[i].get("seqno", -1),
            }
    return result


def _primitives(page, paths, checksum, layers, issues):
    segments, shapes, dots, circles, all_ids = [], [], [], [], set()
    fills = []
    clip_scopes = {}
    split_dots = _split_filled_dots(paths, layers)
    for index, path in enumerate(paths):
        level = path.get("level", 0)
        clip_scopes = {k: v for k, v in clip_scopes.items() if k < level}
        if path.get("type") in {"clip", "group"}:
            items = path.get("items", [])
            simple = (
                path.get("type") == "clip" and len(items) == 1 and items[0][0] == "re"
            )
            clip_scopes[level] = (
                list(path["scissor"]) if simple and path.get("scissor") else None
            )
            if not simple:
                issues.append(
                    _issue(
                        "complex_visibility",
                        "Clipping/transparency groups are not interpreted as equipment or electrical connections.",
                        [
                            _anchor(
                                page,
                                checksum,
                                path.get("rect", page.rect),
                                "vector",
                                path_indices=[index],
                            )
                        ],
                    )
                )
            continue
        ids = [f"{index}:{i}" for i in range(len(path.get("items", [])))]
        box = list(path.get("rect", (0, 0, 0, 0)))
        visible_box = page.rect * page.derotation_matrix
        if (
            box[2] < visible_box.x0
            or box[0] > visible_box.x1
            or box[3] < visible_box.y0
            or box[1] > visible_box.y1
        ):
            continue
        all_ids.update(ids)
        clips_valid = all(
            b is not None
            and box[0] >= b[0] - TOLERANCE
            and box[1] >= b[1] - TOLERANCE
            and box[2] <= b[2] + TOLERANCE
            and box[3] <= b[3] + TOLERANCE
            for b in clip_scopes.values()
        )
        visible = _layer_visible(path.get("layer"), layers) and clips_valid
        if not visible:
            issues.append(
                _issue(
                    "unresolved_vector_visibility",
                    "A hidden, unresolved-layer or clipped vector is excluded from topology.",
                    [_anchor(page, checksum, box, "vector", path_indices=[index])],
                )
            )
            continue
        if path.get("fill") is not None and path.get("fill_opacity", 1) == 1:
            fills.append({"bbox": box, "seqno": path.get("seqno", -1), "ids": set(ids)})
        items = path.get("items", [])
        if index in split_dots:
            if not any(set(d["ids"]) == set(split_dots[index]["ids"]) for d in dots):
                dots.append(split_dots[index])
            continue
        polygon_circle_ids = set()
        for chain in _line_chains(items):
            polygon = _round_polygon([chain[0][1], *[c[2] for c in chain]])
            if polygon is None:
                continue
            polygon_box, center, radius = polygon
            shape = {
                "kind": "circle",
                "bbox": polygon_box,
                "center": center,
                "radius": radius,
                "ids": [ids[c[0]] for c in chain],
                "path_index": index,
                "seqno": path.get("seqno", -1),
            }
            if _junction_fill(path) and radius <= 4:
                dots.append(shape)
            elif path.get("color") is not None and path.get("stroke_opacity", 1) == 1:
                (circles if radius <= 4 else shapes).append(shape)
            else:
                continue
            polygon_circle_ids.update(shape["ids"])
        round_shape = (
            len(items) == 4
            and all(item[0] == "c" for item in items)
            and abs((box[2] - box[0]) - (box[3] - box[1]))
            <= max(0.05, (box[2] - box[0]) * 0.02)
        )
        if round_shape:
            center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
            radius = (box[2] - box[0]) / 2
            endpoints = [_xy(item[1]) for item in items]
            # A tiny arbitrary curve bbox is not sufficient proof of a dot.
            round_shape = (
                radius > 0.1
                and all(
                    abs(math.dist(p, center) - radius) <= max(0.05, radius * 0.03)
                    for p in endpoints
                )
                and all(
                    _close(_xy(a[-1]), _xy(b[1]))
                    for a, b in zip(items, items[1:] + items[:1], strict=True)
                )
            )
        if round_shape:
            shape = {
                "kind": "circle",
                "bbox": box,
                "center": center,
                "radius": radius,
                "ids": ids,
                "path_index": index,
                "seqno": path.get("seqno", -1),
            }
            if _junction_fill(path) and radius <= 4:
                dots.append(shape)
            elif path.get("color") is not None and path.get("stroke_opacity", 1) == 1:
                (circles if radius <= 4 else shapes).append(shape)
            continue
        chain = []
        for item_index, item in enumerate(items):
            identity = ids[item_index]
            if identity in polygon_circle_ids:
                continue
            if item[0] == "re":
                r = item[1]
                points = [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]]
            elif item[0] == "qu":
                q = item[1]
                points = [_xy(p) for p in (q.ul, q.ur, q.lr, q.ll)]
            else:
                points = None
            if points and _rectangle(points):
                if (
                    path.get("color") is not None
                    and path.get("stroke_opacity", 1) == 1
                    and (path.get("dashes") or "[] 0").replace(" ", "") in {"[]0", "[]"}
                ):
                    shapes.append(
                        {
                            "kind": "rectangle",
                            "bbox": _box(points),
                            "ids": [identity],
                            "path_index": index,
                            "seqno": path.get("seqno", -1),
                        }
                    )
                pairs = list(pairwise([*points, points[0]]))
            elif item[0] == "l":
                pairs = [(_xy(item[1]), _xy(item[2]))]
            else:
                continue
            solid = (path.get("dashes") or "[] 0").replace(" ", "") in {"[]0", "[]"}
            if (
                path.get("color") is None
                or path.get("stroke_opacity", 1) != 1
                or not solid
            ):
                continue
            for a, b in pairs:
                if _orthogonal(a, b):
                    # Remove irrelevant microscopic floating-point slants.
                    if abs(a[0] - b[0]) <= TOLERANCE:
                        b[0] = a[0]
                    if abs(a[1] - b[1]) <= TOLERANCE:
                        b[1] = a[1]
                    segments.append(
                        {
                            "id": _id(index, item_index, a, b),
                            "a": a,
                            "b": b,
                            "ids": [identity],
                            "path_index": index,
                            "item_index": item_index,
                            "seqno": path.get("seqno", -1),
                        }
                    )
            if item[0] == "l":
                a, b = pairs[0]
                if chain and not _close(chain[-1][1], a):
                    chain = []
                chain.append((a, b, identity))
                if len(chain) >= 4:
                    recent = chain[-4:]
                    if _close(recent[0][0], recent[-1][1]) and _rectangle(
                        [p[0] for p in recent]
                    ):
                        shapes.append(
                            {
                                "kind": "rectangle",
                                "bbox": _box([p[0] for p in recent]),
                                "ids": [p[2] for p in recent],
                                "path_index": index,
                                "seqno": path.get("seqno", -1),
                            }
                        )
    unique = {}
    for shape in shapes:
        key = (shape["kind"], tuple(round(v, 3) for v in shape["bbox"]))
        if key in unique:
            unique[key]["ids"] += shape["ids"]
        else:
            unique[key] = shape
    return segments, list(unique.values()), dots, circles, fills, all_ids


def _words(page, fills, layers, checksum, issues, clip_box=None):
    traces = page.get_texttrace()
    if sum(len(t.get("chars", [])) for t in traces) > MAX_WORDS * 20:
        raise PDFModelError("The page exceeds the bounded selectable-text work limit.")
    result = []
    visibility_work = 0
    fill_index = _BBoxIndex(fills)
    for span in traces:
        if (
            span.get("type") not in {0, 1}
            or span.get("opacity", 1) != 1
            or not _layer_visible(span.get("layer"), layers)
        ):
            continue
        # Character order is not guaranteed. Preserve writing direction while
        # separating actual whitespace, line changes and visible interword gaps.
        direction = span.get("dir", (1, 0))
        chars = span.get("chars", [])
        groups = defaultdict(list)
        for char in chars:
            origin = char[2]
            across = origin[0] * direction[0] + origin[1] * direction[1]
            line = round((-origin[0] * direction[1] + origin[1] * direction[0]) / 0.5)
            groups[line].append((across, char))
        for group in groups.values():
            pending = []

            def finish(pending, span):
                nonlocal visibility_work
                if not pending:
                    return
                text = "".join(chr(c[0]) for c in pending).strip()
                if not text:
                    return
                box = _box(
                    [
                        [c[3][i], c[3][j]]
                        for c in pending
                        for i in (0, 2)
                        for j in (1, 3)
                    ]
                )
                center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
                if not _inside(center, page.rect * page.derotation_matrix):
                    return
                clipped_text = clip_box is not None and (
                    box[0] < clip_box[0] - TOLERANCE
                    or box[1] < clip_box[1] - TOLERANCE
                    or box[2] > clip_box[2] + TOLERANCE
                    or box[3] > clip_box[3] + TOLERANCE
                )
                if clipped_text:
                    from .pdf_text_scope import proven_unclipped_trace

                    clipped_text = not proven_unclipped_trace(page, span)
                if clipped_text:
                    issues.append(
                        _issue(
                            "unresolved_text_clipping",
                            "The text is not wholly inside the proven visible clip intersection. Its identity is excluded.",
                            [_anchor(page, checksum, box, "text", text=text)],
                        )
                    )
                    return
                candidates = fill_index.intersecting(box)
                visibility_work += len(candidates)
                if visibility_work > MAX_ASSOCIATION_WORK:
                    raise PDFModelError(
                        "Text visibility testing exceeds the bounded work limit."
                    )
                if any(
                    f["seqno"] > span.get("seqno", -1) and _overlap(box, f["bbox"])
                    for f in candidates
                ):
                    issues.append(
                        _issue(
                            "covered_text",
                            "Text covered by a later opaque drawing is not used for equipment identity.",
                            [_anchor(page, checksum, box, "text", text=text)],
                        )
                    )
                    return
                result.append(
                    {
                        "text": text,
                        "bbox": box,
                        "center": center,
                        "size": float(span.get("size", 8)),
                        "evidence": _anchor(
                            page,
                            checksum,
                            box,
                            "text",
                            text=text,
                            seqno=span.get("seqno"),
                        ),
                    }
                )

            previous = None
            for _, char in sorted(group, key=lambda p: p[0]):
                value = chr(char[0])
                if value.isspace():
                    finish(pending, span)
                    pending = []
                    previous = None
                    continue
                if previous is not None:
                    # Glyph advance is along the writing direction, not its
                    # diagonal. The diagonal approximation joined separate
                    # rotated table headers across a visible column rule.
                    bounds = previous[3]
                    projected = [
                        x * direction[0] + y * direction[1]
                        for x in (bounds[0], bounds[2])
                        for y in (bounds[1], bounds[3])
                    ]
                    gap = math.dist(char[2], previous[2]) - (
                        max(projected) - min(projected)
                    )
                    if gap > max(
                        span.get("spacewidth", 2) * 0.8, span.get("size", 8) * 0.45
                    ):
                        finish(pending, span)
                        pending = []
                pending.append(char)
                previous = char
            finish(pending, span)
    if len(result) > MAX_WORDS:
        raise PDFModelError("The page exceeds the bounded word count.")
    return result


def _visible_clip_box(page, paths):
    box = list(page.rect * page.derotation_matrix)
    for path in paths:
        if path.get("type") not in {"clip", "group"}:
            continue
        items = path.get("items", [])
        if (
            path.get("type") == "clip"
            and len(items) == 1
            and items[0][0] == "re"
            and path.get("scissor")
        ):
            r = path["scissor"]
            box = [
                max(box[0], r.x0),
                max(box[1], r.y0),
                min(box[2], r.x1),
                min(box[3], r.y1),
            ]
        else:
            return [1, 1, 0, 0]
    return box


def visible_equipment_words(page, checksum):
    """Shared visibility boundary for property readers and vector observations."""
    if len(page.read_contents()) > MAX_PAGE_CONTENT:
        raise PDFModelError("The page content exceeds the bounded extraction size.")
    paths = page.get_drawings(extended=True)
    if (
        len(paths) > MAX_PATHS
        or sum(len(p.get("items", [])) for p in paths) > MAX_ITEMS
    ):
        raise PDFModelError("The page exceeds bounded vector extraction limits.")
    options = defaultdict(list)
    for value in page.parent.get_ocgs().values():
        options[value["name"]].append(value.get("on"))
    layers = {
        name: all(values) for name, values in options.items() if len(set(values)) == 1
    }
    issues = []
    _, _, _, _, fills, _ = _primitives(page, paths, checksum, layers, issues)
    words = _words(
        page, fills, layers, checksum, issues, _visible_clip_box(page, paths)
    )
    return words, issues


def _shape_contains(shape, point, strict=False):
    if shape["kind"] == "circle":
        return math.dist(shape["center"], point) < shape["radius"] - (
            TOLERANCE if strict else -TOLERANCE
        )
    return _inside(point, shape["bbox"], strict)


def _shape_boundary(shape, point):
    if shape["kind"] == "circle":
        return abs(math.dist(shape["center"], point) - shape["radius"]) <= TOLERANCE
    b = shape["bbox"]
    return (
        _distance_to_box(point, b) <= TOLERANCE
        and min(
            abs(point[0] - b[0]),
            abs(point[0] - b[2]),
            abs(point[1] - b[1]),
            abs(point[1] - b[3]),
        )
        <= TOLERANCE
    )


def _devices(page, checksum, shapes, words, issues):
    if len(shapes) * len(words) > MAX_ASSOCIATION_WORK:
        raise PDFModelError(
            "Device/text association exceeds the bounded geometry work limit."
        )
    words = list(words)
    spaced = []
    prefixes = [w for w in words if re.fullmatch(SPACED_PREFIX, w["text"])]
    if len(prefixes) * len(words) > MAX_ASSOCIATION_WORK:
        raise PDFModelError("Spaced tag association exceeds the bounded work limit.")
    for first in prefixes:
        candidates = [
            second
            for second in words
            if re.fullmatch(r"\d{1,8}[A-Z]?", second["text"])
            and abs(first["center"][1] - second["center"][1])
            <= max(1, first["size"] * 0.3)
            and 0 <= second["bbox"][0] - first["bbox"][2] <= first["size"]
        ]
        if len(candidates) == 1:
            second = candidates[0]
            box = _box(
                [
                    [first["bbox"][0], first["bbox"][1]],
                    [second["bbox"][2], second["bbox"][3]],
                ]
            )
            spaced.append(
                {
                    "text": first["text"] + " " + second["text"],
                    "bbox": box,
                    "center": [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2],
                    "size": first["size"],
                    "evidence": _anchor(
                        page,
                        checksum,
                        box,
                        "text",
                        text=first["text"] + " " + second["text"],
                    ),
                }
            )
        elif len(candidates) > 1:
            issues.append(
                _issue(
                    "ambiguous_spaced_tag",
                    "A printed equipment prefix has multiple adjacent numbered labels; no tag was joined.",
                    [first["evidence"]],
                )
            )
    words.extend(spaced)
    tags = [
        w
        for w in words
        if TAG.fullmatch(w["text"])
        and _inside(w["center"], page.rect * page.derotation_matrix)
    ]
    if len(tags) * len(words) > MAX_ASSOCIATION_WORK:
        raise PDFModelError("Tag context association exceeds the bounded work limit.")
    choices = {}
    for word in tags:
        if any(
            re.sub(r"[^A-Z]", "", w["text"].upper())
            in {
                "FRAME",
                "TRIP",
                "PART",
                "CATALOG",
                "QUANTITY",
                "QTY",
                "NAMEPLATE",
                "VOLTAGE",
                "INTERRUPTING",
                "MODEL",
            }
            and abs(w["center"][1] - word["center"][1])
            < max(w["size"], word["size"]) * 0.6
            and 0 < word["bbox"][0] - w["bbox"][0] < 160
            for w in words
        ):
            # A labelled catalogue/model/rating value is not the device's tag,
            # even when its spelling resembles one inside an equipment table.
            continue
        if any(
            w["text"].upper() in {"SCHEMATIC", "SHEET", "DWG", "DRAWING", "PAGE"}
            and abs(w["center"][1] - word["center"][1]) < max(w["size"], word["size"])
            and abs(w["center"][0] - word["center"][0]) < 160
            for w in words
        ):
            # A literal sheet/drawing reference is document context, not an
            # equipment tag. The corresponding visible label remains in PDF.
            continue
        strong_identity = bool(re.search(r"[-_ ]", word["text"]))
        if not strong_identity and not any(
            _shape_contains(s, word["center"])
            and abs(word["center"][0] - (s["bbox"][0] + s["bbox"][2]) / 2)
            <= (s["bbox"][2] - s["bbox"][0]) * 0.3
            and abs(word["center"][1] - (s["bbox"][1] + s["bbox"][3]) / 2)
            <= (s["bbox"][3] - s["bbox"][1]) * 0.3
            for s in shapes
        ):
            continue
        contained = [
            s
            for s in shapes
            if _shape_contains(s, word["center"])
            and _area(s["bbox"]) < page.rect.width * page.rect.height * 0.35
            and min(s["bbox"][2] - s["bbox"][0], s["bbox"][3] - s["bbox"][1]) >= 8
        ]
        if contained:
            smallest = min(_area(s["bbox"]) for s in contained)
            possible = [s for s in contained if abs(_area(s["bbox"]) - smallest) < 0.01]
        else:
            possible = [
                s
                for s in shapes
                if _distance_to_box(word["center"], s["bbox"])
                <= min(18, word["size"] * 2)
                and s["bbox"][0] <= word["center"][0] <= s["bbox"][2]
                and _area(s["bbox"]) < page.rect.width * page.rect.height * 0.35
            ]
            if possible:
                distance = min(
                    _distance_to_box(word["center"], s["bbox"]) for s in possible
                )
                possible = [
                    s
                    for s in possible
                    if abs(_distance_to_box(word["center"], s["bbox"]) - distance)
                    < TOLERANCE
                ]
        if len(possible) != 1:
            issues.append(
                _issue(
                    "unassociated_tag",
                    "A visible tag has no unique supported closed symbol; it is not treated as a complete device observation.",
                    [word["evidence"]],
                    tag=word["text"],
                )
            )
        else:
            choices.setdefault(id(possible[0]), (possible[0], []))[1].append(word)
    result = []
    for shape, labels in choices.values():
        unique = {w["text"] for w in labels}
        if len(unique) != 1:
            issues.append(
                _issue(
                    "ambiguous_symbol_identity",
                    "Several equipment identities occupy the same candidate symbol.",
                    [w["evidence"] for w in labels],
                )
            )
            continue
        word = labels[0]
        evidence = [
            word["evidence"],
            _anchor(
                page,
                checksum,
                shape["bbox"],
                "vector",
                path_indices=[shape["path_index"]],
            ),
        ]
        result.append(
            {
                "id": _id(checksum, original_page(page), word["text"], shape["bbox"]),
                "tag": word["text"],
                "page": original_page(page),
                "bbox": evidence[1]["bbox"],
                "normalized_bbox": evidence[1]["normalized_bbox"],
                "symbol": {
                    "kind": "boxed_control"
                    if shape["kind"] == "rectangle"
                    else "tagged_circular_symbol",
                    "status": "observed_outline",
                    "function": None,
                },
                "ports": [],
                "evidence": evidence,
                "_shape": shape,
            }
        )
    return result


def _outside(segment, devices):
    a, b = segment["a"], segment["b"]
    vertical = abs(a[0] - b[0]) <= TOLERANCE
    axis = 1 if vertical else 0
    values = {a[axis], b[axis]}
    for device in devices:
        s = device["_shape"]
        box = s["bbox"]
        if s["kind"] == "rectangle":
            if box[1 - axis] - TOLERANCE <= a[1 - axis] <= box[3 - axis] + TOLERANCE:
                values.update([box[axis], box[axis + 2]])
        else:
            fixed = a[1 - axis] - s["center"][1 - axis]
            if abs(fixed) <= s["radius"]:
                delta = math.sqrt(max(0, s["radius"] ** 2 - fixed**2))
                values.update([s["center"][axis] - delta, s["center"][axis] + delta])
    lower, upper = sorted((a[axis], b[axis]))
    values = sorted(v for v in values if lower - TOLERANCE <= v <= upper + TOLERANCE)
    pieces = []
    for low, high in pairwise(values):
        if high - low <= TOLERANCE:
            continue
        start = [a[0], low] if vertical else [low, a[1]]
        stop = [a[0], high] if vertical else [high, a[1]]
        midpoint = [(start[0] + stop[0]) / 2, (start[1] + stop[1]) / 2]
        if not any(_shape_contains(d["_shape"], midpoint, True) for d in devices):
            pieces.append(
                {
                    **segment,
                    "id": _id(segment["id"], start, stop),
                    "a": start,
                    "b": stop,
                }
            )
    return pieces


class _Union:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, n):
        while self.parent[n] != n:
            self.parent[n] = self.parent[self.parent[n]]
            n = self.parent[n]
        return n

    def join(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _candidate_pairs(segments):
    buckets = defaultdict(list)
    total = 0
    seen = set()
    for index, segment in enumerate(segments):
        b = _box([segment["a"], segment["b"]])
        for x in range(math.floor(b[0] / 32), math.floor(b[2] / 32) + 1):
            for y in range(math.floor(b[1] / 32), math.floor(b[3] / 32) + 1):
                total += 1
                if total > MAX_SPATIAL_BUCKET_ITEMS:
                    raise PDFModelError(
                        "Vector spatial indexing exceeds this adapter's bounded work limit."
                    )
                for other in buckets[x, y]:
                    pair = (other, index)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    if len(seen) > MAX_PAIR_TESTS:
                        raise PDFModelError(
                            "Vector contact testing exceeds this adapter's bounded work limit."
                        )
                    yield pair
                buckets[x, y].append(index)


def _topology(page, checksum, segments, devices, dots, circles, words, issues):
    if len(segments) * (len(devices) + len(dots) + len(circles)) > MAX_ASSOCIATION_WORK:
        raise PDFModelError(
            "Terminal/vector association exceeds the bounded geometry work limit."
        )
    body_ids = {v for d in devices for v in d["_shape"]["ids"]}
    wires = [
        piece
        for s in segments
        if not body_ids.intersection(s["ids"])
        for piece in _outside(s, devices)
    ]
    if len(wires) > MAX_SEGMENTS:
        raise PDFModelError("The page exceeds the bounded vector conductor count.")
    union = _Union(len(wires))
    crossings = []
    dot_members = defaultdict(set)
    segment_dots = defaultdict(set)
    accounted = set(body_ids)
    endpoint_counts = Counter(
        tuple(round(v, 3) for v in p) for w in wires for p in (w["a"], w["b"])
    )
    for i, s in enumerate(wires):
        for dot_index, dot in enumerate(dots):
            if _on(dot["center"], s["a"], s["b"]):
                dot_members[dot_index].add(i)
                segment_dots[i].add(dot_index)
    for dot_index, members in dot_members.items():
        if len(members) > 1:
            first = next(iter(members))
            for other in members:
                union.join(first, other)
            accounted.update(dots[dot_index]["ids"])
    for left, right in _candidate_pairs(wires):
        a, b = wires[left], wires[right]
        kind, point = _intersection(a["a"], a["b"], b["a"], b["b"])
        if kind is None:
            continue
        if kind == "overlap":
            if {_id(a["a"]), _id(a["b"])} == {_id(b["a"]), _id(b["b"])}:
                union.join(left, right)
            else:
                issues.append(
                    _issue(
                        "overlapping_vectors",
                        "Partly overlapping conductor strokes have ambiguous identity and are not silently merged.",
                        [
                            _anchor(
                                page,
                                checksum,
                                _box([a["a"], a["b"], b["a"], b["b"]]),
                                "vector",
                                path_indices=sorted({a["path_index"], b["path_index"]}),
                            )
                        ],
                    )
                )
            continue
        ae = any(_close(point, p) for p in (a["a"], a["b"]))
        be = any(_close(point, p) for p in (b["a"], b["b"]))
        dotted = bool(segment_dots[left].intersection(segment_dots[right]))
        endpoint_degree = endpoint_counts[tuple(round(v, 3) for v in point)]
        collinear = (abs(a["a"][0] - a["b"][0]) <= TOLERANCE) == (
            abs(b["a"][0] - b["b"][0]) <= TOLERANCE
        )
        connected = dotted or ae and be and (endpoint_degree <= 2 or collinear)
        if connected:
            union.join(left, right)
        else:
            crossings.append(
                {
                    "position": _visual(page, point),
                    "connected": False,
                    "kind": "ambiguous_t_contact"
                    if ae or be
                    else "unconnected_crossing",
                    "evidence": [
                        _anchor(
                            page,
                            checksum,
                            [point[0] - 1, point[1] - 1, point[0] + 1, point[1] + 1],
                            "vector",
                            path_indices=sorted({a["path_index"], b["path_index"]}),
                        )
                    ],
                }
            )
            if ae or be:
                issues.append(
                    _issue(
                        "ambiguous_t_contact",
                        "A conductor endpoint meets another conductor's interior without an explicit junction dot; no connection is guessed.",
                        crossings[-1]["evidence"],
                    )
                )
    port_members = defaultdict(set)
    unassigned_terminals = []
    used_labels = set()
    label_work = 0
    for device in devices:
        contacts = {}
        shape = device["_shape"]
        for index, segment in enumerate(wires):
            for point in (segment["a"], segment["b"]):
                if _shape_boundary(shape, point):
                    key = tuple(round(v, 3) for v in point)
                    contacts.setdefault(key, set()).add(index)
        for point, indices in contacts.items():
            label_work += len(words)
            if label_work > MAX_ASSOCIATION_WORK:
                raise PDFModelError(
                    "Terminal label association exceeds the bounded work limit."
                )
            adjacent = [
                w
                for w in words
                if w["text"] != device["tag"]
                and PORT.fullmatch(w["text"])
                and w["text"] not in {d["tag"] for d in devices}
                and (
                    any(c.isdigit() for c in w["text"])
                    or w["text"] in {"N", "L", "PE", "GND", "+", "-"}
                )
                and _distance_to_box(point, w["bbox"]) <= max(8, w["size"] * 1.25)
            ]
            adjacent.sort(key=lambda w: _distance_to_box(point, w["bbox"]))
            chosen = (
                adjacent[0]
                if adjacent
                and (
                    len(adjacent) == 1
                    or _distance_to_box(point, adjacent[1]["bbox"])
                    - _distance_to_box(point, adjacent[0]["bbox"])
                    > TOLERANCE
                )
                else None
            )
            label = chosen["text"] if chosen else None
            if chosen and id(chosen) in used_labels:
                issues.append(
                    _issue(
                        "ambiguous_port_label",
                        "One terminal label is adjacent to more than one actual contact; the label is not assigned twice.",
                        [chosen["evidence"]],
                        tag=device["tag"],
                    )
                )
                label = None
                chosen = None
            if chosen:
                used_labels.add(id(chosen))
            evidence = [
                _anchor(
                    page,
                    checksum,
                    [point[0] - 0.5, point[1] - 0.5, point[0] + 0.5, point[1] + 0.5],
                    "vector",
                    path_indices=sorted({wires[i]["path_index"] for i in indices}),
                )
            ]
            if chosen:
                evidence.append(chosen["evidence"])
            identity = (
                device["tag"] + ":" + (label if label else "contact-" + _id(point)[:8])
            )
            first = next(iter(indices))
            for other in indices:
                union.join(first, other)
            p = {
                "id": identity,
                "label": label,
                "position": _visual(page, point),
                "bbox": evidence[0]["bbox"],
                "normalized_bbox": evidence[0]["normalized_bbox"],
                "status": "observed_labelled_terminal"
                if label
                else "observed_geometric_contact",
                "evidence": evidence,
                "_segment_indices": indices,
            }
            device["ports"].append(p)
            if label is None:
                issues.append(
                    _issue(
                        "unlabelled_contact",
                        "The actual geometric contact is visible, but no unique terminal label is established.",
                        evidence,
                        tag=device["tag"],
                    )
                )
            for marker in circles:
                if _close(marker["center"], point):
                    accounted.update(marker["ids"])
        if not device["ports"]:
            device["symbol"]["status"] = "unknown_no_vector_contacts"
            issues.append(
                _issue(
                    "unconfirmed_symbol",
                    "A tagged outline has no established conductor contacts; it may be a table cell or unfamiliar symbol.",
                    device["evidence"],
                    tag=device["tag"],
                )
            )
        labels = [p["label"] for p in device["ports"] if p["label"]]
        if len(labels) != len(set(labels)):
            issues.append(
                _issue(
                    "duplicate_port_label",
                    "A symbol repeats the same terminal label at different physical contacts.",
                    device["evidence"],
                    tag=device["tag"],
                )
            )
            repeated = {label for label, count in Counter(labels).items() if count > 1}
            for port in device["ports"]:
                if port["label"] in repeated:
                    port.update(
                        observed_text=port["label"],
                        label=None,
                        id=device["tag"]
                        + ":ambiguous-contact-"
                        + _id(port["position"])[:8],
                        status="unknown_conflicting_terminal_label",
                    )
        for port in device["ports"]:
            port_members[port["id"]].update(port.pop("_segment_indices"))
    assigned_points = [p["position"] for d in devices for p in d["ports"]]
    for marker in circles:
        if any(_close(_visual(page, marker["center"]), p) for p in assigned_points):
            continue
        indices = {
            index
            for index, s in enumerate(wires)
            if _on(marker["center"], s["a"], s["b"])
            or any(
                abs(math.dist(p, marker["center"]) - marker["radius"]) <= TOLERANCE
                for p in (s["a"], s["b"])
            )
        }
        if not indices:
            continue
        label_work += len(words)
        if label_work > MAX_ASSOCIATION_WORK:
            raise PDFModelError(
                "Terminal label association exceeds the bounded work limit."
            )
        adjacent = [
            w
            for w in words
            if PORT.fullmatch(w["text"])
            and any(c.isdigit() for c in w["text"])
            and not re.search(r"[-_]", w["text"])
            and _distance_to_box(marker["center"], w["bbox"])
            <= marker["radius"] + max(6, w["size"])
        ]
        adjacent.sort(key=lambda w: _distance_to_box(marker["center"], w["bbox"]))
        chosen = (
            adjacent[0]
            if adjacent
            and id(adjacent[0]) not in used_labels
            and (
                len(adjacent) == 1
                or _distance_to_box(marker["center"], adjacent[1]["bbox"])
                - _distance_to_box(marker["center"], adjacent[0]["bbox"])
                > TOLERANCE
            )
            else None
        )
        if chosen:
            used_labels.add(id(chosen))
        label = chosen["text"] if chosen else None
        identity = f"p{original_page(page)}:terminal:{label or 'unlabelled'}:{_id(marker['center'])[:8]}"
        anchor = _anchor(
            page,
            checksum,
            marker["bbox"],
            "vector",
            path_indices=[marker["path_index"]],
        )
        evidence = [anchor, *([chosen["evidence"]] if chosen else [])]
        unassigned_terminals.append(
            {
                "id": identity,
                "label": label,
                "equipment_tag": None,
                "position": _visual(page, marker["center"]),
                "bbox": anchor["bbox"],
                "normalized_bbox": anchor["normalized_bbox"],
                "status": "observed_unassigned_terminal"
                if label
                else "unknown_unlabelled_terminal",
                "evidence": evidence,
            }
        )
        if label is None:
            issues.append(
                _issue(
                    "unlabelled_unassigned_terminal",
                    "An actual terminal marker touches vector conductors, but its terminal identity is not uniquely readable.",
                    evidence,
                )
            )
            # An unnamed open circle may be a wire-hop or an unfamiliar
            # symbol. Its mere proximity cannot join otherwise separate nets.
            continue
        first = next(iter(indices))
        for index in indices:
            union.join(first, index)
        port_members[identity].update(indices)
        accounted.update(marker["ids"])
    groups = defaultdict(list)
    for index in range(len(wires)):
        groups[union.find(index)].append(index)
    group_ports = defaultdict(set)
    for identity, indices in port_members.items():
        for index in indices:
            group_ports[union.find(index)].add(identity)
    connections = []
    for group_id, members in groups.items():
        terminals = sorted(group_ports[group_id])
        evidence = [
            _anchor(
                page,
                checksum,
                _box([wires[i]["a"], wires[i]["b"]]),
                "vector",
                path_indices=[wires[i]["path_index"]],
            )
            for i in members
        ]
        if len(terminals) < 2:
            issues.append(
                _issue(
                    "unresolved_vector_component",
                    "Vector strokes do not establish two supported device terminals; omitted inventory/topology is not presumed absent.",
                    evidence[:4],
                    observed_terminal_ids=terminals,
                )
            )
            continue
        identity = _id(
            checksum, original_page(page), terminals, [wires[i]["id"] for i in members]
        )
        pieces = [
            {
                "points": [_visual(page, wires[i]["a"]), _visual(page, wires[i]["b"])],
                "evidence": evidence[n],
            }
            for n, i in enumerate(members)
        ]
        connections.append(
            {
                "id": identity,
                "net_component": identity,
                "page": original_page(page),
                "terminal_ids": terminals,
                "from": terminals[0] if len(terminals) == 2 else None,
                "to": terminals[1] if len(terminals) == 2 else None,
                "segments": pieces,
                "path_points": None,
                "status": "observed_vector_connectivity",
                "evidence": evidence,
            }
        )
        for i in members:
            accounted.update(wires[i]["ids"])
    junctions = [
        {
            "position": _visual(page, dot["center"]),
            "evidence": [
                _anchor(
                    page,
                    checksum,
                    dot["bbox"],
                    "vector",
                    path_indices=[dot["path_index"]],
                )
            ],
            "connected_segment_count": len(dot_members[index]),
        }
        for index, dot in enumerate(dots)
        if len(dot_members[index]) > 1
    ]
    return connections, junctions, crossings, accounted, unassigned_terminals


def _regional_topology(page, checksum, segments, devices, dots, circles, words, issues):
    """Bound dense-page work without claiming cross-region continuity.

    A full-page topology pass can be quadratic even after visibility indexing.
    Retry only wholly contained geometry in 64 bounded regions. Strokes and
    symbols crossing a region edge are counted as unresolved, never truncated
    into a false open terminal or joined on proximity.
    """
    bounds = list(page.rect * page.derotation_matrix)
    started = time.monotonic()
    outputs = [[], [], [], set(), []]
    consumed, failed, processed = set(), [], 0
    for y in range(8):
        for x in range(8):
            box = [
                bounds[0] + (bounds[2] - bounds[0]) * x / 8,
                bounds[1] + (bounds[3] - bounds[1]) * y / 8,
                bounds[0] + (bounds[2] - bounds[0]) * (x + 1) / 8,
                bounds[1] + (bounds[3] - bounds[1]) * (y + 1) / 8,
            ]

            def contained(b, region=box):
                return (
                    b[0] >= region[0]
                    and b[1] >= region[1]
                    and b[2] <= region[2]
                    and b[3] <= region[3]
                )

            local_segments = [s for s in segments if contained(_box([s["a"], s["b"]]))]
            if not local_segments:
                continue
            if time.monotonic() - started > 20:
                failed.append({"bbox": box, "reason": "regional_time_budget"})
                continue
            local_devices = [d for d in devices if contained(d["_shape"]["bbox"])]
            local_words = [
                w
                for w in words
                if _inside(
                    w["center"], [box[0] - 20, box[1] - 20, box[2] + 20, box[3] + 20]
                )
            ]
            local_dots = [d for d in dots if _inside(d["center"], box)]
            local_circles = [c for c in circles if contained(c["bbox"])]
            local_issues = []
            try:
                result = _topology(
                    page,
                    checksum,
                    local_segments,
                    local_devices,
                    local_dots,
                    local_circles,
                    local_words,
                    local_issues,
                )
            except PDFModelError as exc:
                failed.append({"bbox": box, "reason": str(exc)})
                continue
            processed += 1
            consumed.update(s["id"] for s in local_segments)
            for index, value in enumerate(result):
                outputs[index].update(value) if index == 3 else outputs[index].extend(
                    value
                )
            issues.extend(local_issues)
    unresolved = [s for s in segments if s["id"] not in consumed]
    issues.append(
        _issue(
            "regional_topology_partial",
            "Dense-page topology was inspected in bounded regions. Region-edge strokes and failed regions remain unknown; complete or cross-region connectivity is not established.",
            processed_region_count=processed,
            failed_regions=failed,
            unresolved_segment_count=len(unresolved),
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
    )
    return tuple(outputs)


def _parse_page(page, checksum, layers):
    issues = []
    if len(page.read_contents()) > MAX_PAGE_CONTENT:
        raise PDFModelError("The page content exceeds the bounded extraction size.")
    annotations = list(page.annots() or [])
    if len(annotations) > 1000:
        raise PDFModelError("The page exceeds the bounded review annotation count.")
    if annotations:
        issues.append(
            _issue(
                "annotations_uninterpreted",
                "PDF review annotations are separate requested changes, not proof of actual drawing geometry.",
            )
        )
        # Annotation appearance streams may contain wire-like paths and clip
        # groups. Remove only those overlays from this private in-memory copy;
        # source bytes remain untouched and their hash anchors every result.
        for annotation in annotations:
            page.delete_annot(annotation)
    paths = page.get_drawings(extended=True)
    if (
        len(paths) > MAX_PATHS
        or sum(len(p.get("items", [])) for p in paths) > MAX_ITEMS
    ):
        raise PDFModelError("The page exceeds bounded vector extraction limits.")
    if page.get_image_info():
        issues.append(
            _issue(
                "raster_content",
                "Raster drawing content has not been interpreted and is not replaced by selectable text or nearby vectors.",
            )
        )
    if page.cropbox != page.mediabox:
        issues.append(
            _issue(
                "cropped_page_scope",
                "The visible crop is a bounded drawing subset. Equipment outside the crop remains unknown.",
            )
        )
    segments, shapes, dots, circles, fills, all_ids = _primitives(
        page, paths, checksum, layers, issues
    )
    if len(shapes) * len(circles) > MAX_ASSOCIATION_WORK:
        raise PDFModelError(
            "Symbol enclosure association exceeds the bounded work limit."
        )
    # Texttrace does not expose the active clip stack. Restrict text to the
    # intersection of actual rectangular clips, rather than trusting invisible
    # labels from clipped form XObjects. Multiple disjoint forms deliberately
    # lose identity coverage until a per-text clip interpreter is available.
    clip_box = _visible_clip_box(page, paths)
    words = _words(page, fills, layers, checksum, issues, clip_box)
    if not words:
        issues.append(
            _issue(
                "no_selectable_labels",
                "No visible selectable labels establish equipment identity. Outlined glyphs and scans require a separate interpretation step.",
            )
        )
    footer_context = any(
        w["text"].upper() in {"SCHEMATIC", "DRAWING", "SHEET", "NTS"}
        and w["center"][1] > (page.rect * page.derotation_matrix).height * 0.85
        for w in words
    )
    layout_ids = {
        identity
        for segment in segments
        if footer_context
        and abs(segment["a"][1] - segment["b"][1]) <= TOLERANCE
        and segment["a"][1] > (page.rect * page.derotation_matrix).height * 0.85
        and abs(segment["a"][0] - segment["b"][0])
        > (page.rect * page.derotation_matrix).width * 0.75
        for identity in segment["ids"]
    }
    segments = [s for s in segments if not layout_ids.intersection(s["ids"])]
    containers = [
        s
        for s in shapes
        if s["kind"] == "rectangle"
        and sum(_shape_contains(s, c["center"], True) for c in circles) >= 6
    ]
    container_ids = {identity for shape in containers for identity in shape["ids"]}
    if containers:
        issues.append(
            _issue(
                "multi_terminal_container",
                "A large outline encloses many terminal markers. It is retained as diagram context rather than guessed to be one customer equipment instance.",
                [
                    _anchor(
                        page,
                        checksum,
                        s["bbox"],
                        "vector",
                        path_indices=[s["path_index"]],
                    )
                    for s in containers
                ],
            )
        )
    shapes = [s for s in shapes if s not in containers]
    segments = [s for s in segments if not container_ids.intersection(s["ids"])]
    devices = _devices(page, checksum, shapes, words, issues)
    if not devices:
        issues.append(
            _issue(
                "no_supported_device_symbols",
                "No uniquely labelled supported equipment outlines establish a complete device observation. Visible reference models and unfamiliar symbols are not converted into customer equipment.",
            )
        )
    known_fill_ids = (
        {i for d in devices for i in d["_shape"]["ids"]}
        | {i for dot in dots for i in dot["ids"]}
        | {i for circle in circles for i in circle["ids"]}
    )
    # Unknown later opaque shapes may conceal old wire strokes. Reject those
    # strokes instead of tracing through an invisible earlier revision.
    visible = []
    fill_index = _BBoxIndex(fills)
    visibility_work = 0
    for segment in segments:
        candidates = fill_index.intersecting(_box([segment["a"], segment["b"]]))
        visibility_work += len(candidates)
        if visibility_work > MAX_ASSOCIATION_WORK:
            raise PDFModelError(
                "Conductor visibility testing exceeds the bounded work limit."
            )
        covers = [
            f
            for f in candidates
            if f["seqno"] > segment["seqno"]
            and not f["ids"].intersection(known_fill_ids)
            and _segment_in_box(segment["a"], segment["b"], f["bbox"])
        ]
        if covers:
            issues.append(
                _issue(
                    "covered_conductor",
                    "A later unknown opaque fill covers this stroke; it is excluded from electrical topology.",
                    [
                        _anchor(
                            page,
                            checksum,
                            _box([segment["a"], segment["b"]]),
                            "vector",
                            path_indices=[segment["path_index"]],
                        )
                    ],
                )
            )
        else:
            visible.append(segment)
    try:
        connections, junctions, crossings, accounted, unassigned_terminals = _topology(
            page, checksum, visible, devices, dots, circles, words, issues
        )
    except PDFModelError as exc:
        if len(paths) < 2000 and len(visible) <= MAX_SEGMENTS:
            # An ordinary small page exhausting a deliberately low safety
            # allowance stays a failure. Regional recovery is for dense pages.
            raise
        issues.append(_issue("full_page_topology_budget", str(exc)))
        connections, junctions, crossings, accounted, unassigned_terminals = (
            _regional_topology(
                page, checksum, visible, devices, dots, circles, words, issues
            )
        )
    accounted.update(layout_ids)
    accounted.update(container_ids)
    remaining = all_ids - accounted
    if remaining:
        issues.append(
            _issue(
                "unaccounted_vector_ink",
                "Some drawing primitives are not proven device outlines, terminal markers or connected conductors. Full-sheet equipment coverage remains unknown.",
                unaccounted_vector_count=len(remaining),
            )
        )
    for device in devices:
        device.pop("_shape", None)
    issue_count = len(issues)
    if issue_count > MAX_ISSUES:
        # Keep processing/coverage boundaries visible even when outline-font
        # artifacts generated many low-level unknown-shape observations first.
        priority = [
            i
            for i in issues
            if i["code"] in {"regional_topology_partial", "full_page_topology_budget"}
        ]
        detail = [i for i in issues if i not in priority]
        issues = [
            *priority,
            *detail[: MAX_ISSUES - len(priority)],
            _issue(
                "additional_coverage_issues",
                "Additional unresolved observations were counted; the detailed list is bounded.",
                additional_count=issue_count - MAX_ISSUES,
            ),
        ]
    return {
        "page": original_page(page),
        "width_pt": page.rect.width,
        "height_pt": page.rect.height,
        "rotation": page.rotation,
        "coordinate_system": "visible-page-points",
        "units": "pdf_points",
        "physical_scale": None,
        "sheet_identity": sheet_identity(page, words, checksum),
        "devices": devices,
        "connections": connections,
        "junctions": junctions,
        "crossings": crossings,
        "unassigned_terminals": unassigned_terminals,
        "issues": issues,
        "coverage": {
            "status": "unknown" if issues else "bounded",
            "observed_device_count": sum(bool(d["ports"]) for d in devices),
            "vector_primitive_count": len(all_ids),
            "unaccounted_vector_count": len(remaining),
            "complete_inventory_established": False,
            "issue_count": issue_count,
        },
    }


def _parse(data, checksum):
    with pymupdf.open(stream=data, filetype="pdf") as document:
        if document.needs_pass:
            raise PDFModelError(
                "Encrypted drawings require an authorized readable source copy."
            )
        if document.xref_length() > 50000:
            raise PDFModelError("The PDF object graph exceeds this adapter's bound.")
        layer_options = defaultdict(list)
        for value in document.get_ocgs().values():
            layer_options[value["name"]].append(value.get("on"))
        layers = {
            name: all(values)
            for name, values in layer_options.items()
            if len(set(values)) == 1
        }
        pages = []
        issues = []
        batches = []
        graph_records = 0
        for batch, chunk in document_batches(document):
            batches.append({**batch.public(), "original_sha256": checksum})
            for page in original_pages(batch, chunk):
                try:
                    if graph_records >= MAX_DOCUMENT_GRAPH_RECORDS:
                        raise PDFModelError(
                            "The original document exceeds the bounded graph-output budget; this page remains explicitly uninspected."
                        )
                    report = _parse_page(page, checksum, layers)
                    graph_records += (
                        len(report["devices"])
                        + sum(len(d["ports"]) for d in report["devices"])
                        + sum(len(c["segments"]) for c in report["connections"])
                        + len(report["issues"])
                    )
                    if graph_records > MAX_DOCUMENT_GRAPH_RECORDS:
                        raise PDFModelError(
                            "The original document exceeds the bounded graph-output budget; this page remains unknown."
                        )
                    pages.append(report)
                except PDFModelError as exc:
                    issue = _issue(
                        "page_complexity_or_coverage",
                        str(exc),
                        page=original_page(page),
                    )
                    pages.append(
                        {
                            "page": original_page(page),
                            "width_pt": page.rect.width,
                            "height_pt": page.rect.height,
                            "coordinate_system": "visible-page-points",
                            "units": "pdf_points",
                            "physical_scale": None,
                            "devices": [],
                            "connections": [],
                            "junctions": [],
                            "crossings": [],
                            "unassigned_terminals": [],
                            "issues": [issue],
                            "coverage": {
                                "status": "unknown",
                                "observed_device_count": 0,
                                "complete_inventory_established": False,
                            },
                        }
                    )
        if len(document) > MAX_PAGES:
            issues.append(
                _issue(
                    "unprocessed_pages",
                    "Only the bounded first drawing pages were inspected; later sheets have unknown equipment and connectivity.",
                    unprocessed_page_count=len(document) - MAX_PAGES,
                )
            )
        devices = [d for p in pages for d in p["devices"]]
        duplicates = {
            tag
            for tag, count in Counter(d["tag"] for d in devices).items()
            if count > 1
        }
        for variants in ambiguous_tag_styles(d["tag"] for d in devices):
            issues.append(
                _issue(
                    "ambiguous_tag_styles",
                    "Similar printed labels are distinct exact identities until a company reviewer maps them.",
                    original_tags=variants,
                )
            )
        for tag in sorted(duplicates):
            issues.append(
                _issue(
                    "duplicate_or_multiunit_tag",
                    "The same exact tag occurs on multiple symbols. Multi-unit identity is not guessed or merged.",
                    [e for d in devices if d["tag"] == tag for e in d["evidence"]],
                    tag=tag,
                )
            )
        for device in devices:
            if device["tag"] in duplicates:
                device["symbol"]["status"] = "unknown_multiunit_identity"
        for page in pages:
            page["connections"] = [
                c
                for c in page["connections"]
                if not any(
                    any(t.startswith(tag + ":") for tag in duplicates)
                    for t in c["terminal_ids"]
                )
            ]
        issues += [i for p in pages for i in p["issues"]]
        return {
            "schema_version": SCHEMA_VERSION,
            "sha256": checksum,
            "pages": pages,
            "processing_batches": batches,
            "devices": devices,
            "connections": [c for p in pages for c in p["connections"]],
            "unassigned_terminals": [
                t for p in pages for t in p["unassigned_terminals"]
            ],
            "issues": issues,
            "coverage": {
                "status": "unknown" if issues else "bounded",
                "observed_device_count": sum(
                    p["coverage"]["observed_device_count"] for p in pages
                ),
                "processed_pages": len(pages),
                "total_pages": len(document),
                "unaccounted_vector_count": sum(
                    p["coverage"].get("unaccounted_vector_count", 0) for p in pages
                ),
                "complete_inventory_established": False,
            },
            "limitations": [
                "Only actual supported visible vector outlines, selectable labels and vector contacts are observed; unfamiliar symbols and unaccounted ink remain unknown.",
                "Page coordinates are PDF points on a schematic. No physical millimetres, scale, wire gauge, internal device behavior or production compliance is inferred.",
                "Unmarked interior crossings are not electrical junctions; endpoint-to-interior contacts without dots remain ambiguous.",
                "Cross-sheet links, terminal-strip internal jumpers and repeated multi-unit device identity are not inferred.",
            ],
        }


def _bind(value, source):
    if isinstance(value, list):
        return [_bind(v, source) for v in value]
    if isinstance(value, dict):
        result = {k: _bind(v, source) for k, v in value.items()}
        if "source_id" in result and "sha256" in result:
            result.update(
                source_id=source.id, name=source.name, revision=source.revision
            )
        return result
    return value


def inspect_pdf(source):
    """Read an immutable authorized drawing; cache only by verified actual bytes."""
    source = (
        source
        if isinstance(source, SourceInput)
        else SourceInput.model_validate(source)
    )
    if source.role != "drawing" or Path(source.name).suffix.lower() != ".pdf":
        raise PDFModelError("Only actual drawing PDFs establish this equipment graph.")
    path = Path(source.path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise PDFModelError(
            "The drawing source is unavailable or exceeds the bounded PDF size."
        )
    data = path.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    if checksum != source.sha256.lower():
        raise PDFModelError(
            "The actual drawing bytes no longer match the selected source hash."
        )
    with _CACHE_LOCK:
        cached = _CACHE.get(checksum)
    if cached is None:
        try:
            cached = _parse(data, checksum)
        except (RuntimeError, ValueError) as exc:
            if isinstance(exc, PDFModelError):
                raise
            raise PDFModelError(
                "The selected PDF cannot be read safely as a vector drawing."
            ) from exc
        with _CACHE_LOCK:
            _CACHE[checksum] = deepcopy(cached)
            _CACHE.move_to_end(checksum)
            while len(_CACHE) > 6:
                _CACHE.popitem(last=False)
    report = _bind(deepcopy(cached), source)
    report["source"] = {
        "id": source.id,
        "name": source.name,
        "sha256": checksum,
        "revision": source.revision,
    }
    report["provenance"] = {
        "method": "actual_pdf_vectors_and_visible_text",
        "adapter": SCHEMA_VERSION,
        "source_hash_verified": True,
        "coordinates": "visible-page-points",
        "physical_units": None,
        "native_editability_established": False,
    }
    return report


def compare_pdf(baseline_source, target_source):
    """Compare observed exact-tag geometry and terminal-net relationships.

    An unobserved device/connection is never called removed when either drawing
    has incomplete coverage. Changes are observations, not engineering sign-off.
    """
    before, after = inspect_pdf(baseline_source), inspect_pdf(target_source)
    from .revision_sheets import match_sheet_indexes

    sheet_matches = match_sheet_indexes(before["pages"], after["pages"])
    page_map = {m["baseline_page"]: m["target_page"] for m in sheet_matches["matches"]}
    changes = []
    issues = []
    left = defaultdict(list)
    right = defaultdict(list)
    for device in before["devices"]:
        left[device["tag"]].append(device)
    for device in after["devices"]:
        right[device["tag"]].append(device)
    for tag in sorted(left.keys() | right.keys()):
        old, new = left[tag], right[tag]
        if len(old) > 1 or len(new) > 1:
            issues.append(
                _issue(
                    "ambiguous_device_match",
                    "Repeated exact tags cannot be matched across revisions without a reviewed multi-unit mapping.",
                    tag=tag,
                )
            )
            continue
        if not old or not new:
            changes.append(
                {
                    "kind": "not_observed_in_baseline"
                    if not old
                    else "not_observed_in_target",
                    "device_tag": tag,
                    "status": "unknown",
                    "before": old[0] if old else None,
                    "after": new[0] if new else None,
                    "reason": "Observation coverage cannot prove equipment addition or removal.",
                }
            )
            continue
        a, b = old[0], new[0]
        if page_map.get(a["page"], a["page"]) != b["page"] or a["bbox"] != b["bbox"]:
            changes.append(
                {
                    "kind": "schematic_geometry",
                    "device_tag": tag,
                    "status": "observed_change",
                    "before": {"page": a["page"], "bbox": a["bbox"]},
                    "after": {"page": b["page"], "bbox": b["bbox"]},
                    "reason": "The tagged symbol moved or changed its schematic page extent; no physical size is inferred.",
                    "evidence": [*a["evidence"], *b["evidence"]],
                }
            )

    def nets(report):
        known = {
            p["id"]
            for d in report["devices"]
            if d["symbol"]["status"]
            not in {"unknown_multiunit_identity", "unknown_no_vector_contacts"}
            for p in d["ports"]
            if p["label"]
        }
        return {
            tuple(sorted(c["terminal_ids"])): c
            for c in report["connections"]
            if len(c["terminal_ids"]) >= 2 and set(c["terminal_ids"]) <= known
        }

    old_nets, new_nets = nets(before), nets(after)
    for terminals in sorted(old_nets.keys() | new_nets.keys()):
        if terminals in old_nets and terminals in new_nets:
            a, b = old_nets[terminals], new_nets[terminals]
            if [s["points"] for s in a["segments"]] != [
                s["points"] for s in b["segments"]
            ]:
                changes.append(
                    {
                        "kind": "conductor_geometry",
                        "terminal_ids": list(terminals),
                        "status": "observed_change",
                        "before": a,
                        "after": b,
                    }
                )
        else:
            changes.append(
                {
                    "kind": "connectivity_observation_changed",
                    "terminal_ids": list(terminals),
                    "status": "unknown"
                    if before["coverage"]["status"] == "unknown"
                    or after["coverage"]["status"] == "unknown"
                    else "observed_change",
                    "before": old_nets.get(terminals),
                    "after": new_nets.get(terminals),
                    "reason": "Exact labelled-terminal connectivity differs in the observed vector graph; incomplete coverage prevents claiming a complete circuit change.",
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "baseline": before,
        "target": after,
        "sheet_matches": sheet_matches,
        "changes": changes,
        "issues": issues,
        "summary": {
            "observed_geometry_changes": sum(
                c["kind"] in {"schematic_geometry", "conductor_geometry"}
                for c in changes
            ),
            "observed_connectivity_changes": sum(
                c["kind"] == "connectivity_observation_changed" for c in changes
            ),
            "unknown_changes": sum(c["status"] == "unknown" for c in changes),
            "complete_revision_verified": False,
        },
        "engineering_approved": False,
        "production_released": False,
    }
