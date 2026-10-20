"""Build a bounded, clearly fictional commissioning PDU demo. No network calls."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from email.message import EmailMessage
from pathlib import Path

import ezdxf
import pymupdf
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

DISCLAIMER = "FICTIONAL DEMO - NOT A LARSON PRODUCT - NOT FOR FABRICATION OR RELEASE"
W, H = 1224, 792
INK = HexColor("#142b39")
FIELDS = [
    "equipment_tag",
    "description",
    "frame_a",
    "trip_a",
    "part_number",
    "quantity",
    "nameplate",
]
ROWS = [
    {
        "equipment_tag": "CB-01",
        "description": "Primary main, 3-pole / 480 V",
        "frame_a": 150,
        "trip_a": 150,
        "part_number": "RVT-MCCB-150",
        "quantity": 1,
        "nameplate": "480 V INPUT MAIN",
    },
    {
        "equipment_tag": "XF-01",
        "description": "75 kVA / 480 D - 208Y/120 V / 3 PH / 60 Hz",
        "part_number": "RVT-XF-75",
        "quantity": 1,
        "nameplate": "75 KVA TRANSFORMER",
    },
    {
        "equipment_tag": "CB-02",
        "description": "Secondary main, 3-pole / 208 V",
        "frame_a": 250,
        "trip_a": 225,
        "part_number": "RVT-MCCB-250",
        "quantity": 1,
        "nameplate": "208Y/120 V MAIN",
    },
    {
        "equipment_tag": "CB-11",
        "description": "60 A 3P branch assembly / pin-sleeve / 3P+N+G",
        "frame_a": 100,
        "trip_a": 60,
        "part_number": "RVT-BRANCH-PIN60",
        "quantity": 1,
        "nameplate": "208 V OUTLET",
    },
    {
        "equipment_tag": "CB-12",
        "description": "30 A 2P branch assembly / L14-30R / 120/208 V",
        "frame_a": 60,
        "trip_a": 30,
        "part_number": "RVT-BRANCH-L1430",
        "quantity": 1,
        "nameplate": "120/208 V - 30 A",
    },
    {
        "equipment_tag": "CB-13",
        "description": "20 A 1P branch assembly / 5-20R GFCI / 120 V",
        "frame_a": 20,
        "trip_a": 20,
        "part_number": "RVT-BRANCH-GFCI20",
        "quantity": 1,
        "nameplate": "SPARE",
    },
    {
        "equipment_tag": "CB-14",
        "description": "20 A 1P branch assembly / 5-20R GFCI / 120 V",
        "frame_a": 20,
        "trip_a": 20,
        "part_number": "RVT-BRANCH-GFCI20",
        "quantity": 1,
        "nameplate": "SERVICE TOOLS",
    },
]


def frame(c, job, revision, sheet, title, number):
    c.setStrokeColor(INK)
    c.setFillColor(INK)
    c.setLineWidth(0.65)
    c.rect(24, 24, W - 48, H - 48)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(44, 744, "RIVET / PORTABLE POWER SYSTEMS")
    c.setFont("Helvetica", 10)
    c.drawString(44, 724, f"JOB {job}  |  PDU-75  |  FICTIONAL COMMISSIONING PACKAGE")
    c.line(24, 706, W - 24, 706)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(44, 682, title)
    c.line(24, 88, W - 24, 88)
    c.line(904, 24, 904, 88)
    c.line(1084, 24, 1084, 88)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(40, 67, DISCLAIMER)
    c.setFont("Helvetica", 8)
    c.drawString(
        40,
        49,
        "Invented parts. Schematic only; no SCCR, coordination, compliance or engineering approval established.",
    )
    c.drawString(
        40,
        34,
        "DRAWN: DEMO  |  ISSUE: 2026-10-19  |  SCALE: NTS  |  UNITS: mm WHERE SHOWN",
    )
    c.setFont("Helvetica-Bold", 10)
    c.drawString(919, 65, f"REV {revision}")
    c.setFont("Helvetica", 8)
    c.drawString(919, 47, "FOR REVIEW - NOT RELEASED")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(1100, 60, sheet)
    c.setFont("Helvetica", 8)
    c.drawString(1100, 42, f"SHEET {number} OF 3")


def text(c, x, y, value, size=10, bold=False):
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawString(x, y, value)


def drawing(path, job, revision, rows):
    c = canvas.Canvas(str(path), pagesize=(W, H), invariant=1)
    c.setTitle(f"{job} PDU-75 Rev {revision} - fictional demo")
    frame(c, job, revision, "E-01", "SINGLE-LINE DIAGRAM / EQUIPMENT SCHEDULE", 1)
    c.setLineWidth(1.5)
    c.line(90, 600, 180, 600)
    c.rect(180, 584, 32, 32)
    c.line(186, 589, 206, 610)
    c.line(212, 600, 310, 600)
    for x in (331, 353):
        c.circle(x, 600, 21, stroke=1, fill=0)
    c.line(374, 600, 460, 600)
    c.rect(460, 584, 32, 32)
    c.line(466, 589, 486, 610)
    c.line(492, 600, 1110, 600)
    text(c, 70, 629, "480 V / 3 PH / 60 Hz", 11, True)
    text(c, 170, 564, f"CB-01  {rows[0]['trip_a']} AT / 150 AF / 3P", 10, True)
    text(c, 288, 645, "XF-01 / 75 kVA", 11, True)
    text(c, 286, 629, "480 D - 208Y/120 V", 9)
    text(c, 430, 548, "CB-02  225 AT / 250 AF / 3P", 10, True)
    for i, row in enumerate(rows[3:]):
        x = 625 + i * 150
        c.line(x, 600, x, 545)
        c.rect(x - 12, 521, 24, 24)
        c.line(x - 8, 525, x + 8, 541)
        c.line(x, 521, x, 490)
        text(c, x - 35, 474, row["equipment_tag"], 11, True)
        text(c, x - 45, 456, f"{row['trip_a']} AT / {row['frame_a']} AF", 9)
        text(
            c,
            x - 48,
            438,
            ["5W PIN/SLEEVE", "L14-30R", "5-20R GFCI", "5-20R GFCI"][i],
            8,
        )
    text(c, 60, 510, "N: separate neutral bar", 9)
    text(
        c, 60, 494, "PE: enclosure / transformer bonding per final engineered design", 9
    )
    text(
        c,
        60,
        477,
        "Transformer primary FLA ~90 A; secondary FLA ~208 A (reference only)",
        9,
    )
    widths = [80, 350, 75, 75, 80, 390]
    xs = [45]
    for width in widths:
        xs.append(xs[-1] + width)
    y = 390
    c.setFillColor(HexColor("#edf1f3"))
    c.rect(45, y - 25, 1134, 25, fill=1, stroke=0)
    c.setFillColor(INK)
    text(
        c,
        51,
        y - 16,
        "EQUIPMENT / BREAKER RATINGS / QUANTITY / ENGRAVED NAMEPLATE",
        8,
        True,
    )
    for index, row in enumerate(rows):
        y -= 31
        c.line(45, y - 25, 1179, y - 25)
        values = [row["equipment_tag"]]
        if "frame_a" in row:
            values.extend([f"Frame: {row['frame_a']} A", f"Trip: {row['trip_a']} A"])
        values.extend([f"Qty: {row['quantity']}", "Nameplate: " + row["nameplate"]])
        text(c, 51, y - 9, "  |  ".join(values), 9, True)
        text(c, 51, y - 21, row["description"], 8)
    text(
        c,
        45,
        110,
        "All device identities and ratings must be checked against the same-revision native DXF and BOM.",
        9,
    )
    c.showPage()
    frame(c, job, revision, "E-02", "FRONT ELEVATION / CONNECTION PANELS", 2)
    c.rect(150, 196, 530, 422)
    c.line(150, 410, 680, 410)
    c.rect(184, 445, 180, 142)
    c.rect(396, 445, 250, 142)
    text(c, 210, 553, "INPUT ISOLATION", 12, True)
    text(c, 220, 525, "CB-01", 18, True)
    text(c, 211, 498, f"Trip: {rows[0]['trip_a']} A", 12)
    text(c, 440, 550, "SECONDARY PANEL", 12, True)
    text(c, 475, 514, "CB-02", 18, True)
    for i in range(4):
        x = 193 + i * 117
        c.rect(x, 286, 91, 82)
        c.circle(x + 45, 326, 19)
        text(c, x, 270, ["RP-01", "RP-02", "RP-03", "RP-04"][i], 10, True)
    c.rect(178, 215, 476, 27)
    text(c, 190, 225, "TRANSFORMER COMPARTMENT - XF-01 / 75 kVA", 12, True)
    c.line(136, 180, 694, 180)
    c.line(150, 175, 150, 185)
    c.line(680, 175, 680, 185)
    text(c, 345, 161, "1200 NOMINAL", 10)
    text(c, 742, 604, "FIELD CONNECTION INDEX", 13, True)
    for i, line in enumerate(
        [
            "Input: 480 V, 3 phase + equipment ground",
            "RP-01: 60 A pin/sleeve, 208Y/120 V, 3P+N+G",
            "RP-02: 30 A L14-30R, 120/208 V",
            "RP-03: 20 A, 120 V, 5-20R GFCI duplex",
            "RP-04: 20 A, 120 V, 5-20R GFCI duplex",
            "Two lifting pockets shown schematically",
            "No fabrication dimensions / conductor sizes released",
        ]
    ):
        text(c, 742, 574 - i * 29, line, 10)
    text(
        c,
        178,
        131,
        "SKID / ENCLOSURE REPRESENTATION ONLY - NO FABRICATION CLEARANCES VERIFIED",
        9,
    )
    c.showPage()
    frame(c, job, revision, "E-03", "NAMEPLATE / RECEPTACLE SCHEDULE", 3)
    for i, row in enumerate(rows):
        x = 65 + (i % 2) * 560
        y = 600 - (i // 2) * 118
        c.rect(x, y - 60, 510, 72)
        text(c, x + 14, y - 8, row["equipment_tag"], 10, True)
        text(c, x + 370, y - 8, "PLATE NP-" + str(i + 1).zfill(2), 9)
        text(c, x + 14, y - 32, "Nameplate: " + row["nameplate"], 11, True)
    text(
        c,
        70,
        110,
        "Nameplates: literal customer text; verify spelling and circuit identity before manufacture.",
        10,
    )
    c.showPage()
    c.save()


def native(path, job, revision, rows):
    d = ezdxf.new("R2010")
    d.units = 4
    m = d.modelspace()
    for layer in ("ONE_LINE", "DEVICE_DATA", "TITLE_BLOCK"):
        d.layers.new(layer)
    m.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)
    for i, row in enumerate(rows):
        b = d.blocks.new("PDU_" + row["equipment_tag"].replace("-", "_"))
        if row["equipment_tag"] == "XF-01":
            b.add_circle((-3, 0), 5)
            b.add_circle((3, 0), 5)
        else:
            b.add_lwpolyline([(-5, -5), (5, -5), (5, 5), (-5, 5)], close=True)
            b.add_line((-3, -3), (3, 3))
        col = i % 4
        r = i // 4
        x = 15 + col * 100
        y = 185 - r * 88
        symbol_points = [
            (48, 246),
            (115, 246),
            (181, 246),
            (242, 213),
            (286, 213),
            (330, 213),
            (374, 213),
        ]
        ins = m.add_blockref(b.name, (x + 15, y), dxfattribs={"layer": "ONE_LINE"})
        ins.dxf.insert = symbol_points[i]
        ins.add_attrib(
            "EQUIPMENT_TAG",
            row["equipment_tag"],
            (x, y - 13),
            dxfattribs={"height": 3, "layer": "DEVICE_DATA"},
        )
        for j, field in enumerate(FIELDS[1:]):
            if field not in row:
                continue
            value = str(row[field]) + (" A" if field in {"frame_a", "trip_a"} else "")
            ins.add_attrib(
                field.upper(),
                value,
                (x, y - 23 - j * 7),
                dxfattribs={"height": 1.8, "layer": "DEVICE_DATA"},
            )
    for start, end in [
        ((20, 246), (43, 246)),
        ((53, 246), (107, 246)),
        ((123, 246), (176, 246)),
        ((186, 246), (390, 246)),
    ]:
        m.add_line(start, end, dxfattribs={"layer": "ONE_LINE"})
    for x in (242, 286, 330, 374):
        m.add_line((x, 246), (x, 218), dxfattribs={"layer": "ONE_LINE"})
        m.add_line((x, 208), (x, 198), dxfattribs={"layer": "ONE_LINE"})
    m.add_text(
        f"PDU-75 / JOB {job} / E-01 / REV {revision}",
        height=5,
        dxfattribs={"layer": "TITLE_BLOCK"},
    ).set_placement((15, 277))
    m.add_text(
        DISCLAIMER, height=2.3, dxfattribs={"layer": "TITLE_BLOCK"}
    ).set_placement((15, 16))
    m.add_text(
        "Editable equipment data schematic. Separate PDF contains elevation and plate schedule.",
        height=2.2,
    ).set_placement((15, 8))
    d.saveas(path)


def markup(path, drawing_path, root):
    # Raster notes deliberately have no hidden text transcription.
    notes = [
        "C4: XF-01 plate must read\n75 kVA / 480 D - 208Y/120 V\n3 PH / 60 Hz",
        "C5: CB-11 plate: 60 A\n208Y/120 V / 3P+N+G",
    ]
    font = ImageFont.truetype(
        "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf", 34
    )
    doc = pymupdf.open(drawing_path)
    typed = [
        (
            0,
            (56, 195, 455, 255),
            "C1: CB-01 trip must be 125 A, not 150 A. Update drawing and BOM. See spec 2.1.",
        ),
        (
            2,
            (640, 505, 1170, 555),
            "C2: CB-13 nameplate must read COMMISSIONING TOOLS. Update drawing and BOM. See spec 2.2.",
        ),
        (
            0,
            (550, 336, 1160, 384),
            "C3: CB-14 nameplate must read TEST INSTRUMENTS. Update drawing and BOM. See spec 2.3.",
        ),
    ]
    for page, rect, note in typed:
        a = doc[page].add_freetext_annot(
            pymupdf.Rect(rect),
            note,
            fontsize=12,
            text_color=(0.75, 0.1, 0.1),
            fill_color=(1, 0.98, 0.94),
        )
        a.set_info(title="Fictional commissioning reviewer", subject=note.split(":")[0])
        a.update()
    extra = doc.new_page(width=W, height=H)
    extra.insert_text(
        (60, 70), "FICTIONAL DEMO / RETURNED REVIEW NOTES / PDU-75", fontsize=18
    )
    extra.insert_text(
        (60, 102),
        "Handwritten-style review sheet. Read with E-03 nameplates and customer requirements 2.4 / 2.5.",
        fontsize=12,
    )
    for i, note in enumerate(notes):
        image = Image.new("RGB", (880, 190), "#fffdf5")
        draw = ImageDraw.Draw(image)
        draw.multiline_text((16, 12), note, font=font, fill="#a81922", spacing=5)
        p = root / f"hand-note-{i + 1}.png"
        image.save(p)
        extra.insert_image(
            pymupdf.Rect(80, 170 + i * 220, 1100, 370 + i * 220), filename=str(p)
        )
    cloud = doc[2].add_rect_annot(pymupdf.Rect(615, 416, 1142, 492))
    cloud.set_colors(stroke=(0.8, 0.1, 0.1))
    cloud.set_border(width=1.6, clouds=1)
    cloud.update()
    doc.set_metadata(
        {"title": "Fictional demo / customer returned Rev A / five review comments"}
    )
    doc.save(path, garbage=4, deflate=True)
    doc.close()


def spec(path, job):
    c = canvas.Canvas(str(path), pagesize=(612, 792), invariant=1)
    text(c, 42, 744, "RIVET / FICTIONAL DEMO", 18, True)
    text(c, 42, 720, f"{job} / PDU-75 / CUSTOMER REQUIREMENTS R1", 11, True)
    text(c, 42, 690, "Agreed review targets - 19 October 2026", 13, True)
    lines = [
        (
            "1. Service",
            [
                "75 kVA transformer, 480 V delta to 208Y/120 V, 3 PH, 60 Hz.",
                "Portable commissioning power; all part numbers are invented.",
            ],
        ),
        (
            "2.1 Primary main breaker",
            [
                "CB-01",
                "Trip: 125 A",
                "Frame: 150 A",
                "The customer agreed a 125 A primary main trip rating.",
            ],
        ),
        ("2.2 Circuit identification", ["CB-13", "Nameplate: COMMISSIONING TOOLS"]),
        (
            "2.3 Instrument circuit identification",
            ["CB-14", "Nameplate: TEST INSTRUMENTS"],
        ),
        (
            "2.4 Transformer nameplate",
            ["XF-01", "Nameplate: 75 KVA / 480 D - 208Y/120 V / 3 PH / 60 HZ"],
        ),
        (
            "2.5 Three-phase outlet identification",
            ["CB-11", "Nameplate: 60 A / 208Y/120 V / 3P+N+G"],
        ),
    ]
    y = 658
    for heading, values in lines:
        text(c, 42, y, heading, 11, True)
        y -= 19
        for value in values:
            text(c, 56, y, value, 10)
            y -= 17
        y -= 12
    text(
        c,
        42,
        78,
        "FICTIONAL DEMO - NOT A CUSTOMER CONTRACT OR ENGINEERING APPROVAL",
        8,
        True,
    )
    text(
        c,
        42,
        61,
        "Literal review targets only. Protection, compliance and release need an engineer.",
        8,
    )
    c.save()


def build(root, job):
    root.mkdir(parents=True, exist_ok=True)
    revb = copy.deepcopy(ROWS)
    revb[0]["trip_a"] = 125
    revb[6]["nameplate"] = "TEST INSTRUMENTS"
    revb[1]["nameplate"] = "75 KVA / 480 D - 208Y/120 V / 3 PH / 60 HZ"
    revb[3]["nameplate"] = "60 A / 208Y/120 V / 3P+N+G"
    for revision, rows in (("A", ROWS), ("B", revb)):
        drawing(root / f"PDU-75_Rev-{revision}.pdf", job, revision, rows)
        native(root / f"PDU-75_Rev-{revision}.dxf", job, revision, rows)
    markup(root / "PDU-75_Rev-A_customer-markup.pdf", root / "PDU-75_Rev-A.pdf", root)
    spec(root / "PDU-75_customer-requirements.pdf", job)
    data = {
        "job": job,
        "disclaimer": DISCLAIMER,
        "headers": FIELDS,
        "revisions": {"A": ROWS, "B": revb},
    }
    (root / "bom-data.json").write_text(json.dumps(data, indent=2) + "\n")
    key = {
        "fictional": True,
        "comments": [
            {
                "id": f"C{i + 1}",
                "kind": "typed" if i < 3 else "handwritten-style",
                "expected": v,
            }
            for i, v in enumerate(
                [
                    "CB-01 trip 125 A",
                    "CB-13 nameplate COMMISSIONING TOOLS",
                    "CB-14 nameplate TEST INSTRUMENTS",
                    "XF-01 full transformer nameplate",
                    "CB-11 full outlet nameplate",
                ]
            )
        ],
        "missed_in_rev_b": {
            "tag": "CB-13",
            "field": "nameplate",
            "before": "SPARE",
            "required": "COMMISSIONING TOOLS",
        },
        "post_sales_answer": {
            "main_breaker": "CB-01",
            "trip_a": 125,
            "citation": "customer-requirements.pdf, section 2.1",
        },
        "limits": "Synthetic demonstration only; native DXF has editable attribute data, not a manufacturer production CAD project.",
    }
    (root / "answer-key.json").write_text(json.dumps(key, indent=2) + "\n")
    mail = EmailMessage()
    mail["From"] = "Srikanth Samy <srikanthsamy@rivetusa.onmicrosoft.com>"
    mail["To"] = "srikanthsamy@rivetusa.onmicrosoft.com"
    mail["Subject"] = f"{job} / PDU-75 / Rev A returned comments - FICTIONAL DEMO"
    mail.set_content(
        f"Fictional demo, not a real engineering instruction.\n\nPlease review the five marked comments attached for job {job}. The agreed customer requirements are in the accompanying specification. Please check the drawing AND BOM, and prepare a cited response. Do not release for production.\n\nFictional commissioning reviewer"
    )
    mail.add_attachment(
        (root / "PDU-75_Rev-A_customer-markup.pdf").read_bytes(),
        maintype="application",
        subtype="pdf",
        filename="PDU-75_Rev-A_customer-markup.pdf",
    )
    (root / "customer-email.eml").write_bytes(mail.as_bytes())
    files = [
        {
            "file": p.name,
            "bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(root.iterdir())
        if p.is_file()
    ]
    (root / "manifest.json").write_text(
        json.dumps({"fictional": True, "job": job, "files": files}, indent=2) + "\n"
    )
    return {"job": job, "output": str(root), "files": len(files)}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--job", default="PDU-DEMO-261019")
    a = p.parse_args()
    print(json.dumps(build(a.output, a.job)))
