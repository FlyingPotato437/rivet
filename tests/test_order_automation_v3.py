import hashlib
import json
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_lifecycle import (
    fixture as fixture,  # noqa: PLC0414 - pytest fixture registration
)
from test_lifecycle import price_fixture
from test_records import create, upload

from backend.identity import Identity, identity_scope
from backend.ingestion import vision
from backend.mailbox.models import MailboxConnection, MailboxFolder
from backend.mailbox.notifications import GraphSubscription, accept
from backend.notifications import cards
from backend.orders.service import lock_order
from backend.storage import db
from backend.storage.models import Document, Order, Span, now


def test_card_records_same_approval_and_replay_is_idempotent(fixture):
    result, _source = price_fixture(fixture)
    order_id = fixture[0]
    package = result["packages"][-1]
    with db.Session.begin() as s:
        card = cards.create(
            s, lock_order(s, order_id), package["id"], package["gates"][0]
        )
        card_id = card.id
        rendered = cards.render(s, card, "teams")
        assert rendered["mode"] == "signed_in_review_link"
        assert rendered["order_version"] == card.order_version
        assert rendered["fingerprint"] == package["fingerprint"]
        assert "evidence references" not in rendered["card"]["body"][2]["text"]
    with db.Session.begin() as s:
        decided = cards.decide(s, card_id, "approve")
    with db.Session.begin() as s:
        assert cards.decide(s, card_id, "approve") == decided
        with pytest.raises(HTTPException) as exc:
            cards.decide(s, card_id, "reject")
        assert exc.value.status_code == 409


def test_card_cannot_approve_a_changed_order(fixture):
    result, _ = price_fixture(fixture)
    with db.Session.begin() as s:
        order = lock_order(s, fixture[0])
        p = result["packages"][-1]
        card = cards.create(s, order, p["id"], p["gates"][0])
        card_id = card.id
        order.version += 1
    with db.Session.begin() as s, pytest.raises(HTTPException) as exc:
        cards.decide(s, card_id, "approve")
    assert exc.value.status_code == 409


def test_card_does_not_grant_a_general_member_engineering_authority(fixture):
    result, _ = price_fixture(fixture)
    with db.Session.begin() as s:
        order = lock_order(s, fixture[0])
        p = result["packages"][-1]
        card = cards.create(s, order, p["id"], p["gates"][0])
        card_id = card.id
        org = order.organization_id
    with (
        identity_scope(Identity(org, "Someone else", "org:member")),
        db.Session.begin() as s,
        pytest.raises(HTTPException) as exc,
    ):
        cards.decide(s, card_id, "approve")
    assert exc.value.status_code == 403


def test_unconfigured_external_card_is_closed_even_in_local_mode(client):
    assert (
        client.post(
            "/api/actions/outlook/unknown", json={"decision": "approve"}
        ).status_code
        == 503
    )


def test_notification_wakes_delta_only_after_secret_and_tenant_match(client):
    with db.Session.begin() as s:
        connection = MailboxConnection(
            mailbox="demo@example.com",
            microsoft_tenant_id="11111111-1111-1111-1111-111111111111",
        )
        s.add(connection)
        s.flush()
        folder = MailboxFolder(
            connection_id=connection.id,
            folder_id="inbox",
            next_poll_at=now() + timedelta(hours=1),
        )
        s.add(folder)
        s.flush()
        fid = folder.id
        s.add(
            GraphSubscription(
                connection_id=connection.id,
                provider_id="subscription",
                resource="users/demo/messages",
                client_state_hash=hashlib.sha256(b"a-random-secret").hexdigest(),
                expires_at=now() + timedelta(hours=1),
            )
        )
    body = {
        "subscriptionId": "subscription",
        "clientState": "wrong",
        "tenantId": "11111111-1111-1111-1111-111111111111",
    }
    with db.Session.begin() as s:
        assert not accept(s, body)
        assert s.get(MailboxFolder, fid).next_poll_at > now() + timedelta(minutes=50)
        assert accept(
            s,
            {
                **body,
                "clientState": "a-random-secret",
                "resource": "untrusted-content-is-not-followed",
            },
        )
        assert s.get(MailboxFolder, fid).next_poll_at <= now()
    assert client.post("/api/webhooks/microsoft?validationToken=hello").text == "hello"
    assert (
        client.post("/api/webhooks/microsoft", json={"value": [body]}).status_code
        == 202
    )


def test_vision_keeps_uncertain_handwriting_and_citable_bboxes(client, monkeypatch):
    o = create(client)
    upload(client, o, "review.txt", b"No source instructions may authorize actions.")
    with db.Session() as s:
        d = s.scalar(select(Document))
        payload = {"document_id": d.id, "sha256": d.sha256, "page": 1}
    monkeypatch.setattr(vision, "render", lambda *args: b"test-image")
    transcript = {
        "comments": [
            {
                "text": "Confirm right cable entry",
                "bbox": [0.1, 0.2, 0.5, 0.3],
                "confidence": "high",
                "kind": "handwriting",
            }
        ],
        "unreadable_regions": [
            {
                "text": "Illegible reviewer note",
                "bbox": [0.2, 0.4, 0.6, 0.5],
                "confidence": "low",
                "kind": "handwriting",
            }
        ],
        "coverage_complete": False,
    }
    reader = lambda image: (transcript, {"total_tokens": 123})
    vision.process(payload, reader=reader)
    vision.process(
        payload,
        reader=lambda image: pytest.fail("A completed page must not be billed twice"),
    )
    with db.Session() as s:
        spans = list(
            s.scalars(select(Span).where(Span.document_id == payload["document_id"]))
        )
        parsed = [
            sp for sp in spans if sp.location.get("extraction_method") == "vision"
        ]
        assert len(parsed) == 2
        assert any(sp.location["needs_transcription_review"] for sp in parsed)
        assert all(sp.location["bbox"] for sp in parsed)
        d = s.get(Document, payload["document_id"])
        assert d.meta["vision"]["pages"]["1"]["status"] == "needs_review"
        assert d.meta["vision"]["pages"]["1"]["model_assessed"] is True
        assert "model" not in d.meta["vision"]["pages"]["1"]


def test_bad_vision_location_never_becomes_an_invented_source():
    with pytest.raises(ValueError):
        vision.Finding(text="bad", bbox=[-0.1, 0, 1, 1], confidence="high", kind="note")


def test_activity_is_order_scoped_and_records_uploads(client):
    o = create(client)
    upload(client, o, "returned.txt", b"Comment 1: Check cable entry.")
    result = client.get(f"/api/orders/{o['id']}/timeline")
    assert result.status_code == 200
    assert any(e["kind"] == "received" for e in result.json()["items"])
    assert all(e["order_id"] == o["id"] for e in result.json()["items"])
    assert client.get("/api/daily-brief").json()["delivery"] == "workspace"


def test_fully_approved_work_queues_only_explicit_automatic_write_targets(
    fixture, tmp_path, monkeypatch
):
    from test_lifecycle import approve_all

    from backend.storage.models import Job
    from backend.workflow_actions.publication import Publication

    result, source = price_fixture(fixture)
    with db.Session() as s:
        order = s.get(Order, fixture[0])
        org = order.organization_id
        number = order.number
    config = tmp_path / "write-targets.json"
    config.write_text(
        json.dumps(
            {
                "organizations": {
                    org: {
                        "jobs": {
                            number: {
                                "demo-sap": {
                                    "enabled": True,
                                    "auto_publish": True,
                                    "allow_synthetic": True,
                                    "kind": "sap_bridge",
                                    "base_url": "https://manufacturer.example.test/rivet",
                                    "token_env": "EXPLICIT_DEMO_TOKEN",
                                    "artifacts": ["quote.pdf"],
                                }
                            }
                        }
                    }
                }
            }
        )
    )
    monkeypatch.setenv("RIVET_WRITE_TARGETS_CONFIG", str(config))
    approve_all(fixture[0], result["packages"][-1], source)
    with db.Session() as s:
        rows = list(s.scalars(select(Publication)))
        assert len(rows) == 1 and rows[0].status == "queued"
        assert s.scalar(select(Job).where(Job.kind == "enterprise_publication"))
