"""PMs can inspect saved mail without trusting or executing its HTML."""

from email.message import EmailMessage
from uuid import uuid4

import pytest

from backend.records.intake_preview import email_preview
from backend.storage.db import Session
from backend.storage.models import RecordIntake


def upload(client, payload):
    response = client.post(
        "/api/records/intake",
        files={"file": ("returned.eml", payload)},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()["intake_id"]


def message():
    mail = EmailMessage()
    mail["Subject"] = "Returned drawings — please confirm"
    mail["From"] = "Reviewer <reviewer@example.test>"
    mail["To"] = "PM <pm@example.test>"
    mail["Date"] = "Tue, 13 Oct 2026 12:00:00 -0700"
    return mail


def test_preview_and_attachment_work_before_order_assignment(client):
    mail = message()
    mail.set_content("Please confirm the breaker on E-10.\nNo job number is supplied.")
    mail.add_attachment(
        b"Comment: Confirm the breaker.",
        maintype="text",
        subtype="plain",
        filename="review.txt",
    )
    id = upload(client, mail.as_bytes())
    result = client.get(f"/api/records/intake/{id}/preview")
    assert result.status_code == 200
    p = result.json()
    assert p["subject"] == mail["Subject"]
    assert p["from"] == mail["From"]
    assert "No job number" in p["body"]
    assert p["attachments"][0]["name"] == "review.txt"
    assert p["attachments"][0]["size"] == len(b"Comment: Confirm the breaker.")
    attached = client.get(f"/api/records/intake/{id}/attachments/0")
    assert attached.content == b"Comment: Confirm the breaker."
    assert attached.headers["content-disposition"].startswith("attachment;")
    assert attached.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/records/intake/{id}").json()["order_id"] is None
    assert client.get(f"/api/records/intake/{id}/attachments/-1").status_code == 404
    assert client.get(f"/api/records/intake/{id}/attachments/1").status_code == 404


def test_html_only_email_is_readable_without_images_styles_or_scripts(client):
    mail = message()
    mail.set_content(
        '<html><head><style>hidden-css</style></head><body><p>Check CB-12 &amp; E-10.</p><p>Second paragraph.</p><img src="https://tracker.invalid/pixel"><script>hidden-script</script></body></html>',
        subtype="html",
    )
    id = upload(client, mail.as_bytes())
    p = client.get(f"/api/records/intake/{id}/preview").json()
    assert p["body"] == "Check CB-12 & E-10.\nSecond paragraph."
    assert "tracker" not in p["body"]
    assert "hidden-" not in p["body"]


def test_preview_handles_legacy_intake_without_stored_email_metadata(client):
    mail = message()
    mail.set_content("Original customer message.")
    id = upload(client, mail.as_bytes())
    with Session.begin() as s:
        row = s.get(RecordIntake, id)
        row.data = {k: v for k, v in row.data.items() if k != "email"}
    assert (
        client.get(f"/api/records/intake/{id}/preview").json()["body"]
        == "Original customer message."
    )


def test_preview_does_not_disclose_other_teams_email_or_attachments(client):
    mail = message()
    mail.set_content("Private customer context.")
    mail.add_attachment(
        b"private", maintype="text", subtype="plain", filename="private.txt"
    )
    id = upload(client, mail.as_bytes())
    with Session.begin() as s:
        s.get(RecordIntake, id).organization_id = str(uuid4())
    for suffix in ("preview", "attachments/0"):
        response = client.get(f"/api/records/intake/{id}/{suffix}")
        assert response.status_code == 404
        assert "Private" not in response.text
        assert "private.txt" not in response.text


def test_plain_text_preferred_and_long_content_explicitly_truncated():
    mail = message()
    mail.set_content("A" * 31000)
    mail.add_alternative("<p>Alternate version</p>", subtype="html")
    p = email_preview(mail.as_bytes())
    assert p["body"] == "A" * 30000
    assert p["truncated"] is True


def test_empty_plain_alternative_falls_back_to_readable_html():
    mail = message()
    mail.set_content("")
    mail.add_alternative("<p>The returned drawing needs review.</p>", subtype="html")
    assert (
        email_preview(mail.as_bytes())["body"] == "The returned drawing needs review."
    )


def test_attached_email_is_preserved_as_a_download(client):
    nested = message()
    nested.set_content("Original reviewer note.")
    mail = message()
    mail.set_content("Please see the attached email.")
    mail.add_attachment(nested, filename="reviewer.eml")
    id = upload(client, mail.as_bytes())
    p = client.get(f"/api/records/intake/{id}/preview").json()
    assert p["attachments"][0]["name"] == "reviewer.eml"
    assert p["attachments"][0]["size"] > 0
    response = client.get(f"/api/records/intake/{id}/attachments/0")
    assert response.status_code == 200
    assert b"Original reviewer note." in response.content


def test_missing_original_has_an_explicit_unavailable_state(client):
    id = str(uuid4())
    with Session.begin() as s:
        s.add(
            RecordIntake(
                id=id, name="missing.eml", blob="missing.eml", sha256="f" * 64, data={}
            )
        )
    p = client.get(f"/api/records/intake/{id}/preview").json()
    assert "unavailable" in p["error"]
    assert "body" not in p


def test_set_aside_retains_message_and_restores_without_importing(client):
    mail = message()
    mail.set_content("Account notification, no customer job.")
    id = upload(client, mail.as_bytes())
    headers = {"Idempotency-Key": str(uuid4())}
    response = client.post(
        f"/api/records/intake/{id}/set-aside", json={}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "set_aside"
    assert (
        client.post(
            f"/api/records/intake/{id}/set-aside", json={}, headers=headers
        ).json()
        == response.json()
    )
    assert (
        client.get(f"/api/records/intake/{id}/preview").json()["body"]
        == "Account notification, no customer job."
    )
    assert client.get(f"/api/records/intake/{id}/source").status_code == 200
    blocked = client.post(
        f"/api/records/intake/{id}/confirm",
        json={"job_number": "24-122", "revision_label": "C"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert blocked.status_code == 409
    restored = client.post(
        f"/api/records/intake/{id}/restore",
        json={},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "needs_confirmation"
    assert len(restored.json()["disposition_history"]) == 2
    assert restored.json()["order_id"] is None
    assert not client.get("/api/orders").json()["orders"]


def test_other_team_cannot_change_disposition(client):
    mail = message()
    mail.set_content("Private message.")
    id = upload(client, mail.as_bytes())
    with Session.begin() as s:
        s.get(RecordIntake, id).organization_id = str(uuid4())
    for action in ("set-aside", "restore"):
        assert (
            client.post(
                f"/api/records/intake/{id}/{action}",
                json={},
                headers={"Idempotency-Key": str(uuid4())},
            ).status_code
            == 404
        )


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://outlook.office.com/mail/inbox/id/test", True),
        ("https://outlook.office365.com/mail/inbox/id/test", True),
        ("https://outlook.office.com.attacker.test/mail/id/test", False),
        ("https://attacker.test@outlook.office.com/mail/id/test", False),
        ("https://outlook.office.com:8443/mail/id/test", False),
        ("javascript:alert(1)", False),
    ],
)
def test_outlook_link_uses_only_the_saved_microsoft_origin(client, url, allowed):
    from backend.mailbox.models import MailboxConnection, MailboxReceipt

    mail = message()
    mail.set_content("Returned switchgear package.")
    id = upload(client, mail.as_bytes())
    with Session.begin() as s:
        connection = MailboxConnection(
            microsoft_tenant_id=str(uuid4()), mailbox="pm@example.test"
        )
        s.add(connection)
        s.flush()
        s.add(
            MailboxReceipt(
                connection_id=connection.id,
                provider_message_id="source",
                intake_id=id,
                data={"web_link": url},
            )
        )
    result = client.get(f"/api/records/intake/{id}/preview").json()
    assert result["origin"] == "Outlook"
    assert result.get("outlook_url") == (url if allowed else None)
