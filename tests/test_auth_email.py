import base64
import json
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from svix.webhooks import Webhook

from backend import mail
from backend.identity import Identity, current_org, identity_scope, tenant_id
from backend.storage.db import Session
from backend.storage.models import OrderInbox, Document
from backend.worker import claim, process


@pytest.fixture
def auth(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    monkeypatch.setenv("RIVET_AUTH_MODE", "clerk")
    monkeypatch.setenv("CLERK_SECRET_KEY", "test-placeholder")
    monkeypatch.setenv("CLERK_JWT_KEY", public)
    monkeypatch.setenv(
        "VITE_CLERK_PUBLISHABLE_KEY",
        "pk_test_" + base64.b64encode(b"test.clerk.accounts.dev$").decode(),
    )

    def headers(org="org_alpha", role="admin", **overrides):
        claims = {
            "sub": "user_" + org,
            "sid": "sess_test",
            "v": 2,
            "iss": "https://test.clerk.accounts.dev",
            "azp": "http://127.0.0.1:5178",
            "iat": int(time.time()),
            "nbf": int(time.time()) - 2,
            "exp": int(time.time()) + 60,
            "o": {"id": org, "rol": role},
        }
        claims.update(overrides)
        return {
            "Authorization": "Bearer " + jwt.encode(claims, private, algorithm="RS256"),
            "Idempotency-Key": "same-key",
        }

    return headers


def create(client, headers, name="Auth test order"):
    r = client.post(
        "/api/orders",
        headers=headers,
        json={"title": name, "customer": "Test", "synthetic": True},
    )
    assert r.status_code == 200, r.text
    return r.json()["order"]


def test_auth_requires_valid_session_and_active_team(client, auth):
    assert client.get("/api/records").status_code == 401
    assert (
        client.get(
            "/api/records", headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )
    for overrides in [
        {"exp": int(time.time()) - 60},
        {"azp": "https://attacker.example"},
        {"iss": "https://other.clerk.accounts.dev"},
    ]:
        assert client.get("/api/records", headers=auth(**overrides)).status_code == 401
    assert client.get("/api/records", headers=auth(o={})).status_code == 403
    assert client.get("/api/records", headers=auth(sts="pending")).status_code == 403
    assert client.get("/api/me", headers=auth()).json()["user_id"] == "user_org_alpha"
    assert client.get("/api/auth/config").status_code == 200


def test_teams_isolate_orders_files_exports_and_retry_keys(client, auth):
    a, b = auth(), auth("org_beta")
    order = create(client, a)
    other = create(
        client, b, "Second team"
    )  # Same client retry key is independent by team.
    assert order["id"] != other["id"]
    assert len(client.get("/api/records", headers=a).json()["orders"]) == 1
    for path in [
        f"/orders/{order['id']}",
        f"/orders/{order['id']}/record",
        f"/orders/{order['id']}/record/export",
        f"/orders/{order['id']}/inbox",
    ]:
        assert client.get("/api" + path, headers=b).status_code == 404
    r = client.post(
        f"/api/projects/{order['project_id']}/documents",
        headers=a,
        files={
            "file": ("comments.txt", b"Comment 1: Confirm the finish.", "text/plain")
        },
        data={"kind": "auto"},
    )
    assert r.status_code == 202, r.text
    document_id = r.json()["id"]
    while task := claim():
        process(task)
    assert (
        client.get(f"/api/documents/{document_id}/content", headers=b).status_code
        == 404
    )
    assert (
        client.get(f"/api/documents/{document_id}/content", headers=a).status_code
        == 200
    )
    with identity_scope(Identity(tenant_id("org_alpha"), "test")), Session() as s:
        assert (
            s.scalar(select(Document).where(Document.id == document_id)).organization_id
            == current_org()
        )


def test_signed_actor_cannot_be_spoofed_and_members_cannot_configure_mail(client, auth):
    a = auth()
    order = create(client, a)
    base = f"/api/orders/{order['id']}/record"
    version = client.get(base, headers=a).json()["version"]
    r = client.post(
        base + "/comments",
        headers=a,
        json={
            "expected_version": version,
            "actor": "Forged CEO",
            "reason": "Test recorded identity",
            "number": "1",
            "text": "Confirm finish",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["events"][0]["actor"] == "user_org_alpha"
    assert (
        client.post(
            "/api/integrations/email/check", headers=auth(role="member"), json={}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/orders/{order['id']}/inbox/check",
            headers=auth(role="member"),
            json={},
        ).status_code
        == 403
    )
    assert (
        client.post(
            base + "/comments", headers=auth(role="viewer"), json={}
        ).status_code
        == 403
    )


def test_webhook_signature_dedup_and_scoped_worker_intake(client, auth, monkeypatch):
    a = auth()
    order = create(client, a)
    address = "order-test@inbound.example.com"
    with identity_scope(Identity(tenant_id("org_alpha"), "test")), Session.begin() as s:
        s.add(OrderInbox(order_id=order["id"], address=address))
    secret = (
        "whsec_" + base64.b64encode(b"a-test-webhook-key-of-32-characters").decode()
    )
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", secret)
    provider_id = str(uuid4())
    body = json.dumps(
        {"type": "email.received", "data": {"email_id": provider_id, "to": [address]}}
    )
    now = datetime.now(timezone.utc)
    signature = Webhook(secret).sign("event_1", now, body)
    headers = {
        "svix-id": "event_1",
        "svix-timestamp": str(int(now.timestamp())),
        "svix-signature": signature,
    }
    assert client.post("/api/webhooks/resend", content=body).status_code == 401
    assert (
        client.post(
            "/api/webhooks/resend", content=body + " ", headers=headers
        ).status_code
        == 401
    )
    assert (
        client.post("/api/webhooks/resend", content=body, headers=headers).json()[
            "status"
        ]
        == "queued"
    )
    assert (
        client.post("/api/webhooks/resend", content=body, headers=headers).json()[
            "status"
        ]
        == "duplicate"
    )
    msg = EmailMessage()
    msg["Subject"] = "Drawing comments"
    msg["From"] = "engineer@example.com"
    msg["To"] = address
    msg.set_content("Comment 1: Confirm finish.")
    msg.add_attachment(
        b"Comment 2: Confirm rating.",
        maintype="text",
        subtype="plain",
        filename="review.txt",
    )
    monkeypatch.setattr(
        mail, "raw_email", lambda _: ({"to": [address]}, msg.as_bytes())
    )
    while task := claim():
        process(task)
    inbox = client.get(f"/api/orders/{order['id']}/inbox", headers=a).json()
    assert inbox["emails"][0]["status"] == "imported"
    record = client.get(f"/api/orders/{order['id']}/record", headers=a).json()
    assert len(record["documents"]) == 2
    assert all(not c["reviewed"] for c in record["comments"])
    assert (
        client.get(
            f"/api/orders/{order['id']}/inbox", headers=auth("org_beta")
        ).status_code
        == 404
    )


def test_notice_sending_is_explicit_and_deduplicated(client, auth, monkeypatch):
    from backend.records import service
    from backend.orders.service import lock_order
    from sqlalchemy.orm.attributes import flag_modified

    a = auth()
    order = create(client, a)
    notice_id = str(uuid4())
    with identity_scope(Identity(tenant_id("org_alpha"), "test")), Session.begin() as s:
        row = service.ensure(s, lock_order(s, order["id"]))
        row.data["notices"] = [
            {
                "id": notice_id,
                "title": "Comment updated",
                "body": "Review the finish.",
                "recipients": [
                    {"email": "test@example.com", "name": "Test", "team": "Customer"}
                ],
                "status": "draft",
            }
        ]
        flag_modified(row, "data")
        version = row.version
    monkeypatch.setenv("RESEND_API_KEY", "test-placeholder")
    monkeypatch.setenv("RIVET_EMAIL_FROM", "Rivet <notices@example.com>")
    calls = []

    def send(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"id": "sent_test"}

    monkeypatch.setattr(mail, "request", send)
    assert calls == []
    endpoint = f"/api/orders/{order['id']}/record/notices/{notice_id}/send"
    assert (
        client.post(
            endpoint, headers=a, json={"expected_version": version + 1}
        ).status_code
        == 409
    )
    first = client.post(endpoint, headers=a, json={"expected_version": version})
    assert first.status_code == 202, first.text
    assert (
        client.post(endpoint, headers=a, json={"expected_version": version}).json()[
            "id"
        ]
        == first.json()["id"]
    )
    assert calls == []  # Durable queue; no request-thread sending.
    while task := claim():
        process(task)
    assert len(calls) == 1
    assert calls[0][2]["body"]["to"] == ["test@example.com"]
    assert calls[0][2]["key"].startswith("rivet-notice-")
    assert (
        client.get(f"/api/orders/{order['id']}/notice-deliveries", headers=a).json()[0][
            "status"
        ]
        == "sent"
    )


def test_local_mode_cannot_be_exposed_as_shared_server(client, monkeypatch):
    monkeypatch.setenv("RIVET_LOCAL_ONLY", "false")
    assert client.get("/api/records").status_code == 503
