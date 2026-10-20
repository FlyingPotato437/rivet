"""Microsoft notifications wake durable reconciliation; delta remains authoritative."""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Request, Response
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from backend.domain.service import fail, get, scoped
from backend.identity import Identity, current_identity, identity_scope
from backend.storage.db import Session
from backend.storage.models import Base, Order, Row, now

from .models import MailboxConnection, MailboxFolder


class GraphSubscription(Row, Base):
    __tablename__ = "graph_subscriptions"
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("mailbox_connections.id"), index=True
    )
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"))
    provider_id: Mapped[str] = mapped_column(String(160), unique=True)
    resource: Mapped[str] = mapped_column(Text)
    client_state_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="current")
    __table_args__ = (UniqueConstraint("connection_id", "resource"),)


def callback_url():
    base = os.getenv("RIVET_NOTIFICATION_BASE_URL", "").rstrip("/")
    p = urlsplit(base)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.hostname in {"localhost", "127.0.0.1"}
        or p.username
        or p.query
        or p.fragment
        or p.path not in {"", "/"}
    ):
        fail(
            "Mailbox notifications require a publicly reachable HTTPS callback host.",
            503,
        )
    return base + "/api/webhooks/microsoft"


def subscribe(s, connection, resource, *, order_id=None, graph=None, renew=False):
    from .service import client_for

    if current_identity().role != "org:admin":
        fail("An administrator must configure event subscriptions.", 403)
    existing = s.scalar(
        scoped(GraphSubscription).where(
            GraphSubscription.connection_id == connection.id,
            GraphSubscription.resource == resource,
        )
    )
    if existing and not renew and existing.expires_at > now() + timedelta(minutes=5):
        return {
            "status": existing.status,
            "expires_at": existing.expires_at.isoformat(),
        }
    callback = callback_url()
    owned = graph is None
    graph = graph or client_for(connection)
    secret = secrets.token_urlsafe(32)
    expiry = now() + timedelta(hours=23)
    try:
        if existing:
            response = graph.client.patch(
                "https://graph.microsoft.com/v1.0/subscriptions/"
                + quote(existing.provider_id, safe=""),
                headers=graph.headers(),
                json={"expirationDateTime": expiry.isoformat()},
            )
        else:
            response = graph.client.post(
                "https://graph.microsoft.com/v1.0/subscriptions",
                headers=graph.headers(),
                json={
                    "changeType": "updated" if order_id else "created,updated,deleted",
                    "notificationUrl": callback,
                    "lifecycleNotificationUrl": callback,
                    "resource": resource,
                    "expirationDateTime": expiry.isoformat(),
                    "clientState": secret,
                },
            )
        if response.status_code not in {200, 201}:
            fail(
                f"Microsoft did not accept the event subscription (HTTP {response.status_code}). Polling remains active.",
                502,
            )
        payload = response.json()
        accepted_expiry = datetime.fromisoformat(payload["expirationDateTime"])
        if not existing:
            if payload.get(
                "resource", ""
            ).lower() != resource.lower() or not payload.get("id"):
                fail("Microsoft returned a different notification resource.", 502)
            existing = GraphSubscription(
                connection_id=connection.id,
                order_id=order_id,
                provider_id=payload["id"],
                resource=resource,
                client_state_hash=hashlib.sha256(secret.encode()).hexdigest(),
                expires_at=accepted_expiry,
            )
            s.add(existing)
        else:
            existing.expires_at = accepted_expiry
        s.flush()
        return {"status": "current", "expires_at": existing.expires_at.isoformat()}
    finally:
        if owned:
            graph.close()


def accept(s, notification):
    row = s.scalar(
        select(GraphSubscription).where(
            GraphSubscription.provider_id == notification.get("subscriptionId")
        )
    )
    if not row or not hmac.compare_digest(
        row.client_state_hash,
        hashlib.sha256(str(notification.get("clientState", "")).encode()).hexdigest(),
    ):
        return False
    with identity_scope(
        Identity(row.organization_id, "Microsoft notification", "org:member")
    ):
        connection = get(s, MailboxConnection, row.connection_id)
        if (
            not connection.enabled
            or notification.get("tenantId") != connection.microsoft_tenant_id
        ):
            return False
        # Do not consume body/content/URLs from notifications. The existing
        # bound connector and checkpoint obtain all authoritative state.
        row.last_event_at = now()
        if notification.get("lifecycleEvent"):
            row.status = "renewal_required"
        if row.order_id:
            from backend.connected_context.providers import bindings
            from backend.connected_context.service import enqueue

            for order in s.scalars(scoped(Order)):
                drive_resources = {
                    "drives/" + quote(item["drive_id"], safe="") + "/root"
                    for b in bindings(order.number)
                    if b.get("kind") == "sharepoint"
                    for item in b.get("items", [])
                }
                if row.resource in drive_resources:
                    enqueue(s, order)
        else:
            for f in s.scalars(
                scoped(MailboxFolder).where(
                    MailboxFolder.connection_id == connection.id
                )
            ):
                f.next_poll_at = now()
        return True


def renew_due():
    with Session() as s:
        due = [
            (r.id, r.organization_id)
            for r in s.scalars(
                select(GraphSubscription).where(
                    GraphSubscription.expires_at < now() + timedelta(hours=2)
                )
            )
        ]
    for id, org in due:
        with (
            identity_scope(Identity(org, "Rivet subscription renewer")),
            Session.begin() as s,
        ):
            row = get(s, GraphSubscription, id, True)
            connection = get(s, MailboxConnection, row.connection_id)
            if not connection.enabled:
                continue
            try:
                subscribe(
                    s, connection, row.resource, order_id=row.order_id, renew=True
                )
            except Exception:  # noqa: BLE001 - provider outage cannot disable delta capture
                row.status = "renewal_required"


router = APIRouter(prefix="/api", tags=["Microsoft events"])


@router.api_route("/webhooks/microsoft", methods=["GET", "POST"])
async def webhook(request: Request):
    token = request.query_params.get("validationToken")
    if token is not None:
        return Response(token[:2048], media_type="text/plain")
    raw = await request.body()
    if len(raw) > 262144:
        fail("Notification batch too large.", 413)
    import json

    try:
        values = json.loads(raw).get("value")
        if (
            not isinstance(values, list)
            or len(values) > 1000
            or any(not isinstance(v, dict) for v in values)
        ):
            raise ValueError()
    except (ValueError, AttributeError):
        fail("Invalid notification envelope.")
    with Session.begin() as s:
        for value in values:
            accept(s, value)
    return Response(status_code=202)


@router.post("/mailboxes/{id}/notifications")
def register(id: str):
    with Session.begin() as s:
        c = get(s, MailboxConnection, id)
        resource = "users/" + quote(c.mailbox, safe="") + "/messages"
        return subscribe(s, c, resource)


from backend.orders.schemas import StrictModel


class SourceSubscription(StrictModel):
    connection_id: str
    binding_key: str


@router.post("/orders/{id}/source-notifications")
def register_source(id: str, body: SourceSubscription):
    from backend.connected_context.providers import bindings

    with Session.begin() as s:
        order = get(s, Order, id)
        c = get(s, MailboxConnection, body.connection_id)
        binding = next(
            (
                b
                for b in bindings(order.number)
                if b["key"] == body.binding_key and b["kind"] == "sharepoint"
            ),
            None,
        )
        if not binding or binding.get("tenant_id") != c.microsoft_tenant_id:
            fail("Choose an existing, tenant-matched SharePoint binding.", 403)
        drives = {item["drive_id"] for item in binding.get("items", [])}
        return {
            "subscriptions": [
                subscribe(
                    s, c, "drives/" + quote(drive, safe="") + "/root", order_id=order.id
                )
                for drive in drives
            ]
        }
