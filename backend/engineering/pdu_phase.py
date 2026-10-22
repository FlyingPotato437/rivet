"""Bounded PDU assembly replacement prototype; never transformer winding design.

Only the explicit fictional RVT-PDU-PHASE-1 convention is writable. Input CAD,
catalog choices and BOM are hash-bound. Source bytes and unowned CAD survive.
No model, network, database, approval, release or mail-send operation exists here.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import tempfile
from copy import deepcopy
from pathlib import Path

import ezdxf

from backend.equipment.native_model import entity_signature, set_xdata, xdata

VERSION = "rivet.pdu-phase-assembly.v1"
CONVENTION = "RVT-PDU-PHASE-1"
FIELDS = (
    "PART_NUMBER",
    "DESCRIPTION",
    "POLES",
    "PHASES",
    "VOLTAGE_V",
    "KVA",
    "AMPS",
    "CONNECTION",
    "TERMINALS",
)
BOM_FIELDS = (
    "equipment_tag",
    "part_number",
    "quantity",
    "description",
    "poles",
    "phases",
    "voltage_v",
    "kva",
    "amps",
    "connection",
    "terminals",
    "source_ref",
    "notes",
)
REMAINING = [
    "Short-circuit duty, SCCR, interrupting rating and protection coordination",
    "Conductor ampacity, cable length, terminations and temperature derating",
    "Grounding/bonding location, neutral rating, GFCI and portable-equipment requirements",
    "Transformer impedance, inrush, harmonics, vector group and thermal performance",
    "Mechanical fit, ventilation, mass, stability and manufacturer component approval",
]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def file_sha(path):
    path = Path(path)
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 20 * 1024 * 1024
    ):
        raise ValueError("Use an ordinary, bounded local input file.")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_bom(path):
    file_sha(path)
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not set(BOM_FIELDS) <= set(reader.fieldnames or []):
            raise ValueError("BOM lacks the explicit PDU export headers.")
        rows = list(reader)
    tags = [r["equipment_tag"] for r in rows]
    if (
        not rows
        or len(rows) > 150
        or len(set(tags)) != len(tags)
        or any(not tag for tag in tags)
    ):
        raise ValueError("BOM device identities must be unique and nonempty.")
    return rows, reader.fieldnames


def read_drawing(path):
    file_sha(path)
    doc = ezdxf.readfile(path)
    if doc.units != 4 or len(doc.modelspace()) > 15000:
        raise ValueError("Explicit mm units and bounded geometry are required.")
    markers = [
        e.dxf.text
        for e in doc.modelspace().query("TEXT")
        if e.dxf.layer == "PDU_IDENTITY"
    ]
    if markers != [CONVENTION]:
        raise ValueError(
            "No supported, unambiguous PDU template identity. Manufacturer CAD needs an adapter."
        )
    devices = {}
    for ent in doc.modelspace().query("INSERT"):
        if not ent.has_attrib("EQUIPMENT_TAG"):
            continue
        attrs = {a.dxf.tag: a.dxf.text for a in ent.attribs}
        tag = attrs["EQUIPMENT_TAG"]
        if (
            tag in devices
            or len(attrs) != len(ent.attribs)
            or not set(FIELDS) <= attrs.keys()
        ):
            raise ValueError("Native device identities or attributes are ambiguous.")
        if (
            ent.mcount != 1
            or ent.dxf.rotation
            or any(
                abs(v - 1) > 1e-8
                for v in (ent.dxf.xscale, ent.dxf.yscale, ent.dxf.zscale)
            )
        ):
            raise ValueError(
                "Transformed or arrayed devices need a manufacturer adapter."
            )
        if doc.blocks.get(ent.dxf.name).block.is_xref:
            raise ValueError("Resolve external drawing references first.")
        devices[tag] = {"entity": ent, "attrs": attrs}
    if not devices:
        raise ValueError("No supported native devices.")
    return doc, devices


def owned(ent):
    return (
        xdata(ent)[:1] == ["pdu_owned"]
        or ent.dxftype() == "INSERT"
        and ent.has_attrib("EQUIPMENT_TAG")
    )


def catalog_attributes(part):
    return {key: str(part.get(key.lower(), "")) for key in FIELDS}


def _number(value, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or not 0 < value <= 1e7
    ):
        raise ValueError(f"{label} must be a finite positive explicit number.")
    return float(value)


def plan(drawing, bom, catalog, request):
    """Plan from actual baseline exports and explicit scenario-selected products."""
    _doc, devices = read_drawing(drawing)
    rows, _ = read_bom(bom)
    before = {r["equipment_tag"]: r for r in rows}
    if set(devices) != set(before):
        raise ValueError("Source CAD and BOM inventory differ.")
    if request.get("target") != "three_phase" or request.get("supply_phases") != 3:
        raise ValueError(
            "This adapter requires an explicit three-phase target and available three-phase supply."
        )
    if _number(request.get("power_factor"), "Power factor") != 1:
        raise ValueError(
            "This sample's independent fundamental-frequency model requires an explicit unity power factor."
        )
    if request.get("fictional") is not True or catalog.get("fictional") is not True:
        raise ValueError(
            "This prototype adapter is restricted to labelled fictional assemblies."
        )
    if not re.fullmatch(r"[A-Z][A-Z0-9-]{0,15}", request.get("revision", "")):
        raise ValueError("A bounded draft revision is required.")
    for tag, item in devices.items():
        for field in FIELDS:
            if item["attrs"][field] != before[tag][field.lower()]:
                raise ValueError(f"Source CAD/BOM disagree at {tag} {field}.")
    target = deepcopy(request.get("parts", {}))
    if set(target) != set(devices):
        raise ValueError(
            "Specify every retained/replaced device; no device is silently dropped."
        )
    parts = {}
    for tag, part_id in target.items():
        part = deepcopy(catalog.get("parts", {}).get(part_id))
        if not part or not part.get("source_ref") or not part.get("terminals"):
            raise ValueError(
                f"A traceable component and terminal convention are missing for {tag}."
            )
        if part["source_ref"] not in catalog.get("sources", {}) or len(
            set(part["terminals"].split(","))
        ) != len(part["terminals"].split(",")):
            raise ValueError(
                "Component references and terminal identities must be unambiguous."
            )
        parts[tag] = part
    transformer = parts["XF-1"]
    if (
        transformer.get("phases") != 3
        or transformer.get("connection") != "480D-208Y/120"
    ):
        raise ValueError(
            "The selected replacement transformer is outside this adapter."
        )
    kva = _number(transformer.get("kva"), "Transformer kVA")
    if kva != 30:
        raise ValueError(
            "The current sample supports a 30 kVA replacement assembly only."
        )
    primary_v = _number(request.get("primary_v"), "Primary voltage")
    if (
        primary_v != 480
        or request.get("secondary_ll_v") != 208
        or request.get("secondary_ln_v") != 120
        or request.get("frequency_hz") != 60
    ):
        raise ValueError(
            "This public-reference sample supports 480D to nominal 208Y/120 V at 60 Hz only."
        )
    for tag, volts in (("CB-M1", 480), ("CB-S1", 208)):
        if parts[tag].get("poles") != 3 or parts[tag].get("voltage_v", 0) < volts:
            raise ValueError(
                f"{tag} needs three poles and a sufficient stated voltage rating."
            )
    if parts["R-7"].get("connection") != "L21-30R" or parts["CB-7"].get("poles") != 3:
        raise ValueError(
            "The former 240 V outlet and its breaker must be explicitly replaced."
        )
    loads = request.get("loads", [])
    if len(loads) != 7 or {x.get("id") for x in loads} != {
        f"C{i}" for i in range(1, 8)
    }:
        raise ValueError("All seven circuit loads must be explicitly accounted for.")
    per_phase = {p: 0.0 for p in "ABC"}
    for load in loads:
        va = _number(load.get("va"), "Circuit VA")
        phases = load.get("lines")
        if load["id"] == "C7":
            if (
                phases != ["A", "B", "C"]
                or load.get("voltage_v") != 208
                or load.get("replacement_load_confirmed") is not True
            ):
                raise ValueError(
                    "Do not supply the original 240 V load at 208 V. Explicit compatible replacement load is required."
                )
            for p in "ABC":
                per_phase[p] += va / 3
        else:
            if (
                not isinstance(phases, list)
                or len(phases) != 1
                or phases[0] not in per_phase
                or load.get("voltage_v") != 120
            ):
                raise ValueError("Each 120 V load needs one explicit target phase.")
            per_phase[phases[0]] += va
        tag = "CB-" + load["id"][1:]
        amps = va / (math.sqrt(3) * 208) if load["id"] == "C7" else va / 120
        if amps > _number(parts[tag].get("amps"), "Candidate branch breaker current"):
            raise ValueError(
                f"Stated load exceeds the candidate {tag} rating. Code sizing remains unverified."
            )
    if any(va > kva * 1000 / 3 + 0.001 for va in per_phase.values()):
        raise ValueError(
            "A winding/phase load exceeds one third of the stated transformer capacity."
        )
    primary_a = kva * 1000 / (math.sqrt(3) * primary_v)
    secondary_a = kva * 1000 / (math.sqrt(3) * 208)
    if parts["CB-M1"]["amps"] < primary_a or parts["CB-S1"]["amps"] < secondary_a:
        raise ValueError(
            "The scenario-selected breaker is below transformer nameplate current; no automatic sizing is performed."
        )
    bindings = {
        "drawing": file_sha(drawing),
        "bom": file_sha(bom),
        "catalog": digest(catalog),
        "request": digest(request),
    }
    changes = []
    for tag, part in parts.items():
        after = catalog_attributes(part)
        delta = {
            f: {"before": devices[tag]["attrs"][f], "after": after[f]}
            for f in FIELDS
            if devices[tag]["attrs"][f] != after[f]
        }
        if delta:
            changes.append(
                {"tag": tag, "fields": delta, "source_ref": part["source_ref"]}
            )
    return {
        "schema": VERSION,
        "fictional": True,
        "bindings": bindings,
        "fingerprint": digest(bindings),
        "revision": request["revision"],
        "parts": parts,
        "loads": deepcopy(loads),
        "changes": changes,
        "calculations": {
            "total_kva": kva,
            "primary_full_load_a": primary_a,
            "secondary_full_load_a": secondary_a,
            "phase_load_va": per_phase,
            "total_load_va": sum(per_phase.values()),
            "basis": "fundamental-frequency load VA; no motor starting, harmonics or demand factors",
        },
        "engineering_approved": False,
        "released": False,
        "remaining_engineering": REMAINING,
    }


def terminal_positions(doc):
    result = {}
    for insert in doc.modelspace().query("INSERT"):
        if not insert.has_attrib("EQUIPMENT_TAG"):
            continue
        tag = insert.get_attrib_text("EQUIPMENT_TAG")
        matrix = insert.matrix44()
        for ent in doc.blocks.get(insert.dxf.name):
            data = xdata(ent)
            if ent.dxftype() == "POINT" and data[:1] == ["terminal"]:
                key = (tag, data[1])
                if key in result:
                    raise ValueError("Duplicate native terminal.")
                p = matrix.transform(ent.dxf.location)
                result[key] = (p.x, p.y)
    return result


def _pin_names(part):
    return part["terminals"].split(",")


def add_device(doc, tag, part, position):
    """Explicit symbol conventions; terminal positions are real block geometry."""
    key = "PDU_" + digest({"tag": tag, "part": part})[:16]
    base, suffix = key, 1
    while key in doc.blocks:
        key = f"{base}_R{suffix}"
        suffix += 1
    block = doc.blocks.new(key)
    pins = _pin_names(part)
    width = 82 if tag == "XF-1" else 64
    height = max(30, len(pins) * 9 + 12)
    body = block.add_lwpolyline(
        [
            (-width / 2, -height / 2),
            (width / 2, -height / 2),
            (width / 2, height / 2),
            (-width / 2, height / 2),
        ],
        close=True,
        dxfattribs={"layer": "E01_SYMBOLS"},
    )
    set_xdata(body, ["body"])
    local_pins = {}
    for i, pin in enumerate(pins):
        # H/L inputs on left, secondary/T outputs on right. A terminal table
        # and actual geometry are both retained; no implicit crossing junctions.
        right = pin.startswith(("X", "T", "C"))
        x, y = (width / 2 if right else -width / 2), height / 2 - 10 - i * 9
        point = block.add_point((x, y), dxfattribs={"layer": "E01_PINS"})
        set_xdata(point, ["terminal", pin])
        local_pins[pin] = (x, y)
        block.add_circle((x, y), 1.0, dxfattribs={"layer": "E01_PINS"})
        block.add_text(
            pin, dxfattribs={"height": 4.5, "layer": "E01_SYMBOLS"}
        ).set_placement((x + 3, y + 1))
    if tag == "PNL-1":
        # Actual internal bus links, rather than trusting names on outputs.
        for pin in pins:
            if not pin.startswith("C"):
                continue
            leg = "ABC".index(pin.rsplit("_", 1)[1]) + 1
            source = f"L{leg}"
            a, b = local_pins[source], local_pins[pin]
            lane = -15 + leg * 9
            path = block.add_lwpolyline(
                [a, (lane, a[1]), (lane, b[1]), b], dxfattribs={"layer": "E01_SYMBOLS"}
            )
            set_xdata(path, ["pdu_internal", source, pin])
    elif tag == "XF-1":
        # Connection glyph only: winding/core design is outside this adapter.
        for cx in (-13, 13):
            block.add_circle((cx, 0), 10, dxfattribs={"layer": "E01_SYMBOLS"})
        if part["phases"] == 3:
            block.add_lwpolyline(
                [(-19, -5), (-13, 6), (-7, -5)],
                close=True,
                dxfattribs={"layer": "E01_SYMBOLS"},
            )
            for target in ((13, 7), (7, -5), (19, -5)):
                block.add_line((13, 0), target, dxfattribs={"layer": "E01_SYMBOLS"})
    elif part["kind"] == "breaker":
        for leg in range(1, part["poles"] + 1):
            a, b = local_pins[f"L{leg}"], local_pins[f"T{leg}"]
            block.add_line(a, (-5, a[1]), dxfattribs={"layer": "E01_SYMBOLS"})
            block.add_line(
                (-5, a[1]), (5, b[1] + 3), dxfattribs={"layer": "E01_SYMBOLS"}
            )
            block.add_line((5, b[1]), b, dxfattribs={"layer": "E01_SYMBOLS"})
    for i, attr in enumerate(("EQUIPMENT_TAG", *FIELDS)):
        block.add_attdef(
            attr,
            (-width / 2, height / 2 + 6 + i * 5),
            dxfattribs={"height": 6, "flags": 0 if i == 0 else 1},
        )
    insert = doc.modelspace().add_blockref(
        key, position, dxfattribs={"layer": "E01_SYMBOLS"}
    )
    insert.add_auto_attribs({"EQUIPMENT_TAG": tag, **catalog_attributes(part)})
    set_xdata(insert, ["device", tag, part["kind"], width, height])
    return insert


def connections(parts, loads):
    """Explicit standard sample topology, distinct from the saved-file verifier."""
    n = int(parts["XF-1"]["phases"])
    edges = []

    def add(a, ap, b, bp, net):
        edges.append(
            {
                "wire_id": f"W{len(edges) + 1:03}",
                "from_tag": a,
                "from_pin": ap,
                "to_tag": b,
                "to_pin": bp,
                "net": net,
                "conductor_size": "ENGINEER TO SELECT",
            }
        )

    count = 3 if n == 3 else 2
    for i in range(1, count + 1):
        add("IN-1", f"L{i}", "CB-M1", f"L{i}", f"PRI_L{i}")
        add("CB-M1", f"T{i}", "XF-1", f"H{i}", f"PRI_SW{i}")
        add("XF-1", f"X{i}", "CB-S1", f"L{i}", f"SEC_L{i}")
        add("CB-S1", f"T{i}", "PNL-1", f"L{i}", f"SEC_SW{i}")
    add("XF-1", "X0", "NB-1", "IN", "NEUTRAL")
    add("IN-1", "G", "GB-1", "IN", "PE")
    add("XF-1", "G", "GB-1", "XF", "PE")
    add("PNL-1", "G", "GB-1", "PNL", "PE")
    for load in loads:
        i = int(load["id"][1:])
        lines = load["lines"]
        for j, line in enumerate(lines, 1):
            port = f"C{i}_{line}"
            add("PNL-1", port, f"CB-{i}", f"L{j}", f"C{i}_{line}_IN")
            add(f"CB-{i}", f"T{j}", f"R-{i}", f"L{j}", f"C{i}_{line}_OUT")
        add("NB-1", f"N{i}", f"R-{i}", "N", "NEUTRAL")
        add("GB-1", f"G{i}", f"R-{i}", "G", "PE")
    return edges


def write_connections(doc, wires):
    positions = terminal_positions(doc)
    for index, wire in enumerate(wires):
        a = positions[(wire["from_tag"], wire["from_pin"])]
        b = positions[(wire["to_tag"], wire["to_pin"])]
        # Separate deterministic lanes, with no crossing-implied electrical link.
        lane = (a[0] + b[0]) / 2 + (index % 7 - 3) * 1.5
        points = [a, (lane, a[1]), (lane, b[1]), b]
        layer = (
            "E01_PE"
            if wire["net"] == "PE"
            else "E01_NEUTRAL"
            if wire["net"] == "NEUTRAL"
            else "E01_WIRES"
        )
        entity = doc.modelspace().add_lwpolyline(points, dxfattribs={"layer": layer})
        set_xdata(
            entity,
            [
                "pdu_owned",
                "wire",
                wire["wire_id"],
                wire["from_tag"],
                wire["from_pin"],
                wire["to_tag"],
                wire["to_pin"],
                wire["net"],
            ],
        )


def write_csv(path, rows, headers):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def execute(drawing, bom, catalog, request, approved_plan, out):
    """Write a new draft directory atomically, only after independent readback.

    `approved_plan` means the caller selected this bounded plan for execution;
    it conveys no engineer approval or release authorization.
    """
    from .pdu_phase_demo import update_owned_tables
    from .pdu_phase_verify import verify

    current = plan(drawing, bom, catalog, request)
    if current != approved_plan:
        raise ValueError("Stale or edited plan; re-plan from the current source bytes.")
    out = Path(out)
    if out.exists():
        raise ValueError(
            "Output already exists. Originals and existing results are never overwritten."
        )
    doc, devices = read_drawing(drawing)
    preserved = {
        e.dxf.handle: entity_signature(e) for e in doc.modelspace() if not owned(e)
    }
    positions = {tag: tuple(v["entity"].dxf.insert) for tag, v in devices.items()}
    for ent in list(doc.modelspace()):
        if owned(ent):
            doc.modelspace().delete_entity(ent)
    for tag, part in current["parts"].items():
        add_device(doc, tag, part, positions[tag])
    wires = connections(current["parts"], current["loads"])
    write_connections(doc, wires)
    update_owned_tables(doc, current["parts"], current["loads"], current["revision"])
    rows, headers = read_bom(bom)
    for row in rows:
        part = current["parts"][row["equipment_tag"]]
        row.update({k.lower(): v for k, v in catalog_attributes(part).items()})
        row["source_ref"] = part["source_ref"]
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pdu-draft-", dir=out.parent) as temporary:
        work = Path(temporary)
        doc.saveas(work / "PDU-30-Rev-B.dxf")
        write_csv(work / "BOM-Rev-B.csv", rows, headers)
        write_csv(work / "Connections-Rev-B.csv", wires, list(wires[0]))
        report = verify(
            work / "PDU-30-Rev-B.dxf",
            work / "BOM-Rev-B.csv",
            work / "Connections-Rev-B.csv",
            catalog,
            request,
            preserved=preserved,
        )
        if not report["supported_checks_passed"]:
            raise ValueError(
                "Saved-file recheck failed: "
                + "; ".join(r["detail"] for r in report["checks"] if not r["passed"])
            )
        # Recheck original bytes after work, before exposing results.
        if (
            file_sha(drawing) != current["bindings"]["drawing"]
            or file_sha(bom) != current["bindings"]["bom"]
        ):
            raise ValueError("Originals changed during execution.")
        report.update(
            {
                "source_fingerprint": current["fingerprint"],
                "source_bindings": current["bindings"],
                "changes": current["changes"],
                "remaining_engineering": REMAINING,
            }
        )
        (work / "recheck.json").write_text(json.dumps(report, indent=2))
        # Same-filesystem rename publishes the complete, verified directory.
        Path(temporary).rename(out)
    return report
