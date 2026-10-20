"""Never present an old successful poll as a live, healthy inbox connection."""

from datetime import timedelta
from uuid import uuid4

from backend.identity import Identity, identity_scope
from backend.mailbox.models import MailboxConnection, MailboxFolder, MailboxReceipt
from backend.records.delivery import adoption_status
from backend.reply_assistant.service import outlook_context
from backend.storage.db import Session
from backend.storage.models import now


def statuses():
    with Session() as s:
        reply = outlook_context(s)
    outlook = next(
        item for item in adoption_status()["integrations"] if item["id"] == "outlook"
    )
    return outlook["status"], reply["mailbox_status"], reply["mailbox_connected"]


def test_readiness_tracks_current_stale_paused_and_cross_team(client):
    assert statuses() == ("not_connected", "not_connected", False)
    with Session.begin() as s:
        connection = MailboxConnection(
            microsoft_tenant_id=str(uuid4()), mailbox="pm@example.com"
        )
        s.add(connection)
        s.flush()
        folder = MailboxFolder(connection_id=connection.id, folder_id="inbox")
        s.add(folder)
        s.flush()
        cid, fid = connection.id, folder.id
    assert statuses() == ("attention_required", "attention_required", False)
    with Session.begin() as s:
        folder = s.get(MailboxFolder, fid)
        folder.state, folder.last_synced_at = "current", now()
    assert statuses() == ("current", "current", True)
    with identity_scope(Identity(str(uuid4()), "Another team")):
        assert statuses() == ("not_connected", "not_connected", False)
    with Session.begin() as s:
        s.get(MailboxFolder, fid).last_synced_at = now() - timedelta(hours=1)
    assert statuses() == ("attention_required", "attention_required", False)
    with Session.begin() as s:
        s.get(MailboxConnection, cid).enabled = False
    assert statuses() == ("paused", "not_connected", False)


def test_capture_failure_prevents_false_healthy_status(client):
    with Session.begin() as s:
        connection = MailboxConnection(
            microsoft_tenant_id=str(uuid4()), mailbox="pm@example.com"
        )
        s.add(connection)
        s.flush()
        s.add(
            MailboxFolder(
                connection_id=connection.id,
                folder_id="inbox",
                state="current",
                last_synced_at=now(),
            )
        )
        s.add(
            MailboxReceipt(
                connection_id=connection.id,
                provider_message_id="retained-message",
                status="needs_attention",
            )
        )
    assert statuses() == ("attention_required", "attention_required", False)
