"""RFQ/PO mail is prepared from the compared commercial record, never guessed."""

from urllib.parse import urlencode

from backend.domain.service import digest, fail, get, scoped
from backend.lifecycle import service as lifecycle
from backend.lifecycle.outlook_handoff import _matches_intake
from backend.records import commercial_intake, lifecycle_preparation
from backend.records import service as records
from backend.records.delivery import workspace_url
from backend.storage.models import RecordIntake

from . import automatic, service
from .models import ReplyDraft

INSTRUCTION = "Explain the recorded purchase-order comparison without accepting the order or promising delivery."


def plan(s, order, receipt):
    if automatic.historical(receipt) or not receipt.intake_id:
        return None
    intake = get(s, RecordIntake, receipt.intake_id)
    if intake.order_id != order.id or intake.status not in {"matched", "imported"}:
        return None
    from backend.autopilot.service import draft_for_message

    placed = draft_for_message(s, receipt.internet_message_id)
    if placed and placed["status"] in {
        "created",
        "existing_draft",
        "sent",
        "discarded",
    }:
        return None
    w = records.view(s, order)
    ids = set(automatic.focus_documents(s, order, receipt))
    focused = {
        "documents": [d for d in w["documents"] if d["id"] in ids],
        "sources": [source for source in w["sources"] if source["document_id"] in ids],
    }
    rows, questions = commercial_intake.extract_rows(focused)
    if not rows and not questions:
        return None
    if questions:
        return {
            "status": "blocked",
            "detail": questions[0]["detail"],
            "automatic": True,
        }
    operations = {
        "quote" if row["role"] == "quote_request" else "kickoff" for row in rows
    }
    if len(operations) != 1:
        return {
            "status": "blocked",
            "detail": "This email includes both a quote request and a purchase order. Confirm which document is current.",
            "automatic": True,
        }
    operation = next(iter(operations))
    packages = lifecycle.view(s, order)["packages"]
    package = next(
        (
            p
            for p in packages
            if p["operation"] == operation
            and p["evidence_current"]
            and p["status"] not in {"superseded", "rejected"}
            and _matches_intake(s, order, p, intake)
        ),
        None,
    )
    if operation == "quote" and package:
        return None  # The protected commercial handoff owns this response.
    if not package:
        requests, _ = lifecycle_preparation.automatic_inputs(s, order, w)
        supported = any(request["operation"] == operation for request in requests)
        return {
            "status": "queued" if supported else "blocked",
            "detail": "Rivet is matching the request to trusted catalog pricing and preparing the quote."
            if supported and operation == "quote"
            else "Rivet is comparing the purchase order with the recorded quote."
            if supported
            else "No unique trusted catalog item matches this request. Confirm the equipment model and company price record."
            if operation == "quote"
            else "Confirm the current PO revision, currency and line items before the comparison can be prepared.",
            "automatic": True,
        }
    quote = next(
        (p for p in packages if p["id"] == package["dependencies"].get("quote")), None
    )
    if not quote or quote["status"] != "approved" or not quote["evidence_current"]:
        return {
            "status": "waiting_for_approval",
            "detail": "The purchase order is captured. Sales must approve the current quote before Rivet prepares the comparison reply.",
            "automatic": True,
            "package_ids": [quote["id"]] if quote else [],
            "pending_gates": quote["pending_gates"]
            if quote
            else ["money", "scope", "customer_commitment"],
        }
    return {"status": "ready", "package": package, "quote": quote}


def _body(package, quote):
    po = package["payload"]
    currency = quote["payload"]["currency"]
    body = f"Thank you for purchase order {po['purchase_order_number']}.\n\n"
    scope_mismatches = po.get("scope_mismatches", [])
    if po["mismatches"] or scope_mismatches:
        body += "Our comparison found the following differences from the reviewed quotation:\n"
        for difference in po["mismatches"]:
            before, after = difference["quote"], difference["purchase_order"]
            body += "\n- " + difference["tag"] + ": "
            body += (
                f"quoted quantity {before['quantity']} at {currency} {before['unit_price']} each"
                if before
                else "not included in the quotation"
            )
            body += "; " + (
                f"purchase-order quantity {after['quantity']} at {currency} {after['unit_price']} each."
                if after
                else "not present in the purchase order."
            )
        if not po["mismatches"]:
            body += "\nThe listed equipment tags, quantities and unit prices match, but equipment scope does not.\n"
        for difference in scope_mismatches:
            body += "\n- " + difference["detail"]
        body += "\n\nPlease confirm these differences or send a corrected purchase order before order acceptance."
    else:
        body += "The listed equipment tags, quantities and unit prices match the reviewed quotation. The explicit equipment requirements have no recorded difference from the quoted catalog. We have prepared the internal kickoff record for the team's scope and engineering review."
    if po.get("quoted_exceptions"):
        body += "\n\nThe quotation exceptions remain for review; comparison alone does not establish your acceptance:"
        body += "".join("\n- " + item["text"] for item in po["quoted_exceptions"])
    body += "\n\nThis message acknowledges receipt and the recorded comparison; it does not accept changed scope, authorize production or promise a delivery date."
    return body


def prepare(s, order, receipt, proposed):
    package, quote = proposed["package"], proposed["quote"]
    focused = automatic.focus_documents(s, order, receipt)
    w, data, company, evidence_hash = service.context(s, order, INSTRUCTION, focused)
    key = "auto-mail:" + digest(
        ["commercial-comparison", receipt.id, package["id"], evidence_hash]
    )
    existing = s.scalar(scoped(ReplyDraft).where(ReplyDraft.request_key == key))
    if existing:
        return service.preparation_for_draft(s, existing)
    sources = {source["id"]: source for source in w["sources"]}
    documents = {document["id"]: document for document in w["documents"]}
    ids = set(package["sources"]) | set(quote["sources"])
    refs = [
        {
            "id": sid,
            "label": documents[sources[sid]["document_id"]]["name"],
            "url": workspace_url()
            + "#order/"
            + order.id
            + "?"
            + urlencode({"tab": "work", "source": sid}),
        }
        for sid in sorted(ids)
    ]
    subject = (
        f"{order.number} - purchase order {package['payload']['purchase_order_number']}"
    )
    body = _body(package, quote)
    row = ReplyDraft(
        order_id=order.id,
        request_key=key,
        request_hash=digest([receipt.id, package["id"]]),
        internet_message_id=receipt.internet_message_id,
        status="ready",
        evidence_hash=evidence_hash,
        model="deterministic-po-comparison",
        subject=subject,
        body=body,
        original_body=body,
        source_refs=refs,
        context={
            "instruction": INSTRUCTION,
            "focus_document_ids": focused,
            "current_order": data,
            "company": company,
            "attachment_candidates": [],
            "commercial_comparison": {
                "package_id": package["id"],
                "fingerprint": package["fingerprint"],
                "receipt_id": receipt.id,
                "reply_hash": digest([subject, body, refs]),
            },
        },
        coverage={
            "origin": "recorded_po_comparison",
            "scope": "Explicit tags, quantities and unit prices only.",
        },
    )
    s.add(row)
    s.flush()
    return service.preparation_for_draft(s, row)


def validate(s, order, receipt, reply):
    saved = reply.context["commercial_comparison"]
    package = next(
        (
            p
            for p in lifecycle.view(s, order)["packages"]
            if p["id"] == saved["package_id"]
        ),
        None,
    )
    intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
    if (
        not package
        or not package["evidence_current"]
        or package["status"] in {"rejected", "superseded"}
        or package["fingerprint"] != saved["fingerprint"]
        or receipt.id != saved["receipt_id"]
        or not intake
        or not _matches_intake(s, order, package, intake)
        or digest([reply.subject, reply.body, reply.source_refs]) != saved["reply_hash"]
    ):
        fail(
            "The purchase-order comparison or its captured email changed. Rivet must prepare the current comparison again.",
            409,
        )
