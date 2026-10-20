"""Polling is the source of truth; notifications may only accelerate reconciliation.

Each Graph page commits receipt IDs and its next cursor in one transaction. MIME
retrieval and document processing have independent durable retry states, so one
bad attachment never stops discovery of subsequent email. No message is sent.
"""

from datetime import timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select, text

from backend.domain.service import fail, get, scoped
from backend.identity import Identity, current_org, identity_scope
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import RecordIntake, now, uid

from .graph import GraphClient, GraphError, mailbox_policy, validate_delta_url
from .models import MailboxConnection, MailboxFolder, MailboxReceipt, MailboxUpdate

POLL_SECONDS = 120
LEASE_SECONDS = 180
MAX_AUTOMATIC_ATTEMPTS = 8


def register(s, microsoft_tenant_id, mailbox, folder_ids):
    policy = mailbox_policy(current_org(), mailbox, microsoft_tenant_id)
    if (
        not folder_ids
        or len(folder_ids) > 20
        or any(not f or len(f) > 512 for f in folder_ids)
    ):
        fail("Choose between one and twenty explicit mailbox folders.")
    s.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "mailbox-register:" + current_org()},
    )
    row = s.scalar(
        scoped(MailboxConnection).where(
            MailboxConnection.mailbox == policy["mailbox"],
            MailboxConnection.microsoft_tenant_id == policy["tenant_id"],
        )
    )
    if not row:
        row = MailboxConnection(
            mailbox=policy["mailbox"],
            microsoft_tenant_id=policy["tenant_id"],
            update_recipient=policy["update_recipient"],
        )
        s.add(row)
        s.flush()
    else:
        row.update_recipient = policy["update_recipient"]
    existing = {
        f.folder_id
        for f in s.scalars(
            scoped(MailboxFolder).where(MailboxFolder.connection_id == row.id)
        )
    }
    # Register adds scope explicitly; it never silently removes a tracked folder.
    for folder_id in dict.fromkeys(folder_ids):
        if folder_id not in existing:
            s.add(MailboxFolder(connection_id=row.id, folder_id=folder_id))
    s.flush()
    return row


def client_for(connection):
    return GraphClient(
        connection.organization_id, connection.mailbox, connection.microsoft_tenant_id
    )


def backoff(count, retry_after=None):
    return (
        retry_after
        if retry_after is not None
        else min(3600, 30 * 2 ** min(count - 1, 7))
    )


def connection_snapshot(row):
    # Copy only known non-secret configuration before leaving a transaction.
    return row


def persist_page(s, folder, page):
    """The checkpoint MUST NOT advance unless every item in this page has a receipt."""
    connection = get(s, MailboxConnection, folder.connection_id)
    values = page.get("value")
    next_link, delta_link = page.get("@odata.nextLink"), page.get("@odata.deltaLink")
    if not isinstance(values, list) or bool(next_link) == bool(delta_link):
        raise GraphError(
            "provider_error",
            "Microsoft returned an incomplete synchronization page; the checkpoint was not advanced.",
        )
    validate_delta_url(
        next_link or delta_link,
        connection.mailbox,
        folder.provider_folder_id or folder.folder_id,
    )
    s.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "mailbox-receipts:" + connection.id},
    )
    discovered = 0
    for item in values:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not 0 < len(item["id"]) <= 1024
        ):
            raise GraphError(
                "provider_error",
                "A Microsoft change lacks a valid message ID; the checkpoint was not advanced.",
            )
        receipt = s.scalar(
            scoped(MailboxReceipt).where(
                MailboxReceipt.connection_id == connection.id,
                MailboxReceipt.provider_message_id == item["id"],
            )
        )
        if not receipt:
            receipt = MailboxReceipt(
                connection_id=connection.id,
                provider_message_id=item["id"],
                folder_ids=[],
                data={},
            )
            s.add(receipt)
            s.flush()
            discovered += 1
        receipt.last_seen_at = now()
        if "@removed" in item:
            receipt.folder_ids = [
                f for f in receipt.folder_ids if f != folder.folder_id
            ]
            # Folder removal can mean a move, not permanent deletion. An immutable
            # message ID may still retrieve its MIME from the mailbox. Keep queued
            # capture alive, including an original saved before a worker crash.
            receipt.data = {
                **receipt.data,
                "removed_from_monitored_folders": not bool(receipt.folder_ids),
            }
            continue
        receipt.folder_ids = list(
            dict.fromkeys([*receipt.folder_ids, folder.folder_id])
        )
        receipt.internet_message_id = str(
            item.get("internetMessageId") or receipt.internet_message_id
        )[:1024]
        sender = item.get("from", {})
        sender = sender.get("emailAddress", {}) if isinstance(sender, dict) else {}
        sender = sender if isinstance(sender, dict) else {}
        receipt.data = {
            **receipt.data,
            "removed_from_monitored_folders": False,
            "subject": str(item.get("subject", receipt.data.get("subject", "")))[:2000],
            "web_link": str(item.get("webLink", receipt.data.get("web_link", "")))[
                :4000
            ],
            "sender": str(sender.get("address", receipt.data.get("sender", "")))[:254],
            "sender_name": str(sender.get("name", receipt.data.get("sender_name", "")))[
                :254
            ],
            "conversation_id": str(
                item.get("conversationId", receipt.data.get("conversation_id", ""))
            )[:1024],
            "received_at": str(
                item.get("receivedDateTime", receipt.data.get("received_at", ""))
            )[:64],
            "has_attachments": bool(
                item.get("hasAttachments", receipt.data.get("has_attachments", False))
            ),
        }
        if receipt.status == "removed_before_capture":
            receipt.status, receipt.error = "pending", ""
        if item.get("isDraft") is True and receipt.status == "pending":
            receipt.status = "excluded_draft"
        elif item.get("isDraft") is False and receipt.status == "excluded_draft":
            receipt.status = "pending"
    s.flush()
    folder.cursor = next_link or delta_link
    folder.state = "catching_up" if next_link else "current"
    folder.last_page_at = now()
    folder.discovered_count += discovered
    folder.failure_count, folder.error = 0, ""
    folder.lease_until = now() + timedelta(seconds=LEASE_SECONDS)
    if delta_link:
        folder.last_synced_at = now()
    return bool(next_link), discovered


def sync_folder(folder_id, *, client=None, max_pages=10):
    """Scoped worker entry point; a page budget preserves resumability, not truncation."""
    max_pages = max(1, min(max_pages, 20))
    with Session.begin() as s:
        folder = get(s, MailboxFolder, folder_id, True)
        connection = get(s, MailboxConnection, folder.connection_id)
        if (
            not connection.enabled
            or folder.next_poll_at > now()
            or (folder.lease_until and folder.lease_until > now())
        ):
            return {"status": "not_due", "pages": 0}
        token = uid()
        folder.lease, folder.lease_until = (
            token,
            now() + timedelta(seconds=LEASE_SECONDS),
        )
        folder.last_attempt_at = now()
        snapshot = connection_snapshot(connection)
    owned_client = client is None
    pages, discovered = 0, 0
    try:
        client = client or client_for(snapshot)
        while pages < max_pages:
            with Session() as s:
                folder = get(s, MailboxFolder, folder_id)
                if folder.lease != token:
                    return {"status": "lease_lost", "pages": pages}
                cursor, provider_folder = folder.cursor, folder.provider_folder_id
                configured_folder = folder.folder_id
            if not provider_folder:
                provider_folder = client.resolve_folder(configured_folder)
                with Session.begin() as s:
                    folder = get(s, MailboxFolder, folder_id, True)
                    if folder.lease != token:
                        return {"status": "lease_lost", "pages": pages}
                    folder.provider_folder_id = provider_folder
            page = client.delta(provider_folder, cursor)
            with Session.begin() as s:
                folder = get(s, MailboxFolder, folder_id, True)
                if folder.lease != token:
                    return {"status": "lease_lost", "pages": pages}
                more, count = persist_page(s, folder, page)
                discovered += count
                pages += 1
            if not more:
                break
        with Session.begin() as s:
            folder = get(s, MailboxFolder, folder_id, True)
            if folder.lease == token:
                folder.lease, folder.lease_until = "", None
                folder.next_poll_at = now() + timedelta(
                    seconds=1 if more else POLL_SECONDS
                )
        return {
            "status": "catching_up" if more else "current",
            "pages": pages,
            "discovered": discovered,
        }
    except Exception as exc:  # noqa: BLE001 - durable worker boundary; retain failed checkpoint.
        error = (
            exc
            if isinstance(exc, GraphError)
            else GraphError(
                "internal_error",
                "Mailbox reconciliation failed; the saved checkpoint is retained and retry is scheduled.",
            )
        )
        with Session.begin() as s:
            folder = get(s, MailboxFolder, folder_id, True)
            if folder.lease == token:
                folder.failure_count += 1
                folder.error = str(error)
                folder.lease, folder.lease_until = "", None
                if error.code == "cursor_expired":
                    folder.cursor = ""
                    folder.state = "resync_required"
                    folder.resync_count += 1
                    folder.next_poll_at = now() + timedelta(seconds=5)
                else:
                    folder.state = (
                        "access_required"
                        if error.code in {"access_denied", "setup_required"}
                        else "retry_scheduled"
                    )
                    folder.next_poll_at = now() + timedelta(
                        seconds=backoff(folder.failure_count, error.retry_after)
                    )
        return {
            "status": "resync_required" if error.code == "cursor_expired" else "error",
            "error": str(error),
            "pages": pages,
        }
    finally:
        if owned_client and client is not None:
            client.close()


def process_receipt(receipt_id, *, client=None):
    """Save original MIME, then use the same intake path as a forwarded email."""
    with Session.begin() as s:
        receipt = get(s, MailboxReceipt, receipt_id, True)
        connection = get(s, MailboxConnection, receipt.connection_id)
        if (
            not connection.enabled
            or receipt.status not in {"pending", "retry"}
            or receipt.next_attempt_at > now()
            or (receipt.lease_until and receipt.lease_until > now())
        ):
            return {"status": "not_due"}
        token = uid()
        receipt.lease, receipt.lease_until = (
            token,
            now() + timedelta(seconds=LEASE_SECONDS),
        )
        receipt.attempts += 1
        provider_id, blob = receipt.provider_message_id, receipt.blob
        snapshot = connection_snapshot(connection)
    owned_client = client is None
    try:
        if not blob:
            from backend.ingestion.parser import persist_blob

            client = client or client_for(snapshot)
            payload = client.mime(provider_id)
            blob, _ = persist_blob(payload, ".eml")
            with Session.begin() as s:
                receipt = get(s, MailboxReceipt, receipt_id, True)
                if receipt.lease != token:
                    return {"status": "lease_lost"}
                receipt.blob = blob
                receipt.lease_until = now() + timedelta(seconds=LEASE_SECONDS)
        from backend.records.intake import receive

        with Session.begin() as s:
            receipt = get(s, MailboxReceipt, receipt_id, True)
            if receipt.lease != token:
                return {"status": "lease_lost"}
            payload = (db.BLOBS / blob).read_bytes()
            message = BytesParser(policy=policy.default).parsebytes(payload)
            update_id = str(message.get("X-Rivet-Update-ID", ""))
            own_update = (
                s.scalar(scoped(MailboxUpdate).where(MailboxUpdate.id == update_id))
                if update_id
                else None
            )
            if (
                own_update
                and snapshot.mailbox in own_update.payload.get("to", [])
                and str(message.get("Auto-Submitted", "")).lower() == "auto-generated"
                and parseaddr(str(message.get("From", "")))[1].lower()
                == parseaddr(own_update.payload.get("from", ""))[1].lower()
                and str(message.get("Subject", "")) == own_update.payload.get("subject")
            ):
                receipt.status = "excluded_internal_update"
                receipt.processed_at, receipt.error = now(), ""
                receipt.lease, receipt.lease_until = "", None
                receipt.data = {
                    **receipt.data,
                    "internal_update_id": own_update.id,
                    "exclusion_reason": "Rivet's own internal update; retained without starting another preparation loop.",
                }
                return {"status": receipt.status}
            filename = (
                "mailbox-" + sha256(provider_id.encode()).hexdigest()[:24] + ".eml"
            )
            result = receive(s, filename, payload, forwarded=True, mailbox_capture=True)
            receipt.intake_id = result["intake_id"]
            receipt.status = "imported" if result.get("order_id") else result["status"]
            receipt.processed_at, receipt.error = now(), ""
            receipt.lease, receipt.lease_until = "", None
            intake = get(s, RecordIntake, receipt.intake_id)
            intake.data = {
                **intake.data,
                "mailbox_receipt_id": receipt.id,
                "mailbox_connection_id": receipt.connection_id,
            }
        return {"status": receipt.status, "intake_id": receipt.intake_id}
    except Exception as exc:  # noqa: BLE001 - retain receipt and original even when parsing fails.
        error = (
            exc
            if isinstance(exc, GraphError)
            else GraphError(
                "intake_rejected"
                if isinstance(exc, HTTPException)
                else "internal_error",
                ("Email captured but intake needs attention: " + str(exc.detail)[:500])
                if isinstance(exc, HTTPException)
                else "Original-email processing failed; review or retry the saved receipt.",
            )
        )
        with Session.begin() as s:
            receipt = get(s, MailboxReceipt, receipt_id, True)
            if receipt.lease == token:
                terminal = (
                    error.code in {"too_large", "intake_rejected", "not_found"}
                    or receipt.attempts >= MAX_AUTOMATIC_ATTEMPTS
                )
                receipt.status = (
                    "removed_before_capture"
                    if error.code == "not_found"
                    and receipt.data.get("removed_from_monitored_folders")
                    and not receipt.blob
                    else "needs_attention"
                    if terminal
                    else "retry"
                )
                receipt.error = str(error)
                receipt.next_attempt_at = now() + timedelta(
                    seconds=backoff(receipt.attempts, error.retry_after)
                )
                receipt.lease, receipt.lease_until = "", None
        return {"status": receipt.status, "error": str(error)}
    finally:
        if owned_client and client is not None:
            client.close()


def poll_due_mailboxes(limit=5, *, receipt_limit=10, client_factory=None):
    """Internal scheduler: every query crossing tenants re-enters the row's identity."""
    with Session() as s:
        folders = list(
            s.scalars(
                select(MailboxFolder)
                .join(MailboxConnection)
                .where(
                    MailboxConnection.enabled.is_(True),
                    MailboxFolder.next_poll_at <= now(),
                    or_(
                        MailboxFolder.lease_until.is_(None),
                        MailboxFolder.lease_until <= now(),
                    ),
                )
                .order_by(MailboxFolder.next_poll_at)
                .limit(limit)
            )
        )
    results = []
    for folder in folders:
        with identity_scope(Identity(folder.organization_id, "Rivet mailbox worker")):
            injected = None
            if client_factory:
                with Session() as s:
                    injected = client_factory(
                        get(s, MailboxConnection, folder.connection_id)
                    )
            results.append(sync_folder(folder.id, client=injected))
    with Session() as s:
        receipts = list(
            s.scalars(
                select(MailboxReceipt)
                .join(MailboxConnection)
                .where(
                    MailboxConnection.enabled.is_(True),
                    MailboxReceipt.status.in_(["pending", "retry"]),
                    MailboxReceipt.next_attempt_at <= now(),
                    or_(
                        MailboxReceipt.lease_until.is_(None),
                        MailboxReceipt.lease_until <= now(),
                    ),
                )
                .order_by(MailboxReceipt.next_attempt_at)
                .limit(receipt_limit)
            )
        )
    for receipt in receipts:
        with identity_scope(Identity(receipt.organization_id, "Rivet mailbox worker")):
            injected = None
            if client_factory:
                with Session() as s:
                    injected = client_factory(
                        get(s, MailboxConnection, receipt.connection_id)
                    )
            results.append(process_receipt(receipt.id, client=injected))
    return results


def find_receipt_by_message_id(s, internet_message_id, connection_id=None):
    if not internet_message_id:
        return None
    query = scoped(MailboxReceipt).where(
        MailboxReceipt.internet_message_id == internet_message_id
    )
    if connection_id:
        get(s, MailboxConnection, connection_id)
        query = query.where(MailboxReceipt.connection_id == connection_id)
    rows = list(s.scalars(query.limit(2)))
    return rows[0] if len(rows) == 1 else None


def receipt_view(s, receipt):
    intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
    return {
        "id": receipt.id,
        "connection_id": receipt.connection_id,
        "internet_message_id": receipt.internet_message_id,
        **receipt.data,
        "status": ("imported" if intake.order_id else intake.status)
        if intake
        else receipt.status,
        "intake_id": receipt.intake_id,
        "order_id": intake.order_id if intake else None,
        "attempts": receipt.attempts,
        "error": receipt.error,
        "received_at": receipt.data.get("received_at"),
        "last_seen_at": receipt.last_seen_at,
        "processed_at": receipt.processed_at,
        "next_attempt_at": receipt.next_attempt_at,
    }


def connection_view(s, connection):
    folders = list(
        s.scalars(
            scoped(MailboxFolder).where(MailboxFolder.connection_id == connection.id)
        )
    )
    effective_status = case(
        (RecordIntake.order_id.is_not(None), "imported"),
        (
            RecordIntake.status.in_(
                ["set_aside", "not_order_related", "needs_confirmation"]
            ),
            RecordIntake.status,
        ),
        else_=MailboxReceipt.status,
    )
    counts = dict(
        s.execute(
            select(effective_status, func.count(MailboxReceipt.id))
            .outerjoin(RecordIntake, RecordIntake.id == MailboxReceipt.intake_id)
            .where(
                MailboxReceipt.organization_id == current_org(),
                MailboxReceipt.connection_id == connection.id,
            )
            .group_by(effective_status)
        ).all()
    )
    sync_current = bool(folders) and all(
        f.state == "current"
        and f.last_synced_at
        and (now() - f.last_synced_at).total_seconds() < 3 * POLL_SECONDS
        for f in folders
    )
    attention = any(
        counts.get(state, 0)
        for state in ("retry", "needs_attention", "removed_before_capture")
    )
    return {
        "id": connection.id,
        "provider": "microsoft365",
        "mailbox": connection.mailbox,
        "microsoft_tenant_id": connection.microsoft_tenant_id,
        "update_recipient": connection.update_recipient,
        "enabled": connection.enabled,
        "poll_interval_seconds": POLL_SECONDS,
        "status": "paused"
        if not connection.enabled
        else "current"
        if sync_current and not attention
        else "attention_required",
        "sync_current": sync_current,
        "coverage": "Only the explicitly configured folders are monitored. Mail deleted before capture or moved elsewhere can require manual recovery.",
        "folders": [
            {
                "id": f.id,
                "folder_id": f.folder_id,
                "state": f.state,
                "last_attempt_at": f.last_attempt_at,
                "last_synced_at": f.last_synced_at,
                "next_poll_at": f.next_poll_at,
                "failure_count": f.failure_count,
                "resync_count": f.resync_count,
                "error": f.error,
            }
            for f in folders
        ],
        "counts": {
            state: counts.get(state, 0)
            for state in (
                "pending",
                "retry",
                "imported",
                "needs_confirmation",
                "needs_attention",
                "removed_before_capture",
                "excluded_draft",
                "excluded_internal_update",
                "not_order_related",
                "set_aside",
            )
        },
    }
