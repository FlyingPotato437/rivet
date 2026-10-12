"""Workspace email controls and the signature-verified Resend webhook."""

import os
import re
import secrets
from copy import deepcopy
from email.utils import getaddresses
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from svix.webhooks import Webhook, WebhookVerificationError

from backend import mail
from backend.domain.service import fail, get, scoped
from backend.identity import Identity, current_actor, identity_scope
from backend.orders.service import lock_order
from backend.records import service as records
from backend.storage.db import Session
from backend.storage.models import (
    InboundEmail,
    Job,
    NoticeDelivery,
    Order,
    OrderInbox,
    TeamInbox,
)

router = APIRouter(prefix="/api", tags=["Email"])


def addresses(data):
    # received_for is the SMTP envelope recipient; the message's To can differ after forwarding.
    values = data.get("received_for") or data.get("to") or []
    if isinstance(values, str):
        values = [values]
    return {value.lower() for _, value in getaddresses(values)}


def queue_inbound(s, inbox, email_id):
    try:
        email_id = str(UUID(email_id))
    except (ValueError, TypeError):
        fail("Invalid received-email identifier.")
    s.execute(text("SELECT pg_advisory_xact_lock(777005)"))
    # Provider IDs are global. Never return a different team's row to a caller.
    prior = s.scalar(select(InboundEmail).where(InboundEmail.provider_id == email_id))
    if prior:
        return False
    team_route = isinstance(inbox, TeamInbox)
    order = None if team_route else get(s, Order, inbox.order_id)
    email = InboundEmail(
        order_id=order.id if order else None,
        team_inbox_id=inbox.id if team_route else None,
        provider_id=email_id,
    )
    s.add(email)
    s.flush()
    s.add(
        Job(
            project_id=order.project_id if order else None,
            kind="inbound_email",
            payload={"email_id": email.id},
            key="email:" + email_id,
        )
    )
    s.flush()
    return True


@router.get("/integrations/email")
def email_status():
    return mail.status()


@router.post("/integrations/email/check")
def check_email():
    try:
        mail.request("GET", "/emails/receiving?limit=1")
        return {
            **mail.status(),
            "receiving_access": True,
            "message": "Resend receiving access verified.",
        }
    except HTTPException as exc:
        return {**mail.status(), "receiving_access": False, "message": exc.detail}


@router.get("/integrations/team-inbox")
def team_inbox_status():
    state = mail.status()
    with Session() as s:
        inbox = s.scalar(scoped(TeamInbox))
        emails = (
            list(
                s.scalars(
                    scoped(InboundEmail)
                    .where(InboundEmail.team_inbox_id == inbox.id)
                    .order_by(InboundEmail.created_at.desc())
                    .limit(30)
                )
            )
            if inbox
            else []
        )
        configured = bool(state["configured"] and state["receiving_domain"])
        return {
            **state,
            "status": "configured"
            if configured and inbox and inbox.enabled
            else "setup_needed",
            "automatic_delivery_configured": bool(
                configured
                and inbox
                and inbox.enabled
                and state["webhook_configured"]
                and not state["local_only"]
            ),
            "address": inbox.address if inbox else "",
            "enabled": bool(inbox and inbox.enabled),
            "detail": "Forward returned drawings to this team address. Exact job and revision matches are imported; uncertain matches wait for your confirmation."
            if inbox and inbox.enabled
            else "A team administrator can create a forwarding address after Resend receiving access and the receiving domain are configured.",
            "emails": [
                {
                    "id": email.id,
                    "status": email.status,
                    "intake_id": email.intake_id,
                    "order_id": email.order_id,
                    "document_id": email.document_id,
                    "error": email.error,
                    "created_at": email.created_at.isoformat(),
                }
                for email in emails
            ],
        }


@router.post("/integrations/team-inbox")
def create_team_inbox():
    domain = os.getenv("RESEND_RECEIVING_DOMAIN", "").lower().strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", domain):
        fail(
            "Configure a Resend receiving domain before creating a team forwarding address.",
            503,
        )
    # The API key must be able to retrieve received messages, not only send.
    mail.request("GET", "/emails/receiving?limit=1")
    with Session.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(777006)"))
        inbox = s.scalar(scoped(TeamInbox).with_for_update())
        if not inbox:
            inbox = TeamInbox(address="orders-" + secrets.token_hex(12) + "@" + domain)
            s.add(inbox)
        elif not inbox.address.endswith("@" + domain):
            fail(
                "The existing team address uses another receiving domain. Preserve that domain or migrate its pending email before replacing it.",
                409,
            )
        inbox.enabled = True
        s.flush()
    return team_inbox_status()


@router.post("/integrations/team-inbox/disable")
def disable_team_inbox():
    with Session.begin() as s:
        inbox = s.scalar(scoped(TeamInbox).with_for_update())
        if inbox:
            inbox.enabled = False
    return team_inbox_status()


@router.post("/integrations/team-inbox/check")
def check_team_inbox():
    with Session() as s:
        inbox = s.scalar(scoped(TeamInbox).where(TeamInbox.enabled == True))
        if not inbox:
            fail("Create the team forwarding address first.")
    page = mail.request("GET", "/emails/receiving?limit=100")
    count = 0
    with Session.begin() as s:
        # Recheck enabled state inside the write transaction.
        inbox = get(s, TeamInbox, inbox.id, True)
        if not inbox.enabled:
            fail("Team forwarding has been disabled.", 409)
        for email in page.get("data", []):
            if inbox.address in addresses(email):
                count += int(queue_inbound(s, inbox, email["id"]))
    return {
        "queued": count,
        "has_more": bool(page.get("has_more")),
        "message": f"Queued {count} new emails from the latest 100 received messages.",
    }


@router.post("/integrations/team-inbox/emails/{id}/retry")
def retry_team_email(id: str):
    with Session.begin() as s:
        email = get(s, InboundEmail, id, True)
        if not email.team_inbox_id:
            fail("Team inbox email not found.", 404)
        inbox = get(s, TeamInbox, email.team_inbox_id)
        if not inbox.enabled:
            fail("Enable team forwarding before retrying this email.", 409)
        job = s.scalar(
            scoped(Job).where(Job.key == "email:" + email.provider_id).with_for_update()
        )
        if email.status != "failed" or not job or job.status != "failed":
            fail("Only a failed email can be retried. Refresh the inbox status.", 409)
        email.status, email.error = "queued", ""
        job.status, job.error, job.attempts = "queued", "", 0
        job.lease, job.lease_until = None, None
        return {"id": email.id, "status": "queued"}


@router.get("/orders/{id}/inbox")
def inbox_status(id: str):
    with Session() as s:
        get(s, Order, id)
        inbox = s.scalar(scoped(OrderInbox).where(OrderInbox.order_id == id))
        emails = s.scalars(
            scoped(InboundEmail)
            .where(InboundEmail.order_id == id)
            .order_by(InboundEmail.created_at.desc())
            .limit(25)
        )
        return {
            **mail.status(),
            "address": inbox.address if inbox else "",
            "enabled": inbox.enabled if inbox else False,
            "emails": [
                {
                    "id": e.id,
                    "status": e.status,
                    "document_id": e.document_id,
                    "error": e.error,
                    "created_at": e.created_at.isoformat(),
                }
                for e in emails
            ],
        }


@router.post("/orders/{id}/inbox")
def create_inbox(id: str):
    domain = os.getenv("RESEND_RECEIVING_DOMAIN", "").lower().strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", domain):
        fail(
            "Set a verified Resend receiving domain before creating a forwarding address.",
            503,
        )
    mail.request("GET", "/emails/receiving?limit=1")
    with Session.begin() as s:
        order = lock_order(s, id)
        inbox = s.scalar(scoped(OrderInbox).where(OrderInbox.order_id == order.id))
        if not inbox:
            inbox = OrderInbox(
                order_id=id, address="order-" + secrets.token_hex(12) + "@" + domain
            )
            s.add(inbox)
        s.flush()
        return {"address": inbox.address}


@router.post("/orders/{id}/inbox/check")
def check_inbox(id: str):
    with Session() as s:
        get(s, Order, id)
        inbox = s.scalar(
            scoped(OrderInbox).where(
                OrderInbox.order_id == id, OrderInbox.enabled == True
            )
        )
        if not inbox:
            fail("Create this order’s forwarding address first.")
    # Local development can pull from Resend without exposing a localhost webhook.
    page = mail.request("GET", "/emails/receiving?limit=100")
    count = 0
    with Session.begin() as s:
        for email in page.get("data", []):
            if inbox.address in addresses(email):
                count += int(queue_inbound(s, inbox, email["id"]))
    return {
        "queued": count,
        "has_more": bool(page.get("has_more")),
        "message": f"Queued {count} new emails from the latest 100 received messages.",
    }


@router.post("/webhooks/resend")
async def webhook(request: Request):
    secret = os.getenv("RESEND_WEBHOOK_SECRET")
    if not secret:
        fail("The receiving webhook has not been configured.", 503)
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 256 * 1024:
            fail("Webhook payload too large.", 413)
    try:
        payload = Webhook(secret).verify(bytes(raw), dict(request.headers))
    except (WebhookVerificationError, ValueError):
        fail("Invalid webhook signature.", 401)
    if payload.get("type") != "email.received":
        return {"status": "ignored"}
    data = payload.get("data", {})
    recipients = addresses(data)
    with Session.begin() as s:
        # This lookup is deliberately global: only a verified provider event can route it.
        routes = list(
            s.scalars(
                select(OrderInbox).where(
                    OrderInbox.address.in_(recipients), OrderInbox.enabled == True
                )
            )
        )
        routes.extend(
            s.scalars(
                select(TeamInbox).where(
                    TeamInbox.address.in_(recipients), TeamInbox.enabled == True
                )
            )
        )
        if len(routes) != 1:
            return {"status": "unmatched"}
        inbox = routes[0]
        with identity_scope(Identity(inbox.organization_id, "Resend intake")):
            queued = queue_inbound(s, inbox, data.get("email_id", ""))
    return {"status": "queued" if queued else "duplicate"}


class SendNotice(BaseModel):
    expected_version: int = Field(ge=1)


@router.get("/orders/{id}/notice-deliveries")
def deliveries(id: str):
    with Session() as s:
        get(s, Order, id)
        return [
            {"id": d.id, "notice_id": d.notice_id, "status": d.status, "error": d.error}
            for d in s.scalars(
                scoped(NoticeDelivery).where(NoticeDelivery.order_id == id)
            )
        ]


@router.post("/orders/{id}/record/notices/{item}/send", status_code=202)
def send_notice(id: str, item: str, body: SendNotice):
    if not mail.status()["sending_ready"]:
        fail("Configure a verified sender address before sending notices.", 503)
    with Session.begin() as s:
        order = lock_order(s, id)
        prior = s.scalar(
            scoped(NoticeDelivery).where(
                NoticeDelivery.order_id == id, NoticeDelivery.notice_id == item
            )
        )
        if prior:
            return {"id": prior.id, "status": prior.status}
        record = records.ensure(s, order)
        if record.version != body.expected_version:
            fail(
                "The record changed. Refresh and review the notice before sending.", 409
            )
        notice = next((n for n in record.data["notices"] if n["id"] == item), None)
        if not notice or not notice["recipients"]:
            fail("This notice has no recipients.")
        if len(notice["recipients"]) > 50:
            fail("A notice can have at most 50 recipients.")
        payload = deepcopy(notice)
        payload["from"] = os.environ["RIVET_EMAIL_FROM"]
        payload["subject"] = (
            order.number + " — " + notice["title"].replace("\r", " ").replace("\n", " ")
        )
        row = NoticeDelivery(
            order_id=id, notice_id=item, payload=payload, actor=current_actor()
        )
        s.add(row)
        s.flush()
        s.add(
            Job(
                project_id=order.project_id,
                kind="send_notice",
                payload={"delivery_id": row.id},
                key="notice:" + row.id,
            )
        )
        return {"id": row.id, "status": row.status}
