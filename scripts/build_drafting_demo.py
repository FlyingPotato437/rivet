"""Build real fictional DXF and Excel sources, then prepare draft Rev D copies.

Run from the repository root with its installed Python environment:
  .venv/bin/python -m scripts.build_drafting_demo --output .data/drafting-demo
Existing source/output directories are never overwritten.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

import ezdxf
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from backend.drafting import DraftRevisionRequest, prepare_revision, sha256_file

DEMO_ORG = "6151b990-82f1-57d4-a559-df777cb047af"
DEMO_ORDER = "24c3cffb-f4ab-4390-b77a-96121b70a10e"


def make_sources(
    destination: Path, *, job_number="DEMO-DRAFTING-001"
) -> tuple[Path, Path, str]:
    destination.mkdir(parents=True, exist_ok=False)
    document = ezdxf.new("R2010")
    document.units = 4  # millimetres; illustrative only
    document.layers.new("EQUIPMENT", dxfattribs={"color": 7})
    document.layers.new("NOTES", dxfattribs={"color": 7})
    model = document.modelspace()
    model.add_lwpolyline([(0, 0), (400, 0), (400, 230), (0, 230)], close=True)
    model.add_text("RIVET / SWITCHBOARD APPROVAL", height=8).set_placement((15, 210))
    model.add_text(f"Job {job_number} / E-10", height=5).set_placement((15, 194))
    model.add_text("SOURCE REV C - NOT RELEASED", height=4).set_placement((15, 180))
    model.add_text("DATA HALL A / FRONT ELEVATION", height=4).set_placement((15, 170))
    model.add_lwpolyline([(35, 40), (245, 40), (245, 163), (35, 163)], close=True)
    model.add_line((35, 75), (245, 75))
    model.add_line((130, 40), (130, 163))
    model.add_line((50, 155), (220, 155))
    model.add_line((95, 155), (95, 132))
    model.add_line((95, 80), (95, 45))
    model.add_line((65, 45), (125, 45))
    block = document.blocks.new("RIVET_BREAKER")
    block.add_lwpolyline([(-28, -20), (28, -20), (28, 20), (-28, 20)], close=True)
    block.add_line((-16, -10), (14, 10))
    breaker = model.add_blockref(
        "RIVET_BREAKER", (95, 106), dxfattribs={"layer": "EQUIPMENT"}
    )
    for tag, value, position in [
        ("EQUIPMENT_TAG", "CB-12", (145, 122)),
        ("TRIP_UNIT", "TU-2500-DEMO", (145, 108)),
        ("NAMEPLATE", "CB-12 / CUSTOMER NAME PENDING", (145, 94)),
    ]:
        breaker.add_attrib(
            tag, value, insert=position, dxfattribs={"height": 4, "layer": "NOTES"}
        )
    for x in (55, 80, 105, 155):
        model.add_circle((x, 50), 3.5)
    model.add_text("BOTTOM CABLE ENTRY", height=3.5).set_placement((46, 61))
    model.add_text("FIELD CONDUIT COORDINATION REQUIRED", height=3).set_placement(
        (43, 34)
    )
    model.add_text("PROJECT REQUIREMENTS", height=4.4).set_placement((265, 154))
    for index, text in enumerate(
        [
            "NEMA 1 / INDOOR / FRONT ACCESS",
            "CONTROL SUPPLY: 120 V AC",
            "CT SHORTING TERMINALS: TB1",
            "TB1-01 THROUGH TB1-04",
            "FINISH: ANSI 61 GRAY",
            "REMOTE INDICATION: EXCLUDED EX-02",
        ]
    ):
        model.add_text(text, height=3.3).set_placement((265, 143 - index * 10))
    model.add_text("REVIEW REFERENCES", height=4.4).set_placement((265, 72))
    for index, text in enumerate(
        [
            "SPEC 26 24 13 / REV 2",
            "DR-17: DRAFT SELECTION ONLY",
            "NO PRODUCTION RELEASE",
        ]
    ):
        model.add_text(text, height=3.3).set_placement((265, 61 - index * 10))
    model.add_line((0, 26), (400, 26))
    model.add_line((252, 0), (252, 26))
    model.add_line((337, 0), (337, 26))
    model.add_text(
        "FICTIONAL REVIEW PACKAGE / NOT FOR FABRICATION", height=3.4
    ).set_placement((12, 17))
    model.add_text(
        "ILLUSTRATIVE GEOMETRY / NO DIMENSIONS OR CLEARANCES ESTABLISHED", height=2.9
    ).set_placement((12, 8))
    model.add_text("E-10 / SOURCE REV C", height=4.2).set_placement((261, 15))
    model.add_text("APPROVAL REVIEW", height=2.8).set_placement((261, 7))
    model.add_text("SHEET 1 OF 1", height=3.5).set_placement((345, 15))
    model.add_text("NOT RELEASED", height=2.8).set_placement((345, 7))
    cad_path = destination / "E-10-Rev-C.dxf"
    document.saveas(cad_path)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "BOM"
    sheet.append(["line_id", "equipment_tag", "part_number", "description", "quantity"])
    sheet.append(
        ["10", "CB-12", "TU-2500-DEMO", "Trip unit - fictional catalog item", 1]
    )
    sheet.append(["20", "CB-12", "CB-12-DEMO", "Breaker body - illustrative only", 1])
    for cell in sheet[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="252A35")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in {"A": 12, "B": 20, "C": 25, "D": 48, "E": 14}.items():
        sheet.column_dimensions[column].width = width
    note = workbook.create_sheet("Read me")
    note.append(["FICTIONAL LOCAL DEMO - NOT A CUSTOMER ORDER OR COMMERCIAL OFFER"])
    note.append(["Draft edit copies need engineering approval before use."])
    note.column_dimensions["A"].width = 100
    bom_path = destination / "BOM-Rev-C.xlsx"
    workbook.save(bom_path)
    return cad_path, bom_path, breaker.dxf.handle


def make_request(
    drawing_path: Path,
    bom_path: Path,
    entity_handle: str,
    *,
    organization_id=DEMO_ORG,
    order_id=DEMO_ORDER,
    job_number="DEMO-DRAFTING-001",
) -> DraftRevisionRequest:
    common = {
        "decision_status": "confirmed_for_draft",
        "decision_record_id": "fictional-engineer-confirmation-001",
    }
    return DraftRevisionRequest.model_validate(
        {
            "organization_id": organization_id,
            "order_id": order_id,
            "job_number": job_number,
            "drawing_number": "E-10",
            "source_revision": "C",
            "target_revision": "D",
            "source_lifecycle": "draft",
            "drawing_sha256": sha256_file(drawing_path),
            "bom_sha256": sha256_file(bom_path),
            "provenance": "local_demo",
            "edits": [
                {
                    **common,
                    "id": "edit-trip-unit",
                    "kind": "cad_attribute",
                    "evidence_id": "Rev-C/comment-8",
                    "reason": "Fictional engineer confirmed the governing trip-unit selection for draft preparation.",
                    "entity_handle": entity_handle,
                    "block_name": "RIVET_BREAKER",
                    "equipment_tag": "CB-12",
                    "attribute_tag": "TRIP_UNIT",
                    "expected_value": "TU-2500-DEMO",
                    "replacement_value": "TU-3000-DEMO",
                },
                {
                    **common,
                    "id": "edit-nameplate",
                    "kind": "cad_attribute",
                    "evidence_id": "Rev-C/comment-9",
                    "reason": "Use the customer-approved text supplied in the fictional markup.",
                    "entity_handle": entity_handle,
                    "block_name": "RIVET_BREAKER",
                    "equipment_tag": "CB-12",
                    "attribute_tag": "NAMEPLATE",
                    "expected_value": "CB-12 / CUSTOMER NAME PENDING",
                    "replacement_value": "CB-12 / DATA HALL A",
                },
                {
                    **common,
                    "id": "edit-bom-line",
                    "kind": "bom_cell",
                    "evidence_id": "Rev-C/comment-10",
                    "reason": "Align the trip-unit BOM line with the confirmed draft drawing selection.",
                    "row_key_column": "line_id",
                    "row_key": "10",
                    "column": "part_number",
                    "expected_value": "TU-2500-DEMO",
                    "replacement_value": "TU-3000-DEMO",
                    "worksheet": "BOM",
                },
            ],
            "layout_tasks": [
                {
                    **common,
                    "id": "layout-breaker",
                    "kind": "layout_task",
                    "evidence_id": "Rev-C/comment-11",
                    "reason": "A layout change needs clearance and routing review by the drafter.",
                    "entity_handle": entity_handle,
                    "block_name": "RIVET_BREAKER",
                    "equipment_tag": "CB-12",
                    "instruction": "Move CB-12 lower in the compartment after checking clearances; no new position has been approved.",
                },
            ],
        }
    )


def build(
    destination: Path,
    *,
    organization_id=DEMO_ORG,
    order_id=DEMO_ORDER,
    job_number="DEMO-DRAFTING-001",
) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    drawing, bom, handle = make_sources(
        destination / "originals", job_number=job_number
    )
    request = make_request(
        drawing,
        bom,
        handle,
        organization_id=organization_id,
        order_id=order_id,
        job_number=job_number,
    )
    (destination / "edit-plan.json").write_text(
        request.model_dump_json(indent=2) + "\n"
    )
    result = prepare_revision(
        request,
        drawing_path=drawing,
        bom_path=bom,
        destination=destination / "draft-Rev-D",
    )
    (destination / "bundle.json").write_text(result.model_dump_json(indent=2) + "\n")
    return result.model_dump(mode="json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".data/drafting-demo"))
    parser.add_argument("--organization-id", type=UUID, default=UUID(DEMO_ORG))
    parser.add_argument("--order-id", type=UUID, default=UUID(DEMO_ORDER))
    parser.add_argument("--job", default="DEMO-DRAFTING-001")
    args = parser.parse_args()
    result = build(
        args.output,
        organization_id=str(args.organization_id),
        order_id=str(args.order_id),
        job_number=args.job,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "report": result["report_path"],
                "artifacts": [a["name"] for a in result["artifacts"]],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
