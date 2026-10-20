"""Pilot DXF adapter: actual device bodies, transformed terminals and conductors.

Convention (APPID RIVET; all XDATA values are strings):
 INSERT ['device', tag, kind, advisory_width, advisory_height]
 block POINT/CIRCLE ['terminal', name]; closed polyline ['body']
 modelspace LINE/LWPOLYLINE ['wire', id, from_tag, from_pin, to_tag, to_pin, net]
 modelspace closed rectangular polyline ['enclosure', id]
 modelspace POINT/CIRCLE ['junction', id, net]

Names identify objects; coordinates and polyline entities establish incidence.
Crossings without explicit junctions do not imply an electrical connection.
This adapter is deliberately not an arbitrary CAD or electrical-code interpreter.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

import ezdxf
from ezdxf.math import Vec3

APPID = "RIVET"
EPSILON = 0.001
MAX_BYTES = 30 * 1024 * 1024
MAX_ENTITIES = 30000


class NativeModelError(ValueError):
    pass


def xdata(entity):
    if not entity.has_xdata(APPID):
        return []
    return [str(tag.value) for tag in entity.get_xdata(APPID) if tag.code == 1000]


def set_xdata(entity, values):
    if APPID not in entity.doc.appids:
        entity.doc.appids.new(APPID)
    tags = [(1000, str(value)) for value in values]
    if entity.has_xdata(APPID):
        if [(tag.code, tag.value) for tag in entity.get_xdata(APPID)] == tags:
            return
        # ezdxf's documented replacement API logs "Duplicate XDATA appid"
        # whenever it overwrites a key. Explicitly replace our own section,
        # leaving all other manufacturers' APPID data intact. This does not
        # repair or conceal duplicated source tags during parsing.
        entity.discard_xdata(APPID)
    entity.set_xdata(APPID, tags)


def load_native(drawing, expected_sha256=None):
    if isinstance(drawing, (str, Path)):
        path = Path(drawing)
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
            raise NativeModelError(
                "The native DXF is unavailable or exceeds the bounded source size."
            )
        data = path.read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        if expected_sha256 and checksum != expected_sha256:
            raise NativeModelError("The native drawing hash changed.")
        try:
            return ezdxf.read(io.StringIO(data.decode("utf-8-sig"))), checksum
        except Exception as exc:
            raise NativeModelError(
                "The native DXF cannot be read without recovery."
            ) from exc
    if not hasattr(drawing, "modelspace"):
        raise NativeModelError("Use an owned native DXF file or parsed DXF document.")
    stream = io.StringIO()
    drawing.write(stream)
    data = stream.getvalue().encode()
    return ezdxf.read(io.StringIO(data.decode())), hashlib.sha256(data).hexdigest()


def _point(value):
    point = Vec3(value)
    if not all(math.isfinite(v) for v in point) or abs(point.z) > EPSILON:
        raise NativeModelError(
            "This adapter supports finite, planar XY coordinates only."
        )
    return [round(point.x, 6), round(point.y, 6)]


def _safe(value):
    if isinstance(value, Vec3):
        return list(value)
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {key: _safe(val) for key, val in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def entity_signature(entity):
    def properties(item):
        # DXF serialization legitimately omits explicit default values (e.g.
        # zero rotation and +Z extrusion). Canonicalize those, while retaining
        # every non-default geometry/presentation property for drift checks.
        result = {}
        for key, value in item.dxfattribs().items():
            definition = item.DXFATTRIBS.get(key)
            if definition is not None and _safe(value) == _safe(definition.default):
                continue
            result[key] = _safe(value)
        return result

    result = {
        "type": entity.dxftype(),
        "properties": properties(entity),
        "xdata": xdata(entity),
    }
    if entity.dxftype() == "LWPOLYLINE":
        result["vertices"] = [list(point) for point in entity.get_points()]
    if entity.dxftype() == "INSERT":
        result["attributes"] = [
            {"handle": attr.dxf.handle, "properties": properties(attr)}
            for attr in entity.attribs
        ]
    return result


def _vertices(entity):
    if entity.dxftype() == "LINE":
        return [_point(entity.dxf.start), _point(entity.dxf.end)]
    if (
        entity.dxftype() != "LWPOLYLINE"
        or entity.closed
        or abs(entity.dxf.elevation) > EPSILON
    ):
        raise NativeModelError(
            "Conductors must be open planar LINE or LWPOLYLINE paths."
        )
    vertices = list(entity.get_points())
    if any(abs(v[4]) > EPSILON for v in vertices):
        raise NativeModelError(
            "Arc/bulge conductor paths require another native adapter."
        )
    return [[round(v[0], 6), round(v[1], 6)] for v in vertices]


def _closed(entity):
    if (
        entity.dxftype() != "LWPOLYLINE"
        or not entity.closed
        or any(abs(v[4]) > EPSILON for v in entity.get_points())
    ):
        raise NativeModelError(
            "A declared device/enclosure body must be an actual closed straight polyline."
        )
    return [[float(v[0]), float(v[1])] for v in entity.get_points()]


def _bounds(points):
    return [
        round(min(p[0] for p in points), 6),
        round(min(p[1] for p in points), 6),
        round(max(p[0] for p in points), 6),
        round(max(p[1] for p in points), 6),
    ]


def close(a, b):
    return math.dist(a, b) <= EPSILON


def on_segment(point, a, b):
    return abs(math.dist(a, point) + math.dist(point, b) - math.dist(a, b)) <= EPSILON


def _orthogonal(points):
    return len(points) >= 2 and all(
        not close(a, b) and (abs(a[0] - b[0]) <= EPSILON or abs(a[1] - b[1]) <= EPSILON)
        for a, b in pairwise(points)
    )


def _intersection(a, b, c, d):
    # Route validation permits only orthogonal geometry, so intersections can
    # be checked exactly without guessing a connection at a visual crossing.
    av, cv = abs(a[0] - b[0]) <= EPSILON, abs(c[0] - d[0]) <= EPSILON
    if av != cv:
        point = [a[0], c[1]] if av else [c[0], a[1]]
        return (
            ("point", point)
            if on_segment(point, a, b) and on_segment(point, c, d)
            else (None, None)
        )
    axis = 1 if av else 0
    fixed = 0 if av else 1
    if abs(a[fixed] - c[fixed]) > EPSILON:
        return None, None
    lo, hi = (
        max(min(a[axis], b[axis]), min(c[axis], d[axis])),
        min(max(a[axis], b[axis]), max(c[axis], d[axis])),
    )
    if hi < lo - EPSILON:
        return None, None
    point = [a[0], lo] if av else [lo, a[1]]
    return ("overlap" if hi - lo > EPSILON else "point"), point


def inspect_native(drawing, expected_sha256=None):
    document, checksum = load_native(drawing, expected_sha256)
    return inspect_document(document, checksum)


def inspect_document(document, checksum=None):
    checks, devices, wires, enclosures, junctions = [], [], [], [], []
    annotations = []

    def check(key, status, label, reason, **context):
        checks.append(
            {"id": key, "status": status, "label": label, "reason": reason, **context}
        )

    if len(document.modelspace()) > MAX_ENTITIES:
        raise NativeModelError("The native model exceeds this adapter's entity limit.")
    check(
        "units",
        "pass" if document.units == 4 else "unknown",
        "Drawing units",
        "The pilot adapter requires explicit millimetres (INSUNITS=4); no unit conversion is guessed.",
    )
    definitions = {}
    for entity in document.modelspace():
        data = xdata(entity)
        try:
            if entity.dxftype() == "INSERT" and (
                data[:1] == ["device"] or entity.has_attrib("EQUIPMENT_TAG")
            ):
                attrs = {
                    attribute.dxf.tag: attribute.dxf.text
                    for attribute in entity.attribs
                }
                tag = attrs.get("EQUIPMENT_TAG", "")
                if (
                    not tag
                    or len([a for a in entity.attribs if a.dxf.tag == "EQUIPMENT_TAG"])
                    != 1
                    or data[:1] == ["device"]
                    and (len(data) < 2 or data[1] != tag)
                ):
                    raise NativeModelError(
                        "Device metadata and its one explicit EQUIPMENT_TAG attribute must agree."
                    )
                if entity.mcount != 1:
                    raise NativeModelError("Device block arrays are unsupported.")
                block = document.blocks.get(entity.dxf.name)
                if block.block.is_xref or block.block.is_xref_overlay:
                    raise NativeModelError(
                        "External-reference devices require another adapter."
                    )
                matrix = entity.matrix44()
                bodies, terminals = [], []
                for child in block:
                    native = xdata(child)
                    if native[:1] == ["body"] or child.dxf.layer == "DEVICE_BODY":
                        bodies.extend(
                            _point(matrix.transform(Vec3(x, y, 0)))
                            for x, y in _closed(child)
                        )
                    if (
                        native[:1] == ["terminal"]
                        or child.dxf.layer == "RIVET_TERMINALS"
                    ):
                        name = (
                            native[1]
                            if len(native) > 1
                            else child.dxf.text
                            if child.dxftype() == "TEXT"
                            else child.dxf.tag
                            if child.dxftype() == "ATTDEF"
                            else ""
                        )
                        point = (
                            child.dxf.location
                            if child.dxftype() == "POINT"
                            else child.dxf.center
                            if child.dxftype() == "CIRCLE"
                            else child.dxf.insert
                            if child.dxftype() in {"TEXT", "ATTDEF"}
                            else None
                        )
                        if not name or point is None:
                            raise NativeModelError(
                                "Each native terminal requires a supported marker with an explicit name."
                            )
                        terminals.append(
                            {
                                "id": f"{tag}:{name}",
                                "name": name,
                                "position": _point(matrix.transform(point)),
                                "source": {
                                    "insert_handle": entity.dxf.handle,
                                    "block_handle": child.dxf.handle,
                                },
                            }
                        )
                if not bodies:
                    raise NativeModelError(
                        "The device has no actual declared closed body geometry."
                    )
                if len({t["id"] for t in terminals}) != len(terminals):
                    raise NativeModelError("A device repeats a terminal name.")
                bounds = _bounds(bodies)
                if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                    raise NativeModelError(
                        "The actual device body has no positive extent."
                    )
                devices.append(
                    {
                        "tag": tag,
                        "handle": entity.dxf.handle,
                        "block_name": entity.dxf.name,
                        "kind": data[2] if len(data) > 2 else "unspecified",
                        "position": _point(entity.dxf.insert),
                        "rotation": float(entity.dxf.rotation),
                        "width": bounds[2] - bounds[0],
                        "height": bounds[3] - bounds[1],
                        "bounds": bounds,
                        "attributes": attrs,
                        "terminals": sorted(terminals, key=lambda t: t["id"]),
                        "body_points": bodies,
                    }
                )
                definitions[entity.dxf.name] = {
                    child.dxf.handle: entity_signature(child) for child in block
                }
            elif data[:1] == ["wire"]:
                if len(data) != 7 or not all(data[1:]):
                    raise NativeModelError(
                        "Wire identity requires id, two declared terminals and an explicit net id."
                    )
                wires.append(
                    {
                        "id": data[1],
                        "handle": entity.dxf.handle,
                        "from": f"{data[2]}:{data[3]}",
                        "to": f"{data[4]}:{data[5]}",
                        "net_id": data[6],
                        "points": _vertices(entity),
                    }
                )
            elif data[:1] == ["enclosure"]:
                points = _closed(entity)
                bounds = _bounds(points)
                if len(points) != 4 or any(
                    not (abs(x - bounds[0]) <= EPSILON or abs(x - bounds[2]) <= EPSILON)
                    or not (
                        abs(y - bounds[1]) <= EPSILON or abs(y - bounds[3]) <= EPSILON
                    )
                    for x, y in points
                ):
                    raise NativeModelError(
                        "The pilot enclosure must be a rectangular native boundary."
                    )
                enclosures.append(
                    {
                        "id": data[1],
                        "handle": entity.dxf.handle,
                        "bounds": bounds,
                        "points": points,
                    }
                )
            elif data[:1] == ["junction"]:
                point = (
                    entity.dxf.location
                    if entity.dxftype() == "POINT"
                    else entity.dxf.center
                    if entity.dxftype() == "CIRCLE"
                    else None
                )
                if point is None or len(data) != 3:
                    raise NativeModelError(
                        "An explicit junction requires a native point/circle, identity and net."
                    )
                junctions.append(
                    {
                        "id": f"junction:{data[1]}",
                        "position": _point(point),
                        "net_id": data[2],
                        "handle": entity.dxf.handle,
                    }
                )
            elif data[:1] in (["wire_label"], ["device_label"]):
                if len(data) != 2 or entity.dxftype() not in {"TEXT", "MTEXT"}:
                    raise NativeModelError(
                        "A linked native label requires TEXT/MTEXT and one exact existing owner identity."
                    )
                annotations.append(
                    {
                        "handle": entity.dxf.handle,
                        "kind": data[0],
                        "owner_id": data[1],
                        "position": _point(entity.dxf.insert),
                        "text": entity.dxf.text
                        if entity.dxftype() == "TEXT"
                        else entity.plain_text(),
                    }
                )
        except (ValueError, KeyError, AttributeError, IndexError) as exc:
            check(
                f"unsupported:{entity.dxf.handle}",
                "unknown",
                "Unsupported native object",
                str(exc),
                entity_handle=entity.dxf.handle,
            )
    for collection, identity, label in (
        (devices, "tag", "device"),
        (wires, "id", "wire"),
        (enclosures, "id", "enclosure"),
        (junctions, "id", "junction"),
    ):
        values = [item[identity] for item in collection]
        for value in sorted({v for v in values if values.count(v) > 1}):
            check(
                f"duplicate:{label}:{value}",
                "fail",
                f"Duplicate {label}",
                "Native identities must be unique; matching labels do not merge physical instances.",
            )
    if not devices or not enclosures:
        check(
            "coverage",
            "unknown",
            "Native coverage",
            "At least one declared device and actual enclosure boundary are required.",
        )
    for annotation in annotations:
        owners = wires if annotation["kind"] == "wire_label" else devices
        key = "id" if annotation["kind"] == "wire_label" else "tag"
        valid = sum(o[key] == annotation["owner_id"] for o in owners) == 1
        check(
            f"label_owner:{annotation['handle']}",
            "pass" if valid else "unknown",
            "Native annotation ownership",
            "An explicitly linked label must belong to exactly one actual native wire or device.",
            entity_handle=annotation["handle"],
        )
    terminal_list = [terminal for device in devices for terminal in device["terminals"]]
    terminals = {terminal["id"]: terminal for terminal in terminal_list}
    nodes = [*terminal_list, *junctions]
    for index, terminal in enumerate(terminal_list):
        if any(
            close(terminal["position"], other["position"])
            for other in terminal_list[index + 1 :]
        ):
            check(
                f"coincident:{terminal['id']}",
                "fail",
                "Coincident terminals",
                "Distinct device terminals occupy the same actual position; this can create an accidental short.",
            )
    for device in devices:
        bounds = device["bounds"]
        contained = any(
            bounds[0] >= e["bounds"][0] - EPSILON
            and bounds[1] >= e["bounds"][1] - EPSILON
            and bounds[2] <= e["bounds"][2] + EPSILON
            and bounds[3] <= e["bounds"][3] + EPSILON
            for e in enclosures
        )
        check(
            f"bounds:{device['tag']}",
            "pass" if contained else "fail",
            "Device inside enclosure",
            "Actual transformed body bounds must remain inside a declared enclosure.",
            device_tag=device["tag"],
        )
    for index, left in enumerate(devices):
        for right in devices[index + 1 :]:
            a, b = left["bounds"], right["bounds"]
            if (
                min(a[2], b[2]) - max(a[0], b[0]) > EPSILON
                and min(a[3], b[3]) - max(a[1], b[1]) > EPSILON
            ):
                check(
                    f"collision:{left['tag']}:{right['tag']}",
                    "fail",
                    "Device body collision",
                    "Actual transformed device body extents overlap.",
                    device_tag=left["tag"],
                    other_device_tag=right["tag"],
                )
    incidence = defaultdict(list)
    for wire in wires:
        points = wire["points"]
        check(
            f"orthogonal:{wire['id']}",
            "pass" if _orthogonal(points) else "fail",
            "Orthogonal conductor path",
            "Every actual conductor segment must be nonzero and horizontal or vertical.",
            wire_id=wire["id"],
        )
        if len(points) < 2:
            continue
        segments = list(pairwise(points))
        retraced = any(
            _intersection(a, b, c, d)[0] == "overlap"
            or j > i + 1
            and _intersection(a, b, c, d)[0] == "point"
            for i, (a, b) in enumerate(segments)
            for j, (c, d) in enumerate(segments[i + 1 :], i + 1)
        )
        check(
            f"self_path:{wire['id']}",
            "fail" if retraced else "pass",
            "Conductor path has no retracing",
            "A conductor cannot retrace itself or form an ambiguous self-intersection.",
            wire_id=wire["id"],
        )
        for key, point in (("from", points[0]), ("to", points[-1])):
            attached = [node["id"] for node in nodes if close(node["position"], point)]
            actual = attached[0] if len(attached) == 1 else None
            wire[f"actual_{key}"] = actual
            expected = terminals.get(wire[key])
            valid = expected is not None and actual == wire[key]
            check(
                f"attachment:{wire['id']}:{key}",
                "pass" if valid else "fail",
                "Wire terminal attachment",
                "Declared wire identity must match its actual endpoint on one existing terminal.",
                wire_id=wire["id"],
                terminal_id=wire[key],
                actual_terminal=actual,
            )
        attached_nodes = [
            node
            for node in nodes
            if any(on_segment(node["position"], a, b) for a, b in pairwise(points))
        ]
        inside = all(
            any(
                e["bounds"][0] - EPSILON <= point[0] <= e["bounds"][2] + EPSILON
                and e["bounds"][1] - EPSILON <= point[1] <= e["bounds"][3] + EPSILON
                for e in enclosures
            )
            for point in points
        )
        check(
            f"wire_bounds:{wire['id']}",
            "pass" if inside else "fail",
            "Conductor inside enclosure",
            "Every routed vertex must remain within the declared panel boundary.",
            wire_id=wire["id"],
        )
        for device in devices:
            x0, y0, x1, y1 = device["bounds"]
            penetrates = any(
                (
                    abs(a[1] - b[1]) <= EPSILON
                    and y0 + EPSILON < a[1] < y1 - EPSILON
                    and min(max(a[0], b[0]), x1) - max(min(a[0], b[0]), x0) > EPSILON
                )
                or (
                    abs(a[0] - b[0]) <= EPSILON
                    and x0 + EPSILON < a[0] < x1 - EPSILON
                    and min(max(a[1], b[1]), y1) - max(min(a[1], b[1]), y0) > EPSILON
                )
                for a, b in pairwise(points)
            )
            if penetrates:
                check(
                    f"wire_body:{wire['id']}:{device['tag']}",
                    "fail",
                    "Conductor crosses device body",
                    "The routed conductor penetrates an actual device body; boundary terminals do not authorize routing through the body.",
                    wire_id=wire["id"],
                    device_tag=device["tag"],
                )
        wire["terminal_ids"] = sorted(node["id"] for node in attached_nodes)
        for node in attached_nodes:
            incidence[node["id"]].append(wire)
            if node.get("net_id") and node["net_id"] != wire["net_id"]:
                check(
                    f"junction_net:{wire['id']}:{node['id']}",
                    "fail",
                    "Junction net conflict",
                    "An actual junction and touching conductor declare different nets.",
                    wire_id=wire["id"],
                )
    for node, attached in incidence.items():
        if len({wire["net_id"] for wire in attached}) > 1:
            check(
                f"short:{node}",
                "fail",
                "Conflicting nets physically connected",
                "Actual terminal/junction incidence joins conductors with different net identities.",
                terminal_id=node,
                wire_ids=sorted(wire["id"] for wire in attached),
            )
    crossings = []
    for index, left in enumerate(wires):
        for right in wires[index + 1 :]:
            for a, b in zip(left["points"], left["points"][1:]):
                for c, d in zip(right["points"], right["points"][1:]):
                    kind, point = _intersection(a, b, c, d)
                    if kind is None:
                        continue
                    connected = any(close(node["position"], point) for node in nodes)
                    item = {
                        "wire_ids": [left["id"], right["id"]],
                        "position": point,
                        "connected": connected,
                        "kind": kind,
                    }
                    if item not in crossings:
                        crossings.append(item)
                    if kind == "overlap":
                        check(
                            f"overlap:{left['id']}:{right['id']}",
                            "fail",
                            "Overlapping conductor paths",
                            "Coincident conductor segments are ambiguous and cannot be accepted as a crossing.",
                        )
    graph = {
        "schema_version": "1",
        "adapter": "rivet-native-dxf-1",
        "units": "mm" if document.units == 4 else "unknown",
        "sha256": checksum,
        "devices": sorted(devices, key=lambda d: d["tag"]),
        "wires": sorted(wires, key=lambda w: w["id"]),
        "enclosures": enclosures,
        "junctions": junctions,
        "annotations": annotations,
        "crossings": crossings,
        "checks": checks,
        "entity_manifest": {
            entity.dxf.handle: entity_signature(entity)
            for entity in document.modelspace()
        },
        "block_manifest": definitions,
        "limitations": [
            "Declared native pilot convention only; arbitrary DWG/EPLAN drawings, hidden device internals, electrical-code compliance and production release are not established.",
            "Body overlap and enclosure containment use actual transformed XY extents; depth, thermal clearances and protection coordination are not inferred.",
            "Crossings without explicit junctions remain separate electrical paths.",
        ],
    }
    graph["status"] = (
        "verified" if all(check["status"] == "pass" for check in checks) else "blocked"
    )
    graph["geometry_fingerprint"] = hashlib.sha256(
        json.dumps(
            {
                key: graph[key]
                for key in (
                    "devices",
                    "wires",
                    "enclosures",
                    "junctions",
                    "annotations",
                    "entity_manifest",
                    "block_manifest",
                )
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return graph
