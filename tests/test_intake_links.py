"""Notification links always resolve the exact tenant-owned intake item."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.storage.db import Session
from backend.storage.models import RecordIntake


def test_link_resolves_file_older_than_latest_queue_page(client):
    ids = [str(uuid4()) for _ in range(101)]
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with Session.begin() as session:
        for number, id in enumerate(ids):
            session.add(
                RecordIntake(
                    id=id,
                    name=f"returned-{number}.txt",
                    blob=f"fixture-{number}.txt",
                    sha256=f"{number:064x}",
                    created_at=start + timedelta(minutes=number),
                    status="needs_confirmation",
                    data={
                        "detected": {"revision": "C"},
                        "candidates": [],
                        "reason": "Confirm the order before attaching this file.",
                    },
                )
            )
    recent = client.get("/api/records/intake").json()["items"]
    assert len(recent) == 100
    assert ids[0] not in {row["intake_id"] for row in recent}
    response = client.get(f"/api/records/intake/{ids[0]}")
    assert response.status_code == 200
    assert response.json()["intake_id"] == ids[0]
    assert response.json()["filename"] == "returned-0.txt"
    assert response.json()["status"] == "needs_confirmation"


def test_link_does_not_disclose_another_teams_file(client):
    id = str(uuid4())
    with Session.begin() as session:
        session.add(
            RecordIntake(
                id=id,
                organization_id=str(uuid4()),
                name="private-returned.pdf",
                blob="private.pdf",
                sha256="a" * 64,
                status="needs_confirmation",
                data={"reason": "Confidential customer context"},
            )
        )
    response = client.get(f"/api/records/intake/{id}")
    missing = client.get(f"/api/records/intake/{uuid4()}")
    assert response.status_code == missing.status_code == 404
    assert response.json() == missing.json()
    assert "private" not in response.text
    assert "Confidential" not in response.text


def test_resolved_link_returns_confirmed_order_without_reimporting(client):
    upload = client.post(
        "/api/records/intake",
        files={"file": ("returned.txt", b"Please confirm the cable entry.")},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert upload.status_code == 202, upload.text
    id = upload.json()["intake_id"]
    confirmed = client.post(
        f"/api/records/intake/{id}/confirm",
        json={
            "job_number": "24-118",
            "revision_label": "C",
            "title": "East plant switchboard",
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert confirmed.status_code == 200, confirmed.text
    response = client.get(f"/api/records/intake/{id}")
    assert response.status_code == 200
    assert response.json()["order_id"] == confirmed.json()["order_id"]
    assert response.json()["status"] == "created"
    assert len(client.get("/api/orders").json()["orders"]) == 1


def test_malformed_or_missing_link_is_not_an_unrelated_queue_item(client):
    for id in ("undefined", "invalid-link", str(uuid4())):
        response = client.get(f"/api/records/intake/{id}")
        assert response.status_code == 404
        assert "items" not in response.json()


def test_confirmation_queue_is_not_crowded_out_by_routine_mail_or_other_tenants(client):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    pending_id, other_id = str(uuid4()), str(uuid4())
    with Session.begin() as session:
        session.add(
            RecordIntake(
                id=pending_id,
                name="returned.eml",
                blob="returned.eml",
                sha256="f" * 64,
                created_at=start,
                status="needs_confirmation",
                data={},
            )
        )
        session.add(
            RecordIntake(
                id=other_id,
                organization_id=str(uuid4()),
                name="private.eml",
                blob="private.eml",
                sha256="e" * 64,
                created_at=start,
                status="needs_confirmation",
                data={},
            )
        )
        for number in range(105):
            session.add(
                RecordIntake(
                    name=f"routine-{number}.eml",
                    blob=f"routine-{number}.eml",
                    sha256=f"{number:064x}",
                    created_at=start + timedelta(minutes=number + 1),
                    status="not_order_related",
                    data={},
                )
            )
    recent = client.get("/api/records/intake").json()["items"]
    assert len(recent) == 100
    assert all(row["status"] == "not_order_related" for row in recent)
    pending = client.get("/api/records/intake?status=needs_confirmation")
    assert pending.status_code == 200
    assert [row["intake_id"] for row in pending.json()["items"]] == [pending_id]
    assert other_id not in pending.text
    routine = client.get("/api/records/intake?status=not_order_related").json()["items"]
    assert len(routine) == 100
    assert all(row["status"] == "not_order_related" for row in routine)


def test_intake_status_filter_rejects_unknown_states(client):
    assert client.get("/api/records/intake?status=anything").status_code == 422
