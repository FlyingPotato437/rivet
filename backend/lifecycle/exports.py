"""Portable, immutable PDF proposals rendered only from saved lifecycle data.

No browser, network, remote fonts or document-supplied markup is executed.
Selling prices are copied from Decimal-priced packages; internal costs and
margin rules never appear in the customer-facing PDFs.
"""

import json
import re
from decimal import Decimal
from functools import lru_cache, partial
from html import escape
from io import BytesIO
from pathlib import Path
from threading import Lock

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from backend.domain.service import fail, get, scoped
from backend.storage.models import Document, Project, Span

from . import adapters

FONT = "RivetDocumentSans"
FONT_LOCK = Lock()
FONT_PATH = Path(__file__).parent / "assets" / "IBMPlexSans.ttf"
INK = colors.HexColor("#20232B")
MUTED = colors.HexColor("#646976")
ACCENT = colors.HexColor("#726086")
EDGE = colors.HexColor("#DDE0E5")
WIDTH = 524
GATE_LABELS = {
    "money": "price",
    "scope": "scope",
    "engineering": "engineering",
    "customer_commitment": "customer response",
}


def _font():
    with FONT_LOCK:
        if FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))
            pdfmetrics.registerFontFamily(
                FONT, normal=FONT, bold=FONT, italic=FONT, boldItalic=FONT
            )


def _text(value):
    # Paragraph interprets XML; source text is always escaped as plain content.
    value = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value if value is not None else "")
    )
    return escape(
        value.replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", " - ")
    ).replace("\n", "<br/>")


def _styles():
    return {
        "title": ParagraphStyle(
            "Title",
            fontName=FONT,
            fontSize=27,
            leading=32,
            textColor=INK,
            spaceAfter=10,
        ),
        "heading": ParagraphStyle(
            "Heading",
            fontName=FONT,
            fontSize=14,
            leading=19,
            textColor=INK,
            spaceBefore=20,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body",
            fontName=FONT,
            fontSize=10,
            leading=15,
            textColor=INK,
            spaceAfter=8,
            splitLongWords=True,
        ),
        "small": ParagraphStyle(
            "Small",
            fontName=FONT,
            fontSize=8.5,
            leading=12.5,
            textColor=MUTED,
            spaceAfter=6,
            splitLongWords=True,
        ),
        "label": ParagraphStyle(
            "Label",
            fontName=FONT,
            fontSize=8,
            leading=11,
            textColor=ACCENT,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "table": ParagraphStyle(
            "Table",
            fontName=FONT,
            fontSize=9,
            leading=13,
            textColor=INK,
            splitLongWords=True,
        ),
        "right": ParagraphStyle(
            "Right",
            fontName=FONT,
            fontSize=9,
            leading=13,
            textColor=INK,
            alignment=TA_RIGHT,
            splitLongWords=True,
        ),
        "hash": ParagraphStyle(
            "Hash",
            fontName=FONT,
            fontSize=7.5,
            leading=11,
            textColor=MUTED,
            spaceAfter=5,
            splitLongWords=True,
        ),
    }


def _p(value, styles, name="body"):
    return Paragraph(_text(value), styles[name])


def _money(value, currency):
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("A saved commercial amount is not finite.")
    return f"{currency} {amount:,.2f}"


def _quantity(value):
    number = Decimal(str(value))
    return format(number.normalize(), "f")


def _location(location):
    parts = []
    if location.get("page") is not None:
        parts.append(f"page {location['page']}")
    if location.get("sheet"):
        parts.append(f"sheet {location['sheet']}")
    if location.get("line") is not None:
        parts.append(f"line {location['line']}")
    if location.get("cells"):
        parts.append(f"cells {location['cells']}")
    return ", ".join(parts) or "recorded source"


def _references(ids, evidence):
    return "; ".join(
        evidence[id]["label"] for id in dict.fromkeys(ids) if id in evidence
    )


def _used_evidence(evidence, ids):
    """A document cites its own commercial basis, not unrelated package work."""
    selected = {id: evidence[id] for id in dict.fromkeys(ids) if id in evidence}
    return {
        id: {**source, "label": f"E{index:02d}"}
        for index, (id, source) in enumerate(selected.items(), 1)
    }


@lru_cache(maxsize=1)
def _watermark():
    # An image stamp cannot interleave letters into copied engineering text.
    image = Image.new("RGBA", (700, 190), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.text(
        (10, -5),
        "DRAFT",
        font=ImageFont.truetype(str(FONT_PATH), 170),
        fill=(216, 216, 224, 65),
    )
    output = BytesIO()
    image.save(output, format="PNG")
    return ImageReader(BytesIO(output.getvalue()))


def _page(canvas, doc, *, context, package):
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.setFont(FONT, 17)
    canvas.drawString(44, 754, "rivet.")
    canvas.setFillColor(ACCENT)
    canvas.setFont(FONT, 8)
    canvas.drawRightString(568, 758, "DRAFT - REVIEW BEFORE SENDING")
    canvas.setStrokeColor(EDGE)
    canvas.line(44, 741, 568, 741)
    canvas.line(44, 43, 568, 43)
    canvas.setFont(FONT, 7)
    canvas.setFillColor(MUTED)
    notice = (
        "Fictional demonstration. Not a commercial offer or production release."
        if package.get("synthetic")
        else "Unissued proposal. Approval and production release remain with authorized people."
    )
    canvas.drawString(44, 30, notice)
    canvas.drawRightString(568, 30, f"Page {doc.page}")
    canvas.setFont(FONT, 6.5)
    canvas.drawString(
        44, 19, f"Package {package['id']} | Snapshot {package['fingerprint'][:16]}"
    )
    # A light watermark is visible when the proposal is printed or separated.
    canvas.translate(306, 396)
    canvas.rotate(32)
    canvas.drawImage(_watermark(), -140, -25, width=280, height=76, mask="auto")
    canvas.restoreState()


def _header(title, context, package, styles):
    gates = ", ".join(GATE_LABELS.get(g, g) for g in package.get("gates", []))
    return [
        _p("PREPARED ORDER WORK / " + context["number"], styles, "label"),
        _p(title, styles, "title"),
        _p(context["title"], styles),
        _p(
            f"Customer: {context['customer']}\nPrepared: {package['prepared_at'][:10]} | Local test adapters",
            styles,
            "small",
        ),
        _p(
            f"Proposed work. Required review: {gates}. No customer message has been sent and no engineering release is established by this document.",
            styles,
            "small",
        ),
        Spacer(1, 12),
    ]


def _price_table(lines, currency, styles):
    rows = [
        [
            _p(value, styles, "label")
            for value in ("ITEM / DESCRIPTION", "QTY", "UNIT PRICE", "AMOUNT")
        ]
    ]
    for line in lines:
        rows.append(
            [
                _p(f"{line['tag']}\n{line['description']}", styles, "table"),
                _p(_quantity(line["quantity"]), styles, "right"),
                _p(_money(line["unit_price"], currency), styles, "right"),
                _p(_money(line["extended_price"], currency), styles, "right"),
            ]
        )
    table = Table(
        rows,
        colWidths=[264, 42, 109, 109],
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=1,
        splitInRow=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1EFF5")),
                ("LINEBELOW", (0, 0), (-1, -1), 0.45, EDGE),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 11),
            ]
        )
    )
    return table


def _source_register(package, evidence, styles, *, compact=False):
    introduction = _p(
        "Source snapshots used to prepare this proposal. References identify evidence; they do not establish engineering or commercial approval.",
        styles,
        "small",
    )
    introduction.keepWithNext = True
    result = [
        _p("Source register", styles, "heading"),
        introduction,
    ]
    grouped = {}
    for id, source in evidence.items():
        key = (
            (
                source.get("document_id", id),
                _location(source["location"]),
            )
            if compact
            else id
        )
        if key not in grouped:
            grouped[key] = {"id": id, "source": source, "labels": []}
        grouped[key]["labels"].append(source["label"])
    for item in grouped.values():
        id, source = item["id"], item["source"]
        source_name = _p(
            f"{', '.join(item['labels'])}  {source.get('audience_label') or source['name']} | {_location(source['location'])}",
            styles,
            "small",
        )
        source_name.keepWithNext = not compact
        result.append(source_name)
        if not compact:
            result.append(
                _p(f"Source SHA-256: {package['sources'][id]['hash']}", styles, "hash")
            )
    if compact:
        result.append(
            _p(
                f"Package {package['id']}\nFingerprint: {package['fingerprint']}",
                styles,
                "hash",
            )
        )
    else:
        result.extend(
            [
                _p("Prepared package", styles, "heading"),
                _p(
                    f"Package ID: {package['id']}\nFingerprint: {package['fingerprint']}",
                    styles,
                    "hash",
                ),
            ]
        )
    if package.get("blockers"):
        result.append(_p("Open conditions", styles, "heading"))
        result.extend(
            _p(f"- {message}", styles, "small") for message in package["blockers"]
        )
    return result


def _render(title, story, context, package):
    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=letter,
        leftMargin=44,
        rightMargin=44,
        topMargin=68,
        bottomMargin=60,
        title=title,
        author="Rivet",
        subject="Unissued proposal prepared for human review",
        pageCompression=1,
        allowSplitting=1,
    )
    decorate = partial(_page, context=context, package=package)

    def canvas_factory(*args, **kwargs):
        kwargs["invariant"] = 1
        return Canvas(*args, **kwargs)

    doc.build(
        story,
        onFirstPage=decorate,
        onLaterPages=decorate,
        canvasmaker=canvas_factory,
    )
    return output.getvalue()


def render_documents(context, package, evidence):
    """Pure renderer. Return named PDFs without changing order or approval state."""
    if package["operation"] not in {"quote", "revision_review"}:
        return {}
    if len(json.dumps([context, package, evidence], default=str)) > 4_000_000:
        fail(
            "This prepared work is too large for one PDF export. Split the review into smaller revisions.",
            413,
        )
    _font()
    styles = _styles()
    payload = package["payload"]
    evidence = {
        id: {**source, "label": f"E{index:02d}"}
        for index, (id, source) in enumerate(evidence.items(), 1)
    }
    documents = {}
    if package["operation"] == "quote":
        title = "Quotation and exceptions"
        currency = payload["currency"]
        quote_evidence = _used_evidence(
            evidence,
            [
                id
                for item in [*payload["lines"], *payload.get("exceptions", [])]
                for id in item.get("source_ids", [])
            ],
        )
        story = _header(title, context, package, styles)
        story.extend(
            [
                _price_table(payload["lines"], currency, styles),
                Spacer(1, 12),
                _p("PROPOSED TOTAL", styles, "label"),
                _p(_money(payload["total"], currency), styles, "title"),
            ]
        )
        story.append(
            _p(
                "Only the saved line pricing is included. Commercial terms require separate approval.",
                styles,
                "small",
            )
        )
        story.append(_p("Specification exceptions", styles, "heading"))
        if not payload.get("exceptions"):
            story.append(
                _p(
                    "No exceptions have been recorded in this package. This is not a declaration of complete specification compliance.",
                    styles,
                )
            )
        for index, exception in enumerate(payload.get("exceptions", []), 1):
            story.extend(
                [
                    _p(f"{index:02d}  {exception['text']}", styles),
                    _p(
                        "Evidence: "
                        + _references(exception["source_ids"], quote_evidence),
                        styles,
                        "small",
                    ),
                ]
            )
        story.extend(_source_register(package, quote_evidence, styles, compact=True))
        documents["quote.pdf"] = _render(title, story, context, package)
        return documents

    change = payload.get("change_order", {})
    if change.get("lines"):
        title = "Proposed change order"
        currency = change["lines"][0].get("currency", "USD")
        change_evidence = _used_evidence(
            evidence,
            [
                id
                for line in change["lines"]
                for id in [
                    *line.get("source_ids", []),
                    *line.get("billability_source_ids", []),
                ]
            ],
        )
        story = _header(title, context, package, styles)
        story.extend(
            [
                _p(
                    f"Returned drawing review: Revision {payload['revision']}",
                    styles,
                    "heading",
                ),
                _price_table(change["lines"], currency, styles),
                Spacer(1, 12),
                _p("PROPOSED ADDITION", styles, "label"),
                _p(_money(change["total"], currency), styles, "title"),
                _p(
                    "This is a proposed scope addition. The amount is not accepted, invoiced or authorized by this draft.",
                    styles,
                    "small",
                ),
            ]
        )
        for line in change["lines"]:
            state = (
                "Recorded contract evidence supports billability; scope and price approval are still required."
                if line.get("billability") == "contract_confirmed"
                else "The contract owner must determine whether this request is billable."
            )
            story.extend(
                [
                    _p(f"{line['tag']}: {state}", styles),
                    _p(
                        "Evidence: "
                        + _references(
                            [
                                *line.get("source_ids", []),
                                *line.get("billability_source_ids", []),
                            ],
                            change_evidence,
                        ),
                        styles,
                        "small",
                    ),
                ]
            )
        story.extend(_source_register(package, change_evidence, styles, compact=True))
        documents["change-order.pdf"] = _render(title, story, context, package)

    comments = payload.get("comments", [])
    title = "Comment response letter"
    story = _header(title, context, package, styles)
    story.append(_p(payload["email_draft"]["subject"], styles, "heading"))
    story.append(
        _p(
            f"{len(comments)} returned review items are listed below. Responses are prepared wording, not recorded customer acceptance.",
            styles,
            "small",
        )
    )
    for index, comment in enumerate(comments, 1):
        story.extend(
            [
                _p(f"Review item {index:02d}", styles, "heading"),
                _p(comment.get("text", ""), styles),
            ]
        )
        response = comment.get("response") or (
            "The requested field edit is prepared in the draft copy. Engineering review is required before issue."
            if comment["kind"] == "text_edit"
            else "A located drafting task is prepared. Layout geometry and clearance checks require a drafter; the geometry has not been moved."
        )
        story.extend(
            [
                _p("PREPARED RESPONSE", styles, "label"),
                _p(response, styles),
                _p(
                    "Evidence: " + _references(comment["source_ids"], evidence),
                    styles,
                    "small",
                ),
            ]
        )
    story.extend(_source_register(package, evidence, styles))
    documents["response-letter.pdf"] = _render(title, story, context, package)

    title = "Changes for drafting"
    story = _header(title, context, package, styles)
    edits = [c for c in comments if c["kind"] != "answer"]
    story.append(
        _p(
            f"{len(edits)} drawing/BOM items. Saved text and data edits are draft copies; layout work remains with the drafter.",
            styles,
        )
    )
    for index, comment in enumerate(edits, 1):
        story.extend(
            [
                _p(f"Drafting item {index:02d}", styles, "heading"),
                _p(comment.get("text", ""), styles),
            ]
        )
        bundle = comment.get("draft_bundle", {})
        if comment["kind"] == "text_edit":
            change = next(
                (
                    c
                    for c in bundle.get("changes", [])
                    if c["id"] == comment.get("draft_change_id")
                ),
                None,
            )
            if change:
                story.extend(
                    [
                        _p("SAVED DRAFT EDIT", styles, "label"),
                        _p(
                            change.get("target") or change["type"].replace("_", " "),
                            styles,
                        ),
                        _p(
                            "Before: "
                            + str(change["before"])
                            + "\nDraft value: "
                            + str(change["after"]),
                            styles,
                        ),
                    ]
                )
            else:
                story.append(
                    _p(
                        "The exact saved field edit is unavailable. Do not infer a completed edit from this item.",
                        styles,
                    )
                )
        else:
            location = comment.get("layout_location", {})
            story.extend(
                [
                    _p("LOCATED TASK - GEOMETRY UNCHANGED", styles, "label"),
                    _p(
                        f"Sheet {location.get('sheet', 'not recorded')} | anchor x={location.get('x', 'not recorded')}, y={location.get('y', 'not recorded')} | entity {location.get('entity_handle', 'not recorded')}",
                        styles,
                    ),
                    _p(
                        "Coordinates refer to the saved source drawing. A drafter must check positioning and clearances before moving geometry.",
                        styles,
                        "small",
                    ),
                ]
            )
        story.append(
            _p(
                "Evidence: " + _references(comment["source_ids"], evidence),
                styles,
                "small",
            )
        )
        for artifact in bundle.get("artifacts", []):
            if artifact.get("role") in {"draft_drawing", "draft_bom"}:
                story.append(
                    _p(
                        f"Draft file: {artifact['name']}\nSHA-256: {artifact['sha256']}",
                        styles,
                        "hash",
                    )
                )
    if not edits:
        story.append(
            _p("No drawing or BOM changes were prepared in this package.", styles)
        )
    story.extend(_source_register(package, evidence, styles))
    documents["drafting-list.pdf"] = _render(title, story, context, package)
    return documents


def prepare_artifacts(s, order, package):
    if package["operation"] not in {"quote", "revision_review", "closeout"}:
        return []
    project = get(s, Project, order.project_id)
    spans = s.scalars(scoped(Span).where(Span.id.in_(list(package["sources"])))).all()
    docs = {
        doc.id: doc
        for doc in s.scalars(
            scoped(Document).where(
                Document.id.in_({span.document_id for span in spans})
            )
        ).all()
    }
    evidence = {
        span.id: {
            "name": docs[span.document_id].name,
            "document_id": span.document_id,
            "audience_label": "Manufacturer offer configuration"
            if docs[span.document_id].kind == "erp_price"
            else None,
            "location": span.location,
        }
        for span in sorted(spans, key=lambda x: x.id)
    }
    context = {
        "number": order.number,
        "title": project.title,
        "customer": project.customer,
    }
    if package["operation"] == "closeout":
        from .closeout import prepare_artifacts as handover_artifacts

        return handover_artifacts(s, order, package, context)
    documents = render_documents(context, package, evidence)
    return [
        adapters.write_artifact(
            order.organization_id, order.id, package["id"], name, content
        )
        for name, content in documents.items()
    ]
