"""Reproducible fictional native wiring/geometry revision acceptance packages.

The expected graphs and expected E drawings are authored from scenario intent,
never by calling the correction engine. All identifiers and designs are invented.
Manufacturer references inform document conventions, not component suitability.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import ezdxf
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.pdfgen import canvas

JOB = "RVT-NATIVE-261018"
DISCLAIMER = (
    "FICTIONAL TEST DESIGN - DRAFT FOR ENGINEERING REVIEW - NOT FOR FABRICATION"
)
REFERENCES = [
    {
        "title": "Eaton Switchboard Consulting Application Guide",
        "url": "https://www.eaton.com/content/dam/eaton/products/low-voltage-power-distribution-controls-systems/switchboards/switchboards-low-voltage-TB01500003E-specification-guide.pdf",
        "used_for": "General arrangement, dimension and issue-record conventions only.",
    },
    {
        "title": "Schneider Electric TransferPact catalog - electrical diagrams",
        "url": "https://iportal.se.com/Contents/docs/PACT%20SERIES%20TRANSFERPACT%20CATALOG%202020.PDF",
        "used_for": "Named terminals, auxiliary relay and breaker opening-release documentation conventions only.",
    },
    {
        "title": "GE Vernova bus protection typical applications",
        "url": "https://www.gevernova.com/electrification/automation/protection-control-metering/bus-protection/typical-applications",
        "used_for": "86 lockout auxiliary relay role; this fixture does not duplicate a manufacturer circuit.",
    },
]

HEADERS = [
    "equipment_tag",
    "part_number",
    "quantity",
    "nameplate",
    "device_type",
    "description",
    "drawing_sheet",
    "manufacturer",
    "revision",
]


def _device(tag, kind, position, size, terminals, description):
    return {
        "tag": tag,
        "kind": kind,
        "position": list(position),
        "size": list(size),
        "terminals": {key: list(value) for key, value in terminals.items()},
        "part_number": "RVT-" + kind.upper(),
        "quantity": 1,
        "nameplate": description.upper(),
        "description": description,
    }


def baseline_graph():
    """An explicitly modelled 24 Vdc control cubicle, not an approved circuit."""
    devices = [
        _device(
            "TB-1",
            "terminal_strip",
            (90, 365),
            (34, 350),
            {str(i + 1): (17, 155 - i * 40) for i in range(8)},
            "Control and remote alarm terminals",
        ),
        _device(
            "FU-1",
            "fuse",
            (190, 550),
            (50, 40),
            {"1": (-25, 0), "2": (25, 0)},
            "Control fuse 2 A",
        ),
        _device(
            "K86-1",
            "lockout",
            (340, 480),
            (100, 120),
            {
                "A1": (-50, 35),
                "A2": (-50, -35),
                "11": (50, 40),
                "12": (50, 15),
                "21": (50, -15),
                "22": (50, -40),
            },
            "86 lockout relay - manual reset",
        ),
        _device(
            "CB-12",
            "trip_release",
            (620, 500),
            (100, 100),
            {"TC1": (-50, 25), "TC2": (-50, -25)},
            "Feeder 12 opening release",
        ),
        _device(
            "CB-13",
            "trip_release",
            (620, 340),
            (100, 100),
            {"TC1": (-50, 25), "TC2": (-50, -25)},
            "Feeder 13 opening release",
        ),
        _device(
            "KA-1",
            "aux_relay",
            (350, 235),
            (80, 80),
            {"A1": (-40, 20), "A2": (-40, -20), "13": (40, 20), "14": (40, -20)},
            "Remote alarm interposing relay",
        ),
        _device(
            "PL-1",
            "pilot_light",
            (620, 140),
            (60, 60),
            {"X1": (-30, 15), "X2": (-30, -15)},
            "Amber remote alarm",
        ),
        _device(
            "CT-1",
            "metering_ct",
            (190, 110),
            (60, 60),
            {"S1": (30, 15), "S2": (30, -15)},
            "Metering CT 600/5 - secondary demo only",
        ),
        _device(
            "MTR-1",
            "meter",
            (350, 95),
            (80, 60),
            {"I+": (-40, 15), "I-": (-40, -15)},
            "Digital ammeter input",
        ),
    ]
    graph = {
        "schema_version": "rivet.native.v1",
        "units": "mm",
        "enclosure": {"id": "CP-1", "bounds": [40, 40, 760, 600]},
        "devices": {item["tag"]: item for item in devices},
        "wires": {},
    }
    # The expected circuit's branch identities are explicit. Geometric crossings
    # without a junction are not connections. No implicit internal relay logic.
    definitions = [
        ("W100", "TB-1", "1", "FU-1", "1", "24V_RAW", 130),
        ("W101", "FU-1", "2", "K86-1", "11", "24V_FUSED", 240),
        ("W102", "K86-1", "12", "CB-12", "TC1", "TRIP_12", 450),
        ("W103", "FU-1", "2", "K86-1", "21", "24V_FUSED", 250),
        ("W104", "K86-1", "22", "CB-13", "TC1", "TRIP_13", 470),
        ("W105", "CB-12", "TC2", "TB-1", "2", "RETURN_0V", 710),
        ("W106", "CB-13", "TC2", "TB-1", "5", "RETURN_0V", 720),
        ("W107", "TB-1", "3", "K86-1", "A1", "LOCKOUT_INPUT", 180),
        ("W108", "K86-1", "A2", "TB-1", "6", "RETURN_0V", 265),
        ("W201", "TB-1", "4", "KA-1", "A1", "ALARM_INPUT", 220),
        ("W202", "KA-1", "A2", "TB-1", "7", "RETURN_0V", 280),
        ("W203", "FU-1", "2", "KA-1", "13", "24V_FUSED", 420),
        ("W204", "KA-1", "14", "PL-1", "X1", "ALARM_LIGHT", 480),
        ("W205", "PL-1", "X2", "TB-1", "8", "RETURN_0V", 730),
        ("W301", "CT-1", "S1", "MTR-1", "I+", "CT_S1", 265),
        ("W302", "CT-1", "S2", "MTR-1", "I-", "CT_S2", 280),
    ]
    for wire_id, a, at, b, bt, net, route_x in definitions:
        graph["wires"][wire_id] = {
            "id": wire_id,
            "from": [a, at],
            "to": [b, bt],
            "net_id": net,
            "points": route(graph, [a, at], [b, bt], route_x),
        }
    # Manually drafted non-overlapping paths. The three conductors on the fuse
    # output depart in distinct directions; returns land on separate terminals.
    # These coordinates are independent source data, not our production router.
    explicit = {
        "W101": [[215, 550], [215, 575], [440, 575], [440, 520], [390, 520]],
        "W102": [[390, 495], [460, 495], [460, 525], [570, 525]],
        "W103": [[215, 550], [450, 550], [450, 465], [390, 465]],
        "W104": [[390, 440], [470, 440], [470, 365], [570, 365]],
        "W105": [
            [570, 475],
            [550, 475],
            [550, 410],
            [140, 410],
            [140, 480],
            [107, 480],
        ],
        "W106": [
            [570, 315],
            [550, 315],
            [550, 290],
            [145, 290],
            [145, 360],
            [107, 360],
        ],
        "W108": [[290, 445], [265, 445], [265, 320], [107, 320]],
        "W202": [[310, 215], [290, 215], [290, 280], [107, 280]],
        "W203": [
            [215, 550],
            [215, 535],
            [270, 535],
            [270, 405],
            [430, 405],
            [430, 255],
            [390, 255],
        ],
        "W205": [[590, 125], [560, 125], [560, 50], [140, 50], [140, 240], [107, 240]],
    }
    # Terminal numbering is 1..8 from y=520 down to y=240. Every manual endpoint
    # is asserted against its explicit native terminal before serializing.
    for wire_id, points in explicit.items():
        graph["wires"][wire_id]["points"] = points
    for wire in graph["wires"].values():
        assert wire["points"][0] == terminal(graph, wire["from"])
        assert wire["points"][-1] == terminal(graph, wire["to"])
    return graph


def terminal(graph, endpoint):
    device = graph["devices"][endpoint[0]]
    local = device["terminals"][endpoint[1]]
    return [device["position"][0] + local[0], device["position"][1] + local[1]]


def route(graph, a, b, x):
    start, end = terminal(graph, a), terminal(graph, b)
    candidates = [start, [x, start[1]], [x, end[1]], end]
    return [
        point
        for i, point in enumerate(candidates)
        if not i or point != candidates[i - 1]
    ]


def _xdata(entity, values):
    entity.set_xdata("RIVET", [(1000, str(value)) for value in values])


def _text(space, value, xy, height=4, layer="NOTES"):
    return space.add_text(
        str(value), dxfattribs={"height": height, "layer": layer}
    ).set_placement(xy)


def write_native(path, graph, revision):
    """Serialize actual block bodies, native terminals and electrical polylines."""
    document = ezdxf.new("R2010")
    document.units = 4
    document.appids.new("RIVET")
    for name, color in [
        ("DEVICE_BODY", 7),
        ("WIRING", 3),
        ("NOTES", 7),
        ("TERMINALS", 4),
        ("ENCLOSURE", 7),
        ("DIMENSIONS", 8),
    ]:
        document.layers.new(name, dxfattribs={"color": color})
    model = document.modelspace()
    x1, y1, x2, y2 = graph["enclosure"]["bounds"]
    boundary = model.add_lwpolyline(
        [(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
        close=True,
        dxfattribs={"layer": "ENCLOSURE"},
    )
    _xdata(boundary, ["enclosure", graph["enclosure"]["id"]])
    for item in graph["devices"].values():
        name = "RVT_" + item["kind"].upper()
        if name not in document.blocks:
            block = document.blocks.new(name)
            width, height = item["size"]
            body = block.add_lwpolyline(
                [
                    (-width / 2, -height / 2),
                    (width / 2, -height / 2),
                    (width / 2, height / 2),
                    (-width / 2, height / 2),
                ],
                close=True,
                dxfattribs={"layer": "DEVICE_BODY"},
            )
            _xdata(body, ["body"])
            for name_term, point in item["terminals"].items():
                pin = block.add_point(point, dxfattribs={"layer": "TERMINALS"})
                _xdata(pin, ["terminal", name_term])
                block.add_circle(point, 1.7, dxfattribs={"layer": "TERMINALS"})
                _text(block, name_term, (point[0] + 3, point[1] + 3), 3)
            for index, attribute in enumerate(
                ("EQUIPMENT_TAG", "PART_NUMBER", "QUANTITY", "NAMEPLATE")
            ):
                block.add_attdef(
                    attribute,
                    (0, height / 2 + 4 + index * 4),
                    dxfattribs={"height": 3, "flags": 0 if index == 0 else 1},
                )
        insert = model.add_blockref(name, item["position"])
        insert.add_auto_attribs(
            {
                "EQUIPMENT_TAG": item["tag"],
                "PART_NUMBER": item["part_number"],
                "QUANTITY": str(item["quantity"]),
                "NAMEPLATE": item["nameplate"],
            }
        )
        _xdata(insert, ["device", item["tag"], item["kind"], *item["size"]])
    for wire in graph["wires"].values():
        polyline = model.add_lwpolyline(wire["points"], dxfattribs={"layer": "WIRING"})
        _xdata(
            polyline, ["wire", wire["id"], *wire["from"], *wire["to"], wire["net_id"]]
        )
        center = wire["points"][1] if len(wire["points"]) > 2 else wire["points"][0]
        label = _text(model, wire["id"], (center[0] + 2, center[1] + 2), 3)
        _xdata(label, ["wire_label", wire["id"]])
    # Readable issue information and a literal dimension string; the graph's
    # enclosure coordinates remain the independently measurable native geometry.
    _text(model, "RIVET / NORTHLINE WATER RECLAMATION - FICTIONAL", (40, 665), 8)
    _text(
        model,
        "CP-1  /  24 VDC CONTROL CUBICLE / TERMINAL-TO-TERMINAL ARRANGEMENT",
        (40, 650),
        5,
    )
    _text(
        model,
        f"{JOB}  |  E-21  |  REV {revision}  |  ALL DIMENSIONS mm  |  SCALE NTS",
        (40, 630),
        4,
    )
    _text(model, "720 mm backplate width", (270, 22), 4)
    _text(model, DISCLAIMER, (40, 5), 4)
    _text(
        model,
        "Crossings without a terminal or explicit junction are NOT electrically connected.",
        (40, -8),
        3.5,
    )
    _text(
        model,
        "Contact functions and ratings require independent engineer approval.",
        (40, -18),
        3.5,
    )
    document.saveas(path)


def bom_rows(graph, revision):
    return [
        {
            "equipment_tag": item["tag"],
            "part_number": item["part_number"],
            "quantity": item["quantity"],
            "nameplate": item["nameplate"],
            "device_type": item["kind"],
            "description": item["description"],
            "drawing_sheet": "E-21",
            "manufacturer": "Fictional Rivet Test Works",
            "revision": revision,
        }
        for item in graph["devices"].values()
    ]


def write_bom(path, graph, revision, *, stale_part=False):
    """A native styled workbook whose inventory is independent of edit output."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Equipment BOM"
    sheet.append(HEADERS)
    rows = bom_rows(graph, revision)
    if stale_part:
        rows[5]["part_number"] = "RVT-OBSOLETE-RELAY"
    for item in rows:
        sheet.append([item[name] for name in HEADERS])
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="18242F")
        cell.font = Font(name="Aptos", size=11, bold=True, color="FFFFFF")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[1].height = 30
    for row in sheet.iter_rows(min_row=2):
        sheet.row_dimensions[row[0].row].height = 32
        for cell in row:
            cell.font = Font(name="Aptos", size=11, color="18242F")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if cell.row % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F1F4F6")
    for index, width in enumerate([19, 26, 12, 49, 22, 51, 18, 32, 13], 1):
        sheet.column_dimensions[sheet.cell(1, index).column_letter].width = width
    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_title_rows = "1:1"
    sheet.print_options.horizontalCentered = True
    sheet.oddHeader.center.text = f"{JOB} - BILL OF MATERIALS - REV {revision}"
    sheet.oddFooter.center.text = DISCLAIMER
    issue = workbook.create_sheet("Issue record")
    for row in [
        ("Job", JOB),
        ("Customer", "Fictional Northline Water Reclamation"),
        ("Revision", revision),
        ("Drawing", "E-21"),
        ("Status", "DRAFT - engineering review required"),
        ("Units", "Native coordinates in mm; control supply 24 Vdc"),
        (
            "Inventory",
            "Every separately tagged device appears once; quantity means per tag",
        ),
        (
            "Parts",
            "All RVT part numbers are invented; no manufacturer approval implied",
        ),
        ("Source", "Authored fictional test data; not real customer records"),
    ]:
        issue.append(row)
    issue.column_dimensions["A"].width = 22
    issue.column_dimensions["B"].width = 100
    for row in issue:
        issue.row_dimensions[row[0].row].height = 31
        for cell in row:
            cell.font = Font(name="Aptos", size=11, color="18242F")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        row[0].font = Font(name="Aptos", size=11, bold=True, color="18242F")
    issue.page_setup.orientation = "landscape"
    issue.page_setup.paperSize = issue.PAPERSIZE_A4
    issue.page_setup.fitToWidth = 1
    issue.page_setup.fitToHeight = 1
    issue.sheet_properties.pageSetUpPr.fitToPage = True
    issue.oddFooter.center.text = (
        "Fictional engineering test package - not for fabrication"
    )
    workbook.save(path)


def _pdf_frame(c, revision, title, sheet, page, total_pages=2):
    width, height = 1191, 842
    c.setStrokeColor(colors.HexColor("#20303F"))
    c.setFillColor(colors.HexColor("#20303F"))
    c.setLineWidth(0.7)
    c.rect(24, 24, width - 48, height - 48)
    c.setFont("Helvetica-Bold", 19)
    c.drawString(44, 788, "RIVET / FICTIONAL SWITCHBOARD WORKS")
    c.setFont("Helvetica", 10)
    c.drawString(44, 770, f"{JOB}  /  NORTHLINE WATER RECLAMATION - DEMONSTRATION ONLY")
    c.line(24, 750, width - 24, 750)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(44, 726, title)
    c.line(24, 100, width - 24, 100)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, 78, DISCLAIMER)
    c.setFont("Helvetica", 8)
    c.drawString(
        40,
        60,
        "No protection coordination, fault rating, creepage/clearance or installation certification is implied.",
    )
    c.drawString(
        40,
        44,
        "DATE: 2026-10-18   /   DRAWN: TEST AUTHOR   /   CHECKED: INDEPENDENT EXPECTED GRAPH   /   SCALE: NTS",
    )
    c.setFont("Helvetica-Bold", 12)
    c.drawRightString(1140, 78, f"{sheet} / REV {revision}")
    c.setFont("Helvetica", 9)
    c.drawRightString(1140, 58, f"PAGE {page} OF {total_pages} / FOR APPROVAL")


def write_drawing_pdf(path, graph, revision):
    """Two readable sheets built from the same independent source graph."""
    c = canvas.Canvas(str(path), pagesize=(1191, 842), invariant=1)
    c.setTitle(f"{JOB} E-21 / E-22 Rev {revision} - fictional native arrangement")
    _pdf_frame(
        c,
        revision,
        "CONTROL CUBICLE - PHYSICAL ARRANGEMENT AND ROUTED WIRING",
        "E-21",
        1,
    )
    scale, ox, oy = 0.82, 30, 151

    def xy(point):
        return ox + point[0] * scale, oy + point[1] * scale

    x1, y1, x2, y2 = graph["enclosure"]["bounds"]
    c.setLineWidth(1.2)
    c.rect(*xy((x1, y1)), (x2 - x1) * scale, (y2 - y1) * scale)
    for wire in graph["wires"].values():
        c.setStrokeColor(colors.HexColor("#506975"))
        c.setLineWidth(0.65)
        p = c.beginPath()
        p.moveTo(*xy(wire["points"][0]))
        for point in wire["points"][1:]:
            p.lineTo(*xy(point))
        c.drawPath(p)
    for device in graph["devices"].values():
        x, y = xy(device["position"])
        width, height = [value * scale for value in device["size"]]
        c.setFillColor(colors.white)
        c.setStrokeColor(colors.HexColor("#20303F"))
        c.setLineWidth(1)
        c.rect(x - width / 2, y - height / 2, width, height, fill=1)
        c.setFillColor(colors.HexColor("#20303F"))
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(x, y + 2, device["tag"])
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(x, y - 9, device["kind"].replace("_", " "))
        for name in device["terminals"]:
            tx, ty = xy(terminal(graph, [device["tag"], name]))
            c.circle(tx, ty, 2, fill=0)
            c.drawString(tx + 4, ty + 4, name)
    c.setFont("Helvetica", 8)
    c.drawCentredString(
        ox + 400 * scale,
        165,
        "720 mm backplate width / 560 mm height - literal native boundary",
    )
    c.setFont("Helvetica-Bold", 11)
    c.drawString(730, 670, "EQUIPMENT SCHEDULE")
    c.setFont("Helvetica", 8)
    for index, device in enumerate(graph["devices"].values()):
        y = 646 - index * 43
        c.setFont("Helvetica-Bold", 9)
        c.drawString(730, y, device["tag"] + " / " + device["part_number"])
        c.setFont("Helvetica", 8)
        c.drawString(730, y - 13, device["description"])
        c.drawString(
            730,
            y - 26,
            f"x={device['position'][0]}  y={device['position'][1]} mm / qty 1",
        )
    c.showPage()
    _pdf_frame(
        c, revision, "TERMINAL CONNECTION LIST AND CIRCUIT IDENTITIES", "E-22", 2
    )
    c.setFont("Helvetica", 9)
    c.drawString(
        44,
        700,
        "Native endpoint geometry is authoritative; the connection table is a readable projection of that source.",
    )
    labels = [
        (45, "WIRE"),
        (130, "FROM TERMINAL"),
        (345, "TO TERMINAL"),
        (565, "NET / CIRCUIT"),
        (810, "CONDUCTOR"),
    ]
    c.setFillColor(colors.HexColor("#20303F"))
    c.rect(40, 654, 1090, 25, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 9)
    for x, label in labels:
        c.drawString(x, 662, label)
    for index, wire in enumerate(graph["wires"].values()):
        y = 632 - index * 25
        if index % 2 == 0:
            c.setFillColor(colors.HexColor("#F1F4F6"))
            c.rect(40, y - 6, 1090, 25, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#20303F"))
        c.setFont("Helvetica", 9)
        for x, text in [
            (45, wire["id"]),
            (130, ":".join(wire["from"])),
            (345, ":".join(wire["to"])),
            (565, wire["net_id"]),
            (810, "1.5 mm2 / labelled both ends"),
        ]:
            c.drawString(x, y, text)
    c.setFont("Helvetica", 9)
    c.drawString(
        44,
        165,
        "1. A crossing without a terminal or junction dot is not a connection. Internal relay/coil behavior is not inferred.",
    )
    c.drawString(
        44,
        148,
        "2. Relay and breaker auxiliary compatibility, coil burden and protection duties require engineer approval.",
    )
    c.drawString(
        44,
        131,
        "3. All circuits shown are fictional test circuits. No instruction to change an energized or installed assembly.",
    )
    c.save()


def write_requests(path, scenario, lines):
    c = canvas.Canvas(str(path), pagesize=(1191, 842), invariant=1)
    c.setTitle(f"{JOB} - {scenario} returned change request - fictional")
    _pdf_frame(
        c,
        "R1",
        "CUSTOMER RETURN / EXPLICIT NATIVE CHANGE REQUEST",
        "CR-01",
        1,
        total_pages=1,
    )
    c.setFont("Helvetica-Bold", 14)
    c.drawString(44, 685, scenario)
    c.setFont("Helvetica", 10)
    c.drawString(
        44,
        660,
        "Customer: Fictional Northline Water Reclamation / PM: Morgan Lee (invented)",
    )
    c.drawString(
        44,
        640,
        "Returned status: REVISE AND RESUBMIT / Source: E-21 Rev D and Equipment BOM Rev D",
    )
    c.drawString(
        44,
        620,
        "Requested output: new draft drawing, matching BOM, regenerated preview and independent recheck.",
    )
    y = 575
    for index, line in enumerate(lines, 1):
        line = f"CHANGE C{index:02}: {line}"
        c.setFont("Courier", 10)
        # Grammar is deliberately literal and visible, not hidden metadata.
        c.drawString(50, y, line)
        y -= 25
    c.setFont("Helvetica", 9)
    c.drawString(
        44,
        200,
        "Retain all other devices, terminal identities and conductor endpoints unless explicitly changed above.",
    )
    c.drawString(
        44,
        180,
        "Preserve original files. Draft corrections require review; this request is not a production release.",
    )
    c.save()


def _moved(graph, tag, position):
    graph["devices"][tag]["position"] = list(position)
    for wire in graph["wires"].values():
        if tag in (wire["from"][0], wire["to"][0]):
            # Explicit expected routing authored independently of edit engine.
            x = wire["points"][1][0]
            wire["points"] = route(graph, wire["from"], wire["to"], x)
    if tag == "KA-1" and list(position) == [500, 235]:
        manual = {
            "W201": [[107, 400], [220, 400], [220, 255], [460, 255]],
            "W202": [[460, 215], [440, 215], [440, 280], [107, 280]],
            "W203": [
                [215, 550],
                [215, 535],
                [270, 535],
                [270, 405],
                [555, 405],
                [555, 255],
                [540, 255],
            ],
            "W204": [[540, 215], [560, 215], [560, 155], [590, 155]],
        }
        for key, points in manual.items():
            graph["wires"][key]["points"] = points


def scenario_definitions():
    """Expected outputs are hand-authored graph transforms, not edit-code calls."""
    base = baseline_graph()
    swapped = copy.deepcopy(base)
    for wire_id, tag in [("W102", "CB-13"), ("W104", "CB-12")]:
        wire = swapped["wires"][wire_id]
        wire["to"] = [tag, "TC1"]
        wire["points"] = route(
            swapped, wire["from"], wire["to"], 460 if wire_id == "W102" else 470
        )
    moved = copy.deepcopy(base)
    _moved(moved, "KA-1", (500, 235))
    added = copy.deepcopy(base)
    new = copy.deepcopy(base["devices"]["KA-1"])
    new.update(
        tag="KA-2",
        position=[700, 235],
        nameplate="CUSTOMER ALARM REPEAT RELAY",
        description="Remote alarm interposing relay",
    )
    added["devices"]["KA-2"] = new
    added["wires"]["W206"] = {
        "id": "W206",
        "from": ["KA-1", "14"],
        "to": ["KA-2", "A1"],
        "net_id": "ALARM_LIGHT",
        "points": [[390, 215], [390, 185], [640, 185], [640, 255], [660, 255]],
    }
    added["wires"]["W207"] = {
        "id": "W207",
        "from": ["KA-2", "A2"],
        "to": ["CB-13", "TC2"],
        "net_id": "RETURN_0V",
        "points": [[660, 215], [650, 215], [650, 280], [570, 280], [570, 315]],
    }
    removed = copy.deepcopy(base)
    removed["devices"].pop("KA-1")
    for key in ("W201", "W202", "W203", "W204"):
        removed["wires"].pop(key)
    combined = copy.deepcopy(swapped)
    _moved(combined, "KA-1", (500, 235))
    resized = copy.deepcopy(base)
    relay = resized["devices"]["KA-1"]
    relay["size"] = [100, 100]
    relay["terminals"] = {
        key: [point[0] * 1.25, point[1] * 1.25]
        for key, point in relay["terminals"].items()
    }
    for key, points in {
        "W201": [[107, 400], [220, 400], [220, 260], [300, 260]],
        "W202": [[300, 210], [290, 210], [290, 280], [107, 280]],
        "W203": [
            [215, 550],
            [215, 535],
            [270, 535],
            [270, 405],
            [430, 405],
            [430, 260],
            [400, 260],
        ],
        "W204": [[400, 210], [480, 210], [480, 155], [590, 155]],
    }.items():
        resized["wires"][key]["points"] = points
    rerouted = copy.deepcopy(base)
    rerouted["wires"]["W102"]["points"] = [
        [390, 495],
        [490, 495],
        [490, 525],
        [570, 525],
    ]
    # Requests are assigned centrally below; the grammar is intentionally easy
    # to read in the PDF and map to exact native evidence.
    return [
        {
            "id": "01-trip-rewire",
            "title": "Swap the two lockout trip outputs",
            "expected": swapped,
            "actions": [
                "Rewire W102 from K86-1:12 to CB-13:TC1.",
                "Rewire W104 from K86-1:22 to CB-12:TC1.",
            ],
        },
        {
            "id": "02-relay-relocation",
            "title": "Relocate alarm relay with its connected wires",
            "expected": moved,
            "actions": ["Move KA-1 to (500, 235) mm."],
        },
        {
            "id": "03-add-alarm-relay",
            "title": "Add and wire a customer alarm repeat relay",
            "expected": added,
            "actions": [
                "Add device KA-2 using block RVT_AUX_RELAY at (700, 235) mm; PART_NUMBER=RVT-AUX_RELAY; QUANTITY=1; NAMEPLATE=CUSTOMER ALARM REPEAT RELAY.",
                "Add wire W206 net ALARM_LIGHT from KA-1:14 to KA-2:A1.",
                "Add wire W207 net RETURN_0V from KA-2:A2 to CB-13:TC2.",
            ],
        },
        {
            "id": "04-remove-relay",
            "title": "Remove unused relay and its incident conductors",
            "expected": removed,
            "actions": [
                "Remove wire W201.",
                "Remove wire W202.",
                "Remove wire W203.",
                "Remove wire W204.",
                "Remove device KA-1.",
            ],
        },
        {
            "id": "05-combined-revision",
            "title": "Complete lockout reroute and alarm relay relocation",
            "expected": combined,
            "actions": [
                "Rewire W102 from K86-1:12 to CB-13:TC1.",
                "Rewire W104 from K86-1:22 to CB-12:TC1.",
                "Move KA-1 to (500, 235) mm.",
            ],
        },
        {
            "id": "06-collision",
            "title": "Requested relay position overlaps feeder trip release",
            "blocked": "device_collision",
            "actions": ["Move KA-1 to (620, 340) mm."],
        },
        {
            "id": "07-conflicting-endpoint",
            "title": "Two contradictory requests for one conductor",
            "blocked": "conflicting_request",
            "actions": [
                "Rewire W102 from K86-1:12 to CB-13:TC1.",
                "Rewire W102 from K86-1:12 to CB-12:TC1.",
            ],
        },
        {
            "id": "08-missing-terminal",
            "title": "Request names a terminal absent from the native block",
            "blocked": "unknown_terminal",
            "actions": ["Rewire W102 from K86-1:12 to CB-13:TC9."],
        },
        {
            "id": "09-stale-bom",
            "title": "Current relay catalog number conflicts with BOM",
            "blocked": "bom_mismatch",
            "stale_bom": True,
            "actions": ["Move KA-1 to (500, 235) mm."],
        },
        {
            "id": "10-outside-enclosure",
            "title": "Requested device location is outside the backplate",
            "blocked": "outside_enclosure",
            "actions": ["Move KA-1 to (850, 235) mm."],
        },
        {
            "id": "11-live-device-removal",
            "title": "Device deletion omits its connected wires",
            "blocked": "incident_wires_remain",
            "actions": ["Remove device KA-1."],
        },
        {
            "id": "12-unmodelled-wire",
            "title": "Native wire endpoint metadata disagrees with actual geometry",
            "blocked": "wire_endpoint_mismatch",
            "bad_geometry": True,
            "actions": ["Move KA-1 to (500, 235) mm."],
        },
        {
            "id": "13-resize-relay",
            "title": "Enlarge relay envelope and move its connected terminal endpoints",
            "expected": resized,
            "actions": ["Resize KA-1 to 100 x 100 mm."],
        },
        {
            "id": "14-reroute-wire",
            "title": "Reroute conductor through an explicit service-clearance path",
            "expected": rerouted,
            "actions": [
                "Reroute W102 via (390, 495); (490, 495); (490, 525); (570, 525)."
            ],
        },
    ]


def build_native_corpus(root):
    """Create controlled revisions plus independent expected output packages."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    sources, cases = [], []

    def register(path, role, revision, key):
        sources.append(
            {
                "id": key,
                "name": path.name,
                "path": path.relative_to(root).as_posix(),
                "role": role,
                "revision": revision,
                "lifecycle": "draft",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        return key

    def package(directory, phase, graph, revision, *, stale_bom=False):
        folder = directory / phase
        folder.mkdir(parents=True, exist_ok=True)
        dxf, bom, pdf = (
            folder / f"E-21-Rev-{revision}.dxf",
            folder / f"BOM-Rev-{revision}.xlsx",
            folder / f"E-21-22-Rev-{revision}.pdf",
        )
        write_native(dxf, graph, revision)
        write_bom(bom, graph, revision, stale_part=stale_bom)
        write_drawing_pdf(pdf, graph, revision)
        key = directory.name + "." + phase
        return {
            "dxf": register(dxf, "drawing", revision, key + ".dxf"),
            "bom": register(bom, "bom", revision, key + ".bom"),
            "pdf": register(pdf, "drawing", revision, key + ".pdf"),
        }

    baseline = baseline_graph()
    for case in scenario_definitions():
        directory = root / case["id"]
        before = copy.deepcopy(baseline)
        if case.get("bad_geometry"):
            before["wires"]["W102"]["points"][-1][1] += 10
        c = package(directory, "baseline-C", baseline, "C")
        d = package(
            directory, "returned-D", before, "D", stale_bom=case.get("stale_bom", False)
        )
        req = directory / "CR-01-returned-changes.pdf"
        write_requests(req, case["title"], case["actions"])
        req_id = register(req, "markup", "R1", case["id"] + ".requests")
        expectation = {
            "status": "blocked" if "blocked" in case else "ready",
            "reason_codes": [case["blocked"]] if "blocked" in case else [],
            "request_actions": case["actions"],
        }
        if "expected" in case:
            result = package(directory, "independent-expected-E", case["expected"], "E")
            graph_file = (
                directory / "independent-expected-E" / "expected-native-graph.json"
            )
            graph_file.write_text(json.dumps(case["expected"], indent=2) + "\n")
            expectation.update(
                graph_file=graph_file.relative_to(root).as_posix(), package=result
            )
        cases.append(
            {
                "id": case["id"],
                "title": case["title"],
                "baseline": [c["dxf"], c["bom"]],
                "target": [d["dxf"], d["bom"]],
                "requirements": [req_id],
                "baseline_preview": c["pdf"],
                "target_preview": d["pdf"],
                "expected": expectation,
            }
        )
    manifest = {
        "schema_version": "rivet.native.fixtures.v1",
        "fictional": True,
        "job_number": JOB,
        "references": REFERENCES,
        "sources": sources,
        "cases": cases,
        "scope": "Explicit terminal-to-terminal wiring and backplate geometry; no inferred protection design or production approval.",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
