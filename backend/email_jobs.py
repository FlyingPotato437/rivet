"""Durable Resend work, scoped to the job's organization by the worker."""

from datetime import timedelta
from backend import mail
from backend.domain.service import fail, get, scoped
from backend.email_api import addresses
from backend.orders.service import lock_order
from backend.records.email_intake import import_email
from backend.storage.db import Session
from backend.storage.models import InboundEmail, OrderInbox, NoticeDelivery, now


def receive(email_id):
    with Session() as s:
        email = get(s, InboundEmail, email_id)
        if email.status == "imported":
            return
        provider_id = email.provider_id
    metadata, data = mail.raw_email(provider_id)
    with Session.begin() as s:
        email = get(s, InboundEmail, email_id, True)
        if email.status == "imported":
            return
        order = lock_order(s, email.order_id)
        inbox = s.scalar(
            scoped(OrderInbox).where(
                OrderInbox.order_id == order.id, OrderInbox.enabled == True
            )
        )
        if not inbox or inbox.address not in addresses(metadata):
            fail("The received email does not match this order’s forwarding address.")
        result = import_email(
            s, order, "forwarded-" + provider_id + ".eml", data, forwarded=True
        )
        email.document_id = result["document_id"]
        email.status = "imported"
        email.error = ""


def send(delivery_id):
    with Session.begin() as s:
        row = get(s, NoticeDelivery, delivery_id, True)
        if row.status == "sent":
            return
        if row.created_at < now() - timedelta(hours=23):
            fail(
                "Delivery needs manual review because the provider’s retry window has elapsed."
            )
        payload = row.payload
        recipients = list(dict.fromkeys(r["email"] for r in payload["recipients"]))
        row.status = "sending"
    for index, recipient in enumerate(recipients):
        with Session.begin() as s:
            row = get(s, NoticeDelivery, delivery_id, True)
            if any(item["recipient"] == recipient for item in row.provider_ids):
                continue
            result = mail.request(
                "POST",
                "/emails",
                body={
                    "from": payload["from"],
                    "to": [recipient],
                    "subject": payload["subject"],
                    "text": payload["body"],
                },
                key=f"rivet-notice-{delivery_id}-{index}",
            )
            row.provider_ids = [
                *row.provider_ids,
                {"recipient": recipient, "id": result["id"]},
            ]
    with Session.begin() as s:
        row = get(s, NoticeDelivery, delivery_id, True)
        row.status = "sent"
        row.error = ""


def record_failure(kind, payload, message):
    with Session.begin() as s:
        model, key = (
            (InboundEmail, "email_id")
            if kind == "inbound_email"
            else (NoticeDelivery, "delivery_id")
        )
        row = get(s, model, payload[key], True)
        row.status = "failed"
        row.error = message[:500]
