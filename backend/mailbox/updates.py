"""Opt-in internal updates from actual completed work. Never sends customer replies."""

import os
from datetime import timedelta
from email.utils import parseaddr
from html import escape
from urllib.parse import quote

import httpx
from sqlalchemy import or_, select, text

from backend.domain.service import digest as fingerprint
from backend.domain.service import get, scoped
from backend.identity import Identity, current_org, identity_scope
from backend.orders.service import lock_order
from backend.records import agent_service, delivery
from backend.records import service as records
from backend.reply_assistant import service as replies
from backend.storage.db import Session
from backend.storage.models import (
    RecordAgentProposal,
    RecordAgentRun,
    RecordIntake,
    now,
    uid,
)

from .graph import GraphError, mailbox_policy, retry_seconds
from .models import MailboxConnection, MailboxReceipt, MailboxUpdate
from .service import LEASE_SECONDS, backoff

TERMINAL = {"waiting_for_review", "waiting_for_input", "completed", "failed"}
RETRY_WINDOW = timedelta(hours=23)  # Conservative margin inside Resend's 24h guarantee.


def enabled():
    return os.getenv("RIVET_MAILBOX_UPDATES_ENABLED", "false").lower() == "true"


def sender_address():
    value = os.getenv("RIVET_EMAIL_FROM", "").strip()
    address = parseaddr(value)[1]
    if not address or "@" not in address or any(c in value for c in "\r\n"):
        return ""
    return value


def configuration():
    base = delivery.workspace_url()
    return {
        "enabled": enabled(),
        "sender_configured": bool(sender_address() and os.getenv("RESEND_API_KEY")),
        "workspace_link_configured": base.startswith("https://"),
        "delivery_status": "Provider acceptance only; no delivered/read claim without delivery evidence.",
    }


def render_update(brief, run):
    """Render only fields calculated by records.delivery.digest plus saved run state."""
    if run.status == "failed":
        heading = "Rivet preparation needs attention"
        state = "Rivet stopped before finishing preparation. Saved drafts remain available for review."
    elif run.status == "waiting_for_input":
        heading = "Rivet needs your input"
        state = str(
            run.result.get("question")
            or "Open the saved work to review the missing information."
        )[:1200]
    elif brief["prepared_count"]:
        heading = f"{brief['prepared_count']} drafts ready for review"
        state = "Rivet prepared the following internal coordination work."
    else:
        heading = "Rivet review update"
        state = "The automatic review finished without new coordination drafts. Check open comments and source-review flags below."
    lines = [heading, brief["summary"], "", state, ""]
    sections = [
        f"<h1>{escape(heading)}</h1>",
        f"<p><strong>{escape(brief['summary'])}</strong></p>",
        f"<p>{escape(state)}</p>",
    ]
    for prepared in brief["prepared_work"]:
        label = (
            "AI-prepared drafts" if prepared["origin"] == "ai" else "Rule-based drafts"
        )
        lines.append(label)
        lines.extend("- " + title for title in prepared["titles"])
        sections.append(
            f"<h2>{label}</h2><ul>"
            + "".join(f"<li>{escape(title)}</li>" for title in prepared["titles"])
            + "</ul>"
        )
        if prepared["url"]:
            lines.append(prepared["url"])
            sections.append(
                f'<p><a href="{escape(prepared["url"], quote=True)}">Review prepared work</a></p>'
            )
    if brief["items"]:
        lines.extend(["", "Items needing review"])
        sections.append("<h2>Items needing review</h2><ul>")
        for item in brief["items"][:8]:
            lines.append(f"- {item['label']}: {item['title']}")
            sections.append(
                f"<li><strong>{escape(item['label'])}</strong>: {escape(item['title'])}</li>"
            )
        sections.append("</ul>")
        if len(brief["items"]) > 8:
            lines.append("Additional items are available in the order record.")
            sections.append(
                "<p>Additional items are available in the order record.</p>"
            )
    lines.extend(
        [
            "",
            "Review the order: " + brief["order_url"],
            "",
            "Internal PM update. Flags are review cues, not engineering or commercial determinations. No customer response or approval was sent by this workflow.",
        ]
    )
    sections.extend(
        [
            f'<p><a href="{escape(brief["order_url"], quote=True)}">Open the order record</a> · Team sign-in required</p>',
            "<p>Internal PM update. Flags are review cues, not engineering or commercial determinations. No customer response or approval was sent by this workflow.</p>",
        ]
    )
    return {
        "subject": brief["subject"],
        "text": "\n".join(lines),
        "html": '<!doctype html><html><body style="font-family:Arial,sans-serif;font-size:16px;line-height:1.6;color:#25232b;max-width:680px;margin:32px auto">'
        + "".join(sections)
        + "</body></html>",
    }


def confirmation_hash(intake):
    return fingerprint(
        [
            intake.id,
            intake.sha256,
            intake.status,
            intake.order_id,
            intake.data.get("detected", {}),
        ]
    )


def enqueue_confirmation(s, receipt, connection, intake, base, sender):
    """A deterministic PM question, not a claim that an unmatched email was handled."""
    current_hash = confirmation_hash(intake)
    key = fingerprint([connection.id, "needs_confirmation", intake.id, current_hash])
    s.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "mailbox-update:" + key},
    )
    existing = s.scalar(scoped(MailboxUpdate).where(MailboxUpdate.dedupe_key == key))
    if existing:
        return existing
    subject = " ".join(
        str(
            intake.data.get("email", {}).get("subject")
            or receipt.data.get("subject")
            or "Untitled email"
        ).split()
    )[:180]
    origin = str(
        intake.data.get("email", {}).get("from")
        or receipt.data.get("sender")
        or "Sender not identified"
    )[:254]
    detected = intake.data.get("detected", {})
    jobs = detected.get("job_numbers", [])
    revisions = detected.get("revisions", [])
    observations = [
        "Job references: " + (", ".join(jobs) if jobs else "not identified"),
        "Revision references: "
        + (", ".join(revisions) if revisions else "not identified"),
    ]
    reason = str(
        intake.data.get("reason") or "The order or revision needs confirmation."
    )[:1200]
    link = f"{base}/#inbox?intake={quote(intake.id, safe='')}"
    candidates = intake.data.get("candidates", [])
    question = (
        f"Does this belong to {candidates[0]['number']}?"
        if len(candidates) == 1 and not candidates[0].get("identifier_conflict")
        else "Which order does this email belong to?"
    )
    from backend.records.intake_preview import preview

    excerpt = preview(s, intake).get("body", "")[:600]
    lines = [
        "Rivet captured an email that needs confirmation",
        subject,
        "From: " + origin,
        "",
        excerpt,
        "",
        question,
        reason,
        *observations,
        "",
        "Review this email: " + link,
        "",
        "The original email is saved. Rivet has not attached it to an order or prepared a customer response. After you confirm, processing continues automatically.",
        "Internal PM update · Team sign-in required. Nothing was sent to the customer.",
    ]
    update_id = uid()
    row = MailboxUpdate(
        id=update_id,
        connection_id=connection.id,
        receipt_id=receipt.id,
        order_id=None,
        run_id=None,
        input_hash=current_hash,
        run_status="needs_confirmation",
        dedupe_key=key,
        payload={
            "from": sender,
            "to": [connection.update_recipient],
            "headers": {
                "Auto-Submitted": "auto-generated",
                "X-Rivet-Update-ID": update_id,
            },
            "subject": "Rivet needs an order confirmed: " + subject,
            "text": "\n".join(lines),
            "html": '<!doctype html><html><body style="font-family:Arial,sans-serif;font-size:16px;line-height:1.6;color:#25232b;max-width:680px;margin:32px auto">'
            + "".join(f"<p>{escape(line)}</p>" for line in lines[:-4] if line)
            + f'<p><a href="{escape(link, quote=True)}">Review this email</a></p>'
            + "".join(f"<p>{escape(line)}</p>" for line in lines[-2:])
            + "</body></html>",
        },
    )
    s.add(row)
    s.flush()
    return row


def current_reply(s, order, receipt):
    """Only the latest live customer-thread proposal is worth interrupting the PM for."""
    from backend.reply_assistant.automatic import historical, latest_in_thread

    intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
    if (
        not intake
        or intake.order_id != order.id
        or intake.status not in {"matched", "created"}
        or receipt.status not in {"imported", "needs_confirmation"}
        or historical(receipt)
    ):
        return None, {}
    latest = latest_in_thread(s, order, receipt)
    if latest and latest.id != receipt.id:
        return None, {}
    return replies.prepared_for_receipt(s, order, receipt)


def enqueue_reply_ready(s, receipt, connection, order, draft, base, sender):
    """Notify the authorized PM of a saved draft; never copy the response into email."""
    key = fingerprint(
        [connection.id, "reply_ready", draft["id"], draft["evidence_hash"]]
    )
    s.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "mailbox-update:" + key},
    )
    existing = s.scalar(scoped(MailboxUpdate).where(MailboxUpdate.dedupe_key == key))
    if existing:
        return existing
    subject = " ".join(
        str(receipt.data.get("subject") or draft["subject"] or "Customer email").split()
    )[:180]
    number = " ".join(str(order.number or "Order").split())[:80]
    source_count = len(draft.get("source_refs", []))
    questions = len(draft.get("unresolved", []))
    files = len(draft.get("recommended_attachment_ids", []))
    order_url = f"{base}/#order/{quote(order.id, safe='')}"
    outlook_url = replies.outlook_url(receipt)
    prepared = f"Rivet prepared a reply using {source_count} source reference{'s' if source_count != 1 else ''}."
    unresolved = (
        f"{questions} open point{'s' if questions != 1 else ''} remain{'s' if questions == 1 else ''} explicit in the draft."
        if questions
        else "No unresolved questions were recorded during preparation."
    )
    attachments = (
        f"{files} released supporting file{'s' if files != 1 else ''} suggested for your review."
        if files
        else "No files have been selected for sending."
    )
    lines = [
        f"Reply ready · {number}",
        subject,
        "",
        prepared,
        unresolved,
        attachments,
        "",
    ]
    html = [
        f"<h1>Reply ready · {escape(number)}</h1>",
        f"<p><strong>{escape(subject)}</strong></p>",
        f"<p>{escape(prepared)}</p>",
        f"<p>{escape(unresolved)} {escape(attachments)}</p>",
    ]
    if outlook_url:
        lines.append("Open the customer email in Outlook: " + outlook_url)
        html.append(
            f'<p><a href="{escape(outlook_url, quote=True)}">Open in Outlook</a> · Choose Reply with Rivet to use the prepared draft.</p>'
        )
    lines.extend(
        [
            "Review the sources and history: " + order_url,
            "",
            "Internal PM update. Your reply is saved in Rivet; nothing was inserted into Outlook or sent to the customer. You choose what to send.",
        ]
    )
    html.extend(
        [
            f'<p><a href="{escape(order_url, quote=True)}">View sources and history</a> · Team sign-in required</p>',
            "<p>Internal PM update. Your reply is saved in Rivet; nothing was inserted into Outlook or sent to the customer. You choose what to send.</p>",
        ]
    )
    identifier = uid()
    row = MailboxUpdate(
        id=identifier,
        connection_id=connection.id,
        receipt_id=receipt.id,
        order_id=order.id,
        run_id=None,
        input_hash=draft["evidence_hash"],
        run_status="reply_ready",
        dedupe_key=key,
        payload={
            "from": sender,
            "to": [connection.update_recipient],
            "headers": {
                "Auto-Submitted": "auto-generated",
                "X-Rivet-Update-ID": identifier,
                "X-Rivet-Reply-ID": draft["id"],
            },
            "subject": f"Reply ready · {number}: {subject}",
            "text": "\n".join(lines),
            "html": '<!doctype html><html><body style="font-family:Arial,sans-serif;font-size:16px;line-height:1.6;color:#25232b;max-width:680px;margin:32px auto">'
            + "".join(html)
            + "</body></html>",
        },
    )
    s.add(row)
    s.flush()
    return row


def enqueue_for_receipt(s, receipt_id):
    """Return once per receipt/evidence/final state; do not notify unfinished parsing."""
    if not enabled():
        return None
    receipt = get(s, MailboxReceipt, receipt_id, True)
    connection = get(s, MailboxConnection, receipt.connection_id)
    if not connection.enabled or not receipt.intake_id:
        return None
    policy = mailbox_policy(
        current_org(), connection.mailbox, connection.microsoft_tenant_id
    )
    if policy["update_recipient"] != connection.update_recipient:
        return None
    base, sender = delivery.workspace_url(), sender_address()
    if not base.startswith("https://") or not sender or not os.getenv("RESEND_API_KEY"):
        return None
    intake = get(s, RecordIntake, receipt.intake_id)
    if intake.status not in {"matched", "created", "needs_confirmation"}:
        return None
    if not intake.order_id and intake.status != "needs_confirmation":
        return None
    if not intake.order_id:
        return enqueue_confirmation(s, receipt, connection, intake, base, sender)
    order = lock_order(s, intake.order_id)
    reply, preparation = current_reply(s, order, receipt)
    if reply and reply["status"] == "ready" and reply.get("prepared_automatically"):
        return enqueue_reply_ready(s, receipt, connection, order, reply, base, sender)
    if preparation.get("automatic") and preparation.get("draft_id"):
        # The reply pipeline owns this email. Avoid a second coordination update
        # while its reply is pending, or after the PM has already handled it.
        return None
    w = records.view(s, order)
    if not w["documents"] or any(
        d.get("state") not in {"ready", "mapped"} for d in w["documents"]
    ):
        return None
    current_hash = agent_service.input_hash(w)
    query = scoped(RecordAgentRun).where(
        RecordAgentRun.order_id == order.id, RecordAgentRun.origin == "event"
    )
    if receipt.data.get("update_run_id"):
        query = query.where(RecordAgentRun.id == receipt.data["update_run_id"])
    run = s.scalar(query.order_by(RecordAgentRun.created_at.desc()).limit(1))
    if (
        not run
        or run.status not in TERMINAL
        or run.input_hash != current_hash
        or agent_service.company_context_changed(s, order, run)
    ):
        return None
    # A pass may cover several messages; send one result per mailbox/run.
    key = fingerprint([connection.id, run.id, current_hash, run.status])
    s.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "mailbox-update:" + key},
    )
    existing = s.scalar(scoped(MailboxUpdate).where(MailboxUpdate.dedupe_key == key))
    if existing:
        receipt.data = {**receipt.data, "update_run_id": run.id}
        return existing
    proposals = list(
        s.scalars(
            scoped(RecordAgentProposal)
            .where(
                RecordAgentProposal.order_id == order.id,
                RecordAgentProposal.run_id == run.id,
                RecordAgentProposal.status == "pending",
                RecordAgentProposal.base_version == w["version"],
                RecordAgentProposal.input_hash == current_hash,
            )
            .order_by(RecordAgentProposal.created_at)
        )
    )
    brief = delivery.digest(
        w, base, [agent_service.proposal_view(p) for p in proposals]
    )
    update_id = uid()
    payload = {
        "from": sender,
        "to": [connection.update_recipient],
        "headers": {"Auto-Submitted": "auto-generated", "X-Rivet-Update-ID": update_id},
        **render_update(brief, run),
    }
    row = MailboxUpdate(
        id=update_id,
        connection_id=connection.id,
        receipt_id=receipt.id,
        order_id=order.id,
        run_id=run.id,
        input_hash=current_hash,
        run_status=run.status,
        dedupe_key=key,
        payload=payload,
    )
    s.add(row)
    # Bind the receipt to the automatic run that handled its evidence. Later
    # unrelated revisions should notify their own receipts, not re-mail old ones.
    receipt.data = {**receipt.data, "update_run_id": run.id}
    s.flush()
    return row


class ResendUpdates:
    def __init__(self, *, transport=None):
        self.client = httpx.Client(
            timeout=30, follow_redirects=False, transport=transport
        )

    def close(self):
        self.client.close()

    def send(self, payload, key):
        token = os.getenv("RESEND_API_KEY", "")
        if not token:
            raise GraphError(
                "setup_required",
                "Configure the transactional email provider before enabling internal updates.",
            )
        try:
            response = self.client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={"Authorization": "Bearer " + token, "Idempotency-Key": key},
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise GraphError(
                    "retry",
                    "The email provider is temporarily unavailable; an idempotent retry is scheduled.",
                    retry_after=retry_seconds(response.headers.get("Retry-After")),
                )
            if not response.is_success:
                raise GraphError(
                    "provider_rejected",
                    f"The email provider rejected the internal update (HTTP {response.status_code}).",
                )
            identifier = response.json().get("id")
            if not isinstance(identifier, str) or not 0 < len(identifier) <= 100:
                raise GraphError(
                    "retry",
                    "The email provider returned no acceptance ID. Retry uses the same idempotency key.",
                )
            return identifier
        except httpx.HTTPError:
            raise GraphError(
                "retry",
                "Email provider acknowledgement was unavailable. Retry uses the same idempotency key.",
            )
        except (ValueError, AttributeError):
            raise GraphError(
                "retry",
                "Email provider acknowledgement was invalid. Retry uses the same idempotency key.",
            )


def deliver_update(update_id, *, sender=None):
    if not enabled():
        return {"status": "disabled"}
    with Session.begin() as s:
        row = get(s, MailboxUpdate, update_id, True)
        if (
            row.status not in {"queued", "retry", "sending"}
            or row.next_attempt_at > now()
            or (row.lease_until and row.lease_until > now())
        ):
            return {"status": row.status}
        connection = get(s, MailboxConnection, row.connection_id)
        if not connection.enabled:
            return {"status": "paused"}
        try:
            policy = mailbox_policy(
                current_org(), connection.mailbox, connection.microsoft_tenant_id
            )
            if (
                row.payload.get("to") != [policy["update_recipient"]]
                or connection.update_recipient != policy["update_recipient"]
            ):
                raise GraphError(
                    "access_denied",
                    "The internal notification destination changed. Review the frozen update before sending.",
                )
        except GraphError as exc:
            row.status, row.error = "needs_attention", str(exc)
            return {"status": row.status}
        if row.first_attempt_at and now() - row.first_attempt_at >= RETRY_WINDOW:
            row.status, row.error = (
                "needs_attention",
                "The provider's safe idempotency retry window elapsed. Check provider acceptance before any manual resend.",
            )
            return {"status": row.status}
        if row.run_status == "needs_confirmation":
            receipt = get(s, MailboxReceipt, row.receipt_id)
            intake = (
                get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
            )
            changed = (
                not intake
                or bool(intake.order_id)
                or confirmation_hash(intake) != row.input_hash
            )
        elif row.run_status == "reply_ready":
            order = lock_order(s, row.order_id)
            receipt = get(s, MailboxReceipt, row.receipt_id)
            draft, preparation = current_reply(s, order, receipt)
            changed = (
                not draft
                or draft.get("status") != "ready"
                or preparation.get("status") != "ready"
                or draft.get("id")
                != row.payload.get("headers", {}).get("X-Rivet-Reply-ID")
                or draft.get("evidence_hash") != row.input_hash
            )
        else:
            order = lock_order(s, row.order_id)
            current = records.view(s, order)
            run = get(s, RecordAgentRun, row.run_id)
            receipt = get(s, MailboxReceipt, row.receipt_id)
            _reply, preparation = current_reply(s, order, receipt)
            changed = (
                bool(preparation.get("automatic") and preparation.get("draft_id"))
                or agent_service.input_hash(current) != row.input_hash
                or run.status != row.run_status
                or agent_service.company_context_changed(s, order, run)
            )
        if changed:
            row.status = "needs_attention" if row.first_attempt_at else "superseded"
            row.error = "The saved work changed before this update was confirmed. Review the current order and any earlier provider attempt."
            return {"status": row.status}
        token = uid()
        row.lease, row.lease_until = token, now() + timedelta(seconds=LEASE_SECONDS)
        row.first_attempt_at = row.first_attempt_at or now()
        row.attempts += 1
        row.status = "sending"
        payload, key = row.payload, "rivet-mailbox-update-" + row.id
    owned_sender = sender is None
    try:
        sender = sender or ResendUpdates()
        provider_id = sender.send(payload, key)
        with Session.begin() as s:
            row = get(s, MailboxUpdate, update_id, True)
            if row.lease != token:
                return {"status": "lease_lost"}
            row.status, row.provider_id = "accepted_by_provider", provider_id
            row.accepted_at, row.error = now(), ""
            row.lease, row.lease_until = "", None
        return {"status": "accepted_by_provider", "provider_id": provider_id}
    except Exception as exc:  # noqa: BLE001 - uncertain send outcome must retain original key and payload.
        error = (
            exc
            if isinstance(exc, GraphError)
            else GraphError(
                "retry",
                "Internal update processing was interrupted. Retry preserves its idempotency key.",
            )
        )
        with Session.begin() as s:
            row = get(s, MailboxUpdate, update_id, True)
            if row.lease == token:
                row.status = (
                    "needs_attention"
                    if row.attempts >= 8
                    or error.code in {"provider_rejected", "setup_required"}
                    else "retry"
                )
                row.error = str(error)
                row.next_attempt_at = now() + timedelta(
                    seconds=backoff(row.attempts, error.retry_after)
                )
                row.lease, row.lease_until = "", None
        return {"status": row.status, "error": str(error)}
    finally:
        if owned_sender and sender is not None:
            sender.close()


def schedule_and_deliver_updates(limit=10, *, sender=None):
    if not enabled():
        return []
    # Fair, durable round-robin scheduling: an old unresolved receipt cannot
    # starve new work, and a busy inbox cannot hide older completed work.
    with Session() as s:
        pending = list(
            s.scalars(
                select(MailboxReceipt)
                .join(MailboxConnection)
                .where(
                    MailboxConnection.enabled.is_(True),
                    MailboxReceipt.intake_id.is_not(None),
                    MailboxReceipt.update_check_at <= now(),
                )
                .order_by(MailboxReceipt.update_check_at)
                .limit(limit * 5)
            )
        )
    for receipt in pending:
        with identity_scope(
            Identity(receipt.organization_id, "Rivet internal updates")
        ):
            try:
                with Session.begin() as s:
                    current = get(s, MailboxReceipt, receipt.id, True)
                    current.update_check_at = now() + timedelta(seconds=120)
                    enqueue_for_receipt(s, receipt.id)
                    current.data = {
                        key: value
                        for key, value in current.data.items()
                        if key != "update_preparation_error"
                    }
            except Exception as exc:  # noqa: BLE001 - one bad record must not starve other PM updates.
                # No provider calls or unsafe destination when scope is absent.
                # Keep the error generic: exception text can include source data.
                with Session.begin() as s:
                    current = get(s, MailboxReceipt, receipt.id, True)
                    current.update_check_at = now() + timedelta(seconds=300)
                    current.data = {
                        **current.data,
                        "update_preparation_error": str(exc)
                        if isinstance(exc, GraphError)
                        else "The internal update could not be prepared. The saved email is retained and another attempt is scheduled.",
                    }
    with Session() as s:
        due = list(
            s.scalars(
                select(MailboxUpdate)
                .join(MailboxConnection)
                .where(
                    MailboxConnection.enabled.is_(True),
                    MailboxUpdate.status.in_(["queued", "retry", "sending"]),
                    MailboxUpdate.next_attempt_at <= now(),
                    or_(
                        MailboxUpdate.lease_until.is_(None),
                        MailboxUpdate.lease_until <= now(),
                    ),
                )
                .order_by(MailboxUpdate.next_attempt_at)
                .limit(limit)
            )
        )
    results = []
    for row in due:
        with identity_scope(Identity(row.organization_id, "Rivet internal updates")):
            try:
                results.append(deliver_update(row.id, sender=sender))
            except Exception:  # noqa: BLE001 - isolate one stale/corrupt saved work item.
                with Session.begin() as s:
                    current = get(s, MailboxUpdate, row.id, True)
                    if current.status != "accepted_by_provider":
                        current.status = "needs_attention"
                        current.error = "The internal update could not be validated for delivery. Review its saved work and any provider attempt before retrying."
                    results.append({"status": current.status})
    return results


def update_view(row):
    return {
        "id": row.id,
        "receipt_id": row.receipt_id,
        "order_id": row.order_id,
        "run_id": row.run_id,
        "kind": row.run_status
        if row.run_status in {"needs_confirmation", "reply_ready"}
        else "prepared_work",
        "status": row.status,
        "subject": row.payload["subject"],
        "recipient": row.payload["to"][0],
        "text": row.payload["text"],
        "attempts": row.attempts,
        "error": row.error,
        "created_at": row.created_at,
        "accepted_at": row.accepted_at,
        "provider_id": row.provider_id,
        "delivery_confirmed": False,
    }
