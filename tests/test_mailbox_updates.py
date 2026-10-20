"""Internal PM notifications are derived from real saved work, never customer mail."""

import json
from datetime import timedelta
from email.message import EmailMessage

import httpx
import pytest
from sqlalchemy import select
from test_mailbox_sync import (  # noqa: F401 - fixture
    MAILBOX,
    FakeGraph,
    connection,
    message,
    page,
)

from backend.identity import LOCAL_ORG, Identity, identity_scope
from backend.mailbox import service, updates
from backend.mailbox.graph import GraphError
from backend.mailbox.models import MailboxConnection, MailboxReceipt, MailboxUpdate
from backend.records import agent_service
from backend.storage.db import Session
from backend.storage.models import Document, Order, RecordAgentRun, RecordIntake, now
from backend.worker import claim, process


def enable_updates(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RIVET_MAILBOX_UPDATES_ENABLED", "true")
    monkeypatch.setenv("RIVET_WORKSPACE_URL", "https://workspace.example.com")
    monkeypatch.setenv("RIVET_EMAIL_FROM", "Rivet <updates@rivet.example>")
    monkeypatch.setenv("RESEND_API_KEY", "test-resend-secret")


@pytest.fixture
def prepared(connection, monkeypatch):  # noqa: F811 - imported pytest fixture
    enable_updates(monkeypatch)
    service.poll_due_mailboxes(client_factory=lambda _: FakeGraph([page(["one"])]))
    task = claim()
    assert task and task[2] == "parse"
    process(task)
    with Session.begin() as s:
        order = s.scalar(select(Order))
        run = agent_service.create_run(s, order)
        run_id = run.id
    agent_service.process_run(run_id, lambda: True)
    with Session() as s:
        run = s.get(RecordAgentRun, run_id)
        assert run.status in updates.TERMINAL
        return s.scalar(select(MailboxReceipt)).id, run.id, order.id


@pytest.fixture
def unmatched(connection, monkeypatch):  # noqa: F811 - imported pytest fixture
    enable_updates(monkeypatch)
    email = EmailMessage()
    email["Message-ID"] = "<unknown@example.com>"
    email["Subject"] = "Returned comments requiring review"
    email["From"] = "reviewer@example.com"
    email.set_content(
        "Please see the attached comments. The job and revision are not stated."
    )
    provider_message = {**message("unknown"), "subject": email["Subject"]}
    service.poll_due_mailboxes(
        client_factory=lambda _: FakeGraph([page([provider_message])], email.as_bytes())
    )
    with Session() as s:
        row = s.scalar(select(MailboxReceipt))
        assert row.status == "needs_confirmation"
        assert s.get(RecordIntake, row.intake_id).order_id is None
        return row.id


def enqueue(receipt_id):
    with Session.begin() as s:
        row = updates.enqueue_for_receipt(s, receipt_id)
        return row.id if row else None


class FakeSender:
    def __init__(self, failures=0):
        self.calls = []
        self.failures = failures

    def send(self, payload, key):
        self.calls.append((payload, key))
        if self.failures:
            self.failures -= 1
            raise GraphError(
                "retry", "Synthetic acknowledgement failure", retry_after=75
            )
        return "provider-acceptance-one"


def make_due(id):
    with Session.begin() as s:
        s.get(MailboxUpdate, id).next_attempt_at = now() - timedelta(seconds=1)


def test_disabled_is_inert_and_never_calls_provider(prepared, monkeypatch):
    monkeypatch.delenv("RIVET_MAILBOX_UPDATES_ENABLED")
    assert enqueue(prepared[0]) is None
    sender = FakeSender()
    assert updates.schedule_and_deliver_updates(sender=sender) == []
    assert sender.calls == []


def test_unknown_mail_produces_one_focused_internal_confirmation_notice(unmatched):
    id = enqueue(unmatched)
    assert id == enqueue(unmatched)
    with Session() as s:
        row = s.get(MailboxUpdate, id)
        intake_id = s.get(MailboxReceipt, unmatched).intake_id
        assert row.order_id is None and row.run_id is None
        assert updates.update_view(row)["kind"] == "needs_confirmation"
        assert f"/#inbox?intake={intake_id}" in row.payload["text"]
        assert "Returned comments requiring review" in row.payload["text"]
        assert "Which order does this email belong to?" in row.payload["text"]
        assert "Please see the attached comments." in row.payload["text"]
        assert "has not attached it to an order" in row.payload["text"]
        assert row.payload["to"] == [MAILBOX]
    sender = FakeSender()
    assert updates.deliver_update(id, sender=sender)["status"] == "accepted_by_provider"
    assert updates.schedule_and_deliver_updates(sender=sender) == []
    assert len(sender.calls) == 1


def test_confirmation_notice_superseded_when_pm_resolves_before_send(unmatched):
    from backend.records.intake import ConfirmIntake, confirm

    id = enqueue(unmatched)
    with Session() as s:
        intake_id = s.get(MailboxReceipt, unmatched).intake_id
    result = confirm(
        intake_id,
        ConfirmIntake(job_number="CONFIRM-204", revision_label="C"),
        "confirm-unknown-mail",
    )
    assert result["order_id"]
    sender = FakeSender()
    assert updates.deliver_update(id, sender=sender)["status"] == "superseded"
    assert sender.calls == []
    # Completion waits for this newly attached document and its agent run.
    assert enqueue(unmatched) is None


def test_confirmation_notice_retry_preserves_payload_and_safe_window(unmatched):
    id = enqueue(unmatched)
    sender = FakeSender(failures=1)
    assert updates.deliver_update(id, sender=sender)["status"] == "retry"
    make_due(id)
    assert updates.deliver_update(id, sender=sender)["status"] == "accepted_by_provider"
    assert sender.calls[0] == sender.calls[1]


def test_confirmation_requires_opt_in_and_provider_setup(unmatched, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY")
    assert enqueue(unmatched) is None
    monkeypatch.setenv("RESEND_API_KEY", "test-secret")
    monkeypatch.setenv("RIVET_MAILBOX_UPDATES_ENABLED", "false")
    assert enqueue(unmatched) is None


def test_actual_saved_work_is_frozen_and_only_accepted_by_provider(prepared):
    id = enqueue(prepared[0])
    assert id == enqueue(prepared[0])
    sender = FakeSender()
    assert updates.deliver_update(id, sender=sender)["status"] == "accepted_by_provider"
    assert updates.deliver_update(id, sender=sender)["status"] == "accepted_by_provider"
    assert len(sender.calls) == 1
    payload, key = sender.calls[0]
    assert payload["to"] == [MAILBOX]
    assert "Internal PM update" in payload["text"]
    assert "https://workspace.example.com/#order/" in payload["text"]
    assert "Review and send this draft" not in payload["text"]
    assert key == "rivet-mailbox-update-" + id
    with Session() as s:
        row = s.get(MailboxUpdate, id)
        assert row.provider_id == "provider-acceptance-one"
        assert row.accepted_at and row.attempts == 1
        assert updates.update_view(row)["delivery_confirmed"] is False
        assert len(list(s.scalars(select(MailboxUpdate)))) == 1


def test_no_update_until_all_documents_and_automatic_work_are_finished(prepared):
    with Session.begin() as s:
        doc = s.scalar(select(Document))
        doc.state = "queued"
    assert enqueue(prepared[0]) is None
    with Session.begin() as s:
        doc = s.scalar(select(Document))
        doc.state = "ready"
        run = s.get(RecordAgentRun, prepared[1])
        run.status = "running"
    assert enqueue(prepared[0]) is None


def test_failed_work_is_an_honest_attention_update(prepared):
    with Session.begin() as s:
        run = s.get(RecordAgentRun, prepared[1])
        run.status = "failed"
        run.result = {"error": "Synthetic processing failure"}
    id = enqueue(prepared[0])
    with Session() as s:
        row = s.get(MailboxUpdate, id)
        assert "preparation needs attention" in row.payload["text"]
        assert "stopped before finishing" in row.payload["text"]
        assert row.run_status == "failed"


def test_retry_preserves_exact_payload_and_key_within_window(prepared):
    id = enqueue(prepared[0])
    sender = FakeSender(failures=1)
    before = now()
    assert updates.deliver_update(id, sender=sender)["status"] == "retry"
    with Session() as s:
        assert s.get(MailboxUpdate, id).next_attempt_at >= before + timedelta(
            seconds=75
        )
    make_due(id)
    assert updates.deliver_update(id, sender=sender)["status"] == "accepted_by_provider"
    assert sender.calls[0] == sender.calls[1]


def test_worker_restart_after_uncertain_send_reuses_key(prepared):
    id = enqueue(prepared[0])
    with Session.begin() as s:
        row = s.get(MailboxUpdate, id)
        row.status = "sending"
        row.first_attempt_at = now() - timedelta(minutes=2)
        row.lease_until = now() - timedelta(seconds=1)
    sender = FakeSender()
    assert updates.deliver_update(id, sender=sender)["status"] == "accepted_by_provider"
    assert sender.calls[0][1] == "rivet-mailbox-update-" + id


def test_expired_provider_window_requires_manual_review_not_resend(prepared):
    id = enqueue(prepared[0])
    with Session.begin() as s:
        row = s.get(MailboxUpdate, id)
        row.first_attempt_at = now() - timedelta(hours=24)
        row.status = "retry"
    sender = FakeSender()
    assert updates.deliver_update(id, sender=sender)["status"] == "needs_attention"
    assert sender.calls == []


def test_changed_server_allowlist_cannot_redirect_frozen_update(prepared, monkeypatch):
    id = enqueue(prepared[0])
    monkeypatch.setenv(
        "RIVET_MS_MAILBOX_ALLOWLIST",
        json.dumps({LOCAL_ORG: {MAILBOX: {"update_recipient": "new-pm@example.com"}}}),
    )
    sender = FakeSender()
    assert updates.deliver_update(id, sender=sender)["status"] == "needs_attention"
    assert sender.calls == []


def test_changed_work_supersedes_unsent_update(prepared):
    id = enqueue(prepared[0])
    with Session.begin() as s:
        s.get(RecordAgentRun, prepared[1]).status = "waiting_for_input"
    sender = FakeSender()
    assert updates.deliver_update(id, sender=sender)["status"] == "superseded"
    assert sender.calls == []


def test_cross_tenant_update_cannot_be_sent(prepared):
    from fastapi import HTTPException

    id = enqueue(prepared[0])
    with identity_scope(Identity("other", "Unrelated user")):
        with pytest.raises(HTTPException) as exc:
            updates.deliver_update(id, sender=FakeSender())
        assert exc.value.status_code == 404


def test_scheduler_reconciles_without_duplicate_notifications(prepared):
    sender = FakeSender()
    assert (
        updates.schedule_and_deliver_updates(sender=sender)[0]["status"]
        == "accepted_by_provider"
    )
    assert updates.schedule_and_deliver_updates(sender=sender) == []
    with Session.begin() as s:
        s.get(MailboxReceipt, prepared[0]).update_check_at = now() - timedelta(
            seconds=1
        )
    assert updates.schedule_and_deliver_updates(sender=sender) == []
    assert len(sender.calls) == 1


def test_paused_outbox_does_not_starve_active_connection(prepared, monkeypatch):
    from test_mailbox_sync import TENANT

    paused_id = enqueue(prepared[0])
    second_mailbox = "other-pm@example.com"
    monkeypatch.setenv(
        "RIVET_MS_MAILBOX_ALLOWLIST",
        json.dumps({LOCAL_ORG: [MAILBOX, second_mailbox]}),
    )
    with Session.begin() as s:
        original = s.get(MailboxReceipt, prepared[0])
        s.get(MailboxConnection, original.connection_id).enabled = False
        connection = service.register(s, TENANT, second_mailbox, ["inbox"])
        receipt = MailboxReceipt(
            connection_id=connection.id,
            provider_message_id="second-mailbox-message",
            intake_id=original.intake_id,
            status="imported",
            data={},
        )
        s.add(receipt)
        s.flush()
        receipt_id = receipt.id
    active_id = enqueue(receipt_id)
    sender = FakeSender()
    assert (
        updates.schedule_and_deliver_updates(limit=1, sender=sender)[0]["status"]
        == "accepted_by_provider"
    )
    with Session() as s:
        assert s.get(MailboxUpdate, paused_id).status == "queued"
        assert s.get(MailboxUpdate, active_id).status == "accepted_by_provider"
    assert sender.calls[0][0]["to"] == [second_mailbox]


def test_one_update_preparation_failure_does_not_starve_other_receipts(
    prepared, monkeypatch
):
    original_enqueue = updates.enqueue_for_receipt
    with Session.begin() as s:
        original = s.get(MailboxReceipt, prepared[0])
        second = MailboxReceipt(
            connection_id=original.connection_id,
            provider_message_id="independent-update",
            intake_id=original.intake_id,
            status="imported",
            data={},
        )
        s.add(second)

    def fail_one(s, receipt_id):
        if receipt_id == prepared[0]:
            raise RuntimeError("Synthetic private source contents must not be exposed")
        return original_enqueue(s, receipt_id)

    monkeypatch.setattr(updates, "enqueue_for_receipt", fail_one)
    sender = FakeSender()
    result = updates.schedule_and_deliver_updates(sender=sender)
    assert result[0]["status"] == "accepted_by_provider"
    with Session() as s:
        failed = s.get(MailboxReceipt, prepared[0])
        assert failed.update_check_at > now()
        assert "saved email is retained" in failed.data["update_preparation_error"]
        assert "private source" not in failed.data["update_preparation_error"]


def test_one_delivery_validation_failure_does_not_block_next_update(
    prepared, monkeypatch
):
    first_id = enqueue(prepared[0])
    with Session.begin() as s:
        first = s.get(MailboxUpdate, first_id)
        second = MailboxUpdate(
            connection_id=first.connection_id,
            receipt_id=first.receipt_id,
            order_id=first.order_id,
            run_id=first.run_id,
            input_hash=first.input_hash,
            run_status=first.run_status,
            dedupe_key="synthetic-independent-outbox-row",
            payload=first.payload,
        )
        s.add(second)
        s.flush()
        second_id = second.id
    original_delivery = updates.deliver_update

    def fail_one(id, *, sender=None):
        if id == first_id:
            raise ValueError("Synthetic stale-record validation failure")
        return original_delivery(id, sender=sender)

    monkeypatch.setattr(updates, "deliver_update", fail_one)
    sender = FakeSender()
    result = updates.schedule_and_deliver_updates(sender=sender)
    assert [row["status"] for row in result] == [
        "needs_attention",
        "accepted_by_provider",
    ]
    with Session() as s:
        assert s.get(MailboxUpdate, first_id).status == "needs_attention"
        assert s.get(MailboxUpdate, second_id).status == "accepted_by_provider"
    assert len(sender.calls) == 1


def test_several_receipts_in_one_automatic_pass_produce_one_pm_update(prepared):
    first = enqueue(prepared[0])
    with Session.begin() as s:
        original = s.get(MailboxReceipt, prepared[0])
        second = MailboxReceipt(
            connection_id=original.connection_id,
            provider_message_id="second-message",
            internet_message_id="<second@example.com>",
            intake_id=original.intake_id,
            processed_at=now(),
            status="imported",
            data={},
        )
        s.add(second)
        s.flush()
        second_id = second.id
    assert enqueue(second_id) == first
    with Session() as s:
        assert len(list(s.scalars(select(MailboxUpdate)))) == 1
        assert s.get(MailboxReceipt, second_id).data["update_run_id"] == prepared[1]


def test_internal_update_returning_to_monitored_mailbox_does_not_loop(prepared):
    id = enqueue(prepared[0])
    with Session.begin() as s:
        original = s.get(MailboxReceipt, prepared[0])
        payload = s.get(MailboxUpdate, id).payload
        own_receipt = MailboxReceipt(
            connection_id=original.connection_id,
            provider_message_id="our-update",
            data={},
        )
        s.add(own_receipt)
        s.flush()
        receipt_id = own_receipt.id
    email = EmailMessage()
    email["From"], email["To"], email["Subject"] = (
        payload["from"],
        MAILBOX,
        payload["subject"],
    )
    for name, value in payload["headers"].items():
        email[name] = value
    email.set_content(payload["text"])
    result = service.process_receipt(
        receipt_id, client=FakeGraph(payload=email.as_bytes())
    )
    assert result["status"] == "excluded_internal_update"
    with Session() as s:
        row = s.get(MailboxReceipt, receipt_id)
        assert row.blob and row.processed_at and not row.intake_id
        assert row.data["internal_update_id"] == id


def test_company_guidance_changes_block_new_and_unsent_updates(prepared):
    from backend.company_context.schemas import RuleWrite
    from backend.company_context.service import approve_rule, create_rule

    id = enqueue(prepared[0])
    with Session.begin() as s:
        rule = create_rule(
            s,
            RuleWrite(
                key="pm-drafting",
                title="Review protocol",
                text="Always request the exact drawing revision before responding to customer comments.",
                order_id=prepared[2],
                provenance_note="Internal pilot review instructions.",
            ),
        )
        approve_rule(s, rule["id"], "Administrator approved the changed instructions.")
    assert enqueue(prepared[0]) is None
    sender = FakeSender()
    assert updates.deliver_update(id, sender=sender)["status"] == "superseded"
    assert sender.calls == []


def test_resend_transport_uses_frozen_recipient_and_provider_idempotency(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "fake-token")
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url == "https://api.resend.com/emails"
        assert request.headers["Idempotency-Key"] == "key-one"
        assert json.loads(request.content)["to"] == [MAILBOX]
        return httpx.Response(200, json={"id": "accepted-one"})

    sender = updates.ResendUpdates(transport=httpx.MockTransport(handle))
    assert (
        sender.send({"to": [MAILBOX], "text": "Internal test"}, "key-one")
        == "accepted-one"
    )
    sender.close()
    assert len(calls) == 1


def test_set_aside_mail_does_not_generate_another_confirmation_update(unmatched):
    with Session.begin() as s:
        receipt = s.get(MailboxReceipt, unmatched)
        intake = s.get(RecordIntake, receipt.intake_id)
        intake.status = "set_aside"
    with Session.begin() as s:
        assert updates.enqueue_for_receipt(s, unmatched) is None


def test_routine_mail_is_captured_without_review_or_notification(
    connection,  # noqa: F811 - imported pytest fixture
    monkeypatch,
):
    enable_updates(monkeypatch)
    email = EmailMessage()
    email["Subject"] = "Your Microsoft verification code"
    email.set_content("Please confirm sign-in with code 123456.")
    provider_message = {**message("routine"), "subject": email["Subject"]}
    service.poll_due_mailboxes(
        client_factory=lambda _: FakeGraph([page([provider_message])], email.as_bytes())
    )
    with Session() as s:
        receipt = s.scalar(select(MailboxReceipt))
        assert receipt.status == "not_order_related"
        assert receipt.blob and receipt.intake_id
        assert s.get(RecordIntake, receipt.intake_id).order_id is None
    sender = FakeSender()
    assert updates.schedule_and_deliver_updates(sender=sender) == []
    assert sender.calls == []


def test_filtering_before_delivery_cancels_old_confirmation_without_sending(unmatched):
    id = enqueue(unmatched)
    with Session.begin() as s:
        receipt = s.get(MailboxReceipt, unmatched)
        s.get(RecordIntake, receipt.intake_id).status = "not_order_related"
    sender = FakeSender()
    assert updates.deliver_update(id, sender=sender)["status"] == "superseded"
    assert sender.calls == []


@pytest.fixture
def prepared_reply(prepared):
    """A real saved/current reply snapshot without an LLM call or mail side effect."""
    from backend.domain.service import digest
    from backend.reply_assistant import service as replies
    from backend.reply_assistant.models import ReplyDraft

    with Session.begin() as s:
        receipt = s.get(MailboxReceipt, prepared[0])
        receipt.data = {
            **receipt.data,
            "received_at": now().isoformat(),
            "web_link": "https://outlook.office.com/mail/inbox/id/synthetic-mail",
        }
        order = s.get(Order, prepared[2])
        _, _, _, current_hash = replies.context(
            s, order, "Prepare the captured reply", []
        )
        row = ReplyDraft(
            order_id=order.id,
            internet_message_id=receipt.internet_message_id,
            request_key="auto-mail:reply-update-fixture",
            request_hash=digest([receipt.id]),
            status="ready",
            evidence_hash=current_hash,
            subject="Reply to customer drawing question",
            body="Private complete customer reply must stay inside Outlook and Rivet.",
            original_body="Private complete customer reply must stay inside Outlook and Rivet.",
            context={
                "instruction": "Prepare the captured reply",
                "focus_document_ids": [],
                "recommended_attachment_ids": ["released-drawing"],
            },
            source_refs=[
                {"id": "recorded-source", "label": "Drawing E-10", "url": "#source"}
            ],
            unresolved=["Private customer question"],
            coverage={"documents_read": 1},
        )
        s.add(row)
        s.flush()
        return prepared[0], row.id, order.id


def test_ready_reply_notifies_once_with_links_and_counts_but_without_private_body(
    prepared_reply,
):
    identifier = enqueue(prepared_reply[0])
    assert identifier and identifier == enqueue(prepared_reply[0])
    with Session() as s:
        row = s.get(MailboxUpdate, identifier)
        assert row.run_id is None
        assert row.run_status == updates.update_view(row)["kind"] == "reply_ready"
        assert row.payload["to"] == [MAILBOX]
        assert "Reply ready" in row.payload["subject"]
        assert "1 source reference" in row.payload["text"]
        assert "1 open point" in row.payload["text"]
        assert "1 released supporting file" in row.payload["text"]
        assert (
            "https://outlook.office.com/mail/inbox/id/synthetic-mail"
            in row.payload["text"]
        )
        assert (
            f"https://workspace.example.com/#order/{prepared_reply[2]}"
            in row.payload["text"]
        )
        assert "Private complete" not in str(row.payload)
        assert "Private customer question" not in str(row.payload)
        assert (
            "nothing was inserted into Outlook or sent to the customer"
            in row.payload["text"]
        )
    sender = FakeSender()
    assert (
        updates.deliver_update(identifier, sender=sender)["status"]
        == "accepted_by_provider"
    )
    assert (
        updates.deliver_update(identifier, sender=sender)["status"]
        == "accepted_by_provider"
    )
    assert len(sender.calls) == 1


def test_ready_reply_remains_default_off_and_internal_only(prepared_reply, monkeypatch):
    monkeypatch.delenv("RIVET_MAILBOX_UPDATES_ENABLED")
    assert enqueue(prepared_reply[0]) is None
    monkeypatch.setenv("RIVET_MAILBOX_UPDATES_ENABLED", "true")
    identifier = enqueue(prepared_reply[0])
    monkeypatch.setenv(
        "RIVET_MS_MAILBOX_ALLOWLIST",
        json.dumps({LOCAL_ORG: {MAILBOX: {"update_recipient": "changed@example.com"}}}),
    )
    sender = FakeSender()
    assert (
        updates.deliver_update(identifier, sender=sender)["status"] == "needs_attention"
    )
    assert sender.calls == []


def test_reply_update_revalidates_source_evidence_immediately_before_delivery(
    prepared_reply,
):
    from backend.storage.models import Span

    identifier = enqueue(prepared_reply[0])
    with Session.begin() as s:
        span = s.scalar(select(Span))
        span.text += " New conflicting source detail arrived."
    sender = FakeSender()
    assert updates.deliver_update(identifier, sender=sender)["status"] == "superseded"
    assert sender.calls == []


@pytest.mark.parametrize("status", ["approved", "rejected", "failed", "generating"])
def test_reply_handled_or_no_longer_ready_does_not_notify(prepared_reply, status):
    from backend.reply_assistant.models import ReplyDraft

    identifier = enqueue(prepared_reply[0])
    with Session.begin() as s:
        s.get(ReplyDraft, prepared_reply[1]).status = status
    sender = FakeSender()
    assert updates.deliver_update(identifier, sender=sender)["status"] == "superseded"
    assert sender.calls == []
    assert enqueue(prepared_reply[0]) is None


def test_ready_reply_retry_uses_identical_payload_key_and_revalidates(prepared_reply):
    identifier = enqueue(prepared_reply[0])
    sender = FakeSender(failures=1)
    assert updates.deliver_update(identifier, sender=sender)["status"] == "retry"
    make_due(identifier)
    assert (
        updates.deliver_update(identifier, sender=sender)["status"]
        == "accepted_by_provider"
    )
    assert sender.calls[0] == sender.calls[1]


def test_unknown_reply_delivery_plus_changed_evidence_requires_attention(
    prepared_reply,
):
    from backend.reply_assistant.models import ReplyDraft

    identifier = enqueue(prepared_reply[0])
    sender = FakeSender(failures=1)
    assert updates.deliver_update(identifier, sender=sender)["status"] == "retry"
    with Session.begin() as s:
        s.get(ReplyDraft, prepared_reply[1]).status = "rejected"
    make_due(identifier)
    assert (
        updates.deliver_update(identifier, sender=sender)["status"] == "needs_attention"
    )
    assert len(sender.calls) == 1


def test_reply_notification_omits_untrusted_external_outlook_link(prepared_reply):
    with Session.begin() as s:
        receipt = s.get(MailboxReceipt, prepared_reply[0])
        receipt.data = {
            **receipt.data,
            "web_link": "https://attacker.example/pretend-outlook",
        }
    identifier = enqueue(prepared_reply[0])
    with Session() as s:
        payload = s.get(MailboxUpdate, identifier).payload
        assert "attacker.example" not in str(payload)
        assert "Open in Outlook" not in payload["html"]
        assert "workspace.example.com" in payload["text"]


def test_newer_message_supersedes_old_thread_reply_notice(prepared_reply):
    identifier = enqueue(prepared_reply[0])
    with Session.begin() as s:
        original = s.get(MailboxReceipt, prepared_reply[0])
        s.add(
            MailboxReceipt(
                connection_id=original.connection_id,
                intake_id=original.intake_id,
                provider_message_id="newer-same-thread",
                internet_message_id="<newer@example.com>",
                status="imported",
                data={
                    **original.data,
                    "received_at": (now() + timedelta(seconds=1)).isoformat(),
                },
            )
        )
    sender = FakeSender()
    assert updates.deliver_update(identifier, sender=sender)["status"] == "superseded"
    assert sender.calls == []


def test_new_saved_reply_replaces_older_unsent_reply_notice(prepared_reply):
    from backend.reply_assistant.models import ReplyDraft

    first = enqueue(prepared_reply[0])
    with Session.begin() as s:
        old = s.get(ReplyDraft, prepared_reply[1])
        s.add(
            ReplyDraft(
                order_id=old.order_id,
                internet_message_id=old.internet_message_id,
                request_key="auto-mail:newer-notice-fixture",
                request_hash=old.request_hash,
                status="ready",
                evidence_hash=old.evidence_hash,
                subject=old.subject,
                body=old.body,
                original_body=old.original_body,
                context=old.context,
                source_refs=old.source_refs,
                unresolved=old.unresolved,
                coverage=old.coverage,
            )
        )
    second = enqueue(prepared_reply[0])
    assert second and second != first
    sender = FakeSender()
    assert updates.deliver_update(first, sender=sender)["status"] == "superseded"
    assert (
        updates.deliver_update(second, sender=sender)["status"]
        == "accepted_by_provider"
    )
    assert len(sender.calls) == 1


def test_ready_reply_supersedes_queued_coordination_update_for_same_email(
    prepared_reply,
):
    from backend.reply_assistant.models import ReplyDraft

    with Session.begin() as s:
        draft = s.get(ReplyDraft, prepared_reply[1])
        draft.request_key = "manual-reply-fixture"
    coordination = enqueue(prepared_reply[0])
    with Session.begin() as s:
        draft = s.get(ReplyDraft, prepared_reply[1])
        draft.request_key = "auto-mail:reply-update-fixture"
    ready_reply = enqueue(prepared_reply[0])
    assert coordination and ready_reply and coordination != ready_reply
    sender = FakeSender()
    assert updates.deliver_update(coordination, sender=sender)["status"] == "superseded"
    assert (
        updates.deliver_update(ready_reply, sender=sender)["status"]
        == "accepted_by_provider"
    )
    assert len(sender.calls) == 1
