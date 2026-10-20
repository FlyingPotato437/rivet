"""Read-only lifecycle decisions tied to the email currently selected in Outlook."""

from fastapi import HTTPException

from backend.domain.service import digest, get
from backend.identity import current_actor, current_identity
from backend.mailbox.service import find_receipt_by_message_id
from backend.storage.models import Document, Order, Project, RecordIntake, Span

from . import service
from .outlook_handoff import _matches_intake


def selected_message(s, internet_message_id):
    result = {
        "internet_message_id": internet_message_id,
        "receipt_id": None,
        "order_id": None,
        "order_number": None,
        "revision": None,
        "version": None,
        "status": "not_captured",
        "packages": [],
        "sources": [],
    }
    receipt = find_receipt_by_message_id(s, internet_message_id)
    if not receipt:
        return result
    result.update(receipt_id=receipt.id, status="needs_confirmation")
    intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
    if (
        receipt.status != "imported"
        or not intake
        or not intake.order_id
        or intake.status not in {"matched", "imported"}
    ):
        return result
    order = get(s, Order, intake.order_id)
    view = service.view(s, order)
    local_admin = (
        get(s, Project, order.project_id).synthetic
        and current_identity().role == "org:admin"
    )
    result.update(
        status="matched",
        order_id=order.id,
        order_number=order.number,
        revision=intake.data.get("revision_label") or None,
        version=order.version,
    )
    evidence = {}
    for package in view["packages"]:
        if package["status"] in {"superseded", "rejected"} or not _matches_intake(
            s, order, package, intake
        ):
            continue
        package["allowed_gates"] = [
            gate
            for gate in package["pending_gates"]
            if package["evidence_current"]
            and not package["blockers"]
            and (
                local_admin
                or current_actor() in view["policy"]["gate_approvers"].get(gate, [])
            )
        ]
        handoff = package.get("outlook_handoff", {})
        if handoff.get("receipt_id") and handoff["receipt_id"] != receipt.id:
            package["outlook_handoff"] = {"status": "other_thread"}
        for source_id, saved in package["sources"].items():
            try:
                span = get(s, Span, source_id)
                document = get(s, Document, span.document_id)
            except HTTPException:
                continue  # Removed evidence disables approval; it must not break the pane.
            if document.project_id != order.project_id:
                continue
            current_hash = digest(
                [document.sha256, document.meta, span.text, span.location]
            )
            evidence[source_id] = {
                "id": span.id,
                "document_id": document.id,
                "document_name": document.name,
                "role": document.meta.get("order_role") or document.kind,
                "text": span.text[:6000],
                "truncated": len(span.text) > 6000,
                "location": span.location,
                "hash": current_hash,
                "document_sha256": document.sha256,
                "current": document.state in {"ready", "parsed"}
                and current_hash == saved["hash"]
                and document.sha256 == saved["document_sha256"],
            }
        result["packages"].append(package)
    result["sources"] = list(evidence.values())
    return result
