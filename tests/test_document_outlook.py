"""File responses become native unsent drafts, without guesses or duplicate writes."""

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_company_context import ORG_B, identity
from test_order_packet import returned, run, stage  # noqa: F401 - shared fixture

from backend.autopilot.models import OutlookDraft
from backend.autopilot.service import _due
from backend.mailbox.models import MailboxConnection
from backend.project_flow.service import project_view
from backend.reply_assistant import document_outlook as drafts
from backend.storage.db import Session
from backend.storage.models import Order, Span


class NativeMailbox:
    def __init__(self, status=201):
        self.calls, self.messages, self.status = [], {}, status

    def __call__(self, snapshot):
        def handle(request):
            self.calls.append(request)
            assert "/send" not in request.url.path
            if request.method == "POST":
                assert request.url.path.endswith("/messages")
                body = json.loads(request.content)
                assert body["toRecipients"] == []
                assert "attachments" not in body
                message = {
                    "id": "native-file-draft",
                    "isDraft": True,
                    "parentFolderId": "drafts",
                    "webLink": "https://outlook.office.com/mail/deeplink/read/native-file-draft",
                    **body,
                }
                self.messages[message["id"]] = message
                return httpx.Response(
                    self.status,
                    json=message
                    if self.status < 300
                    else {"error": {"code": "ErrorAccessDenied"}},
                )
            assert request.method == "GET", "Existing human edits must never be patched"
            if "deleteditems" in request.url.path:
                return httpx.Response(200, json={"id": "deleted"})
            return httpx.Response(200, json=self.messages["native-file-draft"])

        client = httpx.Client(transport=httpx.MockTransport(handle))
        return SimpleNamespace(
            mailbox=snapshot["mailbox"],
            client=client,
            headers=lambda: {"Prefer": 'IdType="ImmutableId"'},
            close=client.close,
        )


@pytest.fixture
def packet(returned):  # noqa: F811 - pytest resolves the imported shared fixture
    prepared = stage(returned)
    with identity():
        run()
        with Session.begin() as s:
            connection = MailboxConnection(
                mailbox="pm@example.com", microsoft_tenant_id=str(uuid4())
            )
            s.add(connection)
            s.flush()
            return returned[0], prepared["draft_id"], connection.id, returned[2]


def test_file_response_saves_once_and_reopens_human_edits(packet):
    oid, rid, _, _ = packet
    graph = NativeMailbox()
    with identity():
        result = drafts.save(oid, rid, client_factory=graph)
        assert result["draft"]["status"] == "created"
        assert result["draft"]["rest_item_id"] == ""
        graph.messages["native-file-draft"]["body"]["content"] = (
            "Human correction retained."
        )
        assert (
            drafts.save(oid, rid, client_factory=graph)["draft"]["id"]
            == result["draft"]["id"]
        )
        assert (
            graph.messages["native-file-draft"]["body"]["content"]
            == "Human correction retained."
        )
        with Session() as s:
            assert drafts.metadata(s, oid)["draft"]["id"] == result["draft"]["id"]
            assert s.scalar(select(OutlookDraft)).receipt_id is None
            project = project_view(s, s.get(Order, oid), detail=True)
            assert project["status"] == "ready"
            assert "unaddressed" in project["explanation"]
            assert project["reply_preparation"]["prepared_reply"]["id"] == rid
    assert len([r for r in graph.calls if r.method == "POST"]) == 1


def test_unknown_create_outcome_does_not_retry_or_duplicate(packet):
    oid, rid, _, _ = packet
    graph = NativeMailbox(status=503)
    with identity():
        with pytest.raises(HTTPException):
            drafts.save(oid, rid, client_factory=graph)
        with pytest.raises(HTTPException):
            drafts.save(oid, rid, client_factory=graph)
        with Session() as s:
            assert s.scalar(select(OutlookDraft)).status == "manual_attention"
    assert len([r for r in graph.calls if r.method == "POST"]) == 1


def test_new_response_preserves_and_identifies_the_earlier_outlook_draft(packet):
    oid, rid, _, source = packet
    graph = NativeMailbox()
    with identity():
        original = drafts.save(oid, rid, client_factory=graph)
        with Session.begin() as s:
            s.get(Span, source).text += " New requirement to confirm."
        updated = stage((oid, None, source))
        run()
        with Session() as s:
            meta = drafts.metadata(s, oid)
            assert meta["reply_id"] == updated["draft_id"] != rid
            assert "earlier Outlook draft" in meta["draft"]["error"]
            assert project_view(s, s.get(Order, oid))["status"] == "needs_you"
        reopened = drafts.save(oid, updated["draft_id"], client_factory=graph)
        assert reopened["draft"]["id"] == original["draft"]["id"]
    assert len([r for r in graph.calls if r.method == "POST"]) == 1


def test_known_permission_rejection_can_be_retried_after_consent(packet):
    oid, rid, _, _ = packet
    graph = NativeMailbox(status=403)
    with identity():
        with pytest.raises(HTTPException):
            drafts.save(oid, rid, client_factory=graph)
        with Session() as s:
            assert s.scalar(select(OutlookDraft)).status == "permission_required"
        assert (
            _due(10) == []
        )  # File saves require an explicit action, never the reply-thread retry worker.
        graph.status = 201
        assert (
            drafts.save(oid, rid, client_factory=graph)["draft"]["status"] == "created"
        )


def test_stale_or_foreign_project_never_creates_an_outlook_draft(packet):
    oid, rid, _, source = packet
    graph = NativeMailbox()
    with identity(ORG_B), pytest.raises(HTTPException):
        drafts.save(oid, rid, client_factory=graph)
    with identity(), Session.begin() as s:
        s.get(Span, source).text += " Additional requirement changed."
    with identity(), pytest.raises(HTTPException):
        drafts.save(oid, rid, client_factory=graph)
    assert not graph.calls


def test_project_does_not_call_a_stale_file_response_ready(packet):
    oid, _, _, source = packet
    with identity(), Session.begin() as s:
        assert project_view(s, s.get(Order, oid))["status"] == "prepared"
        s.get(Span, source).text += " New customer requirement."
        s.flush()
        project = project_view(s, s.get(Order, oid), detail=True)
        assert project["status"] == "handling"
        assert project["reply_preparation"]["prepared_reply"] is None
        assert project["progress"][-1]["state"] == "working"


def test_multiple_mailboxes_require_a_choice_and_viewers_cannot_write(packet):
    oid, rid, selected, _ = packet
    graph = NativeMailbox()
    with identity(), Session.begin() as s:
        s.add(
            MailboxConnection(
                mailbox="another@example.com", microsoft_tenant_id=str(uuid4())
            )
        )
    with identity():
        with pytest.raises(HTTPException):
            drafts.save(oid, rid, client_factory=graph)
        assert not graph.calls
        assert (
            drafts.save(oid, rid, selected, client_factory=graph)["draft"]["status"]
            == "created"
        )
    with identity(role="org:viewer"), pytest.raises(HTTPException) as error:
        drafts.save(oid, rid, selected, client_factory=graph)
    assert error.value.status_code == 403
