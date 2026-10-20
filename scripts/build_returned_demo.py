"""Build a fictional returned drawing with three native PDF comments.

Run with reportlab and pypdf installed. Does not change live sources or records.
"""

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Rectangle
from pypdf.generic import ArrayObject, FloatObject, NameObject, TextStringObject
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

root = Path(__file__).resolve().parents[1]
(root / "tmp/pdfs").mkdir(parents=True, exist_ok=True)
(root / "output/pdf").mkdir(parents=True, exist_ok=True)
base = root / "tmp/pdfs/returned-base.pdf"
out = root / "output/pdf/DEMO-OUTLOOK-001-E-10-Rev-C-Returned.pdf"
c = canvas.Canvas(str(base), pagesize=(1008, 720))
c.setTitle("DEMO-OUTLOOK-001 | E-10 Rev C | Returned customer comments")


def text(x, y, s, size=12, bold=False, color="#28313d"):
    c.setFillColor(HexColor(color))
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawString(x, y, s)


def line(x1, y1, x2, y2, color="#a7aeb8", width=1):
    c.setStrokeColor(HexColor(color))
    c.setLineWidth(width)
    c.line(x1, y1, x2, y2)


c.setFillColor(HexColor("#f6f7f9"))
c.rect(0, 0, 1008, 720, fill=1, stroke=0)
text(36, 678, "RIVET / FICTIONAL CUSTOMER RETURN", 11, True)
text(36, 643, "Breaker review - returned comments", 27, True)
text(36, 618, "Job DEMO-OUTLOOK-001  |  Sheet E-10  |  Revision C", 13)
c.setStrokeColor(HexColor("#a0564a"))
c.roundRect(774, 627, 196, 58, 4, stroke=1, fill=0)
text(794, 662, "REVISE AND RESUBMIT", 11, True, "#954538")
text(794, 642, "Draft - not released", 11, False, "#954538")
line(36, 596, 972, 596)
text(48, 567, "01  SIMPLIFIED ONE-LINE", 11, True)
# vector electrical symbol, deliberately simplified
line(94, 502, 512, 502, "#35414e", 4)
text(95, 518, "Incoming bus - illustrative only", 10)
line(304, 502, 304, 442, "#35414e", 2)
c.setStrokeColor(HexColor("#35414e"))
c.setFillColor(HexColor("#ffffff"))
c.rect(249, 354, 110, 88, stroke=1, fill=1)
text(278, 411, "CB-12", 15, True)
text(269, 383, "Main breaker", 11)
line(304, 354, 304, 276, "#35414e", 2)
line(270, 276, 338, 276, "#35414e", 2)
text(390, 413, "TRIP UNIT", 10, True)
text(390, 389, "TU-3000-DEMO", 16, True)
text(390, 367, "Proposed in Rev C", 10)
line(359, 398, 376, 398, "#35414e", 1)
text(90, 231, "Remote indication module: proposed in this revision", 11)
text(90, 209, "This illustrative symbol contains no construction dimensions.", 10)
line(590, 560, 590, 186)
text(622, 567, "02  REVIEW BASIS", 11, True)
for y, label, value in [
    (527, "Accepted order scope", "TU-2500-DEMO"),
    (468, "Released drawing B", "TU-2500-DEMO"),
    (409, "SharePoint specification Rev 2", "TU-3000-DEMO"),
    (350, "Current drawing C", "Draft - not released"),
]:
    text(622, y, label, 11)
    text(622, y - 22, value, 16, True)
text(622, 281, "No technical resolution is approved by this return.", 10)
text(622, 261, "No price, delivery date or production release is authorized.", 10)
line(36, 178, 972, 178)
text(48, 151, "RETURN NOTES", 11, True)
text(
    48,
    129,
    "Customer comments are attached as native PDF markups beside the affected items.",
    11,
)
text(
    48,
    108,
    "Please reconcile the conflicting records and reply with the current released drawing.",
    11,
)
line(36, 84, 972, 84)
text(
    36,
    59,
    "FICTIONAL DEMO - NOT AN ENGINEERING DESIGN, CUSTOMER ORDER OR COMMERCIAL OFFER",
    9,
    True,
    "#954538",
)
text(
    36,
    38,
    "Prepared solely to demonstrate email capture, PDF comments, source retrieval and an unsent reply.",
    9,
)
text(895, 38, "E-10 / C / 1", 10, True)
c.save()
writer = PdfWriter()
writer.append(str(base))
comments = [
    (
        (384, 379, 550, 408),
        "Please confirm which trip unit governs CB-12 on E-10: the accepted scope and released Rev B show TU-2500-DEMO, while specification Rev 2 and draft Rev C show TU-3000-DEMO.",
        "1",
    ),
    (
        (84, 222, 555, 246),
        "The remote indication module is proposed in Rev C but excluded from the accepted scope. Please confirm the requirement and whether a scope review is needed before any commitment.",
        "2",
    ),
    (
        (785, 634, 960, 657),
        "Rev C is marked draft, not released. Please attach the latest released E-10 drawing and confirm whether there is recorded production-release approval. Do not treat this return as approval.",
        "3",
    ),
]
for rect, body, num in comments:
    a = Rectangle(rect=rect)
    a[NameObject("/Contents")] = TextStringObject(body)
    a[NameObject("/T")] = TextStringObject("Fictional customer reviewer")
    a[NameObject("/Subj")] = TextStringObject("Comment " + num)
    a[NameObject("/C")] = ArrayObject(
        [FloatObject(0.76), FloatObject(0.24), FloatObject(0.24)]
    )
    writer.add_annotation(page_number=0, annotation=a)
with out.open("wb") as stream:
    writer.write(stream)
reader = PdfReader(out)
assert len(reader.pages[0]["/Annots"]) == 3
print(str(out))
print("Verified: 1 vector drawing page, 3 native PDF comments, fictional labels.")
