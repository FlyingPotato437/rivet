"""Project communication state must follow provider receipts, not UI actions."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_company_context import ORG_B, identity, order
from test_reply_assistant import receipt

from backend.autopilot.models import OutlookDraft
from backend.domain.service import get
from backend.mailbox.models import MailboxReceipt
from backend.project_flow import service
from backend.project_flow.api import router
from backend.project_flow.models import ProjectSentMessage, ProjectThread
from backend.reply_assistant import automatic
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session
from backend.storage.models import Project, RecordIntake, now


def incoming(s, o, doc, *, connection=None, at=None, message_id=None):
    r = receipt(s, o.id, doc.id, message_id or f"<{uuid4()}@customer.example>")
    if connection:
        r.connection_id = connection
    r.status = "imported"
    r.data = {
        **r.data,
        "conversation_id": "project-thread",
        "received_at": (at or now()).isoformat(),
        "sender": "engineer@customer.example",
        "subject": "Returned drawing · Rev C",
    }
    get(s, RecordIntake, r.intake_id).status = "matched"
    s.flush()
    return r


class SentMailbox:
    def __init__(self, messages, next_link=None, status=200):
        self.messages, self.next_link, self.status = messages, next_link, status
        self.calls = []

    def __call__(self, org, mailbox, tenant):
        def handle(request):
            self.calls.append(request)
            assert request.method == "GET", (
                "Communication history must never send or change mail"
            )
            assert "mailFolders/sentitems/messages" in request.url.path
            payload = {"value": self.messages}
            if self.next_link:
                payload["@odata.nextLink"] = self.next_link
            return httpx.Response(self.status, json=payload)

        return SimpleNamespace(
            mailbox=mailbox,
            client=httpx.Client(transport=httpx.MockTransport(handle)),
            headers=lambda: {"Prefer": 'IdType="ImmutableId"'},
            close=lambda: None,
        )


def sent(*, at=None, recipients=None, **kw):
    return {
        "id": "sent-1",
        "internetMessageId": "<sent-1@rivet.example>",
        "conversationId": "project-thread",
        "subject": "Re: Returned drawing · Rev C",
        "sentDateTime": (at or now()).isoformat(),
        "isDraft": False,
        "toRecipients": [
            {"emailAddress": {"address": email}}
            for email in recipients or ["engineer@customer.example"]
        ],
        "bodyPreview": "Please confirm which drawing revision governs. We have attached the released drawing.",
        "webLink": "https://outlook.office.com/mail/sentitems/id/sent-1",
        **kw,
    }


def native_fixture(s, o, r, **values):
    reply = ReplyDraft(
        order_id=o.id,
        request_key=str(uuid4()),
        request_hash="a" * 64,
        evidence_hash="b" * 64,
        internet_message_id=r.internet_message_id,
    )
    s.add(reply)
    s.flush()
    return OutlookDraft(
        order_id=o.id,
        receipt_id=r.id,
        connection_id=r.connection_id,
        reply_draft_id=reply.id,
        **values,
    )


def test_outlook_communication_loop_resumes_when_customer_answers():
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        r = incoming(s, o, doc, at=now() - timedelta(minutes=5))
        oid, did, rid, cid = o.id, doc.id, r.id, r.connection_id
        assert service.project_view(s, o)["status"] == "handling"
        reply = ReplyDraft(
            order_id=o.id,
            request_key="auto-mail:project-flow",
            request_hash="a" * 64,
            evidence_hash="b" * 64,
            status="ready",
            internet_message_id=r.internet_message_id,
            body="Please confirm the governing revision.",
            subject="Drawing review",
        )
        s.add(reply)
        s.flush()
        assert service.project_view(s, o)["status"] == "prepared"
        native = OutlookDraft(
            order_id=o.id,
            connection_id=cid,
            receipt_id=rid,
            reply_draft_id=reply.id,
            status="created",
            graph_draft_id="sent-1",
        )
        s.add(native)
        s.flush()
        assert service.project_view(s, o)["status"] == "ready"
    graph = SentMailbox([sent()])
    assert service.sync(client_factory=graph)[0]["status"] == "current"
    with identity(), Session.begin() as s:
        o = get(s, type(o), oid)
        p = service.project_view(s, o)
        assert p["status"] == "waiting_customer"
        assert p["last_communication"]["direction"] == "sent"
        assert p["email_count"] == 2
        assert service.answered(s, get(s, MailboxReceipt, rid))
        assert (
            automatic.reconcile_receipt(s, get(s, MailboxReceipt, rid))["reason"]
            == "answered_in_outlook"
        )
        # A self-addressed test reply appearing in Inbox must not start a reply loop.
        copy = incoming(
            s,
            o,
            get(s, type(doc), did),
            connection=cid,
            message_id="<sent-1@rivet.example>",
        )
        assert service.is_sent_copy(s, copy)
        assert automatic.reconcile_receipt(s, copy)["reason"] == "outgoing_copy"
        assert service.project_view(s, o)["email_count"] == 2
        incoming(
            s,
            o,
            get(s, type(doc), did),
            connection=cid,
            at=now() + timedelta(seconds=1),
        )
        assert service.project_view(s, o)["status"] == "handling"
        thread = s.scalar(select(ProjectThread))
        thread.next_check_at = now() - timedelta(seconds=1)
    service.sync(client_factory=graph)
    with identity(), Session() as s:
        assert len(list(s.scalars(select(ProjectSentMessage)))) == 1
        assert service.project_view(s, get(s, type(o), oid))["email_count"] == 3


def test_project_rename_is_scoped_versioned_and_does_not_change_evidence():
    with identity(), Session.begin() as s:
        o, _, _ = order(s)
        original = get(s, Project, o.project_id).title
        result = service.rename(s, o.id, "  North campus   · switchgear  ", 0)
        assert result == {"name": "North campus · switchgear", "name_version": 1}
        assert get(s, Project, o.project_id).title == original
        assert o.version == 1
        assert service.project_view(s, o)["name"] == result["name"]
        with pytest.raises(HTTPException) as error:
            service.rename(s, o.id, "Another name", 0)
        assert error.value.status_code == 409
    with identity(ORG_B), Session.begin() as s:
        assert not service.list_projects(s)["projects"]
        with pytest.raises(HTTPException):
            service.rename(s, o.id, "Cannot rename a foreign project", 1)


@pytest.mark.parametrize(
    "message",
    [
        sent(isDraft=True),
        sent(conversationId="another-thread"),
        sent(sentDateTime="invalid"),
        sent(recipients=["internal@rivet.example"]),
    ],
)
def test_only_confirmed_customer_sends_advance_the_project(message):
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        incoming(s, o, doc, at=now() - timedelta(minutes=5))
        oid = o.id
    service.sync(client_factory=SentMailbox([message]))
    with identity(), Session() as s:
        assert service.project_view(s, get(s, type(o), oid))["status"] == "handling"
        assert not list(s.scalars(select(ProjectSentMessage)))


def test_sent_history_failure_preserves_state_and_cross_mailbox_cursor_is_rejected():
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        incoming(s, o, doc)
        oid = o.id
    graph = SentMailbox(
        [],
        next_link="https://graph.microsoft.com/v1.0/users/other@example.com/mailFolders/sentitems/messages",
    )
    assert service.sync(client_factory=graph)[0]["status"] == "unavailable"
    assert len(graph.calls) == 1
    with identity(), Session() as s:
        p = service.project_view(s, get(s, type(o), oid))
        assert p["communication_sync"] == "delayed"
        assert p["status"] != "waiting_customer"


def test_same_thread_on_two_jobs_never_merges_sent_history():
    with identity(), Session.begin() as s:
        a, doc, _ = order(s)
        first = incoming(s, a, doc)
        b, doc_b, _ = order(s)
        incoming(s, b, doc_b, connection=first.connection_id)
    graph = SentMailbox([sent()])
    assert not service.sync(client_factory=graph)
    assert not graph.calls
    with identity(), Session() as s:
        assert s.scalar(select(ProjectThread)).status == "ambiguous"
        assert not list(s.scalars(select(ProjectSentMessage)))


def test_self_addressed_original_is_not_a_customer_response():
    received = now()
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        incoming(s, o, doc, at=received)
        oid = o.id
    service.sync(client_factory=SentMailbox([sent(at=received - timedelta(seconds=1))]))
    with identity(), Session() as s:
        assert not list(s.scalars(select(ProjectSentMessage)))
        assert service.project_view(s, get(s, type(o), oid))["status"] == "handling"


def test_proven_sent_copy_does_not_ask_the_pm_to_confirm_an_order():
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        r = incoming(s, o, doc, message_id="<sent-copy@example.com>")
        r.status = "needs_confirmation"
        intake = get(s, RecordIntake, r.intake_id)
        intake.status, intake.order_id = "needs_confirmation", None
        s.add(
            ProjectSentMessage(
                order_id=o.id,
                connection_id=r.connection_id,
                provider_id="sent-copy",
                internet_message_id=r.internet_message_id,
                conversation_id="project-thread",
                sent_at=now(),
                recipients=[],
            )
        )
        s.flush()
        assert service.set_aside_sent_copy(s, r)
        assert intake.status == "set_aside"
        assert intake.data["disposition_reason"] == "outgoing_copy"
        assert intake.blob == "fixture.eml"
        assert automatic.reconcile_receipt(s, r)["reason"] == "outgoing_copy"


def test_one_sent_thread_cannot_hide_another_unanswered_thread():
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        first = incoming(s, o, doc, at=now() - timedelta(minutes=5))
        later = incoming(s, o, doc, connection=first.connection_id)
        later.data = {**later.data, "conversation_id": "second-thread"}
        s.add(
            ProjectSentMessage(
                order_id=o.id,
                connection_id=first.connection_id,
                provider_id="confirmed-response",
                conversation_id="project-thread",
                sent_at=now(),
                recipients=["engineer@customer.example"],
            )
        )
        s.flush()
        p = service.project_view(s, o, detail=True)
        assert p["status"] == "handling"
        assert p["pending_threads"] == 1
        assert {t["status"] for t in p["threads"]} == {"waiting_customer", "handling"}
        assert len({m["thread_key"] for m in p["communications"]}) == 2
        assert len(p["communications"]) == 3


def test_discarding_a_draft_is_not_a_confirmed_send():
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        r = incoming(s, o, doc)
        s.add(native_fixture(s, o, r, status="discarded"))
        s.flush()
        p = service.project_view(s, o, detail=True)
        assert p["status"] == "watching"
        assert p["last_sent_at"] is None
        assert p["threads"][0]["status"] == "watching"


def test_preserved_draft_is_not_hidden_by_a_superseded_replacement():
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        r = incoming(s, o, doc)
        s.add(
            native_fixture(
                s,
                o,
                r,
                status="created",
                graph_draft_id="preserved-native-draft",
                error="Evidence changed; draft preserved.",
                created_at=now() - timedelta(minutes=1),
            )
        )
        s.add(
            native_fixture(
                s,
                o,
                r,
                status="superseded",
                created_at=now(),
            )
        )
        s.flush()
        p = service.project_view(s, o, detail=True)
        assert p["status"] == "needs_you"
        assert p["threads"][0]["status"] == "needs_you"
        assert not p["outlook"]["native"]
        assert p["outlook"]["saved_draft_id"]
        assert get(s, OutlookDraft, p["outlook"]["saved_draft_id"]).graph_draft_id == "preserved-native-draft"


def test_project_api_names_and_history_are_tenant_scoped():
    app = FastAPI()
    app.include_router(router)
    with identity(), Session.begin() as s:
        o, _, _ = order(s)
        oid = o.id
    with identity(), TestClient(app) as client:
        assert (
            client.patch(
                f"/api/project-work/{oid}/name",
                json={"name": "Field pilot", "expected_version": 0},
            ).status_code
            == 200
        )
        result = client.get(f"/api/project-work/{oid}").json()
        assert result["name"] == "Field pilot"
        assert result["name_history"][0]["before"] == "Fictional switchgear review"
        assert result["communications"] == []
        assert (
            client.patch(
                f"/api/project-work/{oid}/name",
                json={"name": "stale", "expected_version": 0},
            ).status_code
            == 409
        )
    with identity(ORG_B), TestClient(app) as client:
        assert client.get(f"/api/project-work/{oid}").status_code == 404
        assert client.get("/api/project-work").json()["projects"] == []
