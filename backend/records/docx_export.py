"""Customer-ready comment response matrix as a Word document (.docx).

Written directly as Office Open XML so no extra dependency is needed. The
document is a draft: it lists every comment with its recorded response and the
automatic type, and says so on the page.
"""

from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""
RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""
NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
# twentieths of a point; A4 landscape with 0.6 inch margins leaves ~14,976
WIDTHS = [900, 2900, 4300, 1700, 3900, 1276]
HEAD = ["No.", "Sheet / location", "Customer comment", "Type", "Response", "Status"]


def _run(text, bold=False, size=18, color=None):
    props = "".join(
        [
            "<w:b/>" if bold else "",
            f'<w:color w:val="{color}"/>' if color else "",
            f'<w:sz w:val="{size}"/>',
        ]
    )
    parts = str(text or "").split("\n")
    body = "<w:br/>".join(f'<w:t xml:space="preserve">{escape(p)}</w:t>' for p in parts)
    return f'<w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/>{props}</w:rPr>{body}</w:r>'


def _para(*runs, after=80):
    return f'<w:p><w:pPr><w:spacing w:after="{after}"/></w:pPr>{"".join(runs)}</w:p>'


def _cell(width, content, shade=None):
    fill = f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>' if shade else ""
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{fill}</w:tcPr>'
        f"{content}</w:tc>"
    )


def _row(cells, header=False):
    props = (
        "<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>"
        if header
        else "<w:trPr><w:cantSplit/></w:trPr>"
    )
    return f"<w:tr>{props}{''.join(cells)}</w:tr>"


def _date(value):
    text = str(value or "")
    return text[:10] if len(text) > 10 and text[4:5] == "-" and "T" in text else text


def _money(value, currency):
    amount = f"{float(value):,.0f}"
    return f"${amount}" if currency == "USD" else f"{currency} {amount}"


def matrix_docx(w):
    from .exports import rows

    order = w["order"]
    header = _row(
        [
            _cell(
                width,
                _para(_run(text, bold=True, size=17, color="FFFFFF"), after=0),
                "34323F",
            )
            for width, text in zip(WIDTHS, HEAD)
        ],
        header=True,
    )
    body = []
    for r in rows(w):
        kind, engineer, cost = r[16], r[17], r[18]
        type_runs = [_run(kind or "—", bold=True, size=17)]
        if engineer:
            type_runs.append(_run("\nEngineer decision", size=16, color="6B5BA8"))
        if cost:
            type_runs.append(_run(f"\n{cost}", size=15, color="8A5A50"))
        cells = [
            _para(_run(r[0], bold=True)),
            _para(
                _run(
                    f"{r[2]} · page {r[3]}\nRev {r[1]}\n{r[4]}", size=16, color="555555"
                )
            ),
            _para(
                _run(r[5]), _run(f"\n{r[7]} · {_date(r[8])}", size=15, color="777777")
            ),
            _para(*type_runs),
            _para(
                _run(
                    r[10] or "No reviewed response yet.",
                    color=None if r[10] else "999999",
                )
            )
            + (_para(_run(r[11], size=15, color="777777")) if r[11] else ""),
            _para(_run(r[9], size=16)),
        ]
        body.append(_row([_cell(width, c) for width, c in zip(WIDTHS, cells)]))
    grid = "".join(f'<w:gridCol w:w="{x}"/>' for x in WIDTHS)
    borders = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="D9D9D9"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    table = (
        f'<w:tbl><w:tblPr><w:tblW w:w="{sum(WIDTHS)}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
        f"<w:tblBorders>{borders}</w:tblBorders>"
        '<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="90" w:type="dxa"/>'
        '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar></w:tblPr>'
        f"<w:tblGrid>{grid}</w:tblGrid>{header}{''.join(body)}</w:tbl>"
    )
    impact = w.get("comment_impact") or {}
    scope = impact.get("scope") or []
    summary = (
        f"{len(w['comments'])} comments · "
        f"{impact.get('counts', {}).get('scope', 0)} add scope · "
        f"{len(impact.get('engineer') or [])} need an engineer's decision"
    )
    if impact.get("estimated_total"):
        summary += f" · added scope about {_money(impact['estimated_total'], impact['currency'])} from ERP prices"
    if impact.get("needs_pricing"):
        n = impact["needs_pricing"]
        summary += (
            f" · {n} added-scope {'item needs' if n == 1 else 'items need'} pricing"
        )
    scope_note = (
        _para(
            _run("Comments that add scope: ", bold=True, size=17),
            _run(", ".join(str(i["number"]) for i in scope), size=17),
        )
        if scope
        else ""
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>'
        + _para(
            _run("DRAFT · REVIEW BEFORE SENDING", bold=True, size=16, color="6B5BA8")
        )
        + _para(_run("Comment response matrix", bold=True, size=36), after=40)
        + _para(_run(f"{order['number']} · {order['title']}", size=20))
        + _para(
            _run(
                f"Customer: {order.get('customer') or 'Not recorded'}",
                size=18,
                color="555555",
            )
        )
        + _para(_run(summary, size=17, color="555555"))
        + scope_note
        + table
        + _para(
            _run(
                "Types are automatic labels from the comment text and costs come only from trusted ERP prices. "
                "Rivet responses are prepared drafts, separate from recorded engineer decisions. "
                "An engineer confirms technical content, scope and pricing. "
                "This document does not certify compliance or authorize production.",
                size=14,
                color="777777",
            )
        )
        + '<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
        '<w:pgMar w:top="864" w:right="864" w:bottom="864" w:left="864" w:header="432" w:footer="432" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{escape(order['number'])} comment response matrix</dc:title><dc:creator>Rivet</dc:creator>"
        "</cp:coreProperties>"
    )
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", document)
        z.writestr("docProps/core.xml", core)
    return out.getvalue()
