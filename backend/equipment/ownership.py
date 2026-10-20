"""Keep a returned native-file change with its file workflow, not a text reply.

Ownership follows the captured arrival's actual sources. An unrelated later
email on the same order does not inherit an older change request. This is a
read-only gate shared by preparation and the last check before Outlook writes.
"""

from pathlib import Path

from fastapi import HTTPException

from backend.domain.service import get
from backend.records.email_intake import email_document_ids
from backend.storage.models import Document, RecordIntake

from . import service
from .design_requests import extract_requests

PENDING = {"queued", "processing", "parsing"}


def _state(status, detail, **values):
    return {
        "status": status,
        "detail": detail,
        "automatic": True,
        "reason": "native_equipment",
        "delivery": "equipment",
        **values,
    }


def _opted_in(order, documents):
    return any(
        Path(document.name).suffix.lower() == ".dxf"
        and service.document_info(document, order)["role"] == "drawing"
        and (
            document.meta.get("equipment_package") or order.state.get("equipment_check")
        )
        for document in documents
    )


def _delivery(order, receipt):
    if receipt is None:
        return None
    for bundle_id, row in order.state.get("equipment_outlook", {}).items():
        if row.get("receipt_id") != receipt.id:
            continue
        status = row.get("status")
        if status in {"sent", "discarded", "sent_for_review"}:
            return _state(
                "not_needed",
                "The corrected-file reply was already handled in Outlook. Its history is preserved.",
                bundle_id=bundle_id,
            )
        if status in {"created", "existing_draft"}:
            return _state(
                "not_needed",
                "The corrected-file reply is in Outlook. Rivet will not create a separate text-only reply.",
                bundle_id=bundle_id,
            )
        return _state(
            "blocked" if status == "manual_attention" else "queued",
            row.get("error")
            or "Rivet is saving the checked drawing and BOM in this email's Outlook reply.",
            bundle_id=bundle_id,
        )
    return None


def _claim(s, order, documents, focused):
    candidates = []
    for document in documents:
        if focused is not None and document.id not in focused:
            continue
        if Path(document.name).suffix.lower() != ".pdf":
            continue
        info = service.document_info(document, order)
        # A new returned PDF has to be read before it can safely be routed to
        # either workflow. This hold lasts only for this arrival, not all mail.
        if document.state in PENDING:
            return _state(
                "queued",
                "Rivet is reading this email's returned files before preparing the drawing and BOM work.",
                source_ids=[document.id],
            )
        if info["role"] in {"markup", "specification"}:
            candidates.append((document, info))
    if not candidates:
        return None
    try:
        inputs = [service.source_input(document, info) for document, info in candidates]
    except HTTPException:
        return _state(
            "blocked",
            "A returned native-change source is unavailable. Restore it before preparing a reply.",
            source_ids=[document.id for document, _ in candidates],
        )
    compiled = extract_requests(inputs)
    from .workflow import plan

    report, _, _, _, _ = plan(s, order)
    source_ids = {document.id for document, _ in candidates}
    relevant_blocks = [
        item
        for item in report.get("blockers", [])
        if isinstance(item, dict) and item.get("source_id") in source_ids
    ]
    relevant_edits = [
        edit
        for edit in report.get("edits", [])
        if any(
            evidence.get("source_id") in source_ids
            for evidence in edit.get("requirement_evidence", [])
        )
    ]
    claimed_ids = (
        {request["evidence"]["source_id"] for request in compiled["requests"]}
        | {
            item["source_id"]
            for item in [*compiled["blockers"], *relevant_blocks]
            if item.get("source_id") in source_ids
        }
        | {
            evidence["source_id"]
            for edit in relevant_edits
            for evidence in edit.get("requirement_evidence", [])
            if evidence.get("source_id") in source_ids
        }
    )
    if not (
        compiled["requests"]
        or compiled["blockers"]
        or relevant_blocks
        or relevant_edits
    ):
        return None
    if report["status"] == "blocked" or compiled["blockers"] or relevant_blocks:
        return _state(
            "blocked",
            "The returned drawing changes need clarification before Rivet can safely prepare corrected files. A text-only reply will not replace that work.",
            source_ids=sorted(claimed_ids),
        )
    return _state(
        "queued",
        "Rivet is preparing and rechecking the drawing and BOM for this email. Its Outlook reply will include the corrected files.",
        source_ids=sorted(claimed_ids),
    )


def for_receipt(s, order, receipt):
    """Return a deferral state only when this captured arrival owns file work."""
    if receipt is None or not receipt.intake_id:
        return None
    documents = service.documents(s, order)
    if not _opted_in(order, documents):
        return None
    intake = get(s, RecordIntake, receipt.intake_id)
    if intake.order_id != order.id:
        return None
    if delivery := _delivery(order, receipt):
        return delivery
    root_id = intake.data.get("document_id")
    if not root_id:
        return None
    original = get(s, Document, root_id)
    focused = set(email_document_ids(s, order, original))
    return _claim(s, order, documents, focused)


def for_order(s, order):
    """Defer a file-only generic response while its native package owns the work."""
    documents = service.documents(s, order)
    if not _opted_in(order, documents):
        return None
    _, _, sources, _, _, _ = service.selected_sources(s, order)
    current = {
        item["id"]
        for side in ("target", "requirements")
        for item in sources[side]
        if item["role"] in {"markup", "specification"}
    }
    return _claim(s, order, documents, current)


def for_autorun(s, order):
    """Defer an event investigation only while its latest arrival is native work.

    An ordinary later customer question can start a new investigation. Confirmed
    sent copies are not new customer requests. Manual runs do not call this gate.
    """
    from backend.project_flow.service import is_sent_copy
    from backend.reply_assistant.automatic import receipts_for_order

    for receipt in s.scalars(receipts_for_order(s, order).limit(100)):
        intake = get(s, RecordIntake, receipt.intake_id)
        if intake.status in {"ignored", "dismissed", "not_order_related", "set_aside"}:
            continue
        if is_sent_copy(s, receipt):
            continue
        return for_receipt(s, order, receipt)
    return for_order(s, order)
