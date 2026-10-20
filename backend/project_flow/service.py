"""Communication-led projects. Reads never start work or manufacture sent events."""

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

from sqlalchemy import or_, select, text

from backend.autopilot import outlook
from backend.autopilot.models import OutlookDraft
from backend.company_context.service import require_writer
from backend.connected_context.service import summary as connected_summary
from backend.domain.service import digest, fail, get, scoped
from backend.identity import Identity, current_actor, identity_scope
from backend.mailbox.graph import GraphClient, GraphError
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session
from backend.storage.models import Document, Job, Order, Project, RecordIntake, now

from .models import ProjectSentMessage, ProjectThread, ProjectWorkspace

logger = logging.getLogger(__name__)
LABELS = {
    "handling": "Rivet is working",
    "ready": "Ready in Outlook",
    "prepared": "Response prepared",
    "waiting_customer": "Waiting for customer",
    "needs_you": "Needs your decision",
    "watching": "Watching for updates",
    "up_to_date": "Up to date",
}


def timestamp(value, fallback=None):
    try:
        result = (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
        if result.year < 1970:
            return fallback
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result
    except (TypeError, ValueError):
        return fallback


def safe_url(value):
    try:
        p = urlsplit(value or "")
    except ValueError:
        return ""
    return (
        value
        if p.scheme == "https"
        and p.hostname
        in {
            "outlook.office.com",
            "outlook.office365.com",
            "outlook.live.com",
            "outlook.cloud.microsoft",
        }
        and not p.username
        and not p.password
        else ""
    )


def receipts(s, order_id):
    return list(
        s.scalars(
            scoped(MailboxReceipt)
            .join(RecordIntake, MailboxReceipt.intake_id == RecordIntake.id)
            .where(
                RecordIntake.order_id == order_id,
                RecordIntake.organization_id == MailboxReceipt.organization_id,
                RecordIntake.status.in_(["matched", "created", "imported"]),
                MailboxReceipt.status == "imported",
            )
            .order_by(MailboxReceipt.created_at.desc())
            .limit(300)
        )
    )


def is_sent_copy(s, receipt):
    if not receipt.internet_message_id:
        return False
    return bool(
        s.scalar(
            scoped(ProjectSentMessage).where(
                ProjectSentMessage.connection_id == receipt.connection_id,
                ProjectSentMessage.internet_message_id == receipt.internet_message_id,
            )
        )
    )


def set_aside_sent_copy(s, receipt):
    """Keep the original while removing a proven outgoing copy from match questions."""
    if not is_sent_copy(s, receipt) or not receipt.intake_id:
        return False
    intake = get(s, RecordIntake, receipt.intake_id)
    if intake.order_id or intake.status != "needs_confirmation":
        return False
    intake.status = "set_aside"
    intake.data = {
        **intake.data,
        "reason": "Copy of a confirmed Outlook send; no order confirmation or reply is needed.",
        "disposition_reason": "outgoing_copy",
    }
    receipt.status = "set_aside"
    receipt.data = {
        **receipt.data,
        "automatic_reply": {
            "status": "not_needed",
            "reason": "outgoing_copy",
            "detail": "This is a copy of your team’s sent reply.",
            "automatic": True,
        },
    }
    return True


def answered(s, receipt):
    """Only a confirmed send in this exact mailbox/thread can finish incoming mail."""
    conv = receipt.data.get("conversation_id")
    if not conv:
        return False
    intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
    if not intake or not intake.order_id:
        return False
    received = timestamp(receipt.data.get("received_at"), receipt.created_at)
    return bool(
        s.scalar(
            scoped(ProjectSentMessage).where(
                ProjectSentMessage.connection_id == receipt.connection_id,
                ProjectSentMessage.conversation_id == conv,
                ProjectSentMessage.order_id == intake.order_id,
                ProjectSentMessage.sent_at > received,
            )
        )
    )


def rename(s, order_id, name, expected_version):
    require_writer()
    order = get(s, Order, order_id)
    clean = " ".join(name.split())
    if not clean or len(clean) > 180:
        fail("Use a project name between 1 and 180 characters.", 422)
    s.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int(digest([order.id, "project-name"])[:15], 16)},
    )
    row = s.scalar(
        scoped(ProjectWorkspace)
        .where(ProjectWorkspace.order_id == order.id)
        .with_for_update()
    )
    version = row.version if row else 0
    if version != expected_version:
        fail("The project name changed. Refresh before saving.", 409)
    if not row:
        row = ProjectWorkspace(order_id=order.id)
        s.add(row)
        s.flush()
    prior = row.name or get(s, Project, order.project_id).title
    row.name, row.version = clean, version + 1
    row.changes = [
        *row.changes,
        {
            "before": prior,
            "after": clean,
            "actor": current_actor(),
            "at": now().isoformat(),
        },
    ]
    s.flush()
    return {"name": clean, "name_version": row.version}


def sent_messages(graph, conversation_id):
    """Read Sent Items for one already-linked thread; no full-mailbox import."""
    base = outlook._url(graph, "mailFolders", "sentitems", "messages")
    condition = "conversationId eq '" + conversation_id.replace("'", "''") + "'"
    url = (
        base
        + "?"
        + urlencode(
            {
                "$filter": condition,
                "$top": "50",
                "$select": "id,internetMessageId,conversationId,subject,sentDateTime,toRecipients,bodyPreview,webLink,isDraft",
            }
        )
    )
    result, visited = [], set()
    for _ in range(4):
        if url in visited:
            raise GraphError(
                "invalid_cursor", "Microsoft repeated a communication-history page."
            )
        visited.add(url)
        p = urlsplit(url)
        query = parse_qs(p.query)
        if (
            p.scheme != "https"
            or p.netloc != "graph.microsoft.com"
            or p.path != urlsplit(base).path
            or query.get("$filter", [condition]) != [condition]
        ):
            raise GraphError(
                "invalid_cursor",
                "Microsoft returned a communication-history cursor outside this thread.",
            )
        body = outlook._call(graph, "GET", url)
        if not isinstance(body.get("value"), list):
            raise GraphError(
                "provider_error",
                "Microsoft did not return the sent communication history.",
            )
        for m in body["value"]:
            if (
                m.get("conversationId") != conversation_id
                or m.get("isDraft", True)
                or not timestamp(m.get("sentDateTime"))
            ):
                continue
            result.append(m)
        url = body.get("@odata.nextLink")
        if not url:
            return result
    raise GraphError(
        "too_large",
        "This thread exceeds the current communication-history limit; its sent history needs a wider sync.",
    )


def discover(s):
    """Confirmed intake owns thread identity. A reused thread never merges jobs."""
    candidates = s.execute(
        select(MailboxReceipt, RecordIntake.order_id)
        .join(RecordIntake, MailboxReceipt.intake_id == RecordIntake.id)
        .where(
            MailboxReceipt.organization_id == RecordIntake.organization_id,
            MailboxReceipt.status == "imported",
            RecordIntake.status.in_(["matched", "created", "imported"]),
            RecordIntake.order_id.is_not(None),
        )
    ).all()
    for r, oid in candidates:
        conv = r.data.get("conversation_id")
        if not conv:
            continue
        with identity_scope(Identity(r.organization_id, "Rivet communication tracker")):
            key = int(digest([r.connection_id, conv, "project-thread"])[:15], 16)
            s.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            thread = s.scalar(
                scoped(ProjectThread).where(
                    ProjectThread.connection_id == r.connection_id,
                    ProjectThread.conversation_id == conv,
                )
            )
            if thread and thread.order_id != oid:
                thread.status, thread.error = (
                    "ambiguous",
                    "This email thread is linked to more than one job; automatic sent-mail filing is paused.",
                )
            elif not thread:
                s.add(
                    ProjectThread(
                        order_id=oid,
                        connection_id=r.connection_id,
                        conversation_id=conv,
                    )
                )
                s.flush()


def sync(limit=8, *, client_factory=None):
    """Background communication reconciliation, independent of any open dashboard."""
    with Session.begin() as s:
        discover(s)
        threads = list(
            s.scalars(
                select(ProjectThread)
                .join(
                    MailboxConnection,
                    ProjectThread.connection_id == MailboxConnection.id,
                )
                .where(
                    MailboxConnection.enabled.is_(True),
                    ProjectThread.status != "ambiguous",
                    ProjectThread.next_check_at <= now(),
                    or_(
                        ProjectThread.lease_until.is_(None),
                        ProjectThread.lease_until < now(),
                    ),
                )
                .order_by(ProjectThread.next_check_at)
                .with_for_update(skip_locked=True, of=ProjectThread)
                .limit(limit)
            )
        )
        selected = []
        for t in threads:
            t.lease_until = now() + timedelta(minutes=3)
            c = s.get(MailboxConnection, t.connection_id)
            selected.append(
                (
                    t.id,
                    t.organization_id,
                    c.mailbox,
                    c.microsoft_tenant_id,
                    t.conversation_id,
                )
            )
    results = []
    for tid, org, mailbox, tenant, conv in selected:
        graph = None
        try:
            graph = (
                client_factory(org, mailbox, tenant)
                if client_factory
                else GraphClient(org, mailbox, tenant)
            )
            messages = sent_messages(graph, conv)
            with (
                identity_scope(Identity(org, "Rivet communication tracker")),
                Session.begin() as s,
            ):
                t = get(s, ProjectThread, tid, True)
                if t.status == "ambiguous":
                    continue
                # File replies to the actual correspondent, excluding internal forwards.
                correspondents = {
                    r.data.get("sender", "").lower()
                    for r in receipts(s, t.order_id)
                    if r.connection_id == t.connection_id
                    and r.data.get("conversation_id") == conv
                }
                first_received = min(
                    (
                        timestamp(r.data.get("received_at"), r.created_at)
                        for r in receipts(s, t.order_id)
                        if r.connection_id == t.connection_id
                        and r.data.get("conversation_id") == conv
                    ),
                    default=None,
                )
                for m in messages:
                    # A self-addressed demo trigger (or an older sent message)
                    # is not a response to the captured customer request.
                    if (
                        not first_received
                        or timestamp(m["sentDateTime"]) <= first_received
                    ):
                        continue
                    recipients = [
                        x.get("emailAddress", {}).get("address", "").lower()
                        for x in m.get("toRecipients", [])
                    ]
                    if not set(recipients) & correspondents:
                        continue
                    mid = m.get("id")
                    if not isinstance(mid, str) or not mid:
                        continue
                    native = s.scalar(
                        scoped(OutlookDraft).where(
                            OutlookDraft.connection_id == t.connection_id,
                            OutlookDraft.graph_draft_id == mid,
                        )
                    )
                    existing = s.scalar(
                        scoped(ProjectSentMessage).where(
                            ProjectSentMessage.connection_id == t.connection_id,
                            ProjectSentMessage.provider_id == mid,
                        )
                    )
                    if not existing:
                        s.add(
                            ProjectSentMessage(
                                order_id=t.order_id,
                                connection_id=t.connection_id,
                                provider_id=mid,
                                internet_message_id=str(m.get("internetMessageId", ""))[
                                    :1024
                                ],
                                conversation_id=conv,
                                subject=str(m.get("subject", ""))[:2000],
                                body=str(m.get("bodyPreview", ""))[:10000],
                                recipients=recipients,
                                sent_at=timestamp(m["sentDateTime"]),
                                outlook_url=safe_url(m.get("webLink", "")),
                                origin="rivet" if native else "outlook",
                            )
                        )
                s.flush()
                copies = list(
                    s.scalars(
                        scoped(MailboxReceipt)
                        .join(
                            ProjectSentMessage,
                            (
                                ProjectSentMessage.connection_id
                                == MailboxReceipt.connection_id
                            )
                            & (
                                ProjectSentMessage.internet_message_id
                                == MailboxReceipt.internet_message_id
                            )
                            & (
                                ProjectSentMessage.organization_id
                                == MailboxReceipt.organization_id
                            ),
                        )
                        .where(
                            MailboxReceipt.connection_id == t.connection_id,
                            MailboxReceipt.status == "needs_confirmation",
                        )
                    )
                )
                for copy in copies:
                    set_aside_sent_copy(s, copy)
                t.status, t.error, t.checked_at, t.lease_until = (
                    "current",
                    "",
                    now(),
                    None,
                )
                t.next_check_at = now() + timedelta(minutes=1)
                results.append(
                    {"thread_id": t.id, "status": "current", "sent": len(messages)}
                )
        except Exception as exc:  # noqa: BLE001 - one mailbox must not block the worker
            with (
                identity_scope(Identity(org, "Rivet communication tracker")),
                Session.begin() as s,
            ):
                t = get(s, ProjectThread, tid, True)
                t.status, t.error, t.lease_until = (
                    "unavailable",
                    "Sent-mail synchronization is delayed. Previously confirmed history is preserved.",
                    None,
                )
                t.next_check_at = now() + timedelta(minutes=5)
            results.append({"thread_id": tid, "status": "unavailable"})
            logger.warning(
                "Project communication check needs attention (%s)", type(exc).__name__
            )
        finally:
            if graph:
                graph.close()
    return results


def project_view(s, order, *, detail=False):
    project = get(s, Project, order.project_id)
    identity = s.scalar(
        scoped(ProjectWorkspace).where(ProjectWorkspace.order_id == order.id)
    )
    inbox = [r for r in receipts(s, order.id) if not is_sent_copy(s, r)]
    inbox.sort(
        key=lambda r: timestamp(r.data.get("received_at"), r.created_at), reverse=True
    )
    latest = inbox[0] if inbox else None
    drafts = list(
        s.scalars(
            scoped(ReplyDraft)
            .where(ReplyDraft.order_id == order.id)
            .order_by(ReplyDraft.created_at.desc())
            .limit(100)
        )
    )
    natives = list(
        s.scalars(
            scoped(OutlookDraft)
            .where(OutlookDraft.order_id == order.id)
            .order_by(OutlookDraft.created_at.desc())
            .limit(100)
        )
    )

    def native_for(receipt):
        matches = [n for n in natives if receipt and n.receipt_id == receipt.id]
        # A rejected replacement must never conceal the existing Outlook draft.
        return next((n for n in matches if n.status != "superseded"), None)

    outgoing = list(
        s.scalars(
            scoped(ProjectSentMessage)
            .where(ProjectSentMessage.order_id == order.id)
            .order_by(ProjectSentMessage.sent_at.desc())
            .limit(100)
        )
    )

    def thread_key(connection_id, conv):
        return digest([connection_id, conv])[:24]

    receipt_by_id = {r.id: r for r in inbox}
    docs = list(
        s.scalars(scoped(Document).where(Document.project_id == order.project_id))
    )
    threads = list(
        s.scalars(scoped(ProjectThread).where(ProjectThread.order_id == order.id))
    )
    communications = [
        {
            "id": r.id,
            "direction": "received",
            "subject": r.data.get("subject") or "Returned documents",
            "sender": r.data.get("sender_name") or r.data.get("sender", ""),
            "at": timestamp(r.data.get("received_at"), r.created_at).isoformat(),
            "body": "",
            "outlook_url": safe_url(r.data.get("web_link", "")),
            "origin": "customer",
            "intake_id": r.intake_id,
            "thread_key": thread_key(
                r.connection_id, r.data.get("conversation_id") or r.internet_message_id
            ),
        }
        for r in inbox
    ]
    communications.extend(
        {
            "id": m.id,
            "direction": "sent",
            "subject": m.subject,
            "sender": "Your team",
            "at": m.sent_at.isoformat(),
            "body": m.body if detail else "",
            "outlook_url": m.outlook_url,
            "origin": m.origin,
            "thread_key": thread_key(m.connection_id, m.conversation_id),
        }
        for m in outgoing
    )
    # Native reconciliation can prove a send before the Sent Items scan runs.
    known = {m.provider_id for m in outgoing}
    for n in natives:
        if n.status == "sent" and n.sent_at and n.graph_draft_id not in known:
            reply = next((d for d in drafts if d.id == n.reply_draft_id), None)
            communications.append(
                {
                    "id": n.id,
                    "direction": "sent",
                    "subject": reply.subject if reply else "Project reply",
                    "sender": "Your team",
                    "at": n.sent_at.isoformat(),
                    "body": n.body_text if detail else "",
                    "outlook_url": "",
                    "origin": "rivet",
                    "thread_key": thread_key(
                        n.connection_id,
                        receipt_by_id[n.receipt_id].data.get("conversation_id")
                        or receipt_by_id[n.receipt_id].internet_message_id,
                    )
                    if n.receipt_id in receipt_by_id
                    else "",
                }
            )
    communications.sort(key=lambda m: timestamp(m["at"]), reverse=True)
    latest_at = (
        timestamp(latest.data.get("received_at"), latest.created_at) if latest else None
    )
    # A send in one thread must not hide an unanswered email in another.
    latest_by_thread = {}
    for r in inbox:
        key = (r.connection_id, r.data.get("conversation_id") or r.internet_message_id)
        latest_by_thread.setdefault(key, r)
    unanswered = []
    for r in latest_by_thread.values():
        if r.data.get("automatic_reply", {}).get("reason") == "acknowledgement":
            continue
        if answered(s, r):
            continue
        if any(
            n.receipt_id == r.id and n.status in {"sent", "discarded"} for n in natives
        ):
            continue
        unanswered.append(r)
    active_receipt = next(
        (
            r
            for r in unanswered
            if any(
                n.receipt_id == r.id and n.status in {"created", "existing_draft"}
                for n in natives
            )
        ),
        None,
    ) or next(iter(unanswered), latest)
    relevant_native = native_for(active_receipt)
    relevant_reply = (
        next(
            (
                d
                for d in drafts
                if active_receipt
                and d.internet_message_id == active_receipt.internet_message_id
            ),
            None,
        )
        if active_receipt
        else next(iter(drafts), None)
    )
    if not active_receipt and relevant_reply:
        relevant_native = next(
            (
                n
                for n in natives
                if n.reply_draft_id == relevant_reply.id and n.status != "superseded"
            ),
            None,
        )
    file_preparation = None
    if (
        not active_receipt
        and relevant_reply
        and relevant_reply.request_key.startswith("auto-order:")
    ):
        from backend.reply_assistant.service import order_preparation

        file_preparation = order_preparation(s, order)
        if current_reply := file_preparation.get("prepared_reply"):
            relevant_reply = next(d for d in drafts if d.id == current_reply["id"])
            relevant_native = next(
                (
                    n
                    for n in natives
                    if n.reply_draft_id == relevant_reply.id
                    and n.status != "superseded"
                ),
                None,
            )
        if not relevant_native:
            relevant_native = next(
                (
                    n
                    for n in natives
                    if not n.receipt_id
                    and n.status
                    in {
                        "created",
                        "existing_draft",
                        "creating",
                        "manual_attention",
                        "permission_required",
                    }
                ),
                None,
            )
    reply_ready = bool(
        file_preparation["prepared_reply"]
        if file_preparation is not None
        else relevant_reply and relevant_reply.status in {"ready", "approved"}
    )
    auto = active_receipt.data.get("automatic_reply", {}) if active_receipt else {}
    sent_after = (
        not unanswered and communications and communications[0]["direction"] == "sent"
    )
    status, explanation = (
        "watching",
        "Rivet will prepare the next response when project mail or documents arrive.",
    )
    if sent_after:
        status, explanation = (
            "waiting_customer",
            "Your reply was sent. Rivet is watching for the customer’s response.",
        )
    elif (
        relevant_native
        and relevant_native.status in {"created", "existing_draft"}
        and (
            relevant_native.error
            or (
                file_preparation is not None
                and (
                    not reply_ready
                    or relevant_native.reply_draft_id != relevant_reply.id
                )
            )
        )
    ):
        status, explanation = (
            "needs_you",
            "An Outlook draft was preserved after the project changed. Check the updated evidence before sending it.",
        )
    elif relevant_native and relevant_native.status in {"created", "existing_draft"}:
        status, explanation = (
            "ready",
            "An unaddressed response is saved in Outlook Drafts."
            if not active_receipt
            else "An unsent reply is ready in the customer’s Outlook thread.",
        )
    elif auto.get("status") in {
        "waiting_for_approval",
        "needs_thread",
        "blocked",
        "failed",
    }:
        status, explanation = (
            "needs_you",
            auto.get(
                "detail", "One point needs your decision before Rivet can continue."
            ),
        )
    elif relevant_native and relevant_native.status in {
        "permission_required",
        "manual_attention",
    }:
        status, explanation = (
            "needs_you",
            relevant_native.error
            or "Outlook needs attention before the draft can be placed.",
        )
    elif reply_ready and not (
        relevant_native
        and relevant_native.status in {"sent", "discarded", "superseded"}
    ):
        status, explanation = (
            "prepared",
            "Rivet prepared the response and supporting sources.",
        )
    elif file_preparation and file_preparation["preparation"]["status"] in {
        "queued",
        "running",
        "blocked",
        "failed",
    }:
        preparing = file_preparation["preparation"]
        status, explanation = (
            "handling" if preparing["status"] in {"queued", "running"} else "needs_you",
            preparing["detail"],
        )
    elif relevant_reply and relevant_reply.status == "failed":
        status, explanation = (
            "needs_you",
            "Reply preparation was interrupted. Saved evidence and prior work are preserved.",
        )
    elif latest and auto.get("reason") == "acknowledgement":
        status, explanation = (
            "up_to_date",
            "The customer’s acknowledgement is recorded. No reply is needed.",
        )
    elif latest and (
        not relevant_native or relevant_native.status not in {"discarded", "sent"}
    ):
        status, explanation = (
            "handling",
            "Rivet is checking the latest email, its documents and the connected records.",
        )
    active_jobs = list(
        s.scalars(
            scoped(Job).where(
                Job.project_id == order.project_id,
                Job.status.in_(["queued", "running"]),
            )
        )
    )
    if not latest and active_jobs:
        status, explanation = (
            "handling",
            "Rivet is processing the project documents in the background.",
        )
    equipment_correction = None
    correction_bundles = order.state.get("equipment_corrections", {}).get("bundles", [])
    if correction_bundles:
        from backend.equipment.workflow import public_bundle

        equipment_correction = public_bundle(s, order, correction_bundles[-1])
        native_correction = (equipment_correction.get("outlook") or {}).get("draft") or {}
        newer_mail = latest_at and latest_at > timestamp(equipment_correction["created_at"])
        if not newer_mail:
            if not equipment_correction["current"]:
                status, explanation = "needs_you", "The files changed after Rivet prepared the correction. A fresh check is needed."
            elif native_correction.get("status") == "sent_for_review" and native_correction.get("attachments_verified") is False:
                status, explanation = "needs_you", "The sent attachments differ from Rivet's checked files. Check the final package."
            elif native_correction.get("status") == "sent_for_review":
                status, explanation = "waiting_customer", "The corrected drawing and BOM were sent for review. Rivet is watching for the response."
            elif native_correction.get("status") == "created" and not native_correction.get("error"):
                status, explanation = "ready", "Rivet corrected the drawing and BOM, checked the saved files, and attached them in Outlook."
            elif native_correction.get("status") in {"manual_attention", "permission_required", "existing_draft"}:
                status, explanation = "needs_you", native_correction.get("error") or "Your existing Outlook reply is preserved. Review it before adding the corrected files."
            elif equipment_correction["attachable"]:
                status, explanation = "prepared", "The corrected drawing and BOM passed the file check and are ready for your Outlook draft."
    context = connected_summary(s, order)
    progress = [
        {"label": "Email captured", "state": "done" if latest else "pending"},
        {
            "label": "Documents read",
            "state": "done"
            if docs and all(d.state == "ready" for d in docs)
            else "working"
            if docs
            else "pending",
        },
        {
            "label": "Connected sources"
            if context["status"] != "not_configured"
            else "Order evidence",
            "state": "done"
            if context["status"] == "current"
            or (
                context["status"] == "not_configured"
                and relevant_reply
                and relevant_reply.context.get("current_order", {}).get("sources")
            )
            else "working",
        },
        {
            "label": "Response prepared",
            "state": "done"
            if reply_ready
            else "working"
            if latest or status == "handling"
            else "pending",
        },
    ]
    result = {
        "id": order.id,
        "project_id": project.id,
        "name": identity.name if identity and identity.name else project.title,
        "name_version": identity.version if identity else 0,
        "number": order.number,
        "customer": project.customer,
        "sample": project.synthetic,
        "status": status,
        "status_label": LABELS[status],
        "explanation": explanation,
        "last_communication": communications[0] if communications else None,
        "last_received_at": latest_at.isoformat() if latest_at else None,
        "last_sent_at": next(
            (m["at"] for m in communications if m["direction"] == "sent"), None
        ),
        "email_count": len(communications),
        "thread_count": len(
            {(r.connection_id, r.data.get("conversation_id") or r.id) for r in inbox}
        ),
        "document_count": len(docs),
        "history_partial": len(inbox) >= 300 or len(outgoing) >= 100,
        "progress": progress,
        "pending_threads": len(unanswered),
        "open_points": relevant_reply.unresolved if relevant_reply else [],
        "outlook": {
            "internet_message_id": active_receipt.internet_message_id
            if active_receipt
            else "",
            "url": safe_url(active_receipt.data.get("web_link", ""))
            if active_receipt
            else "",
            "native": status == "ready",
        },
        "communication_sync": "delayed"
        if any(t.status == "unavailable" for t in threads)
        else "ambiguous"
        if any(t.status == "ambiguous" for t in threads)
        else "current"
        if threads and all(t.status == "current" for t in threads)
        else "pending"
        if inbox
        else "not_connected",
        "updated_at": communications[0]["at"]
        if communications
        else order.updated_at.isoformat(),
    }
    if equipment_correction:
        native_correction = (equipment_correction.get("outlook") or {}).get("draft") or {}
        result["equipment_correction"] = equipment_correction
        if status == "waiting_customer" and native_correction.get("sent_at"):
            result["last_sent_at"] = native_correction["sent_at"]
        if status == "ready" and native_correction.get("status") == "created":
            result["outlook"] = {"internet_message_id": "", "url": safe_url(native_correction.get("web_link", "")), "native": True,
                                 "draft_id": native_correction.get("id", ""), "correction_bundle_id": equipment_correction["id"]}
    if detail:
        thread_views = []
        for (cid, conv), received in latest_by_thread.items():
            key = thread_key(cid, conv)
            mail = [item for item in communications if item.get("thread_key") == key]
            native = native_for(received)
            thread_status = (
                "waiting_customer"
                if any(
                    m["direction"] == "sent"
                    and timestamp(m["at"])
                    > timestamp(received.data.get("received_at"), received.created_at)
                    for m in mail
                )
                else "watching"
                if native and native.status == "discarded"
                else "needs_you"
                if native
                and native.error
                and native.status
                in {
                    "created",
                    "existing_draft",
                    "permission_required",
                    "manual_attention",
                }
                else "ready"
                if native and native.status in {"created", "existing_draft"}
                else "handling"
            )
            if (
                received.data.get("automatic_reply", {}).get("reason")
                == "acknowledgement"
            ):
                thread_status = "up_to_date"
            thread_views.append(
                {
                    "id": key,
                    "subject": received.data.get("subject", "Project email"),
                    "status": thread_status,
                    "status_label": LABELS[thread_status],
                    "last_at": mail[0]["at"]
                    if mail
                    else received.created_at.isoformat(),
                    "email_count": len(mail),
                }
            )
        result["threads"] = sorted(
            thread_views, key=lambda t: timestamp(t["last_at"]), reverse=True
        )
        from backend.reply_assistant.service import order_preparation

        result["reply_preparation"] = file_preparation or (
            order_preparation(s, order, active_receipt)
            if active_receipt and status not in {"waiting_customer", "up_to_date"}
            else None
        )
        for item in communications:
            if item["direction"] == "received":
                intake = get(s, RecordIntake, item["intake_id"])
                from backend.records.intake_preview import preview

                p = preview(s, intake)
                item["body"], item["attachments"] = (
                    p.get("body", ""),
                    p.get("attachments", []),
                )
        result["communications"] = communications
        result["connected_context"] = context
        result["name_history"] = identity.changes if identity else []
    return result


def list_projects(s):
    orders = list(s.scalars(scoped(Order).order_by(Order.updated_at.desc()).limit(201)))
    items = [project_view(s, order) for order in orders[:200]]
    items.sort(key=lambda p: timestamp(p["updated_at"]), reverse=True)
    return {
        "projects": items,
        "partial": len(orders) > 200,
        "checked_at": now().isoformat(),
    }
