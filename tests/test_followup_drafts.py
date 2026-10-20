from datetime import timedelta

from sqlalchemy import select
from test_company_context import identity, order
from test_project_flow import incoming

from backend.autopilot import outlook
from backend.project_flow import followups
from backend.project_flow.models import ProjectSentMessage
from backend.storage.db import Session
from backend.storage.models import now


def test_waiting_timer_creates_one_unsent_outlook_followup_and_preserves_quote(
    monkeypatch,
):
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        r = incoming(s, o, doc, at=now() - timedelta(days=7))
        sent = ProjectSentMessage(
            order_id=o.id,
            connection_id=r.connection_id,
            provider_id="sent-confirmed",
            conversation_id="project-thread",
            subject="Drawing review",
            sent_at=now() - timedelta(days=6),
            recipients=["engineer@customer.example"],
        )
        s.add(sent)
        s.flush()
        followups.schedule(s, o)
        s.flush()
        row = s.scalar(select(followups.FollowUp))
        id = row.id
    writes = []
    monkeypatch.setattr(outlook, "existing_reply", lambda *args: None)
    monkeypatch.setattr(
        outlook,
        "create_reply",
        lambda *args: {
            "id": "followup-draft",
            "body": {
                "contentType": "html",
                "content": "<blockquote>Prior customer request</blockquote>",
            },
            "webLink": "https://outlook.office.com/mail/drafts/id/followup-draft",
        },
    )
    monkeypatch.setattr(
        outlook, "set_body", lambda graph, id, body: writes.append((id, body))
    )
    with identity():
        followups.prepare(id, graph=object())
        followups.prepare(id, graph=object())
    with identity(), Session() as s:
        assert s.get(followups.FollowUp, id).status == "drafted"
    assert len(writes) == 1
    assert "Following up" in writes[0][1] and "Prior customer request" in writes[0][1]


def test_new_customer_mail_cancels_a_waiting_nudge_before_any_draft(monkeypatch):
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        r = incoming(s, o, doc, at=now() - timedelta(days=7))
        s.add(
            ProjectSentMessage(
                order_id=o.id,
                connection_id=r.connection_id,
                provider_id="sent-confirmed",
                conversation_id="project-thread",
                subject="Review",
                sent_at=now() - timedelta(days=6),
                recipients=[],
            )
        )
        s.flush()
        followups.schedule(s, o)
        s.flush()
        id = s.scalar(select(followups.FollowUp.id))
        incoming(s, o, doc, connection=r.connection_id, at=now())
    monkeypatch.setattr(
        outlook,
        "create_reply",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("No nudge after a customer reply")
        ),
    )
    with identity():
        followups.prepare(id, graph=object())
    with identity(), Session() as s:
        assert s.get(followups.FollowUp, id).status == "superseded"
