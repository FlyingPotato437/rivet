"""Provider failure scenarios using synthetic mail only; no live Graph calls."""

import json
from datetime import timedelta
from email.message import EmailMessage

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.identity import LOCAL_ORG, Identity, identity_scope
from backend.mailbox import service
from backend.mailbox.graph import (
    GraphClient,
    GraphError,
    initial_delta_url,
    mailbox_policy,
    validate_delta_url,
)
from backend.mailbox.models import MailboxFolder, MailboxReceipt
from backend.storage.db import Session
from backend.storage.models import Document, Job, Order, RecordIntake, now

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
MAILBOX = "pm@example.com"


@pytest.fixture
def connection(monkeypatch):
    monkeypatch.delenv("RIVET_MS_CERTIFICATE_PATH", raising=False)
    monkeypatch.delenv("RIVET_MS_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("RIVET_MS_TENANT_ID", TENANT)
    monkeypatch.setenv("RIVET_MS_CLIENT_ID", "test-client")
    monkeypatch.setenv("RIVET_MS_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv(
        "RIVET_MS_MAILBOX_ALLOWLIST",
        json.dumps({LOCAL_ORG: [MAILBOX], OTHER: [MAILBOX]}),
    )
    with Session.begin() as s:
        row = service.register(s, TENANT, MAILBOX, ["inbox"])
        folder = s.scalar(
            select(MailboxFolder).where(MailboxFolder.connection_id == row.id)
        )
        return row.id, folder.id


def link(token, *, folder="inbox", next_page=False):
    base = initial_delta_url(MAILBOX, folder).split("?")[0]
    return base + ("?$skiptoken=" if next_page else "?$deltatoken=") + token


def page(ids=(), token="done", *, next_page=False):
    return {
        "value": [message(id) if isinstance(id, str) else id for id in ids],
        "@odata.nextLink" if next_page else "@odata.deltaLink": link(
            token, next_page=next_page
        ),
    }


def message(id):
    return {
        "id": id,
        "internetMessageId": f"<{id}@example.com>",
        "subject": "Job MAIL-901: returned comments, Rev C",
        "conversationId": "thread-901",
        "receivedDateTime": "2026-10-12T08:00:00Z",
        "from": {
            "emailAddress": {
                "name": "Example reviewer",
                "address": "reviewer@example.com",
            }
        },
        "hasAttachments": False,
        "isDraft": False,
    }


def eml(id="one"):
    email = EmailMessage()
    email["Message-ID"] = f"<{id}@example.com>"
    email["From"] = "Example reviewer <reviewer@example.com>"
    email["To"] = MAILBOX
    email["Subject"] = "Job MAIL-901: returned comments, Rev C"
    email.set_content("Fictional test only. Please confirm the drawing reference.")
    return email.as_bytes()


class FakeGraph:
    def __init__(self, pages=None, payload=None):
        self.pages = list(pages or [])
        self.payload = payload or eml()
        self.cursors, self.mime_ids = [], []

    def resolve_folder(self, folder_id):
        return folder_id

    def delta(self, folder_id, cursor=""):
        self.cursors.append(cursor)
        result = self.pages.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def mime(self, id):
        self.mime_ids.append(id)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def due(folder_id):
    with Session.begin() as s:
        s.get(MailboxFolder, folder_id).next_poll_at = now() - timedelta(seconds=1)


def receipts():
    with Session() as s:
        return list(s.scalars(select(MailboxReceipt)))


def test_poll_recovers_message_with_no_notification_and_uses_real_intake(connection):
    client = FakeGraph([page(["one"])])
    results = service.poll_due_mailboxes(client_factory=lambda _: client)
    assert [r["status"] for r in results] == ["current", "imported"]
    with Session() as s:
        receipt = s.scalar(select(MailboxReceipt))
        intake = s.get(RecordIntake, receipt.intake_id)
        assert intake.order_id
        assert s.get(Order, intake.order_id).number == "MAIL-901"
        assert s.scalar(select(Document)).name.endswith(".eml")
        assert s.scalar(select(Job).where(Job.kind == "parse"))
        assert receipt.data["sender"] == "reviewer@example.com"
        assert receipt.blob and receipt.processed_at
        assert (
            service.find_receipt_by_message_id(s, "<one@example.com>").id == receipt.id
        )
        assert s.get(MailboxFolder, connection[1]).last_synced_at


def test_nullable_sender_metadata_does_not_block_delta_checkpoint(connection):
    item = {**message("unknown-sender"), "from": {"emailAddress": None}}
    result = service.sync_folder(connection[1], client=FakeGraph([page([item])]))
    assert result["status"] == "current"
    assert receipts()[0].data["sender"] == ""


def test_duplicate_delta_events_and_replay_do_not_duplicate_order_or_receipt(
    connection,
):
    client = FakeGraph([page(["one", "one"], "one"), page(["one"], "two")])
    service.poll_due_mailboxes(client_factory=lambda _: client)
    due(connection[1])
    service.poll_due_mailboxes(client_factory=lambda _: client)
    with Session() as s:
        assert len(list(s.scalars(select(MailboxReceipt)))) == 1
        assert len(list(s.scalars(select(RecordIntake)))) == 1
        assert len(list(s.scalars(select(Order)))) == 1
    assert client.mime_ids == ["one"]
    assert client.cursors == ["", link("one")]


def test_every_page_is_persisted_before_checkpoint_and_budget_resumes(connection):
    client = FakeGraph([page(["one"], "p2", next_page=True), page(["two"], "end")])
    result = service.sync_folder(connection[1], client=client, max_pages=1)
    assert result["status"] == "catching_up"
    with Session() as s:
        folder = s.get(MailboxFolder, connection[1])
        assert folder.cursor == link("p2", next_page=True)
        assert folder.last_synced_at is None
        assert len(list(s.scalars(select(MailboxReceipt)))) == 1
    due(connection[1])
    assert service.sync_folder(connection[1], client=client)["status"] == "current"
    assert client.cursors[-1] == link("p2", next_page=True)
    assert len(receipts()) == 2


def test_malformed_item_rolls_back_whole_page_and_checkpoint(connection):
    client = FakeGraph([page([message("one"), {"subject": "invalid"}])])
    result = service.sync_folder(connection[1], client=client)
    assert result["status"] == "error"
    with Session() as s:
        folder = s.get(MailboxFolder, connection[1])
        assert folder.cursor == ""
        assert folder.failure_count == 1
        assert list(s.scalars(select(MailboxReceipt))) == []


def test_database_failure_before_checkpoint_replays_page(connection, monkeypatch):
    original = service.persist_page

    def crash(s, folder, data):
        original(s, folder, data)
        raise RuntimeError("Synthetic failure before commit")

    monkeypatch.setattr(service, "persist_page", crash)
    service.sync_folder(connection[1], client=FakeGraph([page(["one"])]))
    assert receipts() == []
    with Session() as s:
        assert s.get(MailboxFolder, connection[1]).cursor == ""
    monkeypatch.setattr(service, "persist_page", original)
    due(connection[1])
    assert (
        service.sync_folder(connection[1], client=FakeGraph([page(["one"])]))["status"]
        == "current"
    )
    assert len(receipts()) == 1


def test_429_retains_checkpoint_and_obeys_retry_after(connection):
    client = FakeGraph(
        [
            page(["one"], "p2", next_page=True),
            GraphError("retry", "Throttled", retry_after=777, status=429),
        ]
    )
    before = now()
    assert service.sync_folder(connection[1], client=client)["pages"] == 1
    with Session() as s:
        folder = s.get(MailboxFolder, connection[1])
        assert folder.cursor == link("p2", next_page=True)
        assert folder.next_poll_at >= before + timedelta(seconds=777)
    assert service.sync_folder(connection[1], client=client)["status"] == "not_due"


def test_expired_cursor_resync_replays_existing_ids_without_loss(connection):
    service.sync_folder(connection[1], client=FakeGraph([page(["one"], "old")]))
    due(connection[1])
    result = service.sync_folder(
        connection[1],
        client=FakeGraph([GraphError("cursor_expired", "Expired", status=410)]),
    )
    assert result["status"] == "resync_required"
    with Session() as s:
        folder = s.get(MailboxFolder, connection[1])
        assert folder.cursor == "" and folder.resync_count == 1
        assert folder.state == "resync_required"
    due(connection[1])
    service.sync_folder(connection[1], client=FakeGraph([page(["one", "two"], "new")]))
    assert {r.provider_message_id for r in receipts()} == {"one", "two"}


def test_unavailable_mime_retained_for_retry_while_later_email_is_discovered(
    connection,
):
    graph = FakeGraph(
        [page(["one", "two"])], GraphError("retry", "Service unavailable", status=503)
    )
    service.poll_due_mailboxes(client_factory=lambda _: graph)
    assert len(receipts()) == 2
    assert all(r.status == "retry" and r.attempts == 1 for r in receipts())
    with Session() as s:
        assert s.get(MailboxFolder, connection[1]).state == "current"
    row = receipts()[0]
    with Session.begin() as s:
        s.get(MailboxReceipt, row.id).next_attempt_at = now() - timedelta(seconds=1)
    assert (
        service.process_receipt(
            row.id, client=FakeGraph(payload=eml(row.provider_message_id))
        )["status"]
        == "imported"
    )


def test_unknown_order_is_durable_confirmation_not_silently_discarded(connection):
    email = EmailMessage()
    email["Subject"] = "Please review the returned switchgear drawings"
    email.set_content("No job or revision known.")
    service.poll_due_mailboxes(
        client_factory=lambda _: FakeGraph([page(["one"])], email.as_bytes())
    )
    row = receipts()[0]
    assert row.status == "needs_confirmation" and row.intake_id
    with Session() as s:
        assert s.get(RecordIntake, row.intake_id).order_id is None


def test_tombstone_before_capture_is_visible_and_move_back_requeues(connection):
    service.sync_folder(
        connection[1],
        client=FakeGraph([page([{"id": "one", "@removed": {"reason": "deleted"}}])]),
    )
    row = receipts()[0]
    assert row.status == "pending"
    service.process_receipt(
        row.id,
        client=FakeGraph(
            payload=GraphError("not_found", "Original unavailable", status=404)
        ),
    )
    assert receipts()[0].status == "removed_before_capture"
    due(connection[1])
    service.sync_folder(connection[1], client=FakeGraph([page(["one"])]))
    assert receipts()[0].status == "pending"


def test_tombstone_for_moved_message_still_captures_by_immutable_id(connection):
    service.sync_folder(
        connection[1],
        client=FakeGraph([page([{"id": "moved", "@removed": {"reason": "deleted"}}])]),
    )
    row = receipts()[0]
    graph = FakeGraph(payload=eml("moved"))
    assert service.process_receipt(row.id, client=graph)["status"] == "imported"
    assert graph.mime_ids == ["moved"]


def test_tombstone_does_not_strand_original_saved_before_crash(connection):
    from backend.ingestion.parser import persist_blob

    service.sync_folder(connection[1], client=FakeGraph([page(["one"])]))
    row = receipts()[0]
    blob, _ = persist_blob(eml(), ".eml")
    with Session.begin() as s:
        s.get(MailboxReceipt, row.id).blob = blob
    due(connection[1])
    service.sync_folder(
        connection[1],
        client=FakeGraph([page([{"id": "one", "@removed": {"reason": "deleted"}}])]),
    )
    graph = FakeGraph(
        payload=GraphError("not_found", "Original unavailable", status=404)
    )
    assert service.process_receipt(row.id, client=graph)["status"] == "imported"
    assert graph.mime_ids == []


def test_captured_original_survives_tombstone(connection):
    service.poll_due_mailboxes(client_factory=lambda _: FakeGraph([page(["one"])]))
    due(connection[1])
    service.sync_folder(
        connection[1],
        client=FakeGraph([page([{"id": "one", "@removed": {"reason": "deleted"}}])]),
    )
    assert receipts()[0].status == "imported" and receipts()[0].intake_id


def test_source_reinspection_updates_awaiting_mail_receipt_without_human_confirmation(
    connection, monkeypatch
):
    from backend.records import intake

    inspect = intake.inspect_file

    def old_inspect(name, data):
        detected, metadata = inspect(name, data)
        return {**detected, "po_numbers": ["PO-442", "REVISION-1"]}, metadata

    monkeypatch.setattr(intake, "inspect_file", old_inspect)
    service.sync_folder(connection[1], client=FakeGraph([page(["one"])]))
    row = receipts()[0]
    assert (
        service.process_receipt(row.id, client=FakeGraph(payload=eml()))["status"]
        == "needs_confirmation"
    )
    monkeypatch.setattr(intake, "inspect_file", inspect)
    with Session.begin() as s:
        receipt = s.get(MailboxReceipt, row.id)
        result = intake.reinspect_pending(s, receipt.intake_id)
        assert result["order_id"]
        assert "Order and revision confirmed by a person" != result["reason"]
        assert result["previous_detected"]["po_numbers"] == ["PO-442", "REVISION-1"]
        assert receipt.status == "imported"


def test_tenant_scope_and_provider_tenant_allowlist(connection):
    service.sync_folder(connection[1], client=FakeGraph([page(["one"])]))
    with identity_scope(Identity(OTHER, "Other PM")):
        with pytest.raises(HTTPException) as error:
            service.sync_folder(connection[1], client=FakeGraph())
        assert error.value.status_code == 404
        with Session() as s:
            assert service.find_receipt_by_message_id(s, "<one@example.com>") is None
        with Session.begin() as s:
            separate = service.register(s, TENANT, MAILBOX, ["inbox"])
            assert separate.id != connection[0]
    with pytest.raises(GraphError):
        mailbox_policy(LOCAL_ORG, MAILBOX, OTHER)
    with pytest.raises(GraphError):
        mailbox_policy(LOCAL_ORG, "stranger@example.com", TENANT)


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/v1.0/users/pm%40example.com/mailFolders/inbox/messages/delta?t=x",
        "https://graph.microsoft.com.attacker.example/v1.0/users/pm%40example.com/mailFolders/inbox/messages/delta?t=x",
        "http://graph.microsoft.com/v1.0/users/pm%40example.com/mailFolders/inbox/messages/delta?t=x",
        "https://graph.microsoft.com/v1.0/users/other%40example.com/mailFolders/inbox/messages/delta?t=x",
        "https://graph.microsoft.com/v1.0/users/pm%40example.com/mailFolders/sentitems/messages/delta?t=x",
        "https://token@graph.microsoft.com/v1.0/users/pm%40example.com/mailFolders/inbox/messages/delta?t=x",
        "https://graph.microsoft.com/v1.0/users/pm%40example.com/mailFolders/inbox/messages/delta?t=x#fragment",
    ],
)
def test_cursor_cannot_change_host_account_or_folder(url):
    with pytest.raises(GraphError):
        validate_delta_url(url, MAILBOX, "inbox")


def test_graph_immutable_header_mime_and_no_redirect_following(connection):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == "login.microsoftonline.com":
            assert b"client_secret=test-secret" in request.content
            return httpx.Response(
                200, json={"access_token": "fake-token", "expires_in": 3600}
            )
        assert request.headers["Prefer"].startswith('IdType="ImmutableId"')
        if request.url.path.endswith("/$value"):
            return httpx.Response(200, content=eml())
        return httpx.Response(200, json=page(["one"]))

    graph = GraphClient(
        LOCAL_ORG, MAILBOX, TENANT, transport=httpx.MockTransport(handle)
    )
    assert graph.delta("inbox")["value"][0]["id"] == "one"
    assert graph.mime("one") == eml()
    assert len([r for r in requests if r.url.host == "login.microsoftonline.com"]) == 1
    graph.close()


@pytest.mark.parametrize(
    "status,body,code",
    [
        (429, {}, "retry"),
        (503, {}, "retry"),
        (410, {}, "cursor_expired"),
        (400, {"error": {"code": "syncStateNotFound"}}, "cursor_expired"),
        (
            403,
            {"error": {"message": "Do not leak this provider body"}},
            "access_denied",
        ),
        (302, {}, "provider_error"),
    ],
)
def test_graph_error_handling_never_exposes_tokens_or_provider_body(
    connection, status, body, code
):
    def handle(request):
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(
            status,
            json=body,
            headers={"Retry-After": "155", "Location": "https://attacker.example"},
        )

    graph = GraphClient(
        LOCAL_ORG, MAILBOX, TENANT, transport=httpx.MockTransport(handle)
    )
    with pytest.raises(GraphError) as exc:
        graph.delta("inbox")
    assert exc.value.code == code
    assert "fake-token" not in str(exc.value) and "provider body" not in str(exc.value)
    if code == "retry":
        assert exc.value.retry_after == 155
    graph.close()
