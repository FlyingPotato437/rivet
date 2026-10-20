"""Source-supported native device/routing changes with saved-file verification."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
from copy import deepcopy
from itertools import pairwise
from pathlib import Path

from ezdxf.math import Matrix44

from .native_model import (
    EPSILON,
    _intersection,
    _vertices,
    close,
    inspect_document,
    inspect_native,
    load_native,
    on_segment,
    set_xdata,
    xdata,
)

KINDS = {
    "move_device",
    "resize_device",
    "rewire",
    "reroute",
    "add_device",
    "remove_device",
    "add_wire",
    "remove_wire",
}


MAX_ROUTING_GRID_NODES = 60000
MAX_ROUTING_EXPANSIONS = 40000
MAX_ROUTING_WORK = 4000000


class NativeEditError(ValueError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _xy(point):
    if (
        not isinstance(point, (list, tuple))
        or len(point) != 2
        or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)
    ):
        raise NativeEditError(
            "Native points require two finite source-specified coordinates."
        )
    return [float(point[0]), float(point[1])]


def _evidence(request):
    evidence = request.get("evidence", {})
    text = evidence.get("text", "")
    if (
        not evidence.get("source_id")
        or not re.fullmatch(r"[a-fA-F0-9]{64}", evidence.get("sha256", ""))
        or not isinstance(evidence.get("page"), int)
        or evidence["page"] < 1
        or not isinstance(text, str)
        or not text.strip()
    ):
        raise NativeEditError(
            "Every native change requires a hash-bound source request and page/text anchor."
        )
    kind = request.get("kind")
    action = {
        "move_device": r"move|relocate",
        "resize_device": r"resize",
        "rewire": r"rewire",
        "reroute": r"reroute",
        "add_device": r"add",
        "remove_device": r"remove|delete",
        "add_wire": r"add",
        "remove_wire": r"remove|delete",
    }.get(kind)
    if not action or not re.search(rf"\b(?:{action})\b", text, re.IGNORECASE):
        raise NativeEditError(
            "The supplied source text does not contain this native action."
        )
    values = [
        request.get("original_selectors", {}).get(key, request.get(key))
        for key in (
            "device_tag",
            "wire_id",
            "block_name",
            "from_terminal",
            "to_terminal",
            "net_id",
        )
        if request.get(key)
    ]
    for value in values:
        if (
            not isinstance(value, str)
            or len(value) > 120
            or str(value).casefold() not in text.casefold()
        ):
            raise NativeEditError(
                "A native identity or terminal is not explicitly present in the source request."
            )
    numeric = []
    for field in ("to", "at"):
        if field in request:
            numeric.extend(_xy(request[field]))
    for field in ("width", "height"):
        if field in request:
            numeric.append(float(request[field]))
    for point in request.get("points", []):
        numeric.extend(_xy(point))
    source_numbers = [
        float(value) for value in re.findall(r"(?<![A-Za-z0-9_-])-?\d+(?:\.\d+)?", text)
    ]
    if any(
        not any(abs(number - found) <= EPSILON for found in source_numbers)
        for number in numeric
    ):
        raise NativeEditError(
            "A requested coordinate/dimension is not stated in the source text."
        )
    for key, value in request.get("attributes", {}).items():
        if key != "EQUIPMENT_TAG" and (
            str(key).casefold() not in text.casefold()
            or str(value).casefold() not in text.casefold()
        ):
            raise NativeEditError(
                "An added device attribute must be explicitly specified, not copied or invented."
            )


def _device(document, graph, tag):
    matches = [device for device in graph["devices"] if device["tag"] == tag]
    if len(matches) != 1:
        raise NativeEditError("Choose one exact, existing native equipment tag.")
    return document.entitydb[matches[0]["handle"]], matches[0]


def _wire(document, graph, identity):
    matches = [wire for wire in graph["wires"] if wire["id"] == identity]
    if len(matches) != 1:
        raise NativeEditError("Choose one exact, existing native wire identity.")
    return document.entitydb[matches[0]["handle"]], matches[0]


def _terminal(graph, identity):
    found = [
        terminal
        for device in graph["devices"]
        for terminal in device["terminals"]
        if terminal["id"] == identity
    ]
    if len(found) != 1:
        raise NativeEditError(
            "The requested terminal does not exist unambiguously in the actual native block."
        )
    return found[0]["position"]


def _simplify(points):
    result = []
    for point in points:
        point = _xy(point)
        if result and close(result[-1], point):
            continue
        if len(result) > 1:
            a, b = result[-2:]
            collinear = (
                abs(a[0] - b[0]) <= EPSILON and abs(b[0] - point[0]) <= EPSILON
            ) or (abs(a[1] - b[1]) <= EPSILON and abs(b[1] - point[1]) <= EPSILON)
            # A reversal is actual retracing, not a redundant vertex.
            if collinear and on_segment(b, a, point):
                result[-1] = point
                continue
        result.append(point)
    return result


def _segment_clear(a, b, graph, wire_id, allowed_nodes, other_wires=None, budget=None):
    if budget is not None:
        budget["remaining"] -= budget["unit_cost"]
        if budget["remaining"] < 0:
            raise NativeEditError(
                "The native routing complexity budget is exhausted; use a narrower source-backed route request."
            )
    if close(a, b) or not (abs(a[0] - b[0]) <= EPSILON or abs(a[1] - b[1]) <= EPSILON):
        return False
    if not any(
        all(
            e["bounds"][0] - EPSILON <= p[0] <= e["bounds"][2] + EPSILON
            and e["bounds"][1] - EPSILON <= p[1] <= e["bounds"][3] + EPSILON
            for p in (a, b)
        )
        for e in graph["enclosures"]
    ):
        return False
    for device in graph["devices"]:
        x0, y0, x1, y1 = device["bounds"]
        if (
            abs(a[1] - b[1]) <= EPSILON
            and y0 + EPSILON < a[1] < y1 - EPSILON
            and min(max(a[0], b[0]), x1) - max(min(a[0], b[0]), x0) > EPSILON
        ) or (
            abs(a[0] - b[0]) <= EPSILON
            and x0 + EPSILON < a[0] < x1 - EPSILON
            and min(max(a[1], b[1]), y1) - max(min(a[1], b[1]), y0) > EPSILON
        ):
            return False
    for node in [
        *[t for d in graph["devices"] for t in d["terminals"]],
        *graph["junctions"],
    ]:
        if node["id"] not in allowed_nodes and on_segment(node["position"], a, b):
            return False
    for wire in other_wires if other_wires is not None else graph["wires"]:
        if wire["id"] != wire_id and any(
            _intersection(a, b, c, d)[0] == "overlap"
            for c, d in pairwise(wire["points"])
        ):
            return False
    return True


def _route_clear(points, graph, wire_id, allowed_nodes, other_wires=None, budget=None):
    segments = list(pairwise(points))
    if any(
        _intersection(a, b, c, d)[0] == "overlap"
        or j > i + 1
        and _intersection(a, b, c, d)[0] == "point"
        for i, (a, b) in enumerate(segments)
        for j, (c, d) in enumerate(segments[i + 1 :], i + 1)
    ):
        return False
    return len(points) >= 2 and all(
        _segment_clear(a, b, graph, wire_id, allowed_nodes, other_wires, budget)
        for a, b in pairwise(points)
    )


def _orthogonal_route(
    graph,
    wire_id,
    start,
    stop,
    allowed_nodes,
    candidate=None,
    other_wires=None,
    budget=None,
):
    """Bounded visibility-grid router; it changes paths, never electrical identities.

    Device bodies and unrelated terminals/junctions are hard obstacles. Existing
    conductors permit ordinary crossings but not collinear overlap. The search
    never fabricates a terminal or guesses an electrical relationship.
    """
    budget = budget if budget is not None else {"remaining": MAX_ROUTING_WORK}
    budget["unit_cost"] = (
        len(graph["devices"])
        + len(graph["junctions"])
        + sum(len(d["terminals"]) for d in graph["devices"])
        + sum(
            max(0, len(w["points"]) - 1)
            for w in (other_wires if other_wires is not None else graph["wires"])
        )
    )
    if candidate and _route_clear(
        candidate, graph, wire_id, allowed_nodes, other_wires, budget
    ):
        return _simplify(candidate)
    nodes = [
        *[t for d in graph["devices"] for t in d["terminals"]],
        *graph["junctions"],
    ]
    positions = [start, stop, *[n["position"] for n in nodes]]
    positions += [p for w in graph["wires"] for p in w["points"]]
    positions += [
        [b[i], b[j]]
        for b in [d["bounds"] for d in graph["devices"]]
        for i in (0, 2)
        for j in (1, 3)
    ]
    positions += [p for e in graph["enclosures"] for p in e["points"]]
    # One millimetre routing offsets are an adapter routing resolution, not an
    # asserted insulation/creepage clearance or protection design criterion.
    xs = sorted({round(p[0] + offset, 6) for p in positions for offset in (-1, 0, 1)})
    ys = sorted({round(p[1] + offset, 6) for p in positions for offset in (-1, 0, 1)})
    if len(xs) * len(ys) > MAX_ROUTING_GRID_NODES:
        raise NativeEditError(
            "The constrained routing grid exceeds this adapter's bound."
        )
    origin = (xs.index(start[0]), ys.index(start[1]), 0)
    target = (xs.index(stop[0]), ys.index(stop[1]))
    distances, prior, cache = {origin: 0}, {}, {}
    queue = [(math.dist(start, stop), 0, origin)]
    found = None
    expansions = 0
    while queue:
        _, cost, current = heapq.heappop(queue)
        if cost != distances.get(current):
            continue
        expansions += 1
        if (
            expansions > MAX_ROUTING_EXPANSIONS
            or len(distances) > MAX_ROUTING_EXPANSIONS * 3
        ):
            raise NativeEditError(
                "The bounded native routing search is too complex; supply an explicit verified route."
            )
        x, y, direction = current
        if (x, y) == target:
            found = current
            break
        for nx, ny, heading in (
            (x - 1, y, 1),
            (x + 1, y, 1),
            (x, y - 1, 2),
            (x, y + 1, 2),
        ):
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            a, b = [xs[x], ys[y]], [xs[nx], ys[ny]]
            key = tuple(sorted(((x, y), (nx, ny))))
            if key not in cache:
                cache[key] = _segment_clear(
                    a, b, graph, wire_id, allowed_nodes, other_wires, budget
                )
            if not cache[key]:
                continue
            step = (nx, ny, heading)
            distance = (
                cost
                + math.dist(a, b)
                + (8 if direction and direction != heading else 0)
            )
            if distance < distances.get(step, float("inf")):
                distances[step], prior[step] = distance, current
                heuristic = abs(xs[nx] - stop[0]) + abs(ys[ny] - stop[1])
                heapq.heappush(queue, (distance + heuristic, distance, step))
    if found is None:
        raise NativeEditError(
            "No bounded orthogonal route avoids bodies, unrequested terminals and conductor overlaps."
        )
    route = []
    while True:
        route.append([xs[found[0]], ys[found[1]]])
        if found == origin:
            break
        found = prior[found]
    route.reverse()
    return _simplify(route)


def _shift_endpoint(points, point, first=True):
    oriented = deepcopy(points if first else list(reversed(points)))
    old, neighbor = oriented[:2]
    bend = (
        [neighbor[0], point[1]]
        if abs(old[1] - neighbor[1]) <= EPSILON
        else [point[0], neighbor[1]]
    )
    shifted = _simplify([point, bend, *oriented[1:]])
    return shifted if first else list(reversed(shifted))


def _set_wire(document, entity, points, metadata):
    points = _simplify(points)
    if len(points) < 2:
        raise NativeEditError("A conductor must retain two distinct endpoints.")
    previous = _vertices(entity)
    for label in _linked_labels(document, "wire_label", metadata[1]):
        old = list(label.dxf.insert)[:2]
        fraction, offset = _path_anchor(previous, old)
        new = _path_position(points, fraction)
        label.translate(new[0] + offset[0] - old[0], new[1] + offset[1] - old[1], 0)
    if entity.dxftype() == "LWPOLYLINE":
        entity.set_points(points)
    elif len(points) == 2:
        entity.dxf.start, entity.dxf.end = (*points[0], 0), (*points[1], 0)
    else:
        attrs = {
            key: entity.dxf.get(key)
            for key in ("layer", "color", "linetype", "lineweight", "ltscale")
            if entity.dxf.hasattr(key)
        }
        extras = {
            app.dxf.name: list(entity.get_xdata(app.dxf.name))
            for app in document.appids
            if entity.has_xdata(app.dxf.name)
        }
        document.modelspace().delete_entity(entity)
        entity = document.modelspace().add_lwpolyline(points, dxfattribs=attrs)
        for app, values in extras.items():
            entity.set_xdata(app, values)
    set_xdata(entity, metadata)
    return entity


def _linked_labels(document, kind, owner):
    return [e for e in document.modelspace() if xdata(e) == [kind, owner]]


def _path_position(points, fraction):
    lengths = [math.dist(a, b) for a, b in pairwise(points)]
    target = sum(lengths) * fraction
    for (a, b), length in zip(pairwise(points), lengths, strict=True):
        if target <= length and length:
            return [a[i] + (b[i] - a[i]) * target / length for i in (0, 1)]
        target -= length
    return points[-1]


def _path_anchor(points, position):
    lengths = [math.dist(a, b) for a, b in pairwise(points)]
    traversed, candidates = 0, []
    for (a, b), length in zip(pairwise(points), lengths, strict=True):
        if not length:
            continue
        ratio = max(
            0,
            min(
                1, sum((position[i] - a[i]) * (b[i] - a[i]) for i in (0, 1)) / length**2
            ),
        )
        closest = [a[i] + ratio * (b[i] - a[i]) for i in (0, 1)]
        candidates.append(
            (
                math.dist(position, closest),
                (traversed + length * ratio) / sum(lengths),
                [position[i] - closest[i] for i in (0, 1)],
            )
        )
        traversed += length
    if not candidates:
        raise NativeEditError("A linked wire label has no actual path to follow.")
    _, fraction, offset = min(candidates)
    return fraction, offset


def _move_attached(document, before, after, tag, budget):
    affected = {
        w["id"]
        for w in before["wires"]
        if w["from"].split(":", 1)[0] == tag or w["to"].split(":", 1)[0] == tag
    }
    routed = [w for w in after["wires"] if w["id"] not in affected]
    for wire in before["wires"]:
        points = wire["points"]
        changed = False
        for first, key in ((True, "from"), (False, "to")):
            if wire[key].split(":", 1)[0] == tag:
                points = _shift_endpoint(points, _terminal(after, wire[key]), first)
                changed = True
        if changed:
            entity = document.entitydb.get(wire["handle"])
            allowed = set(wire.get("terminal_ids", [wire["from"], wire["to"]]))
            points = _orthogonal_route(
                after,
                wire["id"],
                _terminal(after, wire["from"]),
                _terminal(after, wire["to"]),
                allowed,
                points,
                routed,
                budget,
            )
            _set_wire(document, entity, points, xdata(entity))
            routed.append({**wire, "points": points})


def _apply(document, requests):
    budget = {"remaining": MAX_ROUTING_WORK}
    priorities = {
        "add_device": 0,
        "resize_device": 1,
        "move_device": 2,
        "rewire": 3,
        "reroute": 4,
        "add_wire": 5,
        "remove_wire": 6,
        "remove_device": 7,
    }
    for request in sorted(requests, key=lambda request: priorities[request["kind"]]):
        graph = inspect_document(document)
        kind = request["kind"]
        if kind in {"move_device", "resize_device"}:
            entity, device = _device(document, graph, request["device_tag"])
            if kind == "move_device":
                point = _xy(request["to"])
                dx, dy = (
                    point[0] - device["position"][0],
                    point[1] - device["position"][1],
                )
                entity.translate(dx, dy, 0)
                for label in _linked_labels(document, "device_label", device["tag"]):
                    label.translate(dx, dy, 0)
            else:
                width, height = float(request["width"]), float(request["height"])
                if width <= 0 or height <= 0 or abs(device["rotation"] % 90) > EPSILON:
                    raise NativeEditError(
                        "Resize requires positive dimensions and a right-angle native block rotation."
                    )
                sx, sy = width / device["width"], height / device["height"]
                x, y = device["position"]
                transform = Matrix44.chain(
                    Matrix44.translate(-x, -y, 0),
                    Matrix44.scale(sx, sy, 1),
                    Matrix44.translate(x, y, 0),
                )
                entity.transform(transform)
                for label in _linked_labels(document, "device_label", device["tag"]):
                    label.transform(transform)
            _move_attached(
                document, graph, inspect_document(document), device["tag"], budget
            )
        elif kind in {"rewire", "reroute"}:
            entity, wire = _wire(document, graph, request["wire_id"])
            origin, end = (
                (request["from_terminal"], request["to_terminal"])
                if kind == "rewire"
                else (wire["from"], wire["to"])
            )
            start, stop = _terminal(graph, origin), _terminal(graph, end)
            points = request.get("points")
            if points is None:
                points = _shift_endpoint(
                    _shift_endpoint(wire["points"], start), stop, False
                )
                points = _orthogonal_route(
                    graph,
                    wire["id"],
                    start,
                    stop,
                    {origin, end},
                    points,
                    budget=budget,
                )
            points = [_xy(point) for point in points]
            if not close(points[0], start) or not close(points[-1], stop):
                raise NativeEditError(
                    "A requested route must end at the exact actual terminal coordinates."
                )
            a, b = origin.split(":", 1), end.split(":", 1)
            _set_wire(
                document, entity, points, ["wire", wire["id"], *a, *b, wire["net_id"]]
            )
        elif kind == "add_device":
            tag, block_name = request["device_tag"], request["block_name"]
            if (
                any(device["tag"] == tag for device in graph["devices"])
                or block_name not in document.blocks
            ):
                raise NativeEditError(
                    "An added device needs a new exact tag and an existing controlled native block template."
                )
            block = document.blocks.get(block_name)
            if block.block.is_xref or block.block.is_xref_overlay:
                raise NativeEditError(
                    "External device templates are not available to this adapter."
                )
            attrs = {
                str(key): str(value)
                for key, value in request.get("attributes", {}).items()
            }
            if (
                not attrs.get("PART_NUMBER")
                or not attrs.get("QUANTITY")
                or attrs.get("EQUIPMENT_TAG", tag) != tag
            ):
                raise NativeEditError(
                    "Added devices require an explicit part number, quantity and consistent equipment identity."
                )
            point = _xy(request["at"])
            entity = document.modelspace().add_blockref(block_name, point)
            attrs["EQUIPMENT_TAG"] = tag
            for index, (key, value) in enumerate(attrs.items()):
                attribute = entity.add_attrib(
                    key,
                    value,
                    (point[0], point[1] - 3 - index * 3),
                    dxfattribs={"height": 2},
                )
                attribute.dxf.owner = entity.dxf.handle
            entity.dxf.attribs_follow = 1
            set_xdata(entity, ["device", tag, "source-specified", "", ""])
        elif kind == "remove_device":
            entity, device = _device(document, graph, request["device_tag"])
            if any(
                terminal.startswith(device["tag"] + ":")
                for wire in graph["wires"]
                for terminal in wire.get("terminal_ids", [])
            ):
                raise NativeEditError(
                    "Explicitly rewire or remove every attached conductor before removing a device."
                )
            document.modelspace().delete_entity(entity)
            for label in _linked_labels(document, "device_label", device["tag"]):
                document.modelspace().delete_entity(label)
        elif kind == "add_wire":
            if any(wire["id"] == request["wire_id"] for wire in graph["wires"]):
                raise NativeEditError("The requested new wire identity already exists.")
            start, end = (
                _terminal(graph, request["from_terminal"]),
                _terminal(graph, request["to_terminal"]),
            )
            points = request.get("points")
            if points is None:
                points = _orthogonal_route(
                    graph,
                    request["wire_id"],
                    start,
                    end,
                    {request["from_terminal"], request["to_terminal"]},
                    [start, [end[0], start[1]], end],
                    budget=budget,
                )
            points = _simplify(points)
            if (
                not close(points[0], start)
                or not close(points[-1], end)
                or not request.get("net_id")
            ):
                raise NativeEditError(
                    "A new conductor requires a declared net and exact actual terminal endpoints."
                )
            entity = document.modelspace().add_lwpolyline(
                points, dxfattribs={"layer": "WIRING"}
            )
            a, b = (
                request["from_terminal"].split(":", 1),
                request["to_terminal"].split(":", 1),
            )
            set_xdata(entity, ["wire", request["wire_id"], *a, *b, request["net_id"]])
            midpoint = _path_position(points, 0.5)
            label = (
                document.modelspace()
                .add_text(
                    request["wire_id"], dxfattribs={"height": 2, "layer": "WIRING"}
                )
                .set_placement((midpoint[0], midpoint[1] + 3))
            )
            set_xdata(label, ["wire_label", request["wire_id"]])
        elif kind == "remove_wire":
            entity, _ = _wire(document, graph, request["wire_id"])
            document.modelspace().delete_entity(entity)
            for label in _linked_labels(document, "wire_label", request["wire_id"]):
                document.modelspace().delete_entity(label)
    return document


def topology_diff(before, after, requests=None):
    changes = []
    for collection, identity, object_kind in (
        ("devices", "tag", "device"),
        ("wires", "id", "wire"),
    ):
        old = {item[identity]: item for item in before[collection]}
        new = {item[identity]: item for item in after[collection]}
        for key in sorted(old.keys() | new.keys()):
            left, right = old.get(key), new.get(key)
            if left is None or right is None:
                kind = ("add_" if left is None else "remove_") + object_kind
            elif object_kind == "device" and any(
                left[field] != right[field]
                for field in (
                    "position",
                    "rotation",
                    "bounds",
                    "terminals",
                    "attributes",
                )
            ):
                kind = "device_geometry"
            elif object_kind == "wire" and any(
                left.get(field) != right.get(field)
                for field in ("from", "to", "points", "terminal_ids")
            ):
                kind = (
                    "rewire"
                    if (left["from"], left["to"]) != (right["from"], right["to"])
                    else "reroute"
                )
            else:
                continue
            evidence = [
                request["id"]
                for request in requests or []
                if request.get("device_tag") == key or request.get("wire_id") == key
            ]
            changes.append(
                {
                    "kind": kind,
                    "device_tag" if object_kind == "device" else "wire_id": key,
                    "before": left,
                    "after": right,
                    "reason": "Actual native entity/topology difference",
                    "request_ids": evidence,
                }
            )
    return {"changes": changes}


def plan_native_changes(
    drawing,
    requests,
    expected_sha256=None,
    *,
    identity_profile=None,
    identity_source_catalog=None,
):
    document, checksum = load_native(drawing, expected_sha256)
    before = inspect_document(document, checksum)
    checks = []
    requests = deepcopy(requests)
    try:
        if not isinstance(requests, list) or not requests or len(requests) > 100:
            raise NativeEditError("Provide one to 100 source-backed native requests.")
        if identity_profile is not None or any(
            r.get("original_selectors") for r in requests
        ):
            from .identity import IdentityError, resolve_native_requests

            try:
                requests = resolve_native_requests(
                    requests,
                    [d["tag"] for d in before["devices"]],
                    identity_profile,
                    identity_source_catalog,
                )
            except IdentityError as exc:
                raise NativeEditError(str(exc)) from exc
        identities = []
        for request in requests:
            if request.get("kind") not in KINDS or not request.get("id"):
                raise NativeEditError(
                    "Every native request requires a supported kind and stable source request ID."
                )
            _evidence(request)
            identities.append(
                (request["kind"], request.get("device_tag") or request.get("wire_id"))
            )
        if len(identities) != len(set(identities)) or len(
            {r["id"] for r in requests}
        ) != len(requests):
            raise NativeEditError(
                "Conflicting or duplicate native requests require a human decision."
            )
        _apply(document, requests)
        after = inspect_document(document)
        checks = deepcopy(after["checks"])
        # Moving/resizing a device may change attached path geometry, but must
        # retain its electrical relationships unless another request changes it.
        explicit_wires = {
            r.get("wire_id") for r in requests if r["kind"] in {"rewire", "remove_wire"}
        }
        old_wires = {w["id"]: w for w in before["wires"]}
        for wire in after["wires"]:
            explicit = next(
                (
                    r
                    for r in requests
                    if r.get("wire_id") == wire["id"]
                    and r["kind"] in {"rewire", "add_wire"}
                ),
                None,
            )
            if explicit and set(wire.get("terminal_ids", [])) != {
                explicit["from_terminal"],
                explicit["to_terminal"],
            }:
                checks.append(
                    {
                        "id": f"incidental_connection:{wire['id']}",
                        "status": "fail",
                        "label": "Unrequested terminal connection",
                        "reason": "The changed conductor touches a terminal or junction beyond its two explicitly requested endpoints.",
                        "wire_id": wire["id"],
                    }
                )
            if (
                wire["id"] in old_wires
                and wire["id"] not in explicit_wires
                and old_wires[wire["id"]].get("terminal_ids")
                != wire.get("terminal_ids")
            ):
                checks.append(
                    {
                        "id": f"incidence_drift:{wire['id']}",
                        "status": "fail",
                        "label": "Unrequested topology change",
                        "reason": "Moving/rerouting must preserve all existing terminal incidence unless rewiring is explicit.",
                        "wire_id": wire["id"],
                    }
                )
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        after = inspect_document(document)
        checks = [
            *after["checks"],
            {
                "id": "request_blocked",
                "status": "fail",
                "label": "Native request blocked",
                "reason": str(exc),
            },
        ]
    status = (
        "ready"
        if checks and all(item["status"] == "pass" for item in checks)
        else "blocked"
    )
    plan = {
        "schema_version": "1",
        "source_sha256": checksum,
        "status": status,
        "requests": requests,
        "before": before,
        "after": after,
        "checks": checks,
        "diff": topology_diff(before, after, requests),
        "engineering_approved": False,
        "production_released": False,
    }
    plan["plan_id"] = _digest(
        {
            "source": checksum,
            "geometry": before["geometry_fingerprint"],
            "requests": requests,
        }
    )
    return plan


def _attribute_policy(expected, actual, allowed):
    expected, actual = deepcopy(expected), deepcopy(actual)
    for change in allowed:
        tag = change.get("device_tag") or change.get("equipment_tag")
        attribute = change.get("attribute") or change.get("attribute_tag")
        before = str(change.get("before", change.get("expected_value", "")))
        after = str(change.get("after", change.get("replacement_value", "")))
        if tag in expected and tag in actual:
            if (
                expected[tag]["attributes"].get(attribute) != before
                or actual[tag]["attributes"].get(attribute) != after
            ):
                raise NativeEditError(
                    "An allowed literal attribute edit does not match its exact before/after values."
                )
            expected[tag]["attributes"][attribute] = after
    return expected, actual


def verify_native_changes(before, after, plan, allowed_attribute_changes=None):
    checks = deepcopy(after["checks"])
    expected = plan["after"]
    allowed = allowed_attribute_changes or []
    try:
        if before["geometry_fingerprint"] != plan["before"]["geometry_fingerprint"]:
            raise NativeEditError(
                "The readback baseline is not the source bound to this native plan."
            )
        left_devices, right_devices = _attribute_policy(
            {d["tag"]: d for d in expected["devices"]},
            {d["tag"]: d for d in after["devices"]},
            allowed,
        )

        def device_geometry(device):
            return {
                key: device[key]
                for key in (
                    "block_name",
                    "position",
                    "rotation",
                    "bounds",
                    "attributes",
                    "body_points",
                )
            } | {
                "terminals": [
                    {key: t[key] for key in ("id", "name", "position")}
                    for t in device["terminals"]
                ]
            }

        if {tag: device_geometry(d) for tag, d in left_devices.items()} != {
            tag: device_geometry(d) for tag, d in right_devices.items()
        }:
            raise NativeEditError(
                "Saved device geometry, terminals or attributes differ from the exact source-backed native plan."
            )

        def wire_geometry(wire):
            return {
                key: wire.get(key)
                for key in (
                    "id",
                    "from",
                    "to",
                    "net_id",
                    "points",
                    "terminal_ids",
                    "actual_from",
                    "actual_to",
                )
            }

        if [wire_geometry(w) for w in expected["wires"]] != [
            wire_geometry(w) for w in after["wires"]
        ]:
            raise NativeEditError(
                "Saved wire paths or endpoint topology differ from the exact native plan."
            )
        if (
            expected["enclosures"] != after["enclosures"]
            or expected["junctions"] != after["junctions"]
            or expected["block_manifest"] != after["block_manifest"]
        ):
            raise NativeEditError(
                "An unrequested enclosure, junction or native block definition changed."
            )
        # Semantic graph checks also protect drafting presentation. A changed
        # attribute value may be authorized; moving its text or altering a
        # conductor's layer/style is not an authorized side effect.
        expected_entities = deepcopy(expected["entity_manifest"])
        for change in allowed:
            tag = change.get("device_tag") or change.get("equipment_tag")
            field = change.get("attribute") or change.get("attribute_tag")
            value = str(change.get("after", change.get("replacement_value", "")))
            device = left_devices.get(tag)
            if device:
                signature = expected_entities[device["handle"]]
                for attribute in signature.get("attributes", []):
                    if attribute["properties"].get("tag") == field:
                        attribute["properties"]["text"] = value
        for handle in (
            {d["handle"] for d in expected["devices"]}
            | {w["handle"] for w in expected["wires"]}
            | {a["handle"] for a in expected.get("annotations", [])}
        ):
            if expected_entities.get(handle) != after["entity_manifest"].get(handle):
                raise NativeEditError(
                    "Saved native drafting geometry or presentation changed outside the exact plan."
                )
        # Guard arbitrary source entities outside the declared edited objects.
        protected = set(before["entity_manifest"])
        device_handles = {d["handle"] for d in before["devices"]}
        wire_handles = {w["handle"] for w in before["wires"]}
        protected -= (
            device_handles
            | wire_handles
            | {a["handle"] for a in before.get("annotations", [])}
        )
        if any(
            before["entity_manifest"][handle] != after["entity_manifest"].get(handle)
            for handle in protected
        ):
            raise NativeEditError(
                "Unrequested source drawing entities changed or disappeared."
            )
        recognized = (
            {d["handle"] for d in after["devices"]}
            | {w["handle"] for w in after["wires"]}
            | {a["handle"] for a in after.get("annotations", [])}
        )
        if set(after["entity_manifest"]) - set(before["entity_manifest"]) - recognized:
            raise NativeEditError("Unrequested native drawing entities were added.")
        checks.append(
            {
                "id": "independent_readback",
                "status": "pass",
                "label": "Saved native readback",
                "reason": "Actual saved devices, transformed terminals, conductor routes and protected entities match the source-supported plan.",
            }
        )
    except (ValueError, KeyError, TypeError) as exc:
        checks.append(
            {
                "id": "independent_readback",
                "status": "fail",
                "label": "Saved native readback",
                "reason": str(exc),
            }
        )
    return {
        "status": "verified"
        if checks and all(item["status"] == "pass" for item in checks)
        else "blocked",
        "before": before,
        "after": after,
        "checks": checks,
        "diff": topology_diff(before, after, plan.get("requests")),
        "engineering_approved": False,
        "production_released": False,
    }


def mutate_document(document, requests):
    """Return an edited clone and its source-derived plan; caller must save/recheck."""
    plan = plan_native_changes(document, requests)
    if plan["status"] != "ready":
        raise NativeEditError(
            "Native changes are blocked by geometry/topology checks.", plan
        )
    clone, _ = load_native(document)
    return _apply(clone, plan["requests"]), plan


def apply_native_changes(
    drawing,
    plan,
    destination,
    allowed_attribute_changes=None,
    *,
    identity_profile=None,
    identity_source_catalog=None,
):
    destination = Path(destination)
    if destination.exists():
        raise NativeEditError("The native draft destination must be new.")
    current = plan_native_changes(
        drawing,
        plan["requests"],
        identity_profile=identity_profile,
        identity_source_catalog=identity_source_catalog,
    )
    if current["plan_id"] != plan["plan_id"] or current["status"] != "ready":
        raise NativeEditError(
            "The native source/request plan changed or is blocked.", current
        )
    document, _ = load_native(drawing)
    _apply(document, current["requests"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation prevents replacing either an original or another
        # prepared draft. The caller publishes only after the readback passes.
        with destination.open("x", encoding="utf-8") as output:
            document.write(output)
        actual = inspect_native(destination)
        result = verify_native_changes(
            current["before"], actual, current, allowed_attribute_changes
        )
        if result["status"] != "verified":
            raise NativeEditError(
                "The saved DXF failed independent native geometry/topology verification.",
                result,
            )
        return {
            **result,
            "plan_id": current["plan_id"],
            "source_sha256": current["source_sha256"],
            "output_sha256": actual["sha256"],
        }
    except Exception:
        destination.unlink(missing_ok=True)
        raise
