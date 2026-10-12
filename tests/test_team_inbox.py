"""Team forwarding uses verified delivery and preserves uncertain originals."""

import base64
import json
from datetime import UTC, datetime
from email.message import EmailMessage
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from svix.webhooks import Webhook
from test_auth_email import auth  # noqa: F401 - shared authenticated fixture
from test_record_scenarios import drain
from test_records import post

from backend import mail
from backend.email_api import team_inbox_status
from backend.identity import Identity, identity_scope
from backend.storage.db import Session
from backend.storage.models import InboundEmail, Job, Order, RecordIntake, TeamInbox


def setup_inbox(client, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-receiving-key")
    monkeypatch.setenv("RESEND_RECEIVING_DOMAIN", "inbound.example.com")
    monkeypatch.setattr(mail, "request", lambda *args, **kwargs: {"data": []})
    response = post(client, "/integrations/team-inbox", {})
    assert response.status_code == 200, response.text
    return response.json()["address"]


def deliver(client, monkeypatch, address, provider_id=None):
    secret = (
        "whsec_" + base64.b64encode(b"a-test-webhook-key-of-32-characters").decode()
    )
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", secret)
    provider_id = provider_id or str(uuid4())
    body = json.dumps(
        {
            "type": "email.received",
            "data": {
                "email_id": provider_id,
                "received_for": [address] if isinstance(address, str) else address,
            },
        }
    )
    now = datetime.now(UTC)
    headers = {
        "svix-id": "event-team",
        "svix-timestamp": str(int(now.timestamp())),
        "svix-signature": Webhook(secret).sign("event-team", now, body),
    }
    return (
        client.post("/api/webhooks/resend", content=body, headers=headers),
        body,
        headers,
    )


def message(subject="Job 24-118 Rev C", text="Comment 1: Confirm cable entry."):
    email = EmailMessage()
    email["Subject"] = subject
    email["Message-ID"] = "<" + str(uuid4()) + "@example.com>"
    email.set_content(text)
    return email.as_bytes()


def test_team_inbox_is_unavailable_without_receiving_configuration(client, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_RECEIVING_DOMAIN", raising=False)
    state = client.get("/api/integrations/team-inbox").json()
    assert state["status"] == "setup_needed" and not state["address"]
    assert post(client, "/integrations/team-inbox", {}).status_code == 503
    monkeypatch.setenv("RESEND_RECEIVING_DOMAIN", "inbound.example.com")
    monkeypatch.setattr(
        mail,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPException(503, "Receiving access is unavailable.")
        ),
    )
    assert post(client, "/integrations/team-inbox", {}).status_code == 503
    with Session() as s:
        assert list(s.scalars(select(TeamInbox))) == []


def test_team_alias_is_stable_private_and_requires_admin_for_configuration(
    client, request, monkeypatch
):
    token = request.getfixturevalue("auth")
    # Authenticated reads never cross an organization's boundary.
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("RESEND_RECEIVING_DOMAIN", "inbound.example.com")
    monkeypatch.setattr(mail, "request", lambda *args, **kwargs: {"data": []})
    response = client.post("/api/integrations/team-inbox", headers=token())
    assert response.status_code == 200, response.text
    address = response.json()["address"]
    assert address.startswith("orders-") and address.endswith("@inbound.example.com")
    assert (
        client.post("/api/integrations/team-inbox", headers=token()).json()["address"]
        == address
    )
    assert (
        client.get("/api/integrations/team-inbox", headers=token("org_beta")).json()[
            "address"
        ]
        == ""
    )
    assert (
        client.post(
            "/api/integrations/team-inbox/check", headers=token(role="member")
        ).status_code
        == 403
    )


def test_verified_team_email_creates_order_in_worker_without_preexisting_project(
    client, monkeypatch
):
    address = setup_inbox(client, monkeypatch)
    response, body, headers = deliver(client, monkeypatch, address)
    assert response.json()["status"] == "queued"
    assert (
        client.post("/api/webhooks/resend", content=body, headers=headers).json()[
            "status"
        ]
        == "duplicate"
    )
    assert client.post("/api/webhooks/resend", content=body).status_code == 401
    with Session() as s:
        job = s.scalar(select(Job))
        assert job.project_id is None
        assert s.scalar(select(Order)) is None
    monkeypatch.setattr(
        mail, "raw_email", lambda _: ({"received_for": [address]}, message())
    )
    drain()
    state = client.get("/api/integrations/team-inbox").json()
    received = state["emails"][0]
    assert (
        received["status"] == "imported"
        and received["order_id"]
        and received["intake_id"]
    )
    record = client.get(f"/api/orders/{received['order_id']}/record").json()
    assert record["order"]["number"] == "24-118"
    assert any("Confirm cable entry" in c["text"] for c in record["comments"])
    assert all(not c["reviewed"] for c in record["comments"])
    with identity_scope(Identity(str(uuid4()), "Other PM")):
        assert team_inbox_status()["emails"] == []


def test_ambiguous_team_email_waits_for_confirmation_and_then_resumes(
    client, monkeypatch
):
    address = setup_inbox(client, monkeypatch)
    deliver(client, monkeypatch, address)
    original = message(
        "Returned drawing",
        "Job 24-118 or Job 24-119 Rev C\nComment 2: Confirm paint finish.",
    )
    monkeypatch.setattr(mail, "raw_email", lambda _: ({"to": [address]}, original))
    drain()
    received = client.get("/api/integrations/team-inbox").json()["emails"][0]
    assert received["status"] == "needs_confirmation" and not received["order_id"]
    assert client.get("/api/orders").json()["orders"] == []
    source = client.get(f"/api/records/intake/{received['intake_id']}/source")
    assert source.content == original
    confirmed = post(
        client,
        f"/records/intake/{received['intake_id']}/confirm",
        {"job_number": "24-118", "revision_label": "C"},
    )
    assert confirmed.status_code == 200, confirmed.text
    drain()
    after = client.get("/api/integrations/team-inbox").json()["emails"][0]
    assert (
        after["status"] == "imported"
        and after["order_id"] == confirmed.json()["order_id"]
    )


def test_provider_original_recipient_is_rechecked_and_wrong_mail_does_not_import(
    client, monkeypatch
):
    address = setup_inbox(client, monkeypatch)
    deliver(client, monkeypatch, address)
    monkeypatch.setattr(
        mail,
        "raw_email",
        lambda _: ({"to": ["other-team@inbound.example.com"]}, message()),
    )
    drain()
    received = client.get("/api/integrations/team-inbox").json()["emails"][0]
    assert received["status"] == "failed" and "does not match" in received["error"]
    with Session() as s:
        assert s.scalar(select(Order)) is None
        assert s.scalar(select(RecordIntake)) is None


def test_multiple_team_recipients_are_never_arbitrarily_routed(client, monkeypatch):
    address = setup_inbox(client, monkeypatch)
    second = "orders-second@inbound.example.com"
    with identity_scope(Identity(str(uuid4()), "Other PM")), Session.begin() as s:
        s.add(TeamInbox(address=second))
    response, _, _ = deliver(client, monkeypatch, [address, second])
    assert response.json()["status"] == "unmatched"
    with Session() as s:
        assert s.scalar(select(InboundEmail)) is None


def test_disabled_team_alias_rejects_queued_intake_and_new_deliveries(
    client, monkeypatch
):
    address = setup_inbox(client, monkeypatch)
    deliver(client, monkeypatch, address)
    disabled = post(client, "/integrations/team-inbox/disable", {})
    assert disabled.status_code == 200 and not disabled.json()["enabled"]
    monkeypatch.setattr(mail, "raw_email", lambda _: ({"to": [address]}, message()))
    drain()
    assert (
        client.get("/api/integrations/team-inbox").json()["emails"][0]["status"]
        == "failed"
    )
    assert deliver(client, monkeypatch, address)[0].json()["status"] == "unmatched"


def test_transient_provider_failure_can_be_explicitly_retried_without_duplicate_intake(
    client, monkeypatch
):
    address = setup_inbox(client, monkeypatch)
    deliver(client, monkeypatch, address)

    def unavailable(_):
        raise HTTPException(502, "Provider temporarily unavailable.")

    monkeypatch.setattr(mail, "raw_email", unavailable)
    drain()
    received = client.get("/api/integrations/team-inbox").json()["emails"][0]
    assert received["status"] == "failed"
    monkeypatch.setattr(mail, "raw_email", lambda _: ({"to": [address]}, message()))
    retry = post(client, f"/integrations/team-inbox/emails/{received['id']}/retry", {})
    assert retry.status_code == 200 and retry.json()["status"] == "queued"
    assert (
        post(
            client, f"/integrations/team-inbox/emails/{received['id']}/retry", {}
        ).status_code
        == 409
    )
    drain()
    after = client.get("/api/integrations/team-inbox").json()["emails"][0]
    assert after["status"] == "imported"
    with Session() as s:
        assert len(list(s.scalars(select(RecordIntake)))) == 1
        assert len(list(s.scalars(select(Order)))) == 1
