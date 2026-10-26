"""Bounded planar curve sampling with explicit geometric error, not connectivity.

Sampling never establishes terminal incidence or a safe physical clearance. The
analytic native parameters and source handles remain in the CAD snapshot.
"""

from __future__ import annotations

import math
from html import escape
from itertools import pairwise


class GeometryBudgetError(ValueError):
    pass


def _arc(center, radius, start, sweep, budget):
    segments = max(1, math.ceil(abs(sweep) / math.radians(5)))
    if segments + 1 > budget:
        raise GeometryBudgetError("Curve point budget exceeded")
    step = sweep / segments
    points = [
        (
            center[0] + radius * math.cos(start + step * i),
            center[1] + radius * math.sin(start + step * i),
        )
        for i in range(segments + 1)
    ]
    error = radius * (1 - math.cos(abs(step) / 2))
    return points, error


def curve_points(item, max_points=4096):
    """Return native XY points and conservative chord-deviation in CAD units."""
    kind = item["kind"]
    if kind in {"ARC", "CIRCLE"}:
        radius = item["radius"]
        if not math.isfinite(radius) or radius <= 0:
            raise ValueError("A curve requires a positive finite radius")
        start = math.radians(item.get("start_angle", 0))
        sweep = (
            2 * math.pi
            if kind == "CIRCLE"
            else math.radians((item["end_angle"] - item["start_angle"]) % 360)
        )
        return _arc((item["x"], item["y"]), radius, start, sweep, max_points)
    if kind != "LWPOLYLINE":
        raise ValueError("Unsupported planar curve")
    vertices = item["vertices"]
    if len(vertices) < 2:
        raise ValueError("A polyline requires at least two vertices")
    points = [(vertices[0]["x"], vertices[0]["y"])]
    maximum_error = 0.0
    pairs = list(pairwise(vertices))
    if item["closed"]:
        pairs.append((vertices[-1], vertices[0]))
    for a, b in pairs:
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        chord = math.hypot(dx, dy)
        bulge = a.get("bulge", 0.0)
        if bulge and chord:
            factor = (1 - bulge * bulge) / (4 * bulge)
            center = (
                (a["x"] + b["x"]) / 2 - dy * factor,
                (a["y"] + b["y"]) / 2 + dx * factor,
            )
            radius = math.hypot(a["x"] - center[0], a["y"] - center[1])
            arc, error = _arc(
                center,
                radius,
                math.atan2(a["y"] - center[1], a["x"] - center[0]),
                4 * math.atan(bulge),
                max_points - len(points) + 1,
            )
            points.extend(arc[1:])
            points[-1] = (b["x"], b["y"])  # exact native endpoint, no rounding drift
            maximum_error = max(maximum_error, error)
        else:
            points.append((b["x"], b["y"]))
        if len(points) > max_points:
            raise GeometryBudgetError("Curve point budget exceeded")
    if any(not math.isfinite(v) for p in points for v in p):
        raise ValueError("A curve produced a non-finite point")
    return points, maximum_error


def transform_curve(item, matrix, max_points=4096):
    points, error = curve_points(item, max_points)
    a, b, c, d, x, y = matrix
    placed = [[a * px + b * py + x, c * px + d * py + y] for px, py in points]
    error *= math.sqrt(a * a + b * b + c * c + d * d)
    if any(not math.isfinite(v) for p in placed for v in p) or not math.isfinite(error):
        raise ValueError("Curve transform produced non-finite geometry")
    bbox = [
        min(p[0] for p in placed) - error,
        min(p[1] for p in placed) - error,
        max(p[0] for p in placed) + error,
        max(p[1] for p in placed) + error,
    ]
    return {
        "points": placed,
        "maximum_chord_error": error,
        "bbox_with_chord_error": bbox,
        "closed": item["kind"] == "CIRCLE" or item.get("closed", False),
    }


def _clip_segment(a, b, box):
    """Clip geometry before SVG output; some reference renderers ignore clipPath."""
    low, high = 0.0, 1.0
    for axis in (0, 1):
        delta = b[axis] - a[axis]
        if abs(delta) < 1e-12:
            if not box[axis] <= a[axis] <= box[axis + 2]:
                return None
            continue
        u, v = sorted(
            ((box[axis] - a[axis]) / delta, (box[axis + 2] - a[axis]) / delta)
        )
        low, high = max(low, u), min(high, v)
        if low > high:
            return None
    return tuple(
        tuple(min(box[i + 2], max(box[i], a[i] + t * (b[i] - a[i]))) for i in (0, 1))
        for t in (low, high)
    )


def render_region(snapshot, bbox, *, width=1600, height=1200, highlight_handles=()):
    """Render actual located geometry for review, with omitted types explicit.

    This is a reference view, not a replacement for the native CAD renderer.
    Text uses a fallback font and curves carry their measured sampling bound.
    """
    x0, y0, x1, y1 = bbox
    if not all(math.isfinite(v) for v in bbox) or x1 <= x0 or y1 <= y0:
        raise ValueError("An explicit finite nonempty CAD region is required")
    if not (200 <= width <= 4000 and 200 <= height <= 4000):
        raise ValueError("CAD review canvas is outside the supported size")
    scale = min((width - 40) / (x1 - x0), (height - 75) / (y1 - y0))

    def point(p):
        return (20 + (p[0] - x0) * scale, 55 + (y1 - p[1]) * scale)

    def intersects(b):
        return b[0] <= x1 and b[2] >= x0 and b[1] <= y1 and b[3] >= y0

    def color(handle):
        return "#7557a2" if handle in highlight_handles else "#20252a"

    content = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        '<text x="20" y="24" font-family="Arial,sans-serif" font-size="13" fill="#555">Native CAD reference · sampled curves, lines and located text · fallback fonts · omitted CAD types remain unverified</text>',
        f'<defs><clipPath id="region"><rect x="20" y="45" width="{width - 40}" height="{height - 60}"/></clipPath></defs><g clip-path="url(#region)">',
    ]
    counts = {"lines": 0, "paths": 0, "text": 0, "invisible_skipped": 0}
    for line in snapshot["placed_lines"]:
        if line.get("invisible"):
            counts["invisible_skipped"] += 1
            continue
        a, b = line["start"], line["end"]
        if not intersects(
            [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]
        ):
            continue
        clipped = _clip_segment(a, b, bbox)
        if clipped is None:
            continue
        ax, ay = point(clipped[0])
        bx, by = point(clipped[1])
        content.append(
            f'<path d="M{ax:.3f},{ay:.3f}L{bx:.3f},{by:.3f}" fill="none" stroke="{color(line["handle"])}" stroke-width=".5"/>'
        )
        counts["lines"] += 1
    for path in snapshot.get("placed_paths", []):
        if path.get("invisible"):
            counts["invisible_skipped"] += 1
            continue
        if not intersects(path["bbox_with_chord_error"]):
            continue
        if all(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in path["points"]):
            points = " ".join(f"{a:.3f},{b:.3f}" for a, b in map(point, path["points"]))
            content.append(
                f'<polyline points="{points}" fill="none" stroke="{color(path["handle"])}" stroke-width=".5"/>'
            )
            counts["paths"] += 1
            continue
        segments = []
        for a, b in pairwise(path["points"]):
            clipped = _clip_segment(a, b, bbox)
            if clipped is not None:
                (ax, ay), (bx, by) = map(point, clipped)
                segments.append(f"M{ax:.3f},{ay:.3f}L{bx:.3f},{by:.3f}")
        content.append(
            f'<path d="{" ".join(segments)}" fill="none" stroke="{color(path["handle"])}" stroke-width=".5"/>'
        )
        counts["paths"] += 1
    from ezdxf.tools.text import plain_mtext

    for item in snapshot["placed_text"]:
        if item.get("invisible"):
            counts["invisible_skipped"] += 1
            continue
        if not (x0 <= item["x"] <= x1 and y0 <= item["y"] <= y1):
            continue
        label = plain_mtext(item["text"]).replace("^J", " ")
        h = item.get("text_height") or (x1 - x0) / 500
        # The reference uses the source text size. An unreadably small source
        # is not enlarged into an overlapping label at a fabricated location.
        x, y = point((item["x"], item["y"]))
        font = h * scale
        attachment = item.get("text_attachment", 7)
        anchor = {1: "start", 2: "middle", 0: "end"}[attachment % 3]
        baseline = (
            "hanging" if attachment <= 3 else "central" if attachment <= 6 else "auto"
        )
        for i, row in enumerate(label.splitlines()[:50]):
            if not row or len(row) > 500:
                continue
            content.append(
                f'<text x="{x:.3f}" y="{y + i * font * 1.2:.3f}" transform="rotate({-item.get("text_rotation", 0):.3f} {x:.3f} {y:.3f})" text-anchor="{anchor}" dominant-baseline="{baseline}" font-family="Arial,sans-serif" font-size="{font:.3f}" fill="{color(item["handle"])}">{escape(row)}</text>'
            )
        counts["text"] += 1
    content.append("</g></svg>")
    svg = "".join(content)
    if len(svg.encode()) > 12_000_000:
        raise GeometryBudgetError("CAD reference render exceeds its output budget")
    return svg, counts
