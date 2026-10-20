"""Reproducible fictional engineering files for equipment revision validation.

These are intentionally readable engineering artifacts, not text masquerading as a
drawing. The independent expected findings at the end are written by hand. This
module never imports the extractor or touches a database, mailbox, or live order.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import ezdxf
import pymupdf
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

JOB = "RVT-DEMO-261017"
FAMILY = "MSB-1 / 480Y/277 V / 3 PH / 4 W / 2500 A BUS"
DISCLAIMER = "FICTIONAL ENGINEERING TEST PACKAGE - NOT FOR FABRICATION OR RELEASE"
LABELS = {
    "frame_a": "Frame",
    "trip_a": "Trip",
    "trip_unit": "Trip unit",
    "part_number": "Part number",
    "quantity": "Quantity",
    "nameplate": "Nameplate",
    "control_voltage_v": "Control voltage",
    "interrupting_ka": "Interrupting",
    "voltage_v": "Rated voltage",
    "current_type": "Current type",
    "rating_standard": "Rating standard",
    "frequency_hz": "Frequency",
}
HEADERS = ["equipment_tag", *LABELS]
BASELINE = [
    {
        "equipment_tag": "CB-12",
        "frame_a": 2500,
        "trip_a": 2000,
        "trip_unit": "TU-2500",
        "part_number": "RVT-ACB-25",
        "quantity": 1,
        "nameplate": "CUSTOMER NAME PENDING",
        "control_voltage_v": 120,
        "interrupting_ka": 65,
        "voltage_v": 480,
        "current_type": "AC",
        "rating_standard": "UL489",
        "frequency_hz": 60,
    },
    {
        "equipment_tag": "CB-14",
        "frame_a": 800,
        "trip_a": 600,
        "trip_unit": "TU-600",
        "part_number": "RVT-MCCB-08",
        "quantity": 1,
        "nameplate": "PUMP 3",
        "control_voltage_v": 120,
        "interrupting_ka": 65,
        "voltage_v": 480,
        "current_type": "AC",
        "rating_standard": "UL489",
        "frequency_hz": 60,
    },
    {
        "equipment_tag": "CB-16",
        "frame_a": 400,
        "trip_a": 250,
        "trip_unit": "TU-250",
        "part_number": "RVT-MCCB-04",
        "quantity": 1,
        "nameplate": "SPARE",
        "control_voltage_v": 120,
        "interrupting_ka": 65,
        "voltage_v": 480,
        "current_type": "AC",
        "rating_standard": "UL489",
        "frequency_hz": 60,
    },
]
COMPLETE = copy.deepcopy(BASELINE)
COMPLETE[0].update(trip_unit="TU-3000", nameplate="DATA HALL A")
REQUIRED = [
    {"equipment_tag": "CB-12", "trip_unit": "TU-3000", "nameplate": "DATA HALL A"}
]


def _display(field: str, value: Any, *, normalized_units: bool = False) -> str:
    if field in {"frame_a", "trip_a"}:
        if normalized_units and field == "frame_a" and value == 2500:
            return "2.5 kA"
        return f"{value} A"
    if field in {"control_voltage_v", "voltage_v"}:
        return f"{value} V"
    if field == "interrupting_ka":
        return f"{value} kA"
    if field == "frequency_hz":
        return f"{value} Hz"
    return str(value)


def _frame(
    c: canvas.Canvas, revision: str, title: str, sheet: str, *, width=1224, height=792
) -> None:
    c.setStrokeColor(colors.HexColor("#18242F"))
    c.setLineWidth(0.75)
    c.rect(24, 24, width - 48, height - 48)
    c.setFillColor(colors.HexColor("#18242F"))
    c.setFont("Helvetica-Bold", 19)
    c.drawString(44, height - 57, "RIVET / FICTIONAL SWITCHBOARD WORKS")
    c.setFont("Helvetica", 10)
    c.drawString(
        44, height - 76, f"{JOB}   |   NORTHLINE WATER RECLAMATION - DEMONSTRATION ONLY"
    )
    c.line(24, height - 94, width - 24, height - 94)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(44, height - 120, title)
    c.setFont("Helvetica", 10)
    c.drawString(44, height - 138, FAMILY)
    c.line(24, 90, width - 24, 90)
    for x in (width - 410, width - 160):
        c.line(x, 24, x, 90)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, 70, DISCLAIMER)
    c.setFont("Helvetica", 8)
    c.drawString(
        40,
        53,
        "Illustrative topology. No protection coordination, ratings selection or clearance verification implied.",
    )
    c.drawString(
        40,
        38,
        "DRAWN: DEMO  /  CHECKED: TEST AUTHOR  /  ISSUE: 2026-10-17  /  SCALE: NTS",
    )
    c.setFont("Helvetica-Bold", 10)
    c.drawString(width - 395, 69, "REVISION / APPROVAL STATUS")
    c.setFont("Helvetica", 9)
    c.drawString(width - 395, 51, f"{revision} / FOR APPROVAL - NOT RELEASED")
    c.drawString(width - 395, 36, "REVIEW DOES NOT AUTHORIZE PRODUCTION")
    c.setFont("Helvetica-Bold", 17)
    c.drawString(width - 143, 62, sheet)
    c.setFont("Helvetica", 9)
    c.drawString(width - 143, 42, "SHEET 1 OF 1")


def write_drawing_pdf(
    path: Path,
    rows: list[dict],
    revision: str,
    *,
    normalized_units=False,
    extra_note: str | None = None,
) -> None:
    """One-line topology with explicit per-device engineering attributes."""
    c = canvas.Canvas(str(path), pagesize=(1224, 792), invariant=1)
    c.setTitle(f"{JOB} - E-10 Rev {revision} - fictional one-line")
    _frame(c, revision, "ONE-LINE DIAGRAM AND BREAKER DATA", "E-10")
    c.setStrokeColor(colors.HexColor("#142B39"))
    c.setFillColor(colors.HexColor("#142B39"))
    c.setLineWidth(2.8)
    c.line(90, 565, 1130, 565)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(90, 582, "MSB-1 - MAIN BUS / ILLUSTRATIVE CONNECTIONS")
    column_width = 1110 / max(3, len(rows))
    for idx, row in enumerate(rows):
        x = 70 + idx * column_width
        center = x + column_width / 2 - 20
        c.setLineWidth(1.1)
        c.line(center, 565, center, 526)
        c.rect(center - 19, 487, 38, 39)
        c.line(center - 12, 495, center + 12, 519)
        c.line(center, 487, center, 457)
        c.line(center - 10, 465, center, 457)
        c.line(center + 10, 465, center, 457)
        c.setFont("Helvetica", 8)
        c.drawCentredString(center, 442, "FEEDER / 3 POLE")
        c.setFillColor(colors.HexColor("#F2F5F7"))
        c.rect(x - 14, 184, column_width - 32, 238, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#142B39"))
        c.setFont("Helvetica-Bold", 16)
        c.drawString(x, 394, row["equipment_tag"])
        c.setFont("Helvetica", 9 if len(rows) > 3 else 10)
        y = 369
        for field, label in LABELS.items():
            if field in row:
                c.drawString(
                    x,
                    y,
                    f"{label}: {_display(field, row[field], normalized_units=normalized_units)}",
                )
                y -= min(21, 185 / len(LABELS))
    c.setFont("Helvetica", 9)
    c.drawString(
        44,
        148,
        "1. Breaker symbols and conductor routing are schematic; no installation dimensions are established.",
    )
    c.drawString(
        44,
        133,
        "2. Read equipment data against the same-revision BOM and the project requirement schedule.",
    )
    if extra_note:
        # A review-layer statement is deliberately outside the engineering data.
        c.setFillColor(colors.HexColor("#9B3734"))
        c.setFont("Helvetica-Bold", 9)
        c.drawString(44, 113, "REVIEW COMMENT: " + extra_note)
    c.showPage()
    c.save()


def write_dxf(
    path: Path, rows: list[dict], revision: str, *, normalized_units=False
) -> None:
    doc = ezdxf.new("R2010")
    doc.units = 4
    for layer in ("ONE_LINE", "DEVICE_DATA", "TITLE_BLOCK"):
        doc.layers.new(layer, dxfattribs={"color": 7})
    model = doc.modelspace()
    model.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)
    model.add_text("RIVET / FICTIONAL SWITCHBOARD WORKS", height=5).set_placement(
        (15, 280)
    )
    model.add_text(FAMILY, height=3).set_placement((15, 268))
    model.add_line((30, 230), (390, 230), dxfattribs={"layer": "ONE_LINE"})
    block = doc.blocks.new("RIVET_ACB")
    block.add_lwpolyline([(-8, -8), (8, -8), (8, 8), (-8, 8)], close=True)
    block.add_line((-5, -5), (5, 5))
    column_width = 390 / max(3, len(rows))
    for idx, row in enumerate(rows):
        x = 15 + idx * column_width
        insert = model.add_blockref(
            "RIVET_ACB", (x + 45, 206), dxfattribs={"layer": "ONE_LINE"}
        )
        model.add_line((x + 45, 230), (x + 45, 214))
        model.add_line((x + 45, 198), (x + 45, 182))
        insert.add_attrib(
            "EQUIPMENT_TAG",
            row["equipment_tag"],
            (x, 167),
            dxfattribs={"height": 4, "layer": "DEVICE_DATA"},
        )
        for index, field in enumerate(LABELS):
            if field in row:
                insert.add_attrib(
                    field.upper(),
                    _display(field, row[field], normalized_units=normalized_units),
                    (x, 153 - index * 11),
                    dxfattribs={"height": 2.5, "layer": "DEVICE_DATA"},
                )
    model.add_line((0, 30), (420, 30))
    model.add_text(
        DISCLAIMER, height=3, dxfattribs={"layer": "TITLE_BLOCK"}
    ).set_placement((10, 19))
    model.add_text(
        f"E-10 / REV {revision} / JOB {JOB} / SCALE NTS",
        height=3,
        dxfattribs={"layer": "TITLE_BLOCK"},
    ).set_placement((10, 8))
    doc.saveas(path)


def write_bom(
    path: Path, rows: list[dict], revision: str, *, formula_unknown=False
) -> None:
    """Actual workbook, with uncached formula support for the fail-closed case."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Equipment BOM"
    ws.append(HEADERS)
    for row in rows:
        ws.append([row.get(field, "") for field in HEADERS])
    if formula_unknown:
        ws.cell(2, HEADERS.index("trip_unit") + 1, '="TU-3000"')
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="142B39")
        cell.font = Font(color="FFFFFF", bold=True, size=10)
        cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 27
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Calibri", size=11, color="142B39")
            cell.alignment = Alignment(vertical="center")
            if cell.row % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F2F5F7")
        ws.row_dimensions[row[0].row].height = 24
    for index, field in enumerate(HEADERS, start=1):
        ws.column_dimensions[ws.cell(1, index).column_letter].width = (
            30 if field == "nameplate" else 20
        )
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.print_options.horizontalCentered = True
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.oddHeader.center.text = f"{JOB} | EQUIPMENT BILL OF MATERIALS | REV {revision}"
    ws.oddFooter.center.text = DISCLAIMER
    readme = wb.create_sheet("Issue information")
    for values in [
        (DISCLAIMER,),
        ("Job", JOB),
        ("Revision", revision),
        ("Status", "For approval - not released"),
        (
            "Catalog",
            "All RVT-* part identifiers are invented; no manufacturer compatibility is implied.",
        ),
        ("Authority", "Test data only. Not an engineering or contractual decision."),
    ]:
        readme.append(values)
    readme.column_dimensions["A"].width = 28
    readme.column_dimensions["B"].width = 108
    wb.save(path)
    if not formula_unknown:
        with path.with_suffix(".csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerows(rows)


def write_requirement_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=(1224, 792), invariant=1)
    c.setTitle(f"{JOB} - accepted requirement schedule - fictional")
    _frame(c, "R1", "PROJECT REQUIREMENT SCHEDULE / CONTROLLED TEST TARGET", "S-01")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(65, 560, "CB-12")
    c.setFont("Helvetica", 13)
    c.drawString(65, 530, "Trip unit: TU-3000")
    c.drawString(65, 504, "Nameplate: DATA HALL A")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(65, 430, "VERIFICATION SCOPE")
    c.setFont("Helvetica", 11)
    for index, line in enumerate(
        [
            "Carry both values into the actual one-line equipment data and the same-revision equipment BOM.",
            "Retain all previously listed devices unless a separate approved deletion is provided.",
            "This fixture contains no deletion instruction and no approved tag rename.",
            "A response or review comment saying 'all fixed' is not evidence that either source changed.",
            "A matching narrow-field check is not a protection study, design approval or release authorization.",
        ]
    ):
        c.drawString(65, 403 - index * 24, line)
    c.setFillColor(colors.HexColor("#F2F5F7"))
    c.rect(65, 165, 1090, 75, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#142B39"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(82, 215, "Independent test target")
    c.setFont("Helvetica", 10)
    c.drawString(
        82,
        194,
        "Expected values and expected failures are declared by the fixture author before extraction is run.",
    )
    c.showPage()
    c.save()


def write_markup_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=(1224, 792), invariant=1)
    _frame(c, "D", "RETURNED REVIEW COMMENTS / NON-AUTHORITATIVE STATEMENTS", "R-01")
    c.setFont("Helvetica-Bold", 15)
    c.drawString(
        65, 553, "Drafting response: All comments addressed. Drawing and BOM corrected."
    )
    c.setFont("Helvetica", 12)
    c.drawString(65, 510, "CB-12 Trip unit: TU-3000")
    c.drawString(65, 485, "CB-12 Nameplate: DATA HALL A")
    c.drawString(
        65,
        430,
        "These values occur in a response/comment document only, not necessarily in equipment data.",
    )
    c.drawString(
        65,
        404,
        "Do not use this text as proof of a drawing or BOM change. Inspect the engineering artifacts.",
    )
    c.showPage()
    c.save()


def write_unreadable_pdf(path: Path, source: Path) -> None:
    """Image-only drawing with no embedded OCR text, deliberately degraded."""
    document = pymupdf.open(source)
    page = document[0]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(0.35, 0.35), alpha=False)
    c = canvas.Canvas(str(path), pagesize=(1224, 792), invariant=1)
    c.setTitle("Fictional image-only low-resolution returned drawing")
    c.drawImage(
        ImageReader(__import__("io").BytesIO(pix.tobytes("png"))), 0, 0, 1224, 792
    )
    c.showPage()
    c.save()
    document.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_demo_corpus(root: Path | str) -> dict[str, Any]:
    """Write reproducible, explicitly fictional files; return a portable manifest.

    Safe to call in a pytest ``tmp_path``. Caller owns the destination. Existing
    generated files in it are replaced, but no database or external system is used.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, Any]] = []

    def register(path: Path, role: str, revision: str, key: str) -> str:
        source = {
            "id": key,
            "name": path.name,
            "path": path.relative_to(root).as_posix(),
            "role": role,
            "revision": revision,
            "lifecycle": "draft",
            "sha256": _sha(path),
        }
        sources.append(source)
        return key

    def package(
        folder: str,
        revision: str,
        drawing_rows: list[dict],
        bom_rows: list[dict],
        *,
        units=False,
        note=None,
        formula=False,
    ) -> dict:
        directory = root / folder
        directory.mkdir(parents=True, exist_ok=True)
        pdf = directory / f"E-10-Rev-{revision}.pdf"
        dxf = directory / f"E-10-Rev-{revision}.dxf"
        xlsx = directory / f"BOM-Rev-{revision}.xlsx"
        write_drawing_pdf(
            pdf, drawing_rows, revision, normalized_units=units, extra_note=note
        )
        write_dxf(dxf, drawing_rows, revision, normalized_units=units)
        write_bom(xlsx, bom_rows, revision, formula_unknown=formula)
        result = {
            "pdf": register(pdf, "drawing", revision, folder + ".drawing.pdf"),
            "dxf": register(dxf, "drawing", revision, folder + ".drawing.dxf"),
            "xlsx": register(xlsx, "bom", revision, folder + ".bom.xlsx"),
        }
        if not formula:
            result["csv"] = register(
                xlsx.with_suffix(".csv"), "bom", revision, folder + ".bom.csv"
            )
        return result

    c = package("01-baseline-C", "C", copy.deepcopy(BASELINE), copy.deepcopy(BASELINE))
    stale_bom = copy.deepcopy(COMPLETE)
    stale_bom[0]["trip_unit"] = "TU-2500"
    d = package(
        "02-partial-D",
        "D",
        [copy.deepcopy(COMPLETE[0]), copy.deepcopy(COMPLETE[2])],
        stale_bom,
        units=True,
        note="All comments addressed. Drawing and BOM corrected.",
    )
    e = package(
        "03-complete-E",
        "E",
        copy.deepcopy(COMPLETE),
        copy.deepcopy(COMPLETE),
        units=True,
    )
    title = package(
        "04-titleblock-only", "D", copy.deepcopy(BASELINE), copy.deepcopy(BASELINE)
    )
    duplicate = copy.deepcopy(COMPLETE)
    duplicate.insert(1, {**copy.deepcopy(COMPLETE[0]), "trip_unit": "TU-2500"})
    dup = package("05-conflicting-tags", "E", duplicate, copy.deepcopy(COMPLETE))
    formula = package(
        "06-formula-unknown",
        "E",
        copy.deepcopy(COMPLETE),
        copy.deepcopy(COMPLETE),
        formula=True,
    )
    unrelated = copy.deepcopy(COMPLETE)
    unrelated[2]["control_voltage_v"] = 230
    ur = package("07-unrelated-change", "E", unrelated, unrelated)
    rename = copy.deepcopy(COMPLETE)
    rename[1]["equipment_tag"] = "CB-114"
    rn = package("08-unapproved-rename", "E", rename, rename)
    support = root / "00-requirements"
    support.mkdir(exist_ok=True)
    req_pdf = support / "S-01-Accepted-Requirements.pdf"
    write_requirement_pdf(req_pdf)
    req = register(req_pdf, "specification", "R1", "requirements.pdf")
    markup_pdf = support / "R-01-Response-All-Fixed.pdf"
    write_markup_pdf(markup_pdf)
    markup = register(markup_pdf, "markup", "D", "response.markup")
    scan_dir = root / "09-unreadable-scan"
    scan_dir.mkdir(exist_ok=True)
    scan_pdf = scan_dir / "E-10-Rev-E-Scanned.pdf"
    write_unreadable_pdf(scan_pdf, root / "03-complete-E" / "E-10-Rev-E.pdf")
    scan = register(scan_pdf, "drawing", "E", "unreadable.drawing")

    def pair(p: dict, kind="pdf") -> list[str]:
        return [p[kind], p["xlsx"]]

    # Golden expectations below are explicit human-authored outcomes. They are
    # never read back from the generated files or inferred by the checker.
    cases = [
        {
            "id": "partial-D",
            "baseline": pair(c),
            "target": [*pair(d), markup],
            "requirements": [req],
            "expected": {
                "satisfied": False,
                "drawing_bom_conflicts": ["CB-12.trip_unit"],
                "missing_from_drawing": ["CB-14"],
                "changed_in_drawing": ["CB-12.trip_unit", "CB-12.nameplate"],
                "unchanged_after_unit_normalization": ["CB-12.frame_a"],
                "comment_claim_is_not_evidence": True,
            },
        },
        {
            "id": "complete-E",
            "baseline": pair(c),
            "target": pair(e),
            "requirements": [req],
            "expected": {
                "satisfied": True,
                "drawing_bom_conflicts": [],
                "missing_from_drawing": [],
                "changed_in_drawing": ["CB-12.trip_unit", "CB-12.nameplate"],
                "unchanged_after_unit_normalization": ["CB-12.frame_a"],
            },
        },
        {
            "id": "native-DXF-partial",
            "baseline": pair(c, "dxf"),
            "target": pair(d, "dxf"),
            "requirements": [req],
            "expected": {
                "satisfied": False,
                "drawing_bom_conflicts": ["CB-12.trip_unit"],
                "missing_from_drawing": ["CB-14"],
            },
        },
        {
            "id": "native-DXF-complete",
            "baseline": pair(c, "dxf"),
            "target": pair(e, "dxf"),
            "requirements": [req],
            "expected": {
                "satisfied": True,
                "drawing_bom_conflicts": [],
                "missing_from_drawing": [],
            },
        },
        {
            "id": "titleblock-only",
            "baseline": pair(c),
            "target": pair(title),
            "requirements": [req],
            "expected": {
                "satisfied": False,
                "changed_in_drawing": [],
                "unmet": ["CB-12.trip_unit", "CB-12.nameplate"],
            },
        },
        {
            "id": "comments-only",
            "baseline": pair(c),
            "target": [*pair(title), markup],
            "requirements": [req],
            "expected": {
                "satisfied": False,
                "changed_in_drawing": [],
                "unmet": ["CB-12.trip_unit", "CB-12.nameplate"],
                "comment_claim_is_not_evidence": True,
            },
        },
        {
            "id": "conflicting-tags",
            "baseline": pair(c),
            "target": pair(dup),
            "requirements": [req],
            "expected": {"satisfied": False, "ambiguous": ["CB-12.trip_unit"]},
        },
        {
            "id": "formula-unknown",
            "baseline": pair(c),
            "target": pair(formula),
            "requirements": [req],
            "expected": {
                "satisfied": False,
                "unknown": ["CB-12.trip_unit"],
                "formula_cell": "Equipment BOM!D2",
            },
        },
        {
            "id": "unreadable-scan",
            "baseline": pair(c),
            "target": [scan, e["xlsx"]],
            "requirements": [req],
            "expected": {
                "satisfied": False,
                "requires_transcription": True,
                "drawing_coverage": "unknown",
            },
        },
        {
            "id": "unrelated-change",
            "baseline": pair(c),
            "target": pair(ur),
            "requirements": [req],
            "expected": {
                "satisfied": False,
                "unrequested_changes": ["CB-16.control_voltage_v"],
                "expected_control_voltage_v": 120,
            },
        },
        {
            "id": "unapproved-rename",
            "baseline": pair(c),
            "target": pair(rn),
            "requirements": [req],
            "expected": {
                "satisfied": False,
                "deleted_tags": ["CB-14"],
                "added_tags": ["CB-114"],
                "automatic_alias": False,
            },
        },
        {
            "id": "csv-complete",
            "baseline": [c["pdf"], c["csv"]],
            "target": [e["pdf"], e["csv"]],
            "requirements": [req],
            "expected": {
                "satisfied": True,
                "drawing_bom_conflicts": [],
                "missing_from_drawing": [],
            },
        },
    ]
    manifest = {
        "schema_version": 1,
        "job_number": JOB,
        "fictional": True,
        "warning": DISCLAIMER,
        "validation_claim": "Functional test corpus, not manufacturer-validated engineering or measured extraction accuracy.",
        "independent_expected_equipment": {
            "baseline_C": copy.deepcopy(BASELINE),
            "complete_E": copy.deepcopy(COMPLETE),
        },
        "independent_requirements": copy.deepcopy(REQUIRED),
        "sources": sources,
        "cases": cases,
        "public_layout_references": [
            {
                "publisher": "City of Alachua",
                "url": "https://www.cityofalachua.com/DocumentCenter/View/1321/Addendum-Number-1-w-Attachments?bidId=",
                "use": "General approval title-block, one-line and equipment-callout conventions; no reproduced engineering data.",
            },
            {
                "publisher": "Antelope Valley Transit Authority",
                "url": "https://www.avta.com/downloads/meetings/bod/2018/042418-agenda.pdf",
                "use": "Returned submittal and image-only drawing failure conventions; no reproduced engineering data.",
            },
        ],
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (root / "README.txt").write_text(
        "RIVET EQUIPMENT REVISION TEST CORPUS\n\n" + DISCLAIMER + "\n\n"
        "Start with 01-baseline-C. Compare 02-partial-D: the drawing changes but the BOM is stale, and a feeder disappears.\n"
        "03-complete-E satisfies only the declared narrow fields. It is not a design or production approval.\n"
        "The review response saying 'all fixed' is intentionally untrue for D. Check actual equipment data.\n"
        "Use manifest.json for source roles, hashes, revision pairs and independently declared expected findings.\n"
        "PDFs, DXFs, CSVs and XLSX files are real formats. Part identifiers and all project data are invented.\n",
        encoding="utf-8",
    )
    return manifest
