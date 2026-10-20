"""A finished, cited response for returned files without a captured mailbox thread.

Uses the same durable draft worker and evidence validation as Outlook. Polling a
view never starts model work. This path cannot create or send a mailbox message.
"""

import logging
import os
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from backend.agents import gateway
from backend.domain.service import digest, scoped
from backend.identity import Identity, identity_scope
from backend.orders.service import lock_order
from backend.records import service as records
from backend.storage.db import Session
from backend.storage.models import Job, OrderRecord, now

from . import service
from .automatic import receipts_for_order
from .models import ReplyDraft
from .schemas import GenerateReply

logger = logging.getLogger(__name__)
INSTRUCTION = (
    "Prepare one concise customer-facing response to the latest returned comments. "
    "Cover every actionable comment in the focused returned files, grouping related "
    "points. Answer what the records support and write specific clarification "
    "questions for missing information. Do not promise engineering changes, "
    "scope, price, approval or release. Earlier revisions are history. Clearly "
    "attribute customer assertions. Record material conflicts separately. "
    "This is a draft for review; no email thread or recipient is connected."
)


def enabled(record):
    # Legacy orders predate the enabled-by-default setting. An explicit opt-out
    # still wins; it must not be undone by a background refresh.
    return record.data.get("agent_settings", {}).get("enabled", True)


def draft_query(order):
    return (
        scoped(ReplyDraft)
        .where(
            ReplyDraft.order_id == order.id,
            ReplyDraft.internet_message_id == "",
            ReplyDraft.request_key.startswith("auto-order:"),
        )
        .order_by(ReplyDraft.created_at.desc())
    )


def focused_files(w):
    latest = w.get("revision_review", {}).get("latest_revision")
    comments = [
        c
        for c in w["comments"]
        if c.get("status") != "closed" and (not latest or c.get("revision") == latest)
    ]
    return list(
        dict.fromkeys(c["document_id"] for c in comments if c.get("document_id"))
    )


def preparation(s, order):
    record = records.ensure(s, order)
    checkpoint = record.data.get("reply_packet", {})
    row = s.scalar(draft_query(order).limit(1))
    if not row and not checkpoint:
        return {
            "prepared_reply": None,
            "preparation": {
                "status": "not_needed",
                "detail": "No prepared response is saved yet.",
                "automatic": True,
            },
            "email_subject": None,
            "outlook_url": None,
            "delivery": "document",
        }
    reply = service.current_view(s, row) if row else None
    # Context can return to an earlier fingerprint (for example, after a
    # presentation-only field is removed). The worker reuses that durable run;
    # reading only the newest row would leave the UI refreshing forever.
    if reply and reply["status"] == "needs_context":
        try:
            w = records.view(s, order, record)
            focused = focused_files(w)
            fingerprint = service.context(s, order, INSTRUCTION, focused)[-1]
            key = "auto-order:" + digest([order.id, fingerprint, focused])
            current = s.scalar(
                draft_query(order).where(ReplyDraft.request_key == key).limit(1)
            )
            if current and current.id != row.id:
                row, reply = current, service.current_view(s, current)
        except HTTPException:
            pass  # Keep the latest source error; a GET must never schedule work.
    if reply and reply["status"] in {"ready", "approved", "generating"}:
        state = service.preparation_for_draft(s, row)
    elif (
        reply
        and reply["status"] == "needs_context"
        and row.status in {"ready", "approved", "generating"}
    ):
        w = records.view(s, order, record)
        connected_status = service.connected_sources.summary(s, order)["status"]
        unreadable, _ = service._source_processing(w)
        if not enabled(record):
            state = {
                "status": "blocked",
                "detail": "The evidence changed and automatic preparation is paused for this order.",
                "automatic": True,
            }
        elif not focused_files(w):
            state = {
                "status": "not_needed",
                "detail": "No open returned comments remain. Earlier responses are retained in history.",
                "automatic": True,
            }
        else:
            blocked = connected_status not in {
                "not_configured",
                "current",
                "pending",
                "syncing",
                "stale",
            } or any(
                d["state"] not in {"queued", "processing", "parsing"}
                for d in unreadable
            )
            state = {
                "status": "blocked" if blocked else "queued",
                "detail": reply["error"],
                "automatic": True,
            }
    elif row:
        state = service.preparation_for_draft(s, row)
    else:
        state = checkpoint or {
            "status": "queued" if enabled(record) else "not_needed",
            "detail": "Rivet will prepare a response when returned comments are available.",
            "automatic": True,
        }
    return {
        "prepared_reply": reply
        if reply and reply["status"] in {"ready", "approved"}
        else None,
        "preparation": state,
        "email_subject": row.subject
        if row and reply and reply["status"] == "ready"
        else None,
        "outlook_url": None,
        "delivery": "document",
    }


def reconcile(s, order):
    record = records.ensure(s, order)
    if not enabled(record) or s.scalar(receipts_for_order(s, order).limit(1)):
        return {
            "status": "not_needed",
            "detail": "Mailbox preparation handles this order.",
        }
    from backend.equipment.ownership import for_order as native_work

    if native := native_work(s, order):
        return native
    w = records.view(s, order, record)
    focused = focused_files(w)
    if not focused:
        return {"status": "not_needed", "detail": "No returned comments to respond to."}
    if not gateway.configured():
        return {
            "status": "blocked",
            "detail": "Connect Rivet’s AI service to prepare responses.",
        }
    try:
        w, data, _, fingerprint = service.context(s, order, INSTRUCTION, focused)
    except HTTPException as exc:
        return {
            "status": "queued"
            if service.connected_sources.summary(s, order)["status"]
            in {"pending", "syncing", "stale"}
            else "blocked",
            "detail": str(exc.detail),
        }
    pending, _ = service._source_processing(w)
    if pending or not data["sources"]:
        return {
            "status": "queued"
            if pending
            and all(d["state"] in {"queued", "processing", "parsing"} for d in pending)
            else "blocked",
            "detail": "Reading the returned files."
            if pending
            else "No source excerpts are available.",
        }
    body = GenerateReply(
        order_id=order.id, instruction=INSTRUCTION, focus_document_ids=focused
    )
    key = "auto-order:" + digest([order.id, fingerprint, focused])
    draft, queued = service.begin(s, body, key)
    if queued and not s.scalar(scoped(Job).where(Job.key == "reply-draft:" + draft.id)):
        s.add(
            Job(
                project_id=order.project_id,
                kind="reply_draft",
                key="reply-draft:" + draft.id,
                payload={"draft_id": draft.id},
            )
        )
        s.flush()
    return service.preparation_for_draft(s, draft)


def schedule(limit=5):
    """Rotate through enabled records; evidence fingerprints deduplicate work."""
    if os.getenv("RIVET_AGENT_AUTORUN", "true").lower() != "true":
        return []
    with Session() as s:
        rows = [
            (r.organization_id, r.order_id)
            for r in s.scalars(
                select(OrderRecord).order_by(
                    func.coalesce(
                        OrderRecord.data["reply_packet"]["checked_at"].astext, ""
                    )
                )
            )
            if enabled(r)
        ][:limit]
    results = []
    for tenant, oid in rows:
        with identity_scope(Identity(tenant, "Rivet response coordinator")):
            try:
                with Session.begin() as s:
                    order = lock_order(s, oid)
                    record = records.ensure(s, order)
                    checked = record.data.get("reply_packet", {}).get("checked_at")
                    if (
                        checked
                        and checked > (now() - timedelta(seconds=30)).isoformat()
                    ):
                        continue
                    state = {
                        **reconcile(s, order),
                        "automatic": True,
                        "checked_at": now().isoformat(),
                    }
                    record.data = {**record.data, "reply_packet": state}
                    results.append(state)
            except Exception as exc:  # noqa: BLE001 - one order must not block the queue
                logger.warning(
                    "Order response %s could not reconcile (%s)",
                    oid,
                    type(exc).__name__,
                )
    return results
