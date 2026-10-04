"""The public waitlist accepts opt-ins without exposing private workspace data."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from backend.storage.db import Session
from backend.storage.models import PilotRequest
from scripts.export_pilot_requests import spreadsheet_text

INQUIRY = {
    "name": "Test PM",
    "email": "pm@example.com",
    "company": "Test Manufacturer",
    "workflow": "Submittal comments",
    "consent": True,
}


def test_public_intake_commits_and_deduplicates_without_overwriting(
    client, monkeypatch
):
    monkeypatch.setenv("RIVET_AUTH_MODE", "clerk")
    response = client.post(
        "/api/pilot-requests", json={**INQUIRY, "email": " PM@EXAMPLE.COM "}
    )
    assert response.status_code == 202, response.text
    assert response.json() == {"accepted": True}
    assert (
        client.post(
            "/api/pilot-requests", json={**INQUIRY, "name": "Replacement"}
        ).json()
        == response.json()
    )
    with Session() as session:
        rows = session.scalars(select(PilotRequest)).all()
        assert len(rows) == 1
        assert rows[0].email == "pm@example.com"
        assert rows[0].name == "Test PM"
        assert rows[0].consent is True
    assert client.get("/api/pilot-requests").status_code == 405
    assert client.get("/api/records").status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"email": "bad address"},
        {"consent": False},
        {"name": ""},
        {"company": "a"},
        {"workflow": "x" * 1201},
        {"unexpected": "field"},
    ],
)
def test_invalid_inquiries_are_not_saved(client, change):
    assert (
        client.post("/api/pilot-requests", json={**INQUIRY, **change}).status_code
        == 422
    )
    with Session() as session:
        assert session.scalar(select(func.count()).select_from(PilotRequest)) == 0


def test_honeypot_and_request_boundaries(client):
    assert (
        client.post(
            "/api/pilot-requests", json={**INQUIRY, "website": "bot.example"}
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/api/pilot-requests",
            headers={"Origin": "https://attacker.example"},
            json=INQUIRY,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/pilot-requests", headers={"X-Rivet-Client": ""}, json=INQUIRY
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/pilot-requests", headers={"Content-Length": "invalid"}, json=INQUIRY
        ).status_code
        == 400
    )
    assert client.post("/api/pilot-requests", content="x" * 6001).status_code == 413
    with Session() as session:
        assert session.scalar(select(func.count()).select_from(PilotRequest)) == 0


def test_intake_hourly_cap(client):
    with Session.begin() as session:
        session.add_all(
            [
                PilotRequest(
                    name="Test",
                    email=f"pm{i}@example.com",
                    company="Test",
                    consent=True,
                    created_at=datetime.now(UTC),
                )
                for i in range(100)
            ]
        )
    assert client.post("/api/pilot-requests", json=INQUIRY).status_code == 429


def test_export_neutralizes_spreadsheet_formulas():
    assert spreadsheet_text(" =HYPERLINK(1)") == "' =HYPERLINK(1)"
    assert spreadsheet_text("Manufacturer") == "Manufacturer"
