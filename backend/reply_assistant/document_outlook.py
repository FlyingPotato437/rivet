"""Explicitly save file-based responses as unaddressed native Outlook drafts.

No automatic send, no invented thread or recipient, no blind retry after an
unknown create outcome. Existing native drafts and human edits are preserved.
"""

from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text

from backend.autopilot import outlook
from backend.autopilot.models import OutlookDraft
from backend.autopilot.service import _client, _current_hash, view
from backend.company_context.service import require_writer
from backend.domain.service import digest, fail, get, scoped
from backend.mailbox.graph import GraphError
from backend.mailbox.models import MailboxConnection
from backend.storage.db import Session
from backend.storage.models import Order, now

from . import service
from .models import ReplyDraft


def native_for_order(s, order_id, reply_id):
    own = s.scalar(
        scoped(OutlookDraft).where(
            OutlookDraft.reply_draft_id == reply_id, OutlookDraft.receipt_id.is_(None)
        )
    )
    return own or s.scalar(
        scoped(OutlookDraft)
        .where(
            OutlookDraft.order_id == order_id,
            OutlookDraft.receipt_id.is_(None),
            OutlookDraft.status.in_(
                ["created", "existing_draft", "creating", "manual_attention"]
            ),
        )
        .order_by(OutlookDraft.created_at.desc())
        .limit(1)
    )


def metadata(s, order_id):
    order = get(s, Order, order_id)
    data = service.order_preparation(s, order)
    reply = data.get("prepared_reply") if data.get("delivery") == "document" else None
    native = native_for_order(s, order.id, reply["id"]) if reply else None
    native_view = view(native) if native else None
    if native and native.reply_draft_id != reply["id"]:
        native_view = {
            **native_view,
            "error": "An earlier Outlook draft is preserved, including your edits. Compare it with the updated response here before sending.",
        }
    return {
        "reply_id": reply["id"] if reply else None,
        "mailboxes": [
            {"id": c.id, "mailbox": c.mailbox}
            for c in s.scalars(
                scoped(MailboxConnection)
                .where(MailboxConnection.enabled.is_(True))
                .order_by(MailboxConnection.mailbox)
            )
        ],
        "draft": native_view,
    }


def save(order_id, reply_id, connection_id=None, *, client_factory=None):
    require_writer()
    lease = str(uuid4())
    with Session.begin() as s:
        order = get(s, Order, order_id)
        s.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": int(digest([order.id, "document-outlook"])[:15], 16)},
        )
        current = metadata(s, order.id)
        if current["reply_id"] != reply_id:
            fail(
                "The prepared response changed. Refresh before saving an Outlook draft.",
                409,
            )
        reply = get(s, ReplyDraft, reply_id, True)
        if reply.order_id != order.id or reply.internet_message_id:
            fail(
                "Use the existing customer email's Outlook reply for this response.",
                409,
            )
        row = native_for_order(s, order.id, reply.id)
        if row:
            row = get(s, OutlookDraft, row.id, True)
        existing = bool(row and row.graph_draft_id)
        if row and row.status in {"sent", "discarded"}:
            fail(
                "This Outlook draft was already sent or removed. Its history is preserved; another draft was not created.",
                409,
            )
        if (
            row
            and not existing
            and (row.status == "manual_attention" or row.attempts >= 8)
        ):
            fail(
                "The earlier Outlook request has an unconfirmed outcome. Check Outlook Drafts before creating another message.",
                409,
            )
        if row and row.status == "creating" and not existing:
            if row.lease_until and row.lease_until > now():
                fail(
                    "This response is already being saved to Outlook. Try opening it shortly.",
                    409,
                )
            row.status, row.error, row.attempts = (
                "manual_attention",
                "The previous save was interrupted; check Outlook Drafts before retrying.",
                8,
            )
            # Commit this guard before reporting the interruption.
            interrupted = True
        else:
            interrupted = False
        if not interrupted:
            mailboxes = current["mailboxes"]
            selected = (
                row.connection_id
                if existing
                else connection_id
                or (mailboxes[0]["id"] if len(mailboxes) == 1 else None)
            )
            if not selected or not any(m["id"] == selected for m in mailboxes):
                fail(
                    "Choose a connected Outlook mailbox."
                    if mailboxes
                    else "Connect Outlook in Workspace settings before saving a native draft.",
                    409,
                )
            connection = get(s, MailboxConnection, selected)
            if not row:
                row = OutlookDraft(
                    order_id=order.id,
                    reply_draft_id=reply.id,
                    connection_id=connection.id,
                )
                s.add(row)
                s.flush()
            if not existing:
                row.connection_id = connection.id
                row.status, row.lease, row.lease_until = (
                    "creating",
                    lease,
                    now() + timedelta(minutes=5),
                )
                row.attempts += 1
                row.notes = [
                    "Saved at the user's request from uploaded files. No recipient was selected and no source files were attached."
                ]
            row_id, native_id, body = row.id, row.graph_draft_id, reply.body
            subject = reply.subject or "Prepared response"
            if order.number.lower() not in subject.lower():
                subject = f"{order.number} · {subject}"
            snapshot = {
                "organization_id": connection.organization_id,
                "mailbox": connection.mailbox,
                "tenant": connection.microsoft_tenant_id,
            }
    if interrupted:
        fail(
            "The previous save was interrupted. Check Outlook Drafts; Rivet has not created another message.",
            409,
        )

    graph, attempted = None, False
    try:
        graph = (client_factory or _client)(snapshot)
        graph.headers()  # Authenticate before attempting a write.
        if not existing:
            with Session() as s:
                reply = get(s, ReplyDraft, reply_id)
                if _current_hash(s, reply) != reply.review.get(
                    "reviewed_evidence_hash", reply.evidence_hash
                ):
                    fail(
                        "The source evidence changed. Wait for the updated response before saving.",
                        409,
                    )
            attempted = True
            message = outlook._call(
                graph,
                "POST",
                outlook._url(graph, "messages"),
                {
                    "subject": subject,
                    "body": {
                        "contentType": "html",
                        "content": outlook.reply_html(body),
                    },
                    "toRecipients": [],
                },
            )
            native_id = message.get("id")
            if not isinstance(native_id, str) or not native_id:
                raise GraphError(
                    "unknown_outcome",
                    "Microsoft did not return the saved draft identity.",
                )
            with Session.begin() as s:
                row = get(s, OutlookDraft, row_id, True)
                row.graph_draft_id, row.body_text = native_id, body
                row.status, row.drafted_at, row.error = "created", now(), ""
                row.lease, row.lease_until = "", None
                row.next_attempt_at = now() + timedelta(minutes=1)
                try:
                    saved_reply = get(s, ReplyDraft, reply_id)
                    if _current_hash(s, saved_reply) != saved_reply.review.get(
                        "reviewed_evidence_hash", saved_reply.evidence_hash
                    ):
                        row.error = "Sources changed while this draft was saved. Check the updated project before sending."
                except HTTPException:
                    row.error = "Sources changed while this draft was saved. Check the updated project before sending."
        message, target = outlook.open_target(graph, native_id)
        if not message.get("isDraft") or outlook.is_deleted(graph, message):
            fail(
                "The saved draft was sent or removed. Open its current state in Outlook.",
                409,
            )
        with Session() as s:
            return {"draft": {**view(get(s, OutlookDraft, row_id)), **target}}
    except (GraphError, HTTPException) as exc:
        with Session.begin() as s:
            row = get(s, OutlookDraft, row_id, True)
            if not row.graph_draft_id:
                rejected = not attempted or (
                    isinstance(exc, GraphError)
                    and exc.status in {400, 401, 403, 404, 413, 422, 429}
                )
                row.status = (
                    "permission_required"
                    if isinstance(exc, GraphError) and exc.status == 403
                    else "failed"
                    if rejected
                    else "manual_attention"
                )
                row.error = (
                    "Microsoft could not save this draft. Check the mailbox connection."
                    if rejected
                    else "The save outcome is unconfirmed. Check Outlook Drafts; Rivet will not create a duplicate."
                )
                if not rejected:
                    row.attempts = 8
                row.lease, row.lease_until = "", None
        if isinstance(exc, HTTPException):
            raise
        fail(
            "Microsoft could not open or save the draft. Its saved state is preserved; check Outlook Drafts before retrying.",
            502,
        )
    finally:
        if graph:
            graph.close()
