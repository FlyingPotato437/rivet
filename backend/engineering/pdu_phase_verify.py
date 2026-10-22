"""Saved-artifact checks, independent of the PDU planner and topology writer.

No claim of electrical-code compliance or manufacturer suitability. This reader
checks the explicit sample contract, real terminal geometry and saved CSV data.
"""

from __future__ import annotations

import cmath
import csv
import hashlib
import math
from pathlib import Path

import ezdxf

from backend.equipment.native_model import entity_signature, xdata


def verify(drawing, bom, wiring, catalog, request, *, preserved=None):
    checks = []

    def check(key, passed, detail):
        checks.append({"id": key, "passed": bool(passed), "detail": detail})

    doc = ezdxf.readfile(drawing)
    check("units", doc.units == 4, "Native geometry retains explicit millimetre units.")
    devices, pins, attrs_by_tag, internal = {}, {}, {}, {}
    for insert in doc.modelspace().query("INSERT"):
        if not insert.has_attrib("EQUIPMENT_TAG"):
            continue
        attrs = {a.dxf.tag: a.dxf.text for a in insert.attribs}
        tag = attrs["EQUIPMENT_TAG"]
        check(
            "unique:" + tag,
            tag not in devices and len(attrs) == len(insert.attribs),
            f"Unique native block and fields for {tag}.",
        )
        devices[tag] = insert
        attrs_by_tag[tag] = attrs
        matrix = insert.matrix44()
        for entity in doc.blocks.get(insert.dxf.name):
            native = xdata(entity)
            if entity.dxftype() == "POINT" and native[:1] == ["terminal"]:
                key = (tag, native[1])
                check(
                    "pin:" + ":".join(key),
                    key not in pins,
                    f"Unique actual terminal {key}.",
                )
                point = matrix.transform(entity.dxf.location)
                pins[key] = (point.x, point.y)
        links = []
        for entity in doc.blocks.get(insert.dxf.name):
            native = xdata(entity)
            if native[:1] != ["pdu_internal"]:
                continue
            good = len(native) == 3 and entity.dxftype() == "LWPOLYLINE"
            if good:
                a, b = pins.get((tag, native[1])), pins.get((tag, native[2]))
                points = list(entity.get_points())
                good = bool(a and b and len(points) >= 2 and not entity.closed)
                if good:
                    start = matrix.transform((points[0][0], points[0][1], 0))
                    end = matrix.transform((points[-1][0], points[-1][1], 0))
                    good = (
                        math.dist(a, (start.x, start.y)) < 0.001
                        and math.dist(b, (end.x, end.y)) < 0.001
                    )
                links.append((native[1], native[2]))
            check(
                "internal_geometry:" + entity.dxf.handle,
                good,
                f"{tag} internal bus physically lands on its named terminals.",
            )
        internal[tag] = links
    with Path(bom).open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    check(
        "bom_duplicates",
        len({r["equipment_tag"] for r in rows}) == len(rows),
        "BOM has one row per device.",
    )
    bom_map = {r["equipment_tag"]: r for r in rows}
    wanted = request["parts"]
    check(
        "inventory",
        set(devices) == set(bom_map) == set(wanted),
        "CAD, BOM and requested device inventories agree exactly.",
    )
    for tag, part_id in wanted.items():
        part = catalog["parts"][part_id]
        attrs = attrs_by_tag.get(tag, {})
        row = bom_map.get(tag, {})
        for field in (
            "part_number",
            "description",
            "poles",
            "phases",
            "voltage_v",
            "kva",
            "amps",
            "connection",
            "terminals",
        ):
            value = str(part.get(field, ""))
            check(
                f"part:{tag}:{field}",
                attrs.get(field.upper()) == value and row.get(field) == value,
                f"{tag} {field}: saved block and BOM match the explicit candidate.",
            )
        actual = {pin for owner, pin in pins if owner == tag}
        check(
            "terminal_set:" + tag,
            actual == set(part["terminals"].split(",")),
            f"{tag} has the required actual terminal markers, including the third phase.",
        )
        check("quantity:" + tag, row.get("quantity") == "1", f"{tag} quantity is one.")
    # Independently reconstruct the required graph from the scenario contract.
    expected = set()
    for leg in ("1", "2", "3"):
        expected.update(
            [
                ("IN-1", "L" + leg, "CB-M1", "L" + leg),
                ("CB-M1", "T" + leg, "XF-1", "H" + leg),
                ("XF-1", "X" + leg, "CB-S1", "L" + leg),
                ("CB-S1", "T" + leg, "PNL-1", "L" + leg),
            ]
        )
    expected.update(
        [
            ("XF-1", "X0", "NB-1", "IN"),
            ("IN-1", "G", "GB-1", "IN"),
            ("XF-1", "G", "GB-1", "XF"),
            ("PNL-1", "G", "GB-1", "PNL"),
        ]
    )
    phase_va = {p: 0.0 for p in "ABC"}
    for load in request["loads"]:
        i = load["id"][1:]
        for index, line in enumerate(load["lines"], 1):
            expected.add(("PNL-1", f"C{i}_{line}", f"CB-{i}", f"L{index}"))
            expected.add((f"CB-{i}", f"T{index}", f"R-{i}", f"L{index}"))
            phase_va[line] += load["va"] / len(load["lines"])
        expected.add(("NB-1", "N" + i, "R-" + i, "N"))
        expected.add(("GB-1", "G" + i, "R-" + i, "G"))
    intended_buses = {
        (f"L{'ABC'.index(line) + 1}", f"C{load['id'][1:]}_{line}")
        for load in request["loads"]
        for line in load["lines"]
    }
    check(
        "internal_phase_buses",
        set(internal.get("PNL-1", [])) == intended_buses
        and len(internal.get("PNL-1", [])) == len(intended_buses),
        "Actual internal panel buses retain the requested phase on each circuit.",
    )
    observed, wire_ids = set(), set()
    wire_map = {}
    for ent in doc.modelspace():
        values = xdata(ent)
        if values[:2] != ["pdu_owned", "wire"]:
            continue
        check(
            "wire_schema:" + ent.dxf.handle,
            len(values) == 8 and ent.dxftype() == "LWPOLYLINE",
            "Saved native conductor has an explicit supported schema.",
        )
        if len(values) != 8 or ent.dxftype() != "LWPOLYLINE":
            continue
        _, _, wire_id, a, ap, b, bp, net = values
        endpoint = (a, ap, b, bp)
        check(
            "wire_unique:" + wire_id,
            wire_id not in wire_ids and endpoint not in observed,
            f"Unique conductor {wire_id}.",
        )
        wire_ids.add(wire_id)
        observed.add(endpoint)
        points = list(ent.get_points())
        start, end = pins.get((a, ap)), pins.get((b, bp))
        good = (
            start
            and end
            and len(points) >= 2
            and not ent.closed
            and not any(p[4] for p in points)
        )
        if good:
            good = (
                math.dist(start, points[0][:2]) < 0.001
                and math.dist(end, points[-1][:2]) < 0.001
            )
        check(
            "incidence:" + wire_id,
            good,
            f"{wire_id} physically lands on its two actual CAD terminals.",
        )
        if net == "PE":
            check(
                "pe:" + wire_id,
                (a == "GB-1" or b == "GB-1")
                and ap not in {"N", "X0"}
                and bp not in {"N", "X0"},
                "Protective earth is not substituted for neutral.",
            )
        wire_map[wire_id] = endpoint + (net,)
    check(
        "topology",
        observed == expected,
        f"Required terminal connections: {len(expected)}; present {len(observed)}; missing {sorted(expected - observed)}; unexpected {sorted(observed - expected)}.",
    )
    with Path(wiring).open(newline="", encoding="utf-8") as stream:
        exported = list(csv.DictReader(stream))
    report_map = {
        r["wire_id"]: (r["from_tag"], r["from_pin"], r["to_tag"], r["to_pin"], r["net"])
        for r in exported
    }
    check(
        "connection_export",
        len(report_map) == len(exported) and report_map == wire_map,
        "The saved connection report matches actual CAD conductor endpoints and net labels.",
    )
    kva = catalog["parts"][wanted["XF-1"]]["kva"]
    check(
        "phase_capacity",
        all(v <= kva * 1000 / 3 + 0.001 for v in phase_va.values()),
        "Each stated phase load fits its one-third transformer kVA share.",
    )
    # A separate inverse relation, rather than calling the planner's calculator.
    currents = {
        "primary_full_load_a": math.sqrt((kva * 1000) ** 2 / (3 * 480**2)),
        "secondary_full_load_a": math.sqrt((kva * 1000) ** 2 / (3 * 208**2)),
    }
    # Independent ideal AC calculation on the observed internal phase mapping.
    # This is a simple zero-impedance, unity-PF fundamental model, not SPICE,
    # protection coordination, harmonic analysis or a transformer design model.
    actual_va = {leg: 0.0 for leg in ("L1", "L2", "L3")}
    buses = {out: source for source, out in internal.get("PNL-1", [])}
    complete = True
    for load in request["loads"]:
        for line in load["lines"]:
            source = buses.get(f"C{load['id'][1:]}_{line}")
            if source not in actual_va:
                complete = False
            else:
                actual_va[source] += load["va"] / len(load["lines"])
    voltages = {
        f"L{i + 1}": cmath.rect(208 / math.sqrt(3), -i * 2 * math.pi / 3)
        for i in range(3)
    }
    line_currents = {
        leg: (va / voltages[leg]).conjugate() for leg, va in actual_va.items()
    }
    recovered_va = sum(
        (voltages[leg] * line_currents[leg].conjugate()).real for leg in actual_va
    )
    check(
        "ac_power_balance",
        complete
        and abs(recovered_va - sum(load["va"] for load in request["loads"])) < 0.001,
        "Independent ideal AC phasors conserve the explicitly stated circuit VA.",
    )
    check(
        "actual_phase_capacity",
        complete and all(va <= kva * 1000 / 3 + 0.001 for va in actual_va.values()),
        "Observed panel bus assignments keep each winding within its stated kVA share.",
    )
    power_flow = {
        "model": "ideal zero-impedance fundamental AC; unity PF; nominal V_LN derived from 208 V_LL",
        "phase_load_va": actual_va,
        "phase_current_a": {leg: abs(value) for leg, value in line_currents.items()},
        "fundamental_neutral_a": abs(sum(line_currents.values())),
        "recovered_apparent_va": recovered_va,
        "harmonics_inrush_and_protection_verified": False,
    }
    labels = [
        e.dxf.text
        for e in doc.modelspace().query("TEXT")
        if xdata(e)[:2] == ["pdu_owned", "table"]
    ]
    check(
        "visible_nameplate",
        f"OUTPUT: 208Y/120 V / 3 PH / 4W+PE / 60 Hz / REV {request['revision']}"
        in labels,
        "The actual visible CAD nameplate has the new phase, voltage, conductors and revision.",
    )
    for tag in wanted:
        check(
            "visible_part:" + tag,
            any(
                text.startswith(tag + " | ")
                and catalog["parts"][wanted[tag]]["part_number"] in text
                for text in labels
            ),
            f"Visible CAD schedule for {tag} matches its candidate part.",
        )
    if preserved is not None:
        actual = {
            e.dxf.handle: entity_signature(e)
            for e in doc.modelspace()
            if e.dxf.handle in preserved
        }
        check(
            "unowned_preserved",
            actual == preserved,
            "Unowned geometry, notes and handles are preserved exactly.",
        )
    return {
        "schema": "rivet.pdu-saved-file-recheck.v1",
        "fictional": True,
        "supported_checks_passed": all(r["passed"] for r in checks),
        "checks": checks,
        "devices_checked": len(devices),
        "connections_checked": len(observed),
        "expected_connections": len(expected),
        "phase_load_va": phase_va,
        "calculations": currents,
        "power_flow": power_flow,
        "engineering_approved": False,
        "released": False,
        "claim": "Supported sample-file consistency checks only; manufacturer CAD and electrical design validation remain unproven.",
        "artifact_sha256": {
            Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in (drawing, bom, wiring)
        },
    }
