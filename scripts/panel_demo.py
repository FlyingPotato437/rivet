"""Fictional industrial control-panel submittal for demos (UL 508A-style panel shop).

Usage:
  .venv/bin/python scripts/panel_demo.py                       # local workspace organization
  .venv/bin/python scripts/panel_demo.py --organization-id UUID

Creates a NEW synthetic order: a returned Rev B drawing whose eight customer
comments are native PDF annotations, the agreed specification, two trusted
local-demo ERP price rows and an accepted-scope record that excludes the surge
protective device. Nothing is approved, priced into a quote, or sent; the order
is ready for Rivet's comment review (types, engineer decisions, added scope).
Everything is fictional.
"""

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.domain.service import scoped
from backend.identity import Identity, current_org, identity_scope
from backend.lifecycle.replay import _pdf_source, _source
from backend.orders.service import new_order
from backend.records.service import ensure
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, now, uid

COMMENTS = [
    "VFD-101 must be 30 HP, not 25 HP, to match the revised pump data sheet. Update drawing and BOM.",
    "Add one spare 20 A breaker, part BKR-20A-1P, in LP-1 for future instrumentation.",
    "Confirm the panel SCCR is at least 65 kA per UL 508A Supplement SB; show it on the nameplate.",
    "Substitute Allen-Bradley PowerFlex 525 for the specified ABB ACS580 drive to match plant standards.",
    "Add a surge protective device on the incoming 480 V line. The accepted scope excludes SPD-1.",
    "Relabel TB-2 terminals to match the customer I/O list, Rev 3.",
    "Is a 10-inch HMI available instead of the 7-inch HMI-1? Provide pricing as an option.",
    "NO EXCEPTION TAKEN on enclosure layout sheet E-02.",
]
CLAUSES = [
    (
        "1.1",
        "Listing",
        "Industrial control panel built and labeled to UL 508A. Panel SCCR to be determined per Supplement SB and marked on the nameplate.",
    ),
    (
        "2.1",
        "Pump drive",
        "VFD-101: ABB ACS580, 25 HP, 480 V, for lift pump P-1. Drive selection follows the pump data sheet in effect at award.",
    ),
    (
        "2.2",
        "Lighting panel",
        "LP-1: 12 single-pole branch breakers, all assigned. No spare breakers are included in the base scope.",
    ),
    (
        "2.3",
        "Surge protection",
        "Surge protective device SPD-1 is excluded from the base scope per accepted exception EX-04.",
    ),
    (
        "2.4",
        "Operator interface",
        "HMI-1: 7-inch color touchscreen. Larger displays are an option at additional cost.",
    ),
    (
        "3.1",
        "Terminals",
        "TB-2 field terminals labeled per the customer I/O list current at award (Rev 2).",
    ),
]
BALLOONS = [
    (500, 600),
    (500, 470),
    (95, 640),
    (500, 560),
    (95, 520),
    (500, 380),
    (500, 320),
    (95, 300),
]


def build_pdfs(destination: Path, job: str):
    """Returned Rev B drawing with native comment annotations, and the agreed specification."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Paragraph

    destination.mkdir(parents=True, exist_ok=True)
    ink, muted, blue, red = (
        colors.HexColor(c) for c in ("#17212B", "#586472", "#29465E", "#B84036")
    )
    width, height = 1191, 842
    style = ParagraphStyle(
        "body", fontName="Helvetica", fontSize=10, leading=14, textColor=ink
    )
    small = ParagraphStyle("small", parent=style, fontSize=8.8, leading=11.4)

    def paragraph(c, text, x, y, available, kind=style):
        p = Paragraph(text, kind)
        _, h = p.wrap(available, 1000)
        p.drawOn(c, x, y - h)
        return h

    def frame(c, title, subtitle, drawing):
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(36, height - 44, "RIVET / FICTIONAL PROJECT PACKAGE")
        c.setFillColor(muted)
        c.setFont("Helvetica", 9)
        c.drawRightString(width - 36, height - 42, "LOCAL DEMO - NOT A CUSTOMER ORDER")
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 24)
        c.drawString(36, height - 80, title)
        c.setFillColor(muted)
        c.setFont("Helvetica", 10)
        c.drawString(36, height - 102, subtitle)
        c.setStrokeColor(blue)
        c.line(36, height - 118, width - 36, height - 118)
        c.setStrokeColor(colors.HexColor("#BBC2C8"))
        c.rect(36, 28, width - 72, 55, stroke=1, fill=0)
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(48, 62, "JOB " + job)
        c.drawString(405, 62, drawing)
        c.setFont("Helvetica", 8.5)
        c.drawString(
            48,
            44,
            "Prepared for demonstration only. Illustrative layout; not an engineering design or fabrication instruction.",
        )
        c.drawRightString(
            width - 48, 44, "No engineering approval or production release implied."
        )

    stream = BytesIO()
    c = canvas.Canvas(stream, pagesize=(width, height))
    c.setTitle(f"{job} - E-01 Rev B returned comments")
    frame(
        c,
        "Lift station pump control panel",
        "Returned Rev B / customer comments to be reviewed",
        "E-01 | REV B | RETURNED / NOT RELEASED",
    )
    c.setFillColor(blue)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(48, 699, "PANEL LAYOUT / ILLUSTRATIVE BACKPLATE")
    c.drawString(647, 699, "RETURNED COMMENT REGISTER")
    c.setStrokeColor(ink)
    c.setLineWidth(1.3)
    c.rect(110, 250, 370, 420, stroke=1, fill=0)  # enclosure
    c.setLineWidth(1)
    parts = [
        (130, 590, 120, 60, "VFD-101", "ABB ACS580 25 HP"),
        (280, 600, 80, 50, "MCB-1", "480 V MAIN"),
        (130, 460, 160, 90, "LP-1", "12 x 1P BRANCH"),
        (310, 470, 150, 70, "PLC-1", "CONTROLLER"),
        (310, 380, 150, 60, "HMI-1", "7-IN TOUCHSCREEN"),
        (130, 280, 330, 50, "TB-2", "FIELD TERMINALS"),
    ]
    for x, y, w, h, tag, note in parts:
        c.rect(x, y, w, h, stroke=1, fill=0)
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(ink)
        c.drawString(x + 8, y + h - 16, tag)
        c.setFont("Helvetica", 8)
        c.setFillColor(muted)
        c.drawString(x + 8, y + 8, note)
    for i in range(12):
        c.line(140 + i * 12, 485, 140 + i * 12, 525)
    for index, (x, y) in enumerate(BALLOONS, 1):
        c.setFillColor(colors.white)
        c.setStrokeColor(red)
        c.setLineWidth(1.1)
        c.circle(x, y, 10, stroke=1, fill=1)
        c.setFillColor(red)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(x, y - 3, str(index))
    y = 674
    for index, text in enumerate(COMMENTS, 1):
        c.setFillColor(red)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(647, y - 10, f"{index:02d}")
        h = paragraph(c, text, 675, y, 468, small)
        y -= max(40, h + 14)
        c.setStrokeColor(colors.HexColor("#DFE3E7"))
        c.setLineWidth(0.4)
        c.line(647, y + 5, 1143, y + 5)
    c.save()
    stream.seek(0)
    writer = PdfWriter()
    writer.append(PdfReader(stream))
    for index, (text, (x, y)) in enumerate(zip(COMMENTS, BALLOONS, strict=True), 1):
        writer.add_annotation(
            0,
            DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Annot"),
                    NameObject("/Subtype"): NameObject("/Text"),
                    NameObject("/Rect"): ArrayObject(
                        [FloatObject(v) for v in (x - 10, y - 10, x + 10, y + 10)]
                    ),
                    NameObject("/Contents"): TextStringObject(text),
                    NameObject("/T"): TextStringObject("Fictional customer reviewer"),
                    NameObject("/Subj"): TextStringObject(f"Comment {index}"),
                    NameObject("/NM"): TextStringObject(f"rivet-demo-comment-{index}"),
                    NameObject("/Name"): NameObject("/Note"),
                    NameObject("/Open"): BooleanObject(False),
                    NameObject("/F"): NumberObject(4),
                }
            ),
        )
    returned = destination / "E-01-Rev-B-Returned-Comments.pdf"
    with returned.open("wb") as output:
        writer.write(output)
    spec = destination / "Panel-Specification-Rev-1.pdf"
    c = canvas.Canvas(str(spec), pagesize=(width, height))
    c.setTitle(f"{job} - control panel specification Rev 1")
    frame(
        c,
        "Pump control panel specification",
        "Fictional agreed requirements and accepted scope",
        "SPEC 40 61 00 | REV 1 | FICTIONAL APPROVED BASIS",
    )
    y = 694
    for clause, title, text in CLAUSES:
        c.setFillColor(blue)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(48, y - 12, clause)
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(122, y - 12, title)
        h = paragraph(c, text, 370, y, 770)
        y -= max(56, h + 20)
    c.save()
    return returned, spec


def build(organization_id=None, number=None):
    number = number or "PCP-DEMO-" + now().strftime("%m%d-%H%M%S")
    with (
        identity_scope(
            Identity(organization_id or current_org(), "Rivet panel demo", "org:admin")
        ),
        Session.begin() as s,
    ):
        order = new_order(
            s,
            {
                "title": "Lift Station 4 · pump control panel",
                "customer": "Harbor Water District · fictional demo",
                "number": number,
                "synthetic": True,
                "category": "Industrial control panel (UL 508A)",
            },
        )
        originals = db.DATA / "panel-demo" / order.organization_id / order.id
        returned, spec = build_pdfs(originals, number)
        document, spans = _pdf_source(s, order, returned, "markup")
        _pdf_source(s, order, spec, "specification", "approved")
        for tag, part, cost, margin in (
            ("BKR-20A-1P", "BKR-20A-1P", "85.00", "30.00"),
            ("SPD-1", "SPD-480-3P", "1450.00", "28.00"),
        ):
            _source(
                s,
                order,
                f"ERP-price-{tag}.json",
                {
                    "tag": tag,
                    "part_number": part,
                    "unit_cost": cost,
                    "currency": "USD",
                    "gross_margin_percent": margin,
                },
                kind="erp_price",
            )
        _source(
            s,
            order,
            "Accepted-scope-EX-04.json",
            {
                "scope_tag": "SPD-1",
                "baseline_quantity": "0",
                "billable_scope_tag": "SPD-1",
                "billable": True,
                "notice": "Fictional contract: surge protective device SPD-1 excluded by EX-04; adding it is billable scope.",
            },
            kind="accepted_exception",
            release="approved",
        )
        # The shared replay helper labels sources as Rev C; this package is Rev B.
        for doc in s.scalars(
            scoped(Document).where(Document.project_id == order.project_id)
        ):
            doc.meta = {**doc.meta, "revision_label": "B"}
        by_number = {
            int(span.location["annotation_id"].rsplit("-", 1)[-1]): span
            for span in spans
            if span.location.get("annotation_id")
        }
        if len(by_number) != len(COMMENTS):
            raise ValueError(
                "The PDF parser did not recover every native comment annotation"
            )
        stamp = now().isoformat()
        comments = [
            {
                "id": uid(),
                "number": str(i),
                "revision": "B",
                "text": by_number[i].text,
                "original_text": by_number[i].text,
                "author": "Fictional customer reviewer",
                "authored_at": stamp,
                "document_id": document.id,
                "source_ids": [by_number[i].id],
                "source_page": 1,
                "target_source_id": "",
                "linked_email_id": "",
                "status": "open",
                "response": "",
                "responder": "",
                "reviewed": False,
                "confidence": "Local demo",
                "flags": [],
                "created_at": stamp,
                "updated_at": stamp,
                "origin": "local_demo",
            }
            for i in range(1, len(COMMENTS) + 1)
        ]
        record = ensure(s, order)
        record.data = {**record.data, "comments": comments}
        record.version += 1
        return {
            "order_id": order.id,
            "number": number,
            "organization_id": order.organization_id,
            "comments": len(comments),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id")
    parser.add_argument("--number")
    args = parser.parse_args()
    print(json.dumps(build(args.organization_id, args.number)))


if __name__ == "__main__":
    main()
