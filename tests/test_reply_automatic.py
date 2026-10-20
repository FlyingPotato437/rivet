"""Automatic preparation is durable, scoped, current, and has no mail side effects."""

from datetime import timedelta
from email.message import EmailMessage

import pytest
from sqlalchemy import select
from test_company_context import ORG_B, identity
from test_reply_assistant import fixture_order, output, receipt

from backend.agents import gateway
from backend.domain.service import get
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.reply_assistant import automatic, service
from backend.reply_assistant.models import ReplyDraft
from backend.reply_assistant.schemas import ReviewReply
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import (
    Document,
    Job,
    NoticeDelivery,
    Order,
    RecordIntake,
    Span,
)
from backend.worker import claim, process


@pytest.fixture
def mail(monkeypatch):
    monkeypatch.setattr(gateway, "configured", lambda: True)
    with identity():
        order_id, document_id, source_id = fixture_order()
        with Session.begin() as s:
            row = receipt(s, order_id, document_id)
            row.status = "imported"
            row.data = {
                **row.data,
                "web_link": "https://outlook.office.com/mail/inbox/id/fixture",
            }
            s.get(RecordIntake, row.intake_id).status = "matched"
            receipt_id = row.id
        monkeypatch.setattr(service, "model_reply", lambda snapshot: output(source_id))
    return order_id, document_id, source_id, receipt_id


def stage(mail):
    with identity(), Session.begin() as s:
        return automatic.reconcile_receipt(s, get(s, MailboxReceipt, mail[3]))


def run():
    task = claim()
    assert task and task[2] == "reply_draft"
    process(task)


def test_worker_prepares_without_browser_or_connector_configuration(mail):
    assert automatic.schedule()[0]["status"] == "queued"
    with identity(), Session() as s:
        drafts = list(s.scalars(select(ReplyDraft)))
        assert len(drafts) == 1
        assert drafts[0].context["focus_document_ids"] == [mail[1]]
    run()
    again = stage(mail)
    assert again["status"] == "ready"
    with identity(), Session() as s:
        reply = service.outlook_context(s, "<fixture@example.com>")
        assert reply["prepared_reply"]["prepared_automatically"]
        assert reply["preparation"]["status"] == "ready"
        assert len(list(s.scalars(select(ReplyDraft)))) == 1
        assert len(list(s.scalars(select(Job)))) == 1
        assert not list(s.scalars(select(NoticeDelivery)))
        order = service.order_preparation(s, get(s, Order, mail[0]))
        assert order["prepared_reply"]["id"] == drafts[0].id
        assert (
            order["outlook_url"] == "https://outlook.office.com/mail/inbox/id/fixture"
        )


def test_pending_source_waits_and_automatically_resumes(mail):
    with identity(), Session.begin() as s:
        s.get(Document, mail[1]).state = "queued"
    assert stage(mail)["status"] == "queued"
    with Session() as s:
        assert not list(s.scalars(select(ReplyDraft)))
    with identity(), Session.begin() as s:
        s.get(Document, mail[1]).state = "ready"
    assert stage(mail)["status"] == "queued"
    run()
    assert stage(mail)["status"] == "ready"


def test_new_evidence_replaces_proposal_without_overwriting_old_work(mail):
    first = stage(mail)
    run()
    with identity(), Session.begin() as s:
        old = s.get(ReplyDraft, first["draft_id"])
        old_body = old.body
        source = s.get(Span, mail[2])
        source.text += " The requested sheet is revision D."
    with identity(), Session() as s:
        context = service.outlook_context(s, "<fixture@example.com>")
        assert context["prepared_reply"] is None
        assert context["preparation"]["status"] == "queued"
    second = stage(mail)
    assert second["draft_id"] != first["draft_id"]
    with Session() as s:
        assert s.get(ReplyDraft, first["draft_id"]).body == old_body
        assert len(list(s.scalars(select(ReplyDraft)))) == 2


def test_latest_email_only_and_sweep_limit(mail):
    with identity(), Session.begin() as s:
        older = s.get(MailboxReceipt, mail[3])
        older.data = {**older.data, "conversation_id": "same-thread"}
        newer = receipt(s, mail[0], mail[1], "<newest@example.com>")
        newer.status = "imported"
        newer.data = {**newer.data, "conversation_id": "same-thread"}
        newer_id = newer.id
    states = automatic.schedule(limit=1)
    assert len(states) == 1
    with Session() as s:
        drafts = list(s.scalars(select(ReplyDraft)))
        assert len(drafts) == 1
        assert drafts[0].internet_message_id == "<newest@example.com>"
        assert (
            s.get(MailboxReceipt, newer_id).data["automatic_reply"]["draft_id"]
            == drafts[0].id
        )
        assert "automatic_reply" not in s.get(MailboxReceipt, mail[3]).data


@pytest.mark.parametrize("state", ["not_order_related", "set_aside"])
def test_excluded_intake_does_not_generate_even_if_receipt_status_lags(mail, state):
    with identity(), Session.begin() as s:
        item = s.get(MailboxReceipt, mail[3])
        s.get(RecordIntake, item.intake_id).status = state
    assert stage(mail)["status"] == "not_needed"
    with Session() as s:
        assert not list(s.scalars(select(ReplyDraft)))


def test_disabled_mailbox_and_unmatched_email_do_not_generate(mail):
    with identity(), Session.begin() as s:
        item = s.get(MailboxReceipt, mail[3])
        s.get(MailboxConnection, item.connection_id).enabled = False
    assert automatic.schedule() == []
    assert stage(mail)["status"] == "not_needed"
    with identity(), Session.begin() as s:
        item = s.get(MailboxReceipt, mail[3])
        s.get(MailboxConnection, item.connection_id).enabled = True
        s.get(RecordIntake, item.intake_id).order_id = None
    assert automatic.schedule() == []
    assert stage(mail)["status"] == "not_needed"


def test_acknowledgements_are_recorded_without_generating_a_reply(mail):
    message = EmailMessage()
    message["Subject"] = "Re: returned drawings"
    message.set_content(
        "Thanks, received.\n\nOn Monday Alex wrote:\nPlease review E-10."
    )
    (db.BLOBS / "fixture.eml").write_bytes(message.as_bytes())
    assert stage(mail)["status"] == "not_needed"
    with Session() as s:
        assert not list(s.scalars(select(ReplyDraft)))


def test_failed_preparation_is_visible_and_not_retried_endlessly(mail, monkeypatch):
    monkeypatch.setattr(
        service, "model_reply", lambda snapshot: {"invalid": "response"}
    )
    first = stage(mail)
    run()
    assert stage(mail)["status"] == "failed"
    with identity(), Session() as s:
        context = service.outlook_context(s, "<fixture@example.com>")
        assert context["prepared_reply"] is None
        assert context["preparation"]["status"] == "failed"
        assert context["preparation"]["draft_id"] == first["draft_id"]
        assert len(list(s.scalars(select(ReplyDraft)))) == 1


def test_rejected_reply_is_not_recreated(mail):
    first = stage(mail)
    run()
    with identity(), Session.begin() as s:
        row = s.get(ReplyDraft, first["draft_id"])
        service.review(
            s,
            row.id,
            ReviewReply(decision="rejected", expected_evidence_hash=row.evidence_hash),
        )
    assert stage(mail)["status"] == "not_needed"
    with Session() as s:
        assert len(list(s.scalars(select(ReplyDraft)))) == 1


def test_tenant_cannot_read_other_order_preparation(mail):
    from fastapi import HTTPException

    with identity(ORG_B), Session() as s, pytest.raises(HTTPException) as exc:
        service.order_preparation(s, get(s, Order, mail[0]))
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://outlook.office.com.evil.test/id",
        "https://attacker@outlook.office.com/id",
        "http://outlook.office.com/id",
    ],
)
def test_only_real_secure_outlook_links_are_exposed(mail, url):
    with identity(), Session() as s:
        row = s.get(MailboxReceipt, mail[3])
        row.data = {**row.data, "web_link": url}
        assert service.outlook_url(row) is None


def test_approved_reply_and_reviewed_guidance_are_preserved(mail):
    first = stage(mail)
    run()
    with identity(), Session.begin() as s:
        row = s.get(ReplyDraft, first["draft_id"])
        reviewed = service.review(
            s,
            row.id,
            ReviewReply(
                decision="approved",
                body=row.body + " Thank you.",
                use_as_example=True,
                expected_evidence_hash=row.evidence_hash,
            ),
        )
    assert stage(mail)["status"] == "reviewed"
    with identity(), Session() as s:
        result = service.outlook_context(s, "<fixture@example.com>")
        assert result["prepared_reply"]["body"] == reviewed["body"]
        assert len(list(s.scalars(select(ReplyDraft)))) == 1


def test_stale_draft_poll_does_not_claim_ready(mail):
    first = stage(mail)
    run()
    with identity(), Session.begin() as s:
        s.get(Span, mail[2]).text += " New revision information."
    with identity(), Session() as s:
        saved = s.get(ReplyDraft, first["draft_id"])
        assert saved.status == "ready"
        assert service.current_view(s, saved)["status"] == "needs_context"


def test_independent_customer_threads_on_one_order_are_both_prepared(mail):
    with identity(), Session.begin() as s:
        row = receipt(s, mail[0], mail[1], "<another-thread@example.com>")
        row.status = "imported"
        row.data = {**row.data, "conversation_id": "other-thread"}
    assert len(automatic.schedule()) == 2
    with Session() as s:
        assert {d.internet_message_id for d in s.scalars(select(ReplyDraft))} == {
            "<fixture@example.com>",
            "<another-thread@example.com>",
        }


def test_later_acknowledgement_does_not_hide_pending_question(mail):
    message = EmailMessage()
    message.set_content("Thanks, received.")
    (db.BLOBS / "ack.eml").write_bytes(message.as_bytes())
    with identity(), Session.begin() as s:
        older = s.get(MailboxReceipt, mail[3])
        older.data = {**older.data, "conversation_id": "same-thread"}
        ack = receipt(s, mail[0], mail[1], "<ack@example.com>")
        ack.status = "imported"
        ack.data = {**ack.data, "conversation_id": "same-thread"}
        s.get(RecordIntake, ack.intake_id).blob = "ack.eml"
    assert automatic.schedule()[0]["status"] == "not_needed"
    assert automatic.schedule()[0]["status"] == "queued"
    with Session() as s:
        drafts = list(s.scalars(select(ReplyDraft)))
        assert (
            len(drafts) == 1
            and drafts[0].internet_message_id == "<fixture@example.com>"
        )


def test_historical_backfill_does_not_displace_latest_received_message(mail):
    from backend.storage.models import now

    with identity(), Session.begin() as s:
        current = s.get(MailboxReceipt, mail[3])
        current.data = {
            **current.data,
            "conversation_id": "same-thread",
            "received_at": now().isoformat(),
        }
        backfill = receipt(s, mail[0], mail[1], "<backfill@example.com>")
        backfill.status = "imported"
        backfill.data = {
            **backfill.data,
            "conversation_id": "same-thread",
            "received_at": (now() - timedelta(days=30)).isoformat(),
        }
    assert automatic.schedule()[0]["status"] == "queued"
    with Session() as s:
        assert (
            s.scalar(select(ReplyDraft)).internet_message_id == "<fixture@example.com>"
        )
    with identity(), Session() as s:
        result = service.outlook_context(s, "<backfill@example.com>")
        assert result["preparation"]["status"] == "not_needed"


def test_old_unanswered_thread_is_context_not_automatic_outgoing_work(mail):
    from backend.storage.models import now

    with identity(), Session.begin() as s:
        row = s.get(MailboxReceipt, mail[3])
        row.data = {**row.data, "received_at": (now() - timedelta(days=30)).isoformat()}
    assert automatic.schedule()[0]["status"] == "not_needed"
    with Session() as s:
        assert not list(s.scalars(select(ReplyDraft)))


def test_bad_receipt_does_not_starve_other_orders(mail, monkeypatch):
    with identity():
        oid, docid, _ = fixture_order()
        with Session.begin() as s:
            next_receipt = receipt(s, oid, docid, "<healthy@example.com>")
            next_receipt.status = "imported"
    original = automatic.reconcile_receipt

    def broken(s, row):
        if row.id == mail[3]:
            raise ValueError("private diagnostic data that must not reach the UI")
        return original(s, row)

    monkeypatch.setattr(automatic, "reconcile_receipt", broken)
    results = automatic.schedule(limit=2)
    assert {x["status"] for x in results} == {"blocked", "queued"}
    assert all("private diagnostic" not in x["detail"] for x in results)
    with Session() as s:
        assert (
            s.scalar(select(ReplyDraft)).internet_message_id == "<healthy@example.com>"
        )
        assert s.get(MailboxReceipt, mail[3]).data["automatic_reply"]["checked_at"]
