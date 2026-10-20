"""Rivet's autopilot: put prepared replies into Outlook and learn from what people send.

The person's normal action is the decision. Rivet places an unsent reply in the
customer's thread; sending it is the approval, editing it is the correction, and
deleting it means it was not needed. Rivet never sends mail to a customer.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from backend.company_context import service as learning
from backend.company_context.schemas import ReuseReview
from backend.domain.service import fail, get, scoped
from backend.identity import (
    Identity,
    current_actor,
    current_identity,
    current_org,
    identity_scope,
)
from backend.mailbox.graph import GraphClient, GraphError
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.mailbox.service import find_receipt_by_message_id, receipt_view
from backend.reply_assistant import attachments, automatic
from backend.reply_assistant import service as replies
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session
from backend.storage.models import Order, RecordIntake, now

from . import outlook
from .models import AutopilotSettings, OutlookDraft

logger = logging.getLogger(__name__)
ACTOR = "Rivet autopilot"
MAX_ATTEMPTS = 8
MISSING_GRACE = timedelta(minutes=10)
DEFAULTS = {"draft_in_outlook": True, "tag_in_outlook": True, "learn_from_sent": "auto"}
PERMISSION = (
    "Rivet can read this mailbox but cannot write drafts. A Microsoft 365 administrator "
    "must grant the Rivet app Mail.ReadWrite on the connected mailbox."
)
SET_ASIDE = {
    "set_aside",
    "not_order_related",
    "excluded_draft",
    "excluded_internal_update",
}


# ----------------------------------------------------------------------------- settings
def settings(s):
    row = s.scalar(scoped(AutopilotSettings))
    if row is None:
        return {**DEFAULTS, "updated_by": "", "updated_at": None}
    return {
        "draft_in_outlook": row.draft_in_outlook,
        "tag_in_outlook": row.tag_in_outlook,
        "learn_from_sent": row.learn_from_sent,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def update_settings(s, body):
    if current_identity().role != "org:admin":
        fail("A team administrator must change what Rivet does on its own.", 403)
    row = s.scalar(scoped(AutopilotSettings).with_for_update())
    if row is None:
        row = AutopilotSettings()
        s.add(row)
    for key in DEFAULTS:
        value = getattr(body, key)
        if value is not None:
            setattr(row, key, value)
    row.updated_by, row.updated_at = current_actor(), now()
    s.flush()
    return settings(s)


# ----------------------------------------------------------------------------- views
def view(row):
    return {
        "id": row.id,
        "reply_draft_id": row.reply_draft_id,
        "order_id": row.order_id,
        "status": row.status,
        "attachments": row.attachments,
        "notes": row.notes,
        "tagged": row.tagged,
        "outcome": row.outcome,
        "learning_status": row.learning_status,
        "drafted_at": row.drafted_at.isoformat() if row.drafted_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "error": row.error,
    }


def draft_for_message(s, internet_message_id):
    """The Outlook draft Rivet placed for this email, if any (for the Outlook pane)."""
    receipt = find_receipt_by_message_id(s, internet_message_id)
    if receipt is None:
        return None
    row = s.scalar(
        scoped(OutlookDraft)
        .where(OutlookDraft.receipt_id == receipt.id)
        .order_by(
            (OutlookDraft.graph_draft_id != "").desc(), OutlookDraft.created_at.desc()
        )
    )
    return view(row) if row else None


def assert_manual_compose_available(s, reply):
    """The pane must not insert another draft while the worker owns placement."""
    if not reply.internet_message_id:
        return
    placed = s.scalar(
        scoped(OutlookDraft)
        .join(ReplyDraft, OutlookDraft.reply_draft_id == ReplyDraft.id)
        .where(
            ReplyDraft.organization_id == current_org(),
            ReplyDraft.internet_message_id == reply.internet_message_id,
            or_(
                OutlookDraft.graph_draft_id != "",
                OutlookDraft.status.in_(["creating", "created", "sent"]),
                (OutlookDraft.status == "failed")
                & (OutlookDraft.attempts >= MAX_ATTEMPTS),
            ),
        )
        .limit(1)
    )
    if placed:
        fail(
            "Rivet has already placed this reply in Outlook or is checking its placement. Open Outlook Drafts to continue; inserting another reply would create a duplicate.",
            409,
        )


def open_draft(internet_message_id, *, client_factory=None):
    """Resolve a user's explicit Open action; polling never makes Graph calls."""
    with Session() as s:
        saved = draft_for_message(s, internet_message_id)
        if not saved or saved["status"] not in {"created", "existing_draft"}:
            fail(
                "There is no active Rivet draft to open. Check Outlook Drafts for the latest reply.",
                409,
            )
        row = get(s, OutlookDraft, saved["id"])
        if not row.graph_draft_id or not row.connection_id:
            fail("The Outlook draft is not available yet. Try again shortly.", 409)
        connection = get(s, MailboxConnection, row.connection_id)
        if not connection.enabled:
            fail(
                "This mailbox connection is paused. Open the draft directly in Outlook.",
                409,
            )
        snapshot = {
            "organization_id": connection.organization_id,
            "mailbox": connection.mailbox,
            "tenant": connection.microsoft_tenant_id,
        }
        message_id = row.graph_draft_id
    graph = None
    try:
        graph = (client_factory or _client)(snapshot)
        message, target = outlook.open_target(graph, message_id)
        if not message.get("isDraft", False) or outlook.is_deleted(graph, message):
            fail(
                "This reply was sent, deleted or moved out of Drafts. Check its current state in Outlook.",
                409,
            )
        if not target["rest_item_id"] and not target["outlook_url"]:
            fail(
                "Microsoft did not return a link to this draft. Open Outlook Drafts directly.",
                409,
            )
        return {"draft": {**saved, **target}}
    except GraphError as exc:
        fail(
            "Microsoft could not open the existing draft. Check Outlook Drafts or try again shortly.",
            409 if exc.code == "not_found" else 502,
        )
    finally:
        close = getattr(graph, "close", None)
        if close:
            close()


# ----------------------------------------------------------------------------- delivery
def _client(connection):
    return GraphClient(
        connection["organization_id"], connection["mailbox"], connection["tenant"]
    )


def _due(limit):
    """Ready automatic replies without an Outlook draft, plus retries that are due."""
    with Session() as s:
        disabled = (
            select(AutopilotSettings.id)
            .where(
                AutopilotSettings.organization_id == ReplyDraft.organization_id,
                AutopilotSettings.draft_in_outlook.is_(False),
            )
            .exists()
        )
        placed = (
            select(OutlookDraft.id)
            .where(
                OutlookDraft.reply_draft_id == ReplyDraft.id,
                OutlookDraft.organization_id == ReplyDraft.organization_id,
            )
            .exists()
        )
        fresh = list(
            s.execute(
                select(ReplyDraft.id, ReplyDraft.organization_id)
                .where(
                    ReplyDraft.status == "ready",
                    or_(
                        ReplyDraft.request_key.like("auto-mail:%"),
                        ReplyDraft.request_key.like("lifecycle-mail:%"),
                    ),
                    ReplyDraft.internet_message_id != "",
                    ~placed,
                    ~disabled,
                )
                .order_by(ReplyDraft.created_at)
                .limit(limit)
            )
        )
        retry = list(
            s.execute(
                select(OutlookDraft.reply_draft_id, OutlookDraft.organization_id)
                .join(ReplyDraft, ReplyDraft.id == OutlookDraft.reply_draft_id)
                .where(
                    ~disabled,
                    OutlookDraft.receipt_id.is_not(None),
                    or_(
                        OutlookDraft.status.in_(
                            [
                                "pending",
                                "failed",
                                "permission_required",
                                "awaiting_approval",
                                "awaiting_preparation",
                            ]
                        ),
                        (OutlookDraft.status == "creating")
                        & (OutlookDraft.lease_until <= now()),
                    ),
                    OutlookDraft.next_attempt_at <= now(),
                    OutlookDraft.attempts < MAX_ATTEMPTS,
                )
                .order_by(OutlookDraft.next_attempt_at)
                .limit(limit)
            )
        )
    # Finish interrupted placements before newer work. Otherwise a steady stream
    # of fresh mail can starve an already-created Outlook draft forever.
    return (retry + fresh)[:limit]


def deliver(limit=5, *, client_factory=None):
    results = []
    for reply_id, organization_id in _due(limit):
        with identity_scope(Identity(organization_id, ACTOR)):
            try:
                results.append(deliver_one(reply_id, client_factory=client_factory))
            except Exception as exc:  # noqa: BLE001 - one reply must not stop the rest
                logger.warning(
                    "Outlook draft for %s needs attention (%s)",
                    reply_id,
                    type(exc).__name__,
                )
    return results


def _newer_in_thread(s, receipt):
    conversation = receipt.data.get("conversation_id")
    if not conversation:
        return False

    def received(item):
        try:
            value = datetime.fromisoformat(item.data.get("received_at", ""))
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return item.created_at

    candidates = s.scalars(
        scoped(MailboxReceipt).where(
            MailboxReceipt.organization_id == current_org(),
            MailboxReceipt.connection_id == receipt.connection_id,
            MailboxReceipt.id != receipt.id,
            MailboxReceipt.data["conversation_id"].astext == conversation,
            MailboxReceipt.status.in_(["imported", "needs_confirmation"]),
        )
    )
    # Capture time is not mail time: mailbox backfills must not supersede a
    # newer customer question with an older email. Acknowledgements also do not.
    for other in candidates:
        if received(other) <= received(receipt):
            continue
        intake = get(s, RecordIntake, other.intake_id) if other.intake_id else None
        if intake and (
            intake.status in SET_ASIDE or automatic.acknowledgement_only(s, intake)
        ):
            continue
        return True
    return False


def _manual_attention(row, reason):
    # createReply has no server idempotency key. An unknown outcome must never
    # cause another POST; preserving a possible draft beats silently duplicating it.
    row.attempts = MAX_ATTEMPTS
    return _close(row, "failed", reason)


def _checkpoint(row_id, lease, **values):
    with Session.begin() as s:
        row = get(s, OutlookDraft, row_id, True)
        if row.lease != lease:
            return False
        for name, value in values.items():
            setattr(row, name, value)
        row.lease_until = now() + timedelta(minutes=5)
        return True


def _still_current(reply_id):
    """Recheck after network setup; the original validation can be seconds old."""
    with Session() as s:
        reply = get(s, ReplyDraft, reply_id)
        receipt = _receipt_for_reply(s, reply)
        if (
            reply.status != "ready"
            or not receipt
            or not settings(s)["draft_in_outlook"]
        ):
            return False
        connection = get(s, MailboxConnection, receipt.connection_id)
        intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
        if (
            not connection.enabled
            or not intake
            or intake.order_id != reply.order_id
            or intake.status in SET_ASIDE | {"ignored", "dismissed"}
            or _newer_in_thread(s, receipt)
        ):
            return False
        from backend.equipment.ownership import for_receipt as native_work

        if native_work(s, get(s, Order, reply.order_id), receipt):
            return False
        if not reply.context.get("lifecycle_handoff"):
            from backend.lifecycle.outlook_handoff import commercial_work_for_receipt

            if commercial_work_for_receipt(
                s, get(s, Order, reply.order_id), receipt, preserve_existing=False
            ):
                return False
            if not reply.context.get("commercial_comparison"):
                from backend.reply_assistant.commercial import plan

                if plan(s, get(s, Order, reply.order_id), receipt):
                    return False
        try:
            return _current_hash(s, reply) == reply.evidence_hash
        except HTTPException:
            return False


def _receipt_for_reply(s, reply):
    handoff = reply.context.get("lifecycle_handoff")
    if handoff:
        return get(s, MailboxReceipt, handoff["receipt_id"])
    return find_receipt_by_message_id(s, reply.internet_message_id)


def _current_hash(s, reply):
    order = get(s, Order, reply.order_id)
    if reply.context.get("routine_status"):
        from backend.routine_status.service import validate

        validate(s, order, reply)
    if reply.context.get("lifecycle_handoff"):
        from backend.lifecycle.outlook_handoff import validate

        validate(s, order, reply)
    if reply.context.get("commercial_comparison"):
        from backend.reply_assistant.commercial import validate

        validate(s, order, _receipt_for_reply(s, reply), reply)
    return replies.context(
        s,
        order,
        reply.context["instruction"],
        reply.context.get("focus_document_ids", []),
    )[-1]


def _stop_changed_placement(row_id, lease):
    with Session.begin() as s:
        row = get(s, OutlookDraft, row_id, True)
        if row.lease != lease:
            return view(row)
        if row.graph_draft_id:
            return _manual_attention(
                row,
                "The order changed while Outlook was creating the draft. Rivet preserved it without writing an outdated reply. Check Outlook Drafts and the current record.",
            )
        return _close(
            row,
            "superseded",
            "The order, thread or mailbox policy changed before the Outlook write.",
        )


def _close(row, status, error=""):
    row.status, row.error, row.lease, row.lease_until = status, error, "", None
    return view(row)


def deliver_one(reply_id, *, client_factory=None):
    """Place one prepared reply in the customer's Outlook thread, unsent."""
    lease = str(uuid4())
    with Session.begin() as s:
        current = settings(s)
        if not current["draft_in_outlook"]:
            return {"status": "off"}
        reply = get(s, ReplyDraft, reply_id)
        receipt = _receipt_for_reply(s, reply)
        # Serialize different generated versions of the same received message.
        # Match the preparation worker's receipt -> reply lock order.
        if receipt is not None:
            receipt = get(s, MailboxReceipt, receipt.id, True)
        reply = get(s, ReplyDraft, reply_id, True)
        row = s.scalar(
            scoped(OutlookDraft)
            .where(OutlookDraft.reply_draft_id == reply.id)
            .with_for_update()
        )
        if row and (
            row.status
            in {"created", "existing_draft", "sent", "discarded", "superseded"}
            or (
                row.status == "creating" and row.lease_until and row.lease_until > now()
            )
        ):
            return view(row)
        if row is None:
            row = OutlookDraft(
                reply_draft_id=reply.id,
                order_id=reply.order_id,
                receipt_id=receipt.id if receipt else None,
                connection_id=receipt.connection_id if receipt else None,
            )
            s.add(row)
            s.flush()
        if row.status == "creating" and not row.graph_draft_id:
            return _manual_attention(
                row,
                "The previous Outlook draft request was interrupted. Check Outlook Drafts before retrying; Rivet will not create a possible duplicate.",
            )
        if row.attempts >= MAX_ATTEMPTS:
            return view(row)
        if receipt is None:
            return _close(
                row, "superseded", "The original email is no longer captured."
            )
        if reply.status != "ready":
            return _close(
                row, "superseded", "The PM already handled this reply in Rivet."
            )
        intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
        if (
            receipt.status not in {"imported", "needs_confirmation"}
            or not intake
            or intake.order_id != reply.order_id
            or intake.status in SET_ASIDE | {"ignored", "dismissed"}
        ):
            return _close(row, "superseded", "The email's order assignment changed.")
        prior = s.scalar(
            scoped(OutlookDraft).where(
                OutlookDraft.receipt_id == receipt.id,
                OutlookDraft.id != row.id,
                or_(
                    OutlookDraft.graph_draft_id != "",
                    OutlookDraft.status == "creating",
                    (OutlookDraft.status == "failed")
                    & (OutlookDraft.attempts >= MAX_ATTEMPTS),
                ),
            )
        )
        if prior and not row.graph_draft_id:
            if prior.status == "created":
                prior.error = "Updated evidence produced a newer reply in Rivet. Check the existing Outlook draft against the current record before sending; your edits were preserved."
            return _close(
                row,
                "superseded",
                "This email already has an Outlook draft or a placement to check. Rivet preserves it instead of creating another version.",
            )
        connection = get(s, MailboxConnection, receipt.connection_id)
        if not connection.enabled:
            row.next_attempt_at = now() + timedelta(hours=1)
            return _close(row, "pending", "The mailbox connection is paused.")
        if _newer_in_thread(s, receipt):
            return _close(
                row, "superseded", "A newer customer email arrived in this thread."
            )
        order = get(s, Order, reply.order_id)
        from backend.equipment.ownership import for_receipt as native_work

        if native := native_work(s, order, receipt):
            automatic._save(receipt, native)
            row.notes = [native["detail"]]
            if native["status"] == "not_needed":
                return _close(row, "superseded", native["detail"])
            row.next_attempt_at = now() + timedelta(minutes=2)
            return _close(row, "awaiting_preparation")
        if not reply.context.get("lifecycle_handoff"):
            from backend.lifecycle.outlook_handoff import (
                commercial_work_for_receipt,
                waiting_state,
            )

            commercial = commercial_work_for_receipt(
                s, order, receipt, preserve_existing=False
            )
            if commercial:
                state = waiting_state(commercial)
                automatic._save(receipt, state)
                row.notes = [state["detail"]]
                row.next_attempt_at = now() + timedelta(minutes=2)
                return _close(row, "awaiting_approval")
            if not reply.context.get("commercial_comparison"):
                from backend.reply_assistant.commercial import plan

                planned = plan(s, order, receipt)
                if planned:
                    state = (
                        {
                            "status": "queued",
                            "detail": "Rivet is preparing the recorded commercial comparison before placing a reply in Outlook.",
                            "automatic": True,
                        }
                        if planned["status"] == "ready"
                        else planned
                    )
                    automatic._save(receipt, state)
                    row.notes = [state["detail"]]
                    row.next_attempt_at = now() + timedelta(minutes=2)
                    return _close(row, "awaiting_preparation")
        try:
            current_hash = _current_hash(s, reply)
        except HTTPException as exc:
            row.next_attempt_at = now() + timedelta(minutes=10)
            return _close(row, "pending", str(exc.detail))
        if current_hash != reply.evidence_hash:
            return _close(
                row,
                "superseded",
                "The order changed after this reply was prepared; Rivet prepares a fresh one.",
            )
        notes, files = [], []
        commercial_handoff = bool(reply.context.get("lifecycle_handoff"))
        try:
            if commercial_handoff:
                from backend.lifecycle.outlook_handoff import validate

                files = validate(s, order, reply)
            else:
                files = attachments.selected(
                    s,
                    order,
                    reply.context,
                    attachments.recommend(s, order, reply.context),
                    include_bytes=True,
                )
        except (HTTPException, OSError) as exc:
            if commercial_handoff:
                return _manual_attention(
                    row,
                    "The approved customer PDF could not be verified. No Outlook draft was created: "
                    + str(getattr(exc, "detail", "File unavailable.")),
                )
            notes.append(
                "Supporting drawings could not be attached; attach them from Rivet."
            )
        row.status, row.lease, row.lease_until = (
            "creating",
            lease,
            now() + timedelta(minutes=5),
        )
        row.attempts += 1
        row_id, body = row.id, reply.body
        existing_id = row.graph_draft_id
        saved_attachments = list(row.attachments)
        message_id, tag_enabled = receipt.provider_message_id, current["tag_in_outlook"]
        snapshot = {
            "organization_id": connection.organization_id,
            "mailbox": connection.mailbox,
            "tenant": connection.microsoft_tenant_id,
        }

    graph = None
    draft_id, body_written, attached, tagged, error = (
        existing_id,
        False,
        saved_attachments,
        False,
        None,
    )
    create_attempted = False
    try:
        graph = (client_factory or _client)(snapshot)
        if existing_id:
            draft = outlook.get_message(graph, draft_id)
            if outlook.is_deleted(graph, draft):
                with Session.begin() as s:
                    row = get(s, OutlookDraft, row_id, True)
                    if row.lease == lease:
                        return _close(row, "discarded")
                    return view(row)
        else:
            # Resolve authentication before createReply, so token/consent errors
            # remain safely retryable rather than an unknown write outcome.
            graph.headers()
            prior_native = outlook.existing_reply(graph, message_id)
            if prior_native:
                with Session.begin() as s:
                    row = get(s, OutlookDraft, row_id, True)
                    if row.lease == lease:
                        row.graph_draft_id = prior_native["id"]
                        row.notes = [
                            "An existing Outlook reply in this thread was preserved. Rivet did not write or change it."
                        ]
                        return _close(row, "existing_draft")
                    return view(row)
            if not _still_current(reply_id):
                return _stop_changed_placement(row_id, lease)
            create_attempted = True
            draft = outlook.create_reply(graph, message_id)
            draft_id = draft["id"]
            if not _checkpoint(row_id, lease, graph_draft_id=draft_id):
                return {"status": "pending"}
        existing_text = outlook.sent_text(draft.get("body", {}).get("content", ""))
        if existing_id and (existing_text or not draft.get("isDraft", True)):
            # A retry may follow a successful PATCH whose response was lost.
            # Never replace edits the PM has made in the meantime.
            if commercial_handoff and not {
                (file["name"], file["sha256"]) for file in files
            } <= {(file["name"], file["sha256"]) for file in saved_attachments}:
                with Session.begin() as s:
                    row = get(s, OutlookDraft, row_id, True)
                    if row.lease == lease:
                        return _manual_attention(
                            row,
                            "The existing commercial reply was preserved, but Rivet cannot confirm its required PDF attachments after the interrupted write. Check Outlook Drafts before sending; no duplicate files or reply were created.",
                        )
                    return view(row)
            body_written = True
            if not outlook.same_words(existing_text, body):
                notes.append(
                    "An existing Outlook draft was preserved, including any edits. Check its supporting files before sending."
                )
            elif files:
                notes.append(
                    "The existing Outlook draft was recovered. Check its supporting files before sending."
                )
            body = existing_text
            files = []
        else:
            if not _still_current(reply_id):
                return _stop_changed_placement(row_id, lease)
            outlook.set_body(
                graph,
                draft_id,
                outlook.compose_body(
                    draft.get("body", {}).get("content", ""),
                    body,
                    draft.get("body", {}).get("contentType", "html"),
                ),
            )
            body_written = True
        _checkpoint(row_id, lease, body_text=body)
        for f in files:
            if commercial_handoff and not _still_current(reply_id):
                return _stop_changed_placement(row_id, lease)
            if f["size_bytes"] > outlook.MAX_INLINE_ATTACHMENT:
                notes.append(f"{f['name']} is over 3 MB; attach it from Rivet.")
                continue
            outlook.attach(
                graph, draft_id, f["name"], f.get("content_type", ""), f["base64"]
            )
            attached.append(
                {
                    k: f[k]
                    for k in ("document_id", "name", "size_bytes", "sha256")
                    if k in f
                }
            )
            _checkpoint(row_id, lease, attachments=attached)
        if tag_enabled:
            try:
                outlook.tag(graph, message_id)
                tagged = True
            except GraphError:
                notes.append("Rivet could not label the original email.")
    except GraphError as exc:
        error = exc
    finally:
        close = getattr(graph, "close", None)
        if close:
            close()

    with Session.begin() as s:
        row = get(s, OutlookDraft, row_id, True)
        if row.lease != lease:
            return view(row)
        row.notes, row.attachments, row.tagged = notes, attached, tagged
        if draft_id and body_written:
            row.graph_draft_id, row.body_text, row.drafted_at = draft_id, body, now()
            if error is not None:
                row.notes = [*notes, "Some supporting files could not be added."]
                if commercial_handoff:
                    return _manual_attention(
                        row,
                        "The commercial proposal draft was preserved, but its required PDF handoff was interrupted. Check Outlook Drafts and the approved package before sending; Rivet will not create another draft or duplicate attachments.",
                    )
            return _close(row, "created")
        if draft_id:
            row.graph_draft_id = draft_id
            row.next_attempt_at = now() + timedelta(minutes=1)
            return _close(
                row,
                "failed",
                "Outlook created the reply, but preparation was interrupted. Rivet will resume that same draft without replacing your edits.",
            )
        if error is not None and error.code == "access_denied":
            row.next_attempt_at = now() + timedelta(hours=6)
            return _close(row, "permission_required", PERMISSION)
        if create_attempted and (
            error is None or error.status == 0 or error.status >= 500
        ):
            return _manual_attention(
                row,
                "Microsoft did not confirm whether the draft was created. Check Outlook Drafts; Rivet will not risk creating a duplicate.",
            )
        row.next_attempt_at = now() + timedelta(
            seconds=min(
                3600,
                ((error.retry_after if error else None) or 60)
                * 2 ** max(0, row.attempts - 1),
            )
        )
        return _close(row, "failed", str(error))


# ----------------------------------------------------------------------------- learning
def sync_sent(limit=10, *, client_factory=None):
    """See what happened to each Outlook draft: still a draft, sent, or deleted."""
    with Session() as s:
        rows = list(
            s.execute(
                select(OutlookDraft.id, OutlookDraft.organization_id)
                .where(
                    OutlookDraft.status.in_(["created", "existing_draft"]),
                    or_(
                        OutlookDraft.checked_at.is_(None),
                        OutlookDraft.checked_at < now() - timedelta(seconds=60),
                    ),
                )
                .order_by(OutlookDraft.checked_at.asc().nulls_first())
                .limit(limit)
            )
        )
    results = []
    for row_id, organization_id in rows:
        with identity_scope(Identity(organization_id, ACTOR)):
            try:
                results.append(check_one(row_id, client_factory=client_factory))
            except Exception as exc:  # noqa: BLE001 - isolate each draft
                logger.warning(
                    "Outlook draft %s check failed (%s)", row_id, type(exc).__name__
                )
    return results


def _sent_at(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.year < 1970:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def check_one(row_id, *, client_factory=None):
    with Session() as s:
        row = get(s, OutlookDraft, row_id)
        if row.status not in {"created", "existing_draft"} or not row.connection_id:
            return view(row)
        connection = get(s, MailboxConnection, row.connection_id)
        if not connection.enabled:
            return view(row)
        snapshot = {
            "organization_id": connection.organization_id,
            "mailbox": connection.mailbox,
            "tenant": connection.microsoft_tenant_id,
        }
        draft_id = row.graph_draft_id
    graph = (client_factory or _client)(snapshot)
    state, message = "draft", {}
    try:
        message = outlook.get_message(graph, draft_id)
        # A moved/deleted non-draft item is not proof that a person sent it.
        state = (
            "sent"
            if not message.get("isDraft", True)
            and _sent_at(message.get("sentDateTime"))
            else "deleted"
            if outlook.is_deleted(graph, message)
            else "draft"
            if message.get("isDraft", True)
            else "unknown"
        )
    except GraphError as exc:
        state = "discarded" if exc.code == "not_found" else "unknown"
    finally:
        close = getattr(graph, "close", None)
        if close:
            close()
    with Session.begin() as s:
        row = get(s, OutlookDraft, row_id, True)
        row.checked_at = now()
        if row.status not in {"created", "existing_draft"}:
            return view(row)
        manual_existing = row.status == "existing_draft"
        if state == "deleted":
            # The provider confirmed its folder; no eventual-consistency grace
            # is needed for an ordinary, still-readable deletion.
            row.status, row.error, row.missing_since = "discarded", "", None
            return view(row)
        if state == "discarded":
            # Deleted only once it stays missing; sending briefly hides the item.
            row.missing_since = row.missing_since or now()
            if now() - row.missing_since >= MISSING_GRACE:
                row.status = "discarded"
            return view(row)
        # A permission/network failure interrupts proof of continuous absence.
        row.missing_since = None
        if state == "sent":
            row.status, row.sent_at = "sent", _sent_at(message.get("sentDateTime"))
            row.error = ""
            if manual_existing:
                row.outcome, row.learning_status = "external", "not_captured"
                return view(row)
            learn_from_sent(
                s,
                row,
                outlook.sent_text((message.get("body") or {}).get("content", "")),
            )
        elif state == "draft" and not manual_existing:
            reply = get(s, ReplyDraft, row.reply_draft_id)
            receipt = get(s, MailboxReceipt, row.receipt_id) if row.receipt_id else None
            try:
                fresh = _current_hash(s, reply) == reply.evidence_hash
            except HTTPException:
                fresh = False
            if not fresh or (receipt and _newer_in_thread(s, receipt)):
                row.error = "The order or email thread changed after this Outlook draft was prepared. Check the current record before sending; your draft and edits were preserved."
        return view(row)


def learn_from_sent(s, row, text):
    """Sending is the approval. Record what was sent and, by team policy, reuse it."""
    reply = get(s, ReplyDraft, row.reply_draft_id, True)
    outcome = "accepted" if outlook.same_words(text, reply.original_body) else "edited"
    row.outcome = outcome
    if reply.review.get("feedback_id"):
        # Already reviewed in the Rivet pane: that review is the feedback of record.
        row.feedback_id = reply.review["feedback_id"]
        row.learning_status = reply.review.get("learning_status", "")
        row.notes = [
            *row.notes,
            "The earlier Rivet review remains the reusable example; the Outlook send outcome is recorded separately.",
        ]
        return
    if not text or not reply.source_refs:
        row.notes = [
            *row.notes,
            "Nothing to learn: the sent reply had no text or sources.",
        ]
        return
    comments = reply.context.get("current_order", {}).get("comments", [])
    try:
        source_current = _current_hash(s, reply) == reply.evidence_hash
    except HTTPException:
        source_current = False
    try:
        feedback = learning.capture_generation_feedback(
            s,
            reply.order_id,
            request_key="outlook-sent:" + reply.id,
            generation={
                "reply_id": reply.id,
                "model": reply.model,
                "evidence_hash": reply.evidence_hash,
                "context_hash": reply.context.get("company", {}).get(
                    "context_hash", ""
                ),
                "delivered": "outlook_draft",
            },
            original_text=reply.original_body,
            input_text=(
                reply.context.get("instruction", "")
                + "\n"
                + "\n".join(
                    c.get("original_text") or c.get("text", "") for c in comments
                )
            )[:12000],
            evidence_ids=[r["id"] for r in reply.source_refs],
            outcome=outcome,
            human_response="" if outcome == "accepted" else text[:10000],
            reason="The PM sent this reply from Outlook.",
            topics=["customer-correspondence"],
        )
    except HTTPException as exc:
        row.notes = [*row.notes, f"Feedback not recorded: {exc.detail}"]
        return
    status = "recorded"
    if settings(s)["learn_from_sent"] == "auto" and source_current:
        try:
            learning.approve_example(
                s,
                feedback["id"],
                ReuseReview(
                    reason="Sent from Outlook. This team reuses sent replies as examples.",
                    scope="order",
                ),
            )
            status = "approved"
        except HTTPException:
            status = "pending_review"
    elif not source_current:
        status = "pending_review"
        row.notes = [
            *row.notes,
            "Order evidence changed before this reply was sent. Its example needs review before reuse.",
        ]
    row.feedback_id, row.learning_status = feedback["id"], status
    reply.status, reply.reviewed_at = "approved", now()
    if outcome == "edited":
        reply.body = text[:10000]
    # Promoting an example changes the context fingerprint. Record that new
    # fingerprint so the reconciler does not regenerate a reply already sent.
    try:
        reviewed_hash = replies.context(
            s,
            get(s, Order, reply.order_id),
            reply.context["instruction"],
            reply.context.get("focus_document_ids", []),
        )[-1]
    except HTTPException:
        reviewed_hash = reply.evidence_hash
    reply.review = {
        **reply.review,
        "actor": "Sent from Outlook",
        "feedback_id": feedback["id"],
        "learning_status": status,
        "reviewed_evidence_hash": reviewed_hash,
        "attachments": row.attachments,
        "sent_from_outlook": True,
    }


# ----------------------------------------------------------------------------- activity
def _iso(value):
    parsed = value if isinstance(value, datetime) else _sent_at(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def activity(s, days=7):
    """Read-only summary for the dashboard: what Rivet did, what waits on people."""
    since = now() - timedelta(days=days)
    receipts = list(
        s.scalars(
            scoped(MailboxReceipt)
            .where(MailboxReceipt.created_at >= since)
            .order_by(MailboxReceipt.created_at.desc())
            .limit(200)
        )
    )
    drafts = list(
        s.scalars(
            scoped(OutlookDraft).order_by(OutlookDraft.created_at.desc()).limit(200)
        )
    )
    # A native sent receipt represents only its exact prepared lifecycle package,
    # never every piece of unfinished work on the same order.
    package_coverage = {}
    for reply_id, order_id, coverage in s.execute(
        select(ReplyDraft.id, ReplyDraft.order_id, ReplyDraft.coverage).where(
            ReplyDraft.organization_id == current_org(),
            ReplyDraft.id.in_([draft.reply_draft_id for draft in drafts]),
        )
    ):
        if (
            isinstance(coverage, dict)
            and coverage.get("origin") == "lifecycle_proposal"
            and isinstance(coverage.get("package_id"), str)
            and coverage["package_id"]
        ):
            package_coverage[(reply_id, order_id)] = {
                "package_id": coverage["package_id"]
            }
    prepared = s.scalar(
        select(func.count(ReplyDraft.id)).where(
            ReplyDraft.organization_id == current_org(),
            ReplyDraft.request_key.like("auto-mail:%"),
            ReplyDraft.created_at >= since,
        )
    )
    receipt_by_id = {r.id: r for r in receipts}
    views = {r.id: receipt_view(s, r) for r in receipts}
    receipt_ids = {
        d.receipt_id for d in drafts if d.receipt_id and d.receipt_id not in views
    }
    for r in s.scalars(
        scoped(MailboxReceipt).where(MailboxReceipt.id.in_(receipt_ids))
    ):
        views[r.id] = receipt_view(s, r)
        receipt_by_id[r.id] = r
    order_ids = {v.get("order_id") for v in views.values()} | {
        d.order_id for d in drafts
    }
    numbers = dict(
        s.execute(
            select(Order.id, Order.number).where(
                Order.organization_id == current_org(),
                Order.id.in_([o for o in order_ids if o]),
            )
        ).all()
    )

    def email(receipt_id):
        v = views.get(receipt_id) or {}
        receipt = receipt_by_id.get(receipt_id)
        return {
            "receipt_id": receipt_id,
            "order_id": v.get("order_id"),
            "intake_id": receipt.intake_id if receipt else None,
            "internet_message_id": receipt.internet_message_id if receipt else "",
            "subject": v.get("subject", ""),
            "sender": v.get("sender", ""),
            "outlook_url": v.get("web_link", ""),
        }

    feed, needs_you, waiting = [], [], []
    for r in receipts:
        v = views[r.id]
        status, order = v.get("status"), numbers.get(v.get("order_id"))
        if status in SET_ASIDE:
            # Keep the capture audit and aggregate count, not an inbox mirror.
            continue
        if order:
            detail = f"Filed to job {order}."
        elif status == "needs_confirmation":
            detail = "Rivet needs to know which job this is."
        else:
            detail = "Reading the email and attachments."
        feed.append(
            {
                "at": _iso(v.get("received_at") or r.created_at),
                "kind": "read",
                "order": order,
                "detail": detail,
                "preparation": r.data.get("automatic_reply", {}),
                **email(r.id),
            }
        )
        if status == "needs_confirmation":
            needs_you.append(
                {"kind": "which_job", "order": order, "detail": detail, **email(r.id)}
            )
        auto = r.data.get("automatic_reply", {})
        if auto.get("status") in {"waiting_for_approval", "needs_thread"}:
            needs_you.append(
                {
                    "kind": "approval"
                    if auto["status"] == "waiting_for_approval"
                    else "choose_thread",
                    "order": order,
                    "detail": auto["detail"],
                    "package_ids": auto.get("package_ids", []),
                    "pending_gates": auto.get("pending_gates", []),
                    **email(r.id),
                }
            )
        if auto.get("status") in {"blocked", "failed"}:
            needs_you.append(
                {
                    "kind": "blocked",
                    "order": order,
                    "detail": auto.get("detail", "Rivet could not prepare a reply."),
                    **email(r.id),
                }
            )
    for d in drafts:
        order, mail = numbers.get(d.order_id), email(d.receipt_id)
        if d.drafted_at and d.drafted_at >= since:
            feed.append(
                {
                    "at": _iso(d.drafted_at),
                    "kind": "drafted",
                    "draft_status": d.status,
                    "order": order,
                    "detail": "Reply drafted in Outlook"
                    + (
                        f" with {len(d.attachments)} attachment(s)."
                        if d.attachments
                        else "."
                    ),
                    **mail,
                }
            )
        if d.sent_at and d.sent_at >= since:
            feed.append(
                {
                    "at": _iso(d.sent_at),
                    "kind": "sent",
                    "draft_status": d.status,
                    "outcome": d.outcome,
                    "coverage": package_coverage.get(
                        (d.reply_draft_id, d.order_id), {}
                    ),
                    "learning_status": d.learning_status,
                    "order": order,
                    "detail": (
                        "Existing Outlook reply sent. Rivet did not learn it as an AI-generated example."
                        if d.outcome == "external"
                        else "Sent as Rivet drafted it."
                        if d.outcome == "accepted"
                        else "Sent after edits."
                    )
                    + (
                        " Saved as an example for this order."
                        if d.learning_status == "approved"
                        else " Changes recorded; reuse needs administrator review."
                        if d.learning_status in {"recorded", "pending_review"}
                        else ""
                    ),
                    **mail,
                }
            )
        if d.status in {"created", "existing_draft"}:
            waiting.append(
                {
                    "order": order,
                    "drafted_at": _iso(d.drafted_at),
                    "origin": "existing" if d.status == "existing_draft" else "rivet",
                    "status": d.status,
                    "attachments": [a.get("name") for a in d.attachments],
                    **mail,
                }
            )
        if d.status in {"permission_required", "failed"} or (
            d.status == "created" and d.error
        ):
            needs_you.append(
                {"kind": d.status, "order": order, "detail": d.error, **mail}
            )
    feed.sort(
        key=lambda item: _sent_at(item["at"]) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )

    def count(predicate):
        return sum(1 for d in drafts if predicate(d))

    return {
        "days": days,
        "summary": {
            "emails_read": len(receipts),
            "filed_to_orders": sum(1 for v in views.values() if v.get("order_id")),
            "set_aside": sum(1 for v in views.values() if v.get("status") in SET_ASIDE),
            "replies_prepared": prepared or 0,
            "drafted_in_outlook": count(
                lambda d: d.drafted_at and d.drafted_at >= since
            ),
            "waiting_in_outlook": len(waiting),
            "sent_unchanged": count(
                lambda d: d.sent_at and d.sent_at >= since and d.outcome == "accepted"
            ),
            "sent_edited": count(
                lambda d: d.sent_at and d.sent_at >= since and d.outcome == "edited"
            ),
            "learned": count(lambda d: d.learning_status == "approved"),
            "needs_you": len(needs_you),
        },
        "waiting_in_outlook": waiting[:20],
        "needs_you": needs_you[:20],
        "feed": feed[:40],
        "settings": settings(s),
    }
