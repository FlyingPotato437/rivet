import re
import secrets
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256

from sqlalchemy import select

from backend.domain.service import fail, get, scoped
from backend.identity import auth_mode, current_actor
from backend.orders.extraction import extract_order_document
from backend.storage.models import (
    Document,
    NoticeDelivery,
    OrderRecord,
    Project,
    RecordShare,
    Span,
    now,
    uid,
)

from .connections import connections


def stamp():
    return now().isoformat()


def event(data, kind, summary, actor, before=None, after=None, reason="", item_id=""):
    item = {
        "id": uid(),
        "kind": kind,
        "summary": summary,
        "actor": actor,
        "at": stamp(),
        "before": before,
        "after": after,
        "reason": reason,
        "item_id": item_id,
    }
    data["events"].insert(0, item)
    recipients = deepcopy(data.get("subscribers", []))
    if recipients and kind in {"comment", "change", "approval", "intake"}:
        data["notices"].insert(
            0,
            {
                "id": uid(),
                "event_id": item["id"],
                "title": summary,
                "body": f"{summary}\nRecorded by {actor} at {item['at']}\n{reason}"
                + "".join(
                    f"\n\n{key.replace('_', ' ').capitalize()}: {(before or {}).get(key) or 'Not recorded'} → {value}"
                    for key, value in (after or {}).items()
                    if key
                    in {
                        "text",
                        "response",
                        "status",
                        "before",
                        "after",
                        "approved_by",
                        "approved_at",
                        "title",
                    }
                    and value != (before or {}).get(key)
                ),
                "recipients": recipients,
                "status": "draft",
                "at": item["at"],
            },
        )


def ensure(s, order):
    record = s.scalar(
        scoped(OrderRecord).where(OrderRecord.order_id == order.id).with_for_update()
    )
    if record is None:
        record = OrderRecord(
            order_id=order.id,
            data={
                "comments": [],
                "changes": [],
                "events": [],
                "subscribers": [],
                "notices": [],
                "approvals": [],
                "ingested": [],
            },
        )
        s.add(record)
        s.flush()
    data = deepcopy(record.data)
    documents = list(
        s.scalars(
            scoped(Document)
            .where(Document.project_id == order.project_id)
            .order_by(Document.created_at)
        )
    )
    spans = list(
        s.scalars(
            scoped(Span)
            .join(Document)
            .where(Document.project_id == order.project_id)
            .order_by(Span.created_at)
        )
    )
    added = False
    for doc in documents:
        # Include original metadata in the identity: reclassifying a source can reveal comments.
        signature = f"{doc.id}:{doc.meta.get('order_role', doc.kind)}:{doc.meta.get('revision_label', '')}"
        if signature in data["ingested"] or doc.state in {
            "queued",
            "failed",
            "processing",
            "parsing",
        }:
            continue
        docspans = [sp for sp in spans if sp.document_id == doc.id]
        annotated_pages = {
            sp.location.get("page")
            for sp in docspans
            if sp.location.get("annotation_id")
        }
        # Native markup objects are the review layer on these pages. Numbered
        # drawing specifications underneath them are not reviewer comments.
        review_spans = [
            sp
            for sp in docspans
            if sp.location.get("page") not in annotated_pages
            or sp.location.get("annotation_id")
        ]
        extracted = extract_order_document(doc, review_spans)
        candidates = extracted["comments"]
        # All PDF annotation text is a review record, irrespective of equipment vocabulary.
        used = {sid for c in candidates for sid in c["source_ids"]}
        for sp in docspans:
            if sp.location.get("annotation_id") and sp.id not in used:
                candidates.append(
                    {
                        "number": str(len(candidates) + 1),
                        "text": sp.text
                        or "Graphical markup has no text. Transcribe the original area.",
                        "source_ids": [sp.id],
                        "location": sp.location,
                    }
                )
            elif sp.location.get("email") and not candidates:
                # Never invent splits for an unstructured email; retain one verifiable record.
                candidates.append(
                    {
                        "number": str(len(candidates) + 1),
                        "text": sp.text,
                        "source_ids": [sp.id],
                        "location": sp.location,
                    }
                )
        for raw in candidates:
            source = next((sp for sp in docspans if sp.id in raw["source_ids"]), None)
            if not source:
                continue
            identity = sha256(
                f"{doc.id}:{raw['number']}:{raw['text']}".encode()
            ).hexdigest()[:24]
            if any(c["id"] == identity for c in data["comments"]):
                continue
            location = raw.get("location") or source.location
            metadata = doc.meta.get("email") or {}
            author = location.get("author") or metadata.get("from", "")
            date = location.get("authored_at") or metadata.get("date", "")
            refs = re.findall(
                r"\b(?:page|pg\.?|p\.)\s*(\d+)\b", raw["text"], re.IGNORECASE
            )
            flags = ["Confirm the drawing location this comment refers to."]
            if refs and (
                not location.get("page") or int(refs[0]) != location.get("page")
            ):
                flags = [
                    f"Comment references page {refs[0]}; its source is on page {location.get('page', 'not stated')}. Confirm the target drawing."
                ]
            if not source.text:
                flags.insert(0, "Graphical markup: human transcription required.")
            if not author:
                flags.append("Author not stated in source.")
            if not date:
                flags.append("Comment date not stated in source.")
            data["comments"].append(
                {
                    "id": identity,
                    "number": str(raw["number"]),
                    "revision": extracted.get("revision_label") or "Unspecified",
                    "text": raw["text"],
                    "original_text": raw["text"] if source.text else "",
                    "author": author,
                    "authored_at": date,
                    "document_id": doc.id,
                    "source_ids": raw["source_ids"],
                    "source_page": location.get("page"),
                    "target_source_id": "",
                    "linked_email_id": doc.meta.get("email_document_id", "")
                    or (doc.id if metadata else ""),
                    "status": "open",
                    "response": "",
                    "responder": "",
                    "reviewed": False,
                    "confidence": "Needs review",
                    "flags": flags,
                    "created_at": stamp(),
                    "updated_at": stamp(),
                    "origin": "email"
                    if metadata
                    else "annotation"
                    if location.get("annotation_id")
                    else "numbered text",
                }
            )
        # Preserve graphical annotation coverage as an actionable item rather than hiding it.
        for index, coverage in enumerate(doc.coverage):
            if coverage.get("annotation_id") and any(
                sp.location.get("annotation_id") == coverage["annotation_id"]
                for sp in docspans
            ):
                continue
            if coverage.get("state") not in {"unsupported", "scanned", "failed"}:
                continue
            identity = f"coverage:{doc.id}:{index}"
            if any(c["id"] == identity for c in data["comments"]):
                continue
            source = next(
                (sp for sp in docspans if sp.id == coverage.get("source_id")), None
            )
            page_match = re.match(r"Page (\d+)\b", coverage.get("label", ""))
            data["comments"].append(
                {
                    "id": identity,
                    "number": "?",
                    "revision": extracted.get("revision_label") or "Unspecified",
                    "text": coverage.get("detail")
                    or "Unreadable source area. Transcription required.",
                    "original_text": "",
                    "author": "",
                    "authored_at": "",
                    "document_id": doc.id,
                    "source_ids": [source.id] if source else [],
                    "source_page": source.location.get("page")
                    if source
                    else int(page_match[1])
                    if page_match
                    else None,
                    "target_source_id": "",
                    "linked_email_id": "",
                    "status": "open",
                    "response": "",
                    "responder": "",
                    "reviewed": False,
                    "confidence": "Needs review",
                    "flags": [
                        coverage.get("label", "Source")
                        + ": human transcription required."
                    ],
                    "created_at": stamp(),
                    "updated_at": stamp(),
                    "origin": "unreadable area",
                }
            )
        data["ingested"].append(signature)
        event(
            data,
            "intake",
            "Imported " + doc.name,
            "Document reader",
            after={"document_id": doc.id, "comments": len(candidates)},
        )
        added = True
    # One-time migration of previously accepted responses; never replace PM edits.
    migrated = data.setdefault("legacy_responses", [])
    for legacy in order.state.get("comments", []):
        response = order.state.get("responses", {}).get(legacy["id"])
        if not response or legacy["id"] in migrated:
            continue
        item = next(
            (
                c
                for c in data["comments"]
                if c["document_id"] == legacy["document_id"]
                and c["original_text"] == legacy["text"]
            ),
            None,
        )
        if not item:
            continue
        if not item["response"] and not item["reviewed"]:
            item.update(
                response=response["text"],
                responder=response.get("actor", "Recorded reviewer"),
                status="responded",
                updated_at=stamp(),
            )
            event(
                data,
                "migration",
                "Retained accepted response to comment " + item["number"],
                response.get("actor", "Recorded reviewer"),
                reason="Imported from the earlier order workflow.",
                item_id=item["id"],
            )
        migrated.append(legacy["id"])
        added = True
    from .automation import synchronize

    docs_view, sources_view = source_data(s, order)
    added = synchronize(data, docs_view, sources_view) or added
    if added:
        record.version += 1
        record.data = data
        order.updated_at = now()
        s.flush()
    return record


def source_data(s, order):
    docs = list(
        s.scalars(
            scoped(Document)
            .where(Document.project_id == order.project_id)
            .order_by(Document.created_at)
        )
    )
    spans = list(
        s.scalars(
            scoped(Span).join(Document).where(Document.project_id == order.project_id)
        )
    )
    return (
        [
            {
                "id": d.id,
                "name": d.name,
                "kind": d.kind,
                "state": d.state,
                "role": d.meta.get("order_role", d.kind),
                "revision_label": d.meta.get("revision_label", ""),
                "coverage": d.coverage,
                "warnings": [],
                "email": d.meta.get("email"),
                "public_source": d.meta.get("public_source"),
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
        [
            {
                "id": sp.id,
                "document_id": sp.document_id,
                "text": sp.text,
                "location": sp.location,
            }
            for sp in spans
        ],
    )


def view(s, order, record=None):
    record = record or ensure(s, order)
    p = get(s, Project, order.project_id)
    from backend.mail import status as mail_status

    email = mail_status()
    docs, sources = source_data(s, order)
    from .automation import projection

    # Deliveries freeze their own recipients and payload. Project their actual
    # state without rewriting the immutable draft or changing record versions.
    delivery_by_notice = {
        delivery.notice_id: {"status": delivery.status, "payload": delivery.payload}
        for delivery in s.scalars(
            scoped(NoticeDelivery).where(NoticeDelivery.order_id == order.id)
        )
    }
    return {
        "coordination": projection(record.data, docs, sources, delivery_by_notice),
        "order": {
            "id": order.id,
            "project_id": p.id,
            "title": p.title,
            "customer": p.customer,
            "category": p.category,
            "number": order.number,
            "synthetic": p.synthetic,
        },
        "version": record.version,
        **{k: deepcopy(v) for k, v in record.data.items() if k != "coordination"},
        "approvals": [
            {k: v for k, v in a.items() if k != "snapshot"}
            for a in record.data["approvals"]
        ],
        "documents": docs,
        "sources": sources,
        "connections": connections(record.data, docs),
        "shares": [
            {
                "id": x.id,
                "revoked": x.revoked,
                "created_at": x.created_at.isoformat(),
                "expires_at": x.expires_at.isoformat(),
                "label": x.snapshot.get("approval", {}).get("label", "Approved record"),
            }
            for x in s.scalars(
                scoped(RecordShare).where(RecordShare.order_id == order.id)
            )
        ],
        "capabilities": {
            "email_import": True,
            "inbox_connected": bool(email["receiving_domain"] and email["configured"]),
            "outbound_connected": email["sending_ready"],
            "hosted_sharing": not email["local_only"],
        },
    }


def change_record(s, order, record, body, kind, item_id=""):
    if auth_mode() == "clerk":
        body = body.model_copy(update={"actor": current_actor()})
    if body.expected_version != record.version:
        fail("The record changed. Refresh before saving your edit.", 409)
    data = deepcopy(record.data)
    values = body.model_dump(exclude={"expected_version", "actor", "reason"})
    if kind == "comment":
        item = next((c for c in data["comments"] if c["id"] == item_id), None)
        if not item and item_id:
            fail("Comment not found.", 404)
        sources = source_data(s, order)[1]
        if body.target_source_id and not any(
            sp["id"] == body.target_source_id for sp in sources
        ):
            fail("Drawing location must belong to this order.", 422)
        if body.linked_email_id:
            doc = get(s, Document, body.linked_email_id)
            if doc.project_id != order.project_id or not doc.meta.get("email"):
                fail("Choose an email imported into this order.", 422)
        if body.status in {"responded", "closed"} and (
            not body.response or not body.responder
        ):
            fail("Record the response and its author before changing the status.", 422)
        if body.status == "closed" and not body.reviewed:
            fail("Review the comment and its location before closing it.", 422)
        before = deepcopy(item)
        if item is None:
            item = {
                "id": uid(),
                "original_text": "",
                "source_ids": [],
                "document_id": "",
                "source_page": None,
                "created_at": stamp(),
                "origin": "manual",
                "flags": [],
            }
            data["comments"].append(item)
        item.update(
            values,
            updated_at=stamp(),
            confidence="PM reviewed" if body.reviewed else "Needs review",
        )
        event(
            data,
            kind,
            f"{'Updated' if before else 'Added'} comment {body.number}",
            body.actor,
            before,
            deepcopy(item),
            body.reason,
            item["id"],
        )
    elif kind == "change":
        if set(body.comment_ids) - {c["id"] for c in data["comments"]}:
            fail("Linked comments must belong to this order.")
        if bool(body.approved_by) != bool(body.approved_at):
            fail("Record both the approver and the approval date.")
        item = next((c for c in data["changes"] if c["id"] == item_id), None)
        if not item and item_id:
            fail("Change not found.", 404)
        before = deepcopy(item)
        if item is None:
            item = {"id": uid(), "created_at": stamp()}
            data["changes"].append(item)
        item.update(values, updated_at=stamp())
        event(
            data,
            kind,
            body.title,
            body.actor,
            before,
            deepcopy(item),
            body.reason,
            item["id"],
        )
    elif kind == "subscriber":
        if not re.fullmatch(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", body.email):
            fail("Enter a valid email address.")
        if any(x["email"].lower() == body.email.lower() for x in data["subscribers"]):
            fail("This address is already subscribed.")
        data["subscribers"].append({"id": uid(), **values})
        event(
            data,
            kind,
            "Added notice recipient " + body.name,
            body.actor,
            reason=body.reason,
        )
    elif kind == "unsubscribe":
        person = next((x for x in data["subscribers"] if x["id"] == item_id), None)
        if not person:
            fail("Recipient not found.", 404)
        data["subscribers"].remove(person)
        event(
            data,
            "subscriber",
            "Removed notice recipient " + person["name"],
            body.actor,
            reason=body.reason,
        )
    elif kind == "notice_recipients":
        notice = next((n for n in data["notices"] if n["id"] == item_id), None)
        if not notice:
            fail("Notice not found.", 404)
        if notice.get("status") != "draft" or s.scalar(
            scoped(NoticeDelivery).where(
                NoticeDelivery.order_id == order.id,
                NoticeDelivery.notice_id == item_id,
            )
        ):
            fail(
                "This notice has entered the delivery queue and its recipients cannot be changed.",
                409,
            )
        subscribers = {person["id"]: person for person in data["subscribers"]}
        if set(body.recipient_ids) - subscribers.keys():
            fail(
                "Choose recipients saved on this order. Refresh if the recipient list changed.",
                422,
            )
        recipients = [deepcopy(subscribers[value]) for value in body.recipient_ids]
        if notice["recipients"] == recipients:
            return view(s, order, record)
        before = {"recipients": deepcopy(notice["recipients"])}
        notice["recipients"] = recipients
        event(
            data,
            "notice_recipients",
            "Updated recipients for draft: " + notice["title"],
            body.actor,
            before,
            {"recipients": deepcopy(recipients)},
            body.reason,
            notice["id"],
        )
    elif kind == "approval":
        docs, sources = source_data(s, order)
        if not data["comments"]:
            fail("Add comments before approving a record.")
        if any(
            d["state"] in {"queued", "processing", "failed", "parsing"} for d in docs
        ):
            fail("Wait for source processing or resolve failed files before approving.")
        if any(not c["reviewed"] for c in data["comments"]):
            fail("Review every comment before approving the record.")
        approval = {
            "id": uid(),
            "label": body.label,
            "actor": body.actor,
            "reason": body.reason,
            "at": stamp(),
            "version": record.version + 1,
        }
        snapshot = {
            "comments": deepcopy(data["comments"]),
            "changes": deepcopy(data["changes"]),
            "documents": docs,
            "sources": sources,
        }
        approval["snapshot"] = snapshot
        data["approvals"].append(approval)
        event(
            data, kind, "Approved record: " + body.label, body.actor, reason=body.reason
        )
    from .automation import synchronize

    docs, sources = source_data(s, order)
    synchronize(data, docs, sources)
    record.data = data
    record.version += 1
    order.updated_at = now()
    s.flush()
    return view(s, order, record)


def share(s, order, record, body):
    if body.expected_version != record.version:
        fail("The record changed. Refresh before sharing.", 409)
    approval = next(
        (a for a in record.data["approvals"] if a["id"] == body.approval_id), None
    )
    if not approval:
        fail("Select an approved record.", 422)
    snapshot = deepcopy(approval["snapshot"])
    snapshot["approval"] = {k: v for k, v in approval.items() if k != "snapshot"}
    snapshot["order"] = view(s, order, record)["order"]
    # Explicitly share only sources referenced by reviewed records, not the full order inbox.
    ids = {
        sid
        for c in snapshot["comments"]
        for sid in [*c["source_ids"], c.get("target_source_id", "")]
        if sid
    }
    snapshot["sources"] = [sp for sp in snapshot["sources"] if sp["id"] in ids]
    docids = {sp["document_id"] for sp in snapshot["sources"]}
    docids.update(
        c["document_id"] for c in snapshot["comments"] if c.get("document_id")
    )
    snapshot["documents"] = [
        {k: v for k, v in d.items() if k != "email"}
        for d in snapshot["documents"]
        if d["id"] in docids
    ]
    token = secrets.token_urlsafe(32)
    row = RecordShare(
        order_id=order.id,
        token_hash=sha256(token.encode()).hexdigest(),
        snapshot=snapshot,
        expires_at=now() + timedelta(days=30),
    )
    s.add(row)
    s.flush()
    return {"token": token, "id": row.id, "expires_at": row.expires_at.isoformat()}


def shared(s, token):
    row = s.scalar(
        select(RecordShare).where(
            RecordShare.token_hash == sha256(token.encode()).hexdigest()
        )
    )
    if not row or row.revoked or row.expires_at < now():
        fail("This record link has expired or been revoked.", 404)
    return row


def coordinate(s, order, record, body, kind, item_id=""):
    from .automation import mutate

    if auth_mode() == "clerk":
        body = body.model_copy(update={"actor": current_actor()})
    if body.expected_version != record.version:
        fail("The record changed. Refresh before applying this action.", 409)
    data = deepcopy(record.data)
    if kind == "undo":
        activity = next(
            (
                x
                for x in data.get("coordination", {}).get("activity", [])
                if x["id"] == item_id
            ),
            {},
        )
        undo = activity.get("undo") or {}
        if undo.get("type") == "notice" and s.scalar(
            scoped(NoticeDelivery).where(
                NoticeDelivery.order_id == order.id,
                NoticeDelivery.notice_id == undo.get("after", {}).get("id"),
            )
        ):
            fail(
                "This notice has entered the delivery queue and can no longer be undone.",
                409,
            )
    docs, sources = source_data(s, order)
    mutate(data, docs, sources, body, kind, item_id)
    if data != record.data:
        record.data = data
        record.version += 1
        order.updated_at = now()
        s.flush()
    return view(s, order, record)
