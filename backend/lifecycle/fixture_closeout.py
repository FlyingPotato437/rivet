"""Readable fictional factory record for the isolated completion demonstration."""

from functools import partial
from hashlib import sha256
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from backend.storage.models import now

from .exports import FONT, _font, _p, _styles


def _page(canvas, document, *, job):
    canvas.saveState()
    canvas.setFont(FONT, 17)
    canvas.setFillColor(colors.HexColor("#20232B"))
    canvas.drawString(44, 752, "rivet.")
    canvas.setFont(FONT, 8)
    canvas.drawRightString(568, 756, "SIMULATED FACTORY RECORD / FTR-17")
    canvas.setStrokeColor(colors.HexColor("#DDE0E5"))
    canvas.line(44, 740, 568, 740)
    canvas.line(44, 46, 568, 46)
    canvas.setFillColor(colors.HexColor("#646976"))
    canvas.setFont(FONT, 7)
    canvas.drawString(
        44,
        32,
        "Fictional example data. No physical testing, certification or production release is represented.",
    )
    canvas.drawString(44, 20, job)
    canvas.drawRightString(568, 20, f"Page {document.page}")
    canvas.restoreState()


def write_test_report(destination: Path, *, job: str, drawing_path: Path):
    """Keep invented demonstration measurements inside an explicitly named fixture."""
    _font()
    styles = _styles()
    drawing_hash = sha256(drawing_path.read_bytes()).hexdigest()
    story = [
        _p("FACTORY DOCUMENT / FICTIONAL COMPLETION REPLAY", styles, "label"),
        _p("Routine functional test record", styles, "title"),
        _p("FTR-17 / Revision 0", styles),
        _p(
            "This sample demonstrates the records Rivet collects at closeout. Every result and signatory below is simulated; no test was performed on real equipment.",
            styles,
            "small",
        ),
        Spacer(1, 12),
    ]
    rows = [
        ["Job", job],
        ["Equipment", "SWBD-01 / Data Hall A"],
        ["Serial", "DEMO-SWBD-001"],
        ["Drawing basis", "E-10 / completed revision D"],
        ["Record date", now().date().isoformat()],
        ["Procedure", "FTR-DEMO-01 / illustrative routine functional sequence"],
        ["Operator", "Fictional test technician / isolated simulation"],
    ]
    table = Table(
        [[_p(a, styles, "small"), _p(b, styles)] for a, b in rows], colWidths=[125, 399]
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE0E5")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story += [table, _p("Illustrative recorded observations", styles, "heading")]
    checks = [
        (
            "01",
            "Nameplate and document check",
            "CB-12 / DATA HALL A shown; drawing reference recorded",
            "SIMULATED",
        ),
        (
            "02",
            "Control supply observation",
            "119.8 V AC example reading / nominal 120 V AC",
            "SIMULATED",
        ),
        (
            "03",
            "Breaker operation sequence",
            "Close / trip / open indication sequence recorded",
            "SIMULATED",
        ),
        (
            "04",
            "Metering terminal identification",
            "TB1-01 through TB1-04 / shorting block identified",
            "SIMULATED",
        ),
        (
            "05",
            "Final document reconciliation",
            "Completed Rev D and BOM included in the local record",
            "SIMULATED",
        ),
    ]
    grid = [
        [
            _p(v, styles, "label")
            for v in ("REF", "CHECK", "RECORDED OBSERVATION", "RESULT")
        ]
    ]
    grid += [[_p(v, styles, "table") for v in row] for row in checks]
    table = Table(grid, colWidths=[30, 145, 269, 80], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1EFF5")),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE0E5")),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story += [
        table,
        _p("Record review", styles, "heading"),
        _p(
            "Fictional quality reviewer / explicit isolated completion scenario. This is a sample reviewed record, not a real signature or a claim of compliance with a testing standard.",
            styles,
            "small",
        ),
        _p(
            f"Recorded drawing source: {drawing_path.name}\nSHA-256: {drawing_hash}",
            styles,
            "hash",
        ),
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(destination),
        pagesize=letter,
        leftMargin=44,
        rightMargin=44,
        topMargin=68,
        bottomMargin=60,
        title="FTR-17 - fictional routine functional test record",
        author="Rivet demo fixture",
    )
    draw_page = partial(_page, job=job)
    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return destination
