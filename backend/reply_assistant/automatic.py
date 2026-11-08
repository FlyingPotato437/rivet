"""Durable preparation for captured order mail; never sends or changes Outlook.

The scheduler is a reconciler, not a browser callback. Receipts are revisited after
parsing, source synchronization, and reviewed customer guidance change. A saved
reply is reused only for exactly the same email, order, and evidence fingerprint.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from backend.agents import gateway
from backend.domain.service import digest, get, scoped
from backend.identity import Identity, identity_scope
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.mailbox.service import find_receipt_by_message_id
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Document, Job, RecordIntake, now

from .models import ReplyDraft
from .schemas import GenerateReply

logger = logging.getLogger(__name__)

INSTRUCTION = (
    "Prepare a response to this captured customer email and its returned comments."
)


def received_order():
    return func.coalesce(
        func.nullif(MailboxReceipt.data["received_at"].astext, ""),
        func.to_char(
            MailboxReceipt.created_at.op("AT TIME ZONE")("UTC"),
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"',
        ),
    )


def thread_key():
    return func.coalesce(
        func.nullif(MailboxReceipt.data["conversation_id"].astext, ""),
        MailboxReceipt.internet_message_id,
    )


def historical(receipt):
    raw = receipt.data.get("received_at", "")
    try:
        received = datetime.fromisoformat(raw) if raw else receipt.created_at
        if received.tzinfo is None:
            received = received.replace(tzinfo=UTC)
        return received < now() - timedelta(days=7)
    except (ValueError, TypeError):
        # Unusable provider timestamps must not hide a newly captured request.
        return False


def latest_in_thread(s, order, receipt):
    key = receipt.data.get("conversation_id") or receipt.internet_message_id
    return s.scalar(receipts_for_order(s, order).where(thread_key() == key).limit(1))


def receipts_for_order(s, order):
    return (
        scoped(MailboxReceipt)
        .join(RecordIntake, MailboxReceipt.intake_id == RecordIntake.id)
        .join(MailboxConnection, MailboxReceipt.connection_id == MailboxConnection.id)
        .where(
            RecordIntake.order_id == order.id,
            RecordIntake.organization_id == order.organization_id,
            MailboxConnection.organization_id == order.organization_id,
            MailboxConnection.enabled.is_(True),
            MailboxReceipt.status.in_(["imported", "needs_confirmation"]),
            MailboxReceipt.internet_message_id != "",
            func.coalesce(MailboxReceipt.data["automatic_reply"]["reason"].astext, "")
            != "acknowledgement",
        )
        .order_by(received_order().desc(), MailboxReceipt.created_at.desc())
    )


def focus_documents(s, order, receipt):
    from backend.records.email_intake import email_document_ids

    intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
    if not intake or intake.order_id != order.id:
        return []
    root_id = intake.data.get("document_id")
    if not root_id:
        return []
    root = get(s, Document, root_id)
    if root.project_id != order.project_id:
        return []
    return email_document_ids(s, order, root)


def acknowledgement_only(s, intake):
    """Suppress only unmistakable short acknowledgements, never broad intent guesses."""
    from backend.records.intake_preview import preview

    incoming = preview(s, intake)
    if (
        incoming.get("kind") != "email"
        or incoming.get("attachments")
        or incoming.get("error")
    ):
        return False
    # Ignore an old quoted thread, but retain all newly written paragraphs.
    text = re.split(
        r"(?im)^On .{1,300}wrote:|^From:\s|^>", incoming.get("body", ""), maxsplit=1
    )[0].strip()
    text = re.sub(
        r"(?is)\n(?:best(?: regards)?|regards|sincerely)[,!.]?\s*\n[^\n]{1,80}$",
        "",
        text,
    ).strip()
    return bool(
        re.fullmatch(
            r"(?:thanks(?: you)?|thank you|received(?: with thanks)?|noted(?: with thanks)?|acknowledged|got it|thanks,? (?:received|noted))[.!\s]*",
            text,
            re.IGNORECASE,
        )
    )


def _state(status, detail, **extra):
    return {"status": status, "detail": detail, "automatic": True, **extra}


def _save(receipt, state):
    receipt.data = {
        **receipt.data,
        "automatic_reply": {**state, "checked_at": now().isoformat()},
    }
    return state


def completed_outlook_reply(s, receipt):
    """A normal Outlook send/delete finishes this email, even as the order evolves."""
    from backend.autopilot.models import OutlookDraft

    native = s.scalar(
        scoped(OutlookDraft)
        .where(
            OutlookDraft.receipt_id == receipt.id,
            OutlookDraft.status.in_(["sent", "discarded"]),
        )
        .order_by(OutlookDraft.created_at.desc())
        .limit(1)
    )
    if native:
        return _state(
            "not_needed",
            "The reply was sent from Outlook. Its sent record is saved."
            if native.status == "sent"
            else "This Outlook draft was removed. Rivet will not recreate it.",
            reason="outlook_" + native.status,
            draft_id=native.reply_draft_id,
        )
    return None


def existing_lifecycle_draft(s, order, receipt):
    """Reconcile an already placed lifecycle handoff without preparing anew."""
    from backend.autopilot.models import OutlookDraft
    from backend.lifecycle.outlook_handoff import validate as validate_handoff

    if not receipt.internet_message_id:
        return None
    replies = s.scalars(
        scoped(ReplyDraft)
        .where(
            ReplyDraft.order_id == order.id,
            ReplyDraft.internet_message_id == receipt.internet_message_id,
            ReplyDraft.request_key.like("lifecycle-mail:%"),
        )
        .order_by(ReplyDraft.created_at.desc())
        .limit(5)
    )
    for reply in replies:
        handoff = reply.context.get("lifecycle_handoff", {})
        if handoff.get("receipt_id") != receipt.id:
            continue
        native = s.scalar(
            scoped(OutlookDraft)
            .where(OutlookDraft.reply_draft_id == reply.id)
            .limit(1)
        )
        if not native or not native.graph_draft_id:
            continue
        if native.status == "superseded":
            return _state(
                "superseded",
                "A newer customer email replaced this proposal. The existing Outlook draft was preserved for review.",
                reason="lifecycle_handoff_superseded",
                draft_id=reply.id,
            )
        if native.status not in {"created", "existing_draft"}:
            continue
        from backend.autopilot.service import _newer_in_thread

        if _newer_in_thread(s, receipt):
            return _state(
                "superseded",
                "A newer customer email replaced this proposal. The existing Outlook draft was preserved for review.",
                reason="newer_customer_email",
                draft_id=reply.id,
            )
        try:
            validate_handoff(s, order, reply)
        except HTTPException:
            return _state(
                "blocked",
                "The existing Outlook draft is no longer backed by current approved work. Review the package and draft before continuing.",
                reason="lifecycle_handoff_changed",
                draft_id=reply.id,
            )
        return _state(
            "ready",
            "The current approved proposal is already in Outlook Drafts for human review.",
            reason="lifecycle_handoff_in_outlook",
            draft_id=reply.id,
            package_id=handoff["package_id"],
        )
    return None


def reconcile_receipt(s, receipt):
    """Stage one idempotent proposal after all required evidence is available."""
    from backend.project_flow.service import answered, is_sent_copy, set_aside_sent_copy

    from . import service

    s.execute(
        select(
            func.pg_advisory_xact_lock(
                int(
                    digest([receipt.organization_id, "auto-reply", receipt.id])[:15], 16
                )
            )
        )
    )
    receipt = s.scalar(
        scoped(MailboxReceipt)
        .where(MailboxReceipt.id == receipt.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if is_sent_copy(s, receipt):
        set_aside_sent_copy(s, receipt)
        return _save(
            receipt,
            _state(
                "not_needed",
                "This is a copy of your team’s sent reply.",
                reason="outgoing_copy",
            ),
        )
    if answered(s, receipt):
        return _save(
            receipt,
            _state(
                "not_needed",
                "A reply was already sent from Outlook. Rivet is watching for the customer’s answer.",
                reason="answered_in_outlook",
            ),
        )
    if (
        receipt.status not in {"imported", "needs_confirmation"}
        or not receipt.intake_id
    ):
        return _save(
            receipt, _state("not_needed", "No confirmed order email to prepare.")
        )
    connection = get(s, MailboxConnection, receipt.connection_id)
    intake = get(s, RecordIntake, receipt.intake_id)
    if (
        not connection.enabled
        or not intake.order_id
        or intake.status in {"ignored", "dismissed", "not_order_related", "set_aside"}
    ):
        return _save(
            receipt,
            _state(
                "not_needed", "This email is not attached to an active order workflow."
            ),
        )
    if (
        not receipt.internet_message_id
        or find_receipt_by_message_id(s, receipt.internet_message_id) is None
    ):
        return _save(
            receipt,
            _state(
                "blocked",
                "This email does not have a unique captured message identifier.",
            ),
        )
    if completed := completed_outlook_reply(s, receipt):
        return _save(receipt, completed)
    order = lock_order(s, intake.order_id)
    focused = focus_documents(s, order, receipt)
    if not focused:
        return _save(
            receipt,
            _state(
                "blocked",
                "The captured email source is not available in this order yet.",
            ),
        )
    if acknowledgement_only(s, intake):
        return _save(
            receipt,
            _state(
                "not_needed",
                "Acknowledgement recorded. No reply is needed.",
                reason="acknowledgement",
            ),
        )
    if historical(receipt):
        return _save(
            receipt,
            _state(
                "not_needed",
                "Historical email retained for context. Generate a reply in Outlook if you need one.",
                reason="historical",
            ),
        )
    from backend.equipment.ownership import for_receipt as native_work

    if native := native_work(s, order, receipt):
        return _save(receipt, native)
    if existing := existing_lifecycle_draft(s, order, receipt):
        return _save(receipt, existing)
    from backend.lifecycle import outlook_handoff

    commercial = outlook_handoff.commercial_work_for_receipt(s, order, receipt)
    if commercial:
        state = outlook_handoff.waiting_state(commercial)
        if len(commercial) == 1 and commercial[0]["status"] == "approved":
            handoff = outlook_handoff.enqueue_if_unambiguous(s, order, commercial[0])
            if handoff["status"] == "queued":
                state.update(
                    status="queued",
                    draft_id=handoff["reply_id"],
                    detail="The approved response and commercial PDFs are queued for Outlook.",
                )
            elif handoff["status"] == "blocked":
                state.update(status="blocked", detail=handoff["message"])
        return _save(receipt, state)
    from . import commercial

    planned = commercial.plan(s, order, receipt)
    if planned:
        if planned["status"] == "ready":
            planned = commercial.prepare(s, order, receipt, planned)
        return _save(receipt, planned)
    from backend.routine_status.service import prepare_for_receipt

    routine = prepare_for_receipt(s, order, receipt)
    if routine:
        return _save(receipt, routine)
    if not gateway.configured():
        return _save(
            receipt,
            _state(
                "blocked",
                "Connect Rivet's AI service to prepare replies automatically.",
            ),
        )
    try:
        workspace, data, _company, evidence_hash = service.context(
            s, order, INSTRUCTION, focused
        )
    except HTTPException as exc:
        connected = service.connected_sources.summary(s, order)
        pending = connected.get("status") in {"pending", "syncing", "stale"}
        return _save(
            receipt,
            _state(
                "queued" if pending else "blocked",
                "Checking the order's connected sources before preparing your reply."
                if pending
                else str(exc.detail),
            ),
        )
    pending, _ = service._source_processing(workspace)
    if pending or not data["sources"]:
        processing = pending and all(
            d["state"] in {"queued", "processing", "parsing"} for d in pending
        )
        return _save(
            receipt,
            _state(
                "queued" if processing else "blocked",
                "Reading the email and its supporting documents."
                if processing
                else "A source could not be read. Resolve the affected file before Rivet prepares the reply.",
            ),
        )
    # Use the latest compatible manual or automatic result. A rejected draft is
    # deliberately left rejected; automatic work must not undo a person's choice.
    candidates = s.scalars(
        scoped(ReplyDraft)
        .where(
            ReplyDraft.order_id == order.id,
            ReplyDraft.internet_message_id == receipt.internet_message_id,
        )
        .order_by(ReplyDraft.created_at.desc())
        .limit(5)
    )
    for candidate in candidates:
        try:
            current_hash = service.context(
                s,
                order,
                candidate.context["instruction"],
                candidate.context.get("focus_document_ids", []),
            )[-1]
        except (HTTPException, KeyError):
            continue
        if current_hash == candidate.review.get(
            "reviewed_evidence_hash", candidate.evidence_hash
        ):
            state = service.preparation_for_draft(s, candidate)
            return _save(receipt, {**state, "evidence_hash": current_hash})
    body = GenerateReply(
        order_id=order.id,
        internet_message_id=receipt.internet_message_id,
        instruction=INSTRUCTION,
    )
    key = "auto-mail:" + digest([receipt.id, order.id, evidence_hash])
    draft, queued = service.begin(s, body, key)
    if queued:
        # begin also serializes on the deterministic key. Existing jobs are never
        # recreated by a worker restart, a repeated poll, or opening the add-in.
        existing = s.scalar(scoped(Job).where(Job.key == "reply-draft:" + draft.id))
        if not existing:
            s.add(
                Job(
                    project_id=order.project_id,
                    kind="reply_draft",
                    key="reply-draft:" + draft.id,
                    payload={"draft_id": draft.id},
                )
            )
            s.flush()
    return _save(
        receipt,
        {
            **service.preparation_for_draft(s, draft),
            "evidence_hash": draft.evidence_hash,
        },
    )


def schedule_order(s, order, limit=1):
    # Kick-start one receipt after a source refresh. The rotating worker covers
    # every other current thread in separate transactions (receipt -> order locks).
    rows = s.scalars(receipts_for_order(s, order).limit(100))
    seen = set()
    for receipt in rows:
        key = receipt.data.get("conversation_id") or receipt.internet_message_id
        if key in seen:
            continue
        seen.add(key)
        reconcile_receipt(s, receipt)
        if len(seen) >= limit:
            break


def schedule(limit=10):
    """Bounded, rotating tenant-scoped reconciliation in the worker's mail loop."""
    with Session() as s:
        latest = (
            select(
                MailboxReceipt.id.label("receipt_id"),
                MailboxReceipt.organization_id.label("organization_id"),
                MailboxReceipt.data["automatic_reply"]["checked_at"].astext.label(
                    "checked_at"
                ),
                MailboxReceipt.created_at.label("created_at"),
                func.row_number()
                .over(
                    partition_by=[RecordIntake.order_id, thread_key()],
                    order_by=[
                        received_order().desc(),
                        MailboxReceipt.created_at.desc(),
                    ],
                )
                .label("position"),
            )
            .join(RecordIntake, MailboxReceipt.intake_id == RecordIntake.id)
            .join(
                MailboxConnection, MailboxReceipt.connection_id == MailboxConnection.id
            )
            .where(
                MailboxConnection.enabled.is_(True),
                MailboxConnection.organization_id == MailboxReceipt.organization_id,
                RecordIntake.organization_id == MailboxReceipt.organization_id,
                RecordIntake.order_id.is_not(None),
                MailboxReceipt.status.in_(["imported", "needs_confirmation"]),
                MailboxReceipt.internet_message_id != "",
                func.coalesce(
                    MailboxReceipt.data["automatic_reply"]["reason"].astext, ""
                )
                != "acknowledgement",
            )
            .subquery()
        )
        rows = list(
            s.execute(
                select(latest.c.receipt_id, latest.c.organization_id)
                .where(latest.c.position == 1)
                .order_by(func.coalesce(latest.c.checked_at, ""), latest.c.created_at)
                .limit(limit)
            )
        )
    results = []
    for receipt_id, organization_id in rows:
        with identity_scope(Identity(organization_id, "Rivet reply coordinator")):
            try:
                with Session.begin() as s:
                    receipt = s.scalar(
                        scoped(MailboxReceipt).where(MailboxReceipt.id == receipt_id)
                    )
                    if receipt is None:
                        continue
                    checked = receipt.data.get("automatic_reply", {}).get("checked_at")
                    if (
                        checked
                        and checked > (now() - timedelta(seconds=30)).isoformat()
                    ):
                        continue
                    results.append(reconcile_receipt(s, receipt))
            except Exception as exc:  # noqa: BLE001 - isolate each durable receipt
                # One bad original or configuration must not block other orders.
                # Roll back first, then checkpoint a bounded, non-sensitive error.
                logger.warning(
                    "Automatic reply receipt %s needs attention (%s)",
                    receipt_id,
                    type(exc).__name__,
                )
                try:
                    with Session.begin() as s:
                        receipt = get(s, MailboxReceipt, receipt_id, True)
                        results.append(
                            _save(
                                receipt,
                                _state(
                                    "blocked",
                                    "Rivet could not prepare this email. Its saved source or connection needs attention.",
                                ),
                            )
                        )
                except Exception as checkpoint_error:  # noqa: BLE001 - retain loop isolation
                    logger.warning(
                        "Could not checkpoint reply receipt %s (%s)",
                        receipt_id,
                        type(checkpoint_error).__name__,
                    )
    return results
