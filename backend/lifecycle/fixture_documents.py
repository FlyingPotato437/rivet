"""Professional fictional drawing/spec package for the local lifecycle replay.

Run with the artifact Python runtime (ReportLab and pypdf), not the API worker.
The drawing is an illustrative approval-review fixture, never a design drawing.
"""

import argparse
import json
from io import BytesIO
from pathlib import Path

COMMENTS = [
    "Confirm that the enclosure is NEMA 1 indoor and all routine service access is from the front.",
    "Confirm bottom cable entry. Identify any field coordination still required before conduit installation.",
    "Confirm the control supply is 120 V AC; our building controls package must not be connected to a 230 V circuit.",
    "Show where the metering CT secondary lands and confirm that a shorting terminal block is included.",
    "Is remote breaker position indication included? Please distinguish the accepted base scope from an extra-price option.",
    "Confirm the specified paint finish so the lineup matches the adjacent indoor equipment.",
    "Confirm which routine factory test documentation will be included in the closeout package. Do not treat this as a witnessed-test booking.",
    "Following fictional engineering decision DR-17, change CB-12's trip-unit text from TU-2500-DEMO to TU-3000-DEMO in draft Rev D.",
    "Change the CB-12 nameplate text to CB-12 / DATA HALL A, matching the fictional approved room designation.",
    "Update BOM line 10 from TU-2500-DEMO to TU-3000-DEMO after the confirmed trip-unit selection.",
    "Move CB-12 lower in the compartment after clearance and routing review. Also add one extra breaker; accepted exception EX-02 excludes that additional device.",
]

ANSWERS = [
    "Specification 26 24 13 clause 2.1 calls for NEMA 1 indoor construction with front service access. No outdoor rating is established by this package.",
    "Clause 2.2 identifies bottom cable entry. The installer must coordinate final conduit positions against the dimensioned installation drawing before installation; this review fixture does not establish those dimensions.",
    "Clause 2.3 states 120 V AC control power. The reply identifies this requirement and does not authorize a wiring change or certify the completed circuit.",
    "Clause 2.4 identifies shorting terminal block TB1 with CT secondary terminals TB1-01 through TB1-04. The released wiring sheet must be checked before field work.",
    "Clause 2.5 and accepted exception EX-02 exclude remote breaker position indication from the base scope. A requested addition requires a separate commercial and engineering decision.",
    "Clause 2.6 specifies ANSI 61 gray finish for the indoor lineup. The exact finish remains subject to the approved project finish schedule.",
    "Clause 3.1 includes the manufacturer's routine functional-test record in closeout. No witnessed test, third-party certification or test date is established by this fixture.",
]

CLAUSES = [
    (
        "2.1",
        "Enclosure and access",
        "NEMA 1 indoor enclosure. Front service access. This fixture establishes no outdoor rating or installed clearances.",
    ),
    (
        "2.2",
        "Cable entry",
        "Bottom cable entry. Installer to coordinate conduit positions against the dimensioned installation drawing before installation.",
    ),
    (
        "2.3",
        "Control power",
        "120 V AC control supply. Building controls interfaces require engineering review against the released wiring diagram.",
    ),
    (
        "2.4",
        "Metering CT terminals",
        "Shorting terminal block TB1 is included. CT secondary termination references: TB1-01, TB1-02, TB1-03 and TB1-04.",
    ),
    (
        "2.5",
        "Remote indication / EX-02",
        "Base scope excludes remote breaker position indication and an additional breaker. Accepted exception EX-02 applies. Any addition requires written scope and price approval.",
    ),
    (
        "2.6",
        "Paint finish",
        "ANSI 61 gray finish for the indoor lineup, subject to the approved project finish schedule.",
    ),
    (
        "3.1",
        "Factory test documents",
        "Include the manufacturer's routine functional-test record in closeout. Witnessed tests, third-party certification and dates are not established here.",
    ),
    (
        "DR-17",
        "Draft selection only",
        "Fictional engineering decision DR-17 confirms TU-3000-DEMO for preparation of draft Rev D. Source Rev C and its BOM still state TU-2500-DEMO. No production release is granted.",
    ),
]


def build(destination: Path, job: str):
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DecodedStreamObject,
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
        colors.HexColor("#17212B"),
        colors.HexColor("#586472"),
        colors.HexColor("#29465E"),
        colors.HexColor("#B84036"),
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
        c.setLineWidth(1)
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
            "Prepared for demonstration only. Illustrative geometry; not an engineering design or fabrication instruction.",
        )
        c.drawRightString(
            width - 48, 44, "No engineering approval or production release implied."
        )

    stream = BytesIO()
    c = canvas.Canvas(stream, pagesize=(width, height))
    c.setTitle(f"{job} - E-10 Rev C returned comments")
    frame(
        c,
        "Switchboard approval review",
        "Returned Rev C / customer comments / drawing and BOM corrections to be reviewed",
        "E-10 | REV C | RETURNED / NOT RELEASED",
    )
    c.setFillColor(blue)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(48, 699, "INDOOR LINEUP / ILLUSTRATIVE FRONT VIEW")
    c.drawString(647, 699, "RETURNED COMMENT REGISTER")
    # Coherent equipment illustration, with dimensions intentionally omitted.
    c.setStrokeColor(ink)
    c.setLineWidth(1.3)
    c.rect(100, 286, 390, 360, stroke=1, fill=0)
    for y in (406, 526):
        c.line(100, y, 490, y)
    c.line(282, 286, 282, 646)
    c.setLineWidth(3)
    c.line(145, 622, 444, 622)
    c.setLineWidth(1.2)
    c.line(207, 622, 207, 574)
    c.rect(177, 540, 60, 34, stroke=1, fill=0)
    c.line(190, 547, 222, 566)
    c.line(207, 540, 207, 492)
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(ink)
    c.drawString(315, 574, "CB-12")
    c.setFont("Helvetica", 10)
    c.drawString(315, 552, "TU-2500-DEMO")
    c.drawString(315, 530, "Nameplate: customer text pending")
    c.drawString(122, 468, "TB1 / CT SHORTING TERMINALS")
    c.setFont("Helvetica", 9)
    c.drawString(122, 448, "TB1-01 | TB1-02 | TB1-03 | TB1-04")
    for x in (155, 207, 259, 311):
        c.circle(x, 315, 9, stroke=1, fill=0)
    c.setFont("Helvetica", 9)
    c.drawString(122, 340, "BOTTOM CABLE ENTRY - FIELD COORDINATE")
    paragraph(
        c,
        "<b>CONSTRUCTION BASIS</b><br/>NEMA 1 indoor / front access<br/>Control power: 120 V AC<br/>Finish: ANSI 61 gray<br/>Remote indication: excluded by EX-02<br/><br/><b>REVIEW LIMITS</b><br/>Geometry is illustrative. No dimensions or clearances are established. Existing Rev C values remain visible until a separate draft copy is prepared.",
        100,
        258,
        430,
    )
    balloons = [
        (506, 626),
        (506, 318),
        (506, 487),
        (506, 450),
        (506, 410),
        (506, 375),
        (506, 230),
        (506, 552),
        (506, 530),
        (506, 200),
        (95, 555),
    ]
    for index, (x, y) in enumerate(balloons, 1):
        c.setFillColor(colors.white)
        c.setStrokeColor(red)
        c.setLineWidth(1.1)
        c.circle(x, y, 10, stroke=1, fill=1)
        c.setFillColor(red)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(x, y - 3, str(index))
    annotation_rects = []
    y = 674
    for index, text in enumerate(COMMENTS, 1):
        c.setFillColor(red)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(647, y - 10, f"{index:02d}")
        h = paragraph(c, text, 675, y, 468, small)
        y -= max(36, h + 12)
        c.setStrokeColor(colors.HexColor("#DFE3E7"))
        c.setLineWidth(0.4)
        c.line(647, y + 5, 1143, y + 5)
        x0, y0 = balloons[index - 1]
        annotation_rects.append([x0 - 10, y0 - 10, x0 + 10, y0 + 10])
    if y < 100:
        raise ValueError("Review comment register exceeded available page height")
    c.save()
    stream.seek(0)
    reader = PdfReader(stream)
    writer = PdfWriter()
    writer.append(reader)
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica-Bold"),
            }
        )
    )
    for index, (text, rectangle) in enumerate(
        zip(COMMENTS, annotation_rects, strict=True), 1
    ):
        annotation = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Text"),
                NameObject("/Rect"): ArrayObject(
                    [FloatObject(value) for value in rectangle]
                ),
                NameObject("/Contents"): TextStringObject(text),
                NameObject("/T"): TextStringObject("Fictional customer reviewer"),
                NameObject("/Subj"): TextStringObject(f"Comment {index}"),
                NameObject("/NM"): TextStringObject(f"rivet-demo-comment-{index}"),
                NameObject("/Name"): NameObject("/Note"),
                NameObject("/Open"): BooleanObject(False),
                NameObject("/F"): NumberObject(4),
            }
        )
        appearance = DecodedStreamObject()
        appearance.set_data(
            (
                "q 1 1 1 rg 0.72 0.25 0.21 RG 1.1 w "
                "20 10 m 20 15.523 15.523 20 10 20 c 4.477 20 0 15.523 0 10 c 0 4.477 4.477 0 10 0 c 15.523 0 20 4.477 20 10 c B "
                f"0.72 0.25 0.21 rg BT /F1 9 Tf {5 if index >= 10 else 7.5} 7 Td ({index}) Tj ET Q"
            ).encode()
        )
        appearance.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject(
                    [
                        NumberObject(0),
                        NumberObject(0),
                        NumberObject(20),
                        NumberObject(20),
                    ]
                ),
                NameObject("/Resources"): DictionaryObject(
                    {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
                ),
            }
        )
        annotation[NameObject("/AP")] = DictionaryObject(
            {NameObject("/N"): writer._add_object(appearance)}
        )
        writer.add_annotation(0, annotation)
    returned = destination / "E-10-Rev-C-Returned-Comments.pdf"
    with returned.open("wb") as output:
        writer.write(output)
    # Specification uses real clauses so source links support useful PM answers.
    spec = destination / "Specification-26-24-13-Rev-2.pdf"
    c = canvas.Canvas(str(spec), pagesize=(width, height))
    c.setTitle(f"{job} - specification 26 24 13 Rev 2")
    frame(
        c,
        "Low-voltage switchboard specification",
        "Fictional agreed requirements and accepted scope / reference for the approval-review replay",
        "26 24 13 | REV 2 | FICTIONAL APPROVED BASIS",
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
        c.setStrokeColor(colors.HexColor("#DFE3E7"))
        c.setLineWidth(0.5)
        c.line(48, y + 8, 1143, y + 8)
    c.save()
    check = PdfReader(str(returned))
    if len(check.pages[0].get("/Annots", [])) != 11:
        raise ValueError("Expected exactly eleven native PDF annotations")
    return {
        "returned_pdf": str(returned),
        "specification_pdf": str(spec),
        "comments": COMMENTS,
        "answers": ANSWERS,
        "clauses": CLAUSES,
        "annotation_rects": annotation_rects,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.job)))


if __name__ == "__main__":
    main()
