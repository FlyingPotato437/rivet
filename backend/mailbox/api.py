"""Authenticated administration and observable mailbox coverage; never accepts secrets."""

from fastapi import APIRouter
from pydantic import Field

from backend.domain.service import fail, get, scoped
from backend.identity import current_identity
from backend.orders.schemas import StrictModel
from backend.storage.db import Session
from backend.storage.models import now

from .graph import GraphError, configured
from .models import MailboxConnection, MailboxFolder, MailboxReceipt, MailboxUpdate
from .service import connection_view, receipt_view, register

router = APIRouter(prefix="/api/mailboxes", tags=["Microsoft 365 mailbox"])


def admin():
    if current_identity().role != "org:admin":
        fail("A team administrator must manage mailbox connections.", 403)


class RegisterMailbox(StrictModel):
    microsoft_tenant_id: str = Field(min_length=36, max_length=36)
    mailbox: str = Field(min_length=3, max_length=254)
    folder_ids: list[str] = Field(
        default_factory=lambda: ["inbox"], min_length=1, max_length=20
    )


class MailboxState(StrictModel):
    enabled: bool


@router.get("")
def status():
    with Session() as s:
        return {
            "provider_configured": configured(),
            "items": [
                connection_view(s, row) for row in s.scalars(scoped(MailboxConnection))
            ],
        }


@router.post("", status_code=201)
def create(body: RegisterMailbox):
    admin()
    try:
        with Session.begin() as s:
            row = register(s, body.microsoft_tenant_id, body.mailbox, body.folder_ids)
            return connection_view(s, row)
    except GraphError as exc:
        fail(str(exc), 403 if exc.code == "access_denied" else 503)


@router.patch("/{id}")
def set_enabled(id: str, body: MailboxState):
    admin()
    with Session.begin() as s:
        connection = get(s, MailboxConnection, id, True)
        connection.enabled = body.enabled
        if body.enabled:
            for folder in s.scalars(
                scoped(MailboxFolder).where(MailboxFolder.connection_id == id)
            ):
                folder.next_poll_at = now()
        return connection_view(s, connection)


@router.post("/{id}/reconcile", status_code=202)
def reconcile(id: str):
    admin()
    with Session.begin() as s:
        get(s, MailboxConnection, id)
        for folder in s.scalars(
            scoped(MailboxFolder).where(MailboxFolder.connection_id == id)
        ):
            # Preserve cursor and outstanding leases. This is a wake-up, not a reset.
            folder.next_poll_at = now()
    return {"status": "scheduled"}


@router.get("/{id}/receipts")
def receipts(id: str, limit: int = 100):
    with Session() as s:
        get(s, MailboxConnection, id)
        return {
            "items": [
                receipt_view(s, r)
                for r in s.scalars(
                    scoped(MailboxReceipt)
                    .where(MailboxReceipt.connection_id == id)
                    .order_by(MailboxReceipt.created_at.desc())
                    .limit(max(1, min(limit, 200)))
                )
            ]
        }


@router.post("/{id}/receipts/{receipt_id}/retry", status_code=202)
def retry_receipt(id: str, receipt_id: str):
    admin()
    with Session.begin() as s:
        get(s, MailboxConnection, id)
        row = get(s, MailboxReceipt, receipt_id, True)
        if row.connection_id != id:
            fail("Record not found.", 404)
        if row.status not in {"retry", "needs_attention", "removed_before_capture"}:
            fail("Only an unsuccessful receipt can be retried.", 409)
        if row.lease_until and row.lease_until > now():
            fail("This email is being processed.", 409)
        row.status, row.error, row.attempts = "pending", "", 0
        row.next_attempt_at = now()
    return {"status": "scheduled"}


@router.get("/{id}/updates")
def updates(id: str, limit: int = 100):
    from .updates import configuration, update_view

    with Session() as s:
        get(s, MailboxConnection, id)
        return {
            **configuration(),
            "items": [
                update_view(row)
                for row in s.scalars(
                    scoped(MailboxUpdate)
                    .where(MailboxUpdate.connection_id == id)
                    .order_by(MailboxUpdate.created_at.desc())
                    .limit(max(1, min(limit, 200)))
                )
            ],
        }
