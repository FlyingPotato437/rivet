"""Autopilot: prepared replies land in Outlook unsent; sending is the approval.

A fake Microsoft mailbox stands in for Graph. No test sends mail, and the fake
has no send endpoint: any attempt to call one fails the test.
"""

import json
import re
from datetime import timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from test_company_context import identity
from test_reply_automatic import mail, run  # noqa: F401 - fixture reused

from backend.autopilot import outlook, service
from backend.autopilot.api import AutopilotSettingsWrite
from backend.autopilot.models import OutlookDraft
from backend.company_context.models import CompanyFeedback
from backend.domain.service import get
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.reply_assistant import automatic
from backend.reply_assistant import service as replies
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session
from backend.storage.models import Order, RecordIntake, Span, now

QUOTED = (
    "<html><head><style>p{margin:0}</style></head><body><div><br></div>"
    '<hr><div id="divRplyFwdMsg"><b>From:</b> customer@example.com</div>'
    "<div>Please confirm sheet E-10.</div></body></html>"
)


class FakeMailbox:
    """Just enough of Graph: createReply, PATCH, attachments, GET, and nothing else."""

    def __init__(self, *, deny=False):
        self.messages, self.calls, self.deny = {}, [], deny
        self.mailbox = "pm@example.com"

    def handler(self, request):
        path = request.url.path.split("/users/pm@example.com/", 1)[-1]
        self.calls.append((request.method, path))
        assert "/send" not in path, "Rivet must never send mail"
        if self.deny:
            return httpx.Response(403, json={"error": {"code": "ErrorAccessDenied"}})
        parts = path.split("/")
        if request.method == "GET" and path == "mailFolders/deleteditems":
            return httpx.Response(200, json={"id": "folder-deleted"})
        if request.method == "GET" and path == "mailFolders/drafts/messages":
            conversation = (
                request.url.params["$filter"]
                .split("'", 1)[1]
                .rsplit("'", 1)[0]
                .replace("''", "'")
            )
            return httpx.Response(
                200,
                json={
                    "value": [
                        value
                        for value in self.messages.values()
                        if value.get("isDraft")
                        and value.get("parentFolderId") == "folder-drafts"
                        and value.get("conversationId") == conversation
                    ][:50]
                },
            )
        if request.method == "POST" and path.endswith("/createReply"):
            draft_id = f"draft-{len(self.messages) + 1}"
            self.messages[draft_id] = {
                "id": draft_id,
                "isDraft": True,
                "parentFolderId": "folder-drafts",
                "webLink": "https://outlook.office.com/mail/deeplink/compose/"
                + draft_id,
                "body": {"contentType": "html", "content": QUOTED},
                "attachments": [],
            }
            return httpx.Response(201, json=self.messages[draft_id])
        message = self.messages.setdefault(parts[1], {"id": parts[1], "categories": []})
        if request.method == "PATCH":
            message.update(json.loads(request.content))
            return httpx.Response(200, json=message)
        if request.method == "POST" and path.endswith("/attachments"):
            message["attachments"].append(json.loads(request.content)["name"])
            return httpx.Response(201, json={})
        if request.method == "GET":
            if message.get("deleted"):
                return httpx.Response(
                    404, json={"error": {"code": "ErrorItemNotFound"}}
                )
            result = dict(message)
            if 'IdType="ImmutableId"' not in request.headers.get("Prefer", ""):
                result["id"] = "rest-" + message["id"]
            return httpx.Response(200, json=result)
        return httpx.Response(405)

    def __call__(self, _snapshot):
        mailbox = self

        class Client:
            def __init__(self):
                self.mailbox = mailbox.mailbox
                self.client = httpx.Client(
                    transport=httpx.MockTransport(mailbox.handler)
                )

            def headers(self):
                return {
                    "Authorization": "Bearer fake",
                    "Prefer": 'IdType="ImmutableId"',
                }

            def close(self):
                self.client.close()

        return Client()

    def draft(self):
        return next(m for k, m in self.messages.items() if k.startswith("draft-"))


@pytest.fixture
def ready(mail):  # noqa: F811 - pytest fixture injection
    automatic.schedule()
    run()
    with identity(), Session() as s:
        reply = s.scalar(select(ReplyDraft))
        assert reply.status == "ready"
        receipt = get(s, MailboxReceipt, mail[3])
        return reply.id, receipt.provider_message_id


def test_ready_reply_lands_in_the_customer_thread_unsent(ready):
    graph = FakeMailbox()
    result = service.deliver(client_factory=graph)
    assert result[0]["status"] == "created"
    assert ("POST", f"messages/{ready[1]}/createReply") in graph.calls
    body = graph.draft()["body"]["content"]
    assert "cable entry location" in body
    # Rivet's words sit above Outlook's quoted original, inside the same body.
    assert body.index('id="rivet-reply"') < body.index("divRplyFwdMsg")
    assert graph.messages[ready[1]]["categories"] == [outlook.TAG]
    # Idempotent: the next pass does not create a second draft.
    assert service.deliver(client_factory=graph) == []
    assert sum(1 for m, p in graph.calls if p.endswith("createReply")) == 1


@pytest.mark.parametrize("status", sorted(service.SET_ASIDE))
def test_unrelated_email_stays_out_of_dashboard_activity(mail, status):  # noqa: F811
    with identity(), Session.begin() as s:
        receipt = get(s, MailboxReceipt, mail[3])
        intake = get(s, RecordIntake, receipt.intake_id)
        intake.order_id = None
        intake.status = status
        # An obsolete preparation failure must not reintroduce excluded mail.
        receipt.data = {**receipt.data, "automatic_reply": {"status": "failed"}}
    with identity(), Session() as s:
        result = service.activity(s)
        assert result["feed"] == []
        assert result["needs_you"] == []
        assert result["summary"]["set_aside"] == 1
        assert result["summary"]["emails_read"] == 1
        assert s.get(MailboxReceipt, mail[3]) is not None


def test_uncertain_order_email_still_requests_confirmation(mail):  # noqa: F811
    with identity(), Session.begin() as s:
        receipt = get(s, MailboxReceipt, mail[3])
        intake = get(s, RecordIntake, receipt.intake_id)
        intake.order_id = None
        intake.status = "needs_confirmation"
    with identity(), Session() as s:
        result = service.activity(s)
        assert result["needs_you"][0]["kind"] == "which_job"
        assert result["summary"]["set_aside"] == 0


def test_sending_unchanged_is_the_approval_and_is_learned(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    graph.draft().update({"isDraft": False, "sentDateTime": now().isoformat()})
    checked = service.sync_sent(client_factory=graph)
    assert checked[0]["status"] == "sent" and checked[0]["outcome"] == "accepted"
    assert checked[0]["learning_status"] == "approved"
    with identity(), Session() as s:
        reply = get(s, ReplyDraft, ready[0])
        assert reply.status == "approved" and reply.review["sent_from_outlook"]
        feedback = s.scalar(select(CompanyFeedback))
        assert feedback.outcome == "accepted" and feedback.reuse_status == "approved"
        activity = service.activity(s)
        summary = activity["summary"]
        assert summary["drafted_in_outlook"] == 1 and summary["sent_unchanged"] == 1
        assert summary["learned"] == 1
        sent = next(item for item in activity["feed"] if item["kind"] == "sent")
        assert sent["outcome"] == "accepted"
        assert sent["learning_status"] == "approved"


def test_activity_orders_provider_utc_and_database_local_timestamps_by_instant(ready):
    graph = FakeMailbox()
    placed = service.deliver(client_factory=graph)[0]
    current = now()
    pacific = timezone(timedelta(hours=-7))
    with identity(), Session.begin() as s:
        native = get(s, OutlookDraft, placed["id"])
        receipt = get(s, MailboxReceipt, native.receipt_id)
        receipt.data = {
            **receipt.data,
            "received_at": (current - timedelta(minutes=3))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        native.drafted_at = (current - timedelta(minutes=2)).astimezone(pacific)
        native.sent_at = (current - timedelta(minutes=1)).astimezone(pacific)
        native.status, native.outcome = "sent", "edited"
        native.learning_status = "approved"
    with identity(), Session() as s:
        feed = service.activity(s)["feed"]
        assert [item["kind"] for item in feed] == ["sent", "drafted", "read"]
        assert all(item["at"].endswith("+00:00") for item in feed)


@pytest.mark.parametrize("coverage_origin", ["lifecycle_proposal", "automatic"])
def test_sent_activity_exposes_only_the_exact_native_reply_lifecycle_package(
    ready, coverage_origin
):
    graph = FakeMailbox()
    placed = service.deliver(client_factory=graph)[0]
    with identity(), Session.begin() as s:
        reply = get(s, ReplyDraft, ready[0])
        reply.coverage = {
            **reply.coverage,
            "origin": coverage_origin,
            "package_id": "exact-quote-package",
        }
        native = get(s, OutlookDraft, placed["id"])
        native.status, native.outcome, native.sent_at = "sent", "accepted", now()
    with identity(), Session() as s:
        sent = next(
            item for item in service.activity(s)["feed"] if item["kind"] == "sent"
        )
        assert sent["draft_status"] == "sent"
        assert sent["coverage"] == (
            {"package_id": "exact-quote-package"}
            if coverage_origin == "lifecycle_proposal"
            else {}
        )


def test_sent_package_coverage_is_not_projected_onto_another_order(ready):
    from backend.orders.service import new_order

    graph = FakeMailbox()
    placed = service.deliver(client_factory=graph)[0]
    with identity(), Session.begin() as s:
        reply = get(s, ReplyDraft, ready[0])
        reply.coverage = {
            **reply.coverage,
            "origin": "lifecycle_proposal",
            "package_id": "other-order-package",
        }
        other = new_order(
            s,
            {
                "title": "Separate fictional order",
                "customer": "Fictional customer",
                "number": "OTHER-FEED-001",
                "synthetic": True,
            },
        )
        reply.order_id = other.id
        native = get(s, OutlookDraft, placed["id"])
        native.status, native.outcome, native.sent_at = "sent", "accepted", now()
    with identity(), Session() as s:
        sent = next(
            item for item in service.activity(s)["feed"] if item["kind"] == "sent"
        )
        assert sent["coverage"] == {}


def test_edits_before_sending_are_recorded_as_the_correction(ready):
    with identity(), Session.begin() as s:
        service.update_settings(s, AutopilotSettingsWrite(learn_from_sent="review"))
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    edited = "Please send sheet E-10 Rev C so we can confirm the cable entry."
    graph.draft().update(
        {
            "isDraft": False,
            "sentDateTime": now().isoformat(),
            "body": {"contentType": "html", "content": f"<div>{edited}</div>" + QUOTED},
        }
    )
    checked = service.sync_sent(client_factory=graph)[0]
    assert checked["outcome"] == "edited" and checked["learning_status"] == "recorded"
    with identity(), Session() as s:
        assert get(s, ReplyDraft, ready[0]).body == edited
        feedback = s.scalar(select(CompanyFeedback))
        assert (
            feedback.human_response == edited and feedback.reuse_status == "unapproved"
        )
        sent = next(
            item for item in service.activity(s)["feed"] if item["kind"] == "sent"
        )
        assert sent["outcome"] == "edited"
        assert sent["learning_status"] == "recorded"
        assert "administrator review" in sent["detail"]
        assert "next time" not in sent["detail"]


@pytest.mark.parametrize("final_status", ["sent", "discarded"])
def test_completed_outlook_mail_is_not_reopened_when_order_evidence_changes(
    ready, final_status
):
    graph = FakeMailbox()
    placed = service.deliver(client_factory=graph)[0]
    with identity(), Session.begin() as s:
        native = get(s, OutlookDraft, placed["id"])
        native.status = final_status
        original = get(s, ReplyDraft, ready[0])
        source = get(s, Span, original.source_refs[0]["id"])
        source.text += " A separate later return added new context."
        receipt = get(s, MailboxReceipt, native.receipt_id)
        assert automatic.reconcile_receipt(s, receipt)["status"] == "not_needed"
        prepared, state = replies.prepared_for_receipt(
            s, get(s, Order, original.order_id), receipt
        )
        assert prepared is None and state["status"] == "not_needed"
        assert len(list(s.scalars(select(ReplyDraft)))) == 1
    assert service.deliver(client_factory=graph) == []


def test_deleted_draft_means_not_needed_and_teaches_nothing(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    graph.draft()["deleted"] = True
    # Missing once is not deleted: a sent draft is briefly unreadable while it moves.
    assert service.sync_sent(client_factory=graph)[0]["status"] == "created"
    with identity(), Session.begin() as s:
        row = s.scalar(select(OutlookDraft))
        row.missing_since -= service.MISSING_GRACE
        row.checked_at -= service.MISSING_GRACE
    assert service.sync_sent(client_factory=graph)[0]["status"] == "discarded"
    with identity(), Session() as s:
        assert s.scalar(select(CompanyFeedback)) is None
        assert get(s, ReplyDraft, ready[0]).status == "ready"


def test_missing_write_permission_asks_an_administrator_once(ready):
    graph = FakeMailbox(deny=True)
    result = service.deliver(client_factory=graph)[0]
    assert result["status"] == "permission_required"
    assert "Mail.ReadWrite" in result["error"]
    # Not retried every loop: the next attempt waits hours.
    assert service.deliver(client_factory=graph) == []
    with identity(), Session() as s:
        needs = service.activity(s)["needs_you"]
        assert any(n["kind"] == "permission_required" for n in needs)


def test_turning_autopilot_drafts_off_stops_outlook_writes(ready):
    with identity(), Session.begin() as s:
        service.update_settings(s, AutopilotSettingsWrite(draft_in_outlook=False))
    graph = FakeMailbox()
    assert service.deliver(client_factory=graph) == []
    assert graph.calls == []


def test_pane_review_first_supersedes_the_outlook_draft(ready):
    with identity(), Session.begin() as s:
        get(s, ReplyDraft, ready[0], True).status = "approved"
    graph = FakeMailbox()
    # An approved reply is no longer "ready", so autopilot leaves it alone.
    assert service.deliver(client_factory=graph) == []
    assert graph.calls == []


def test_only_administrators_change_autonomy(ready):
    with identity(role="org:member"), Session.begin() as s:
        with pytest.raises(Exception) as exc:
            service.update_settings(s, AutopilotSettingsWrite(learn_from_sent="review"))
        assert "administrator" in str(exc.value.detail)


def test_sent_text_keeps_only_what_the_person_wrote():
    html = (
        "<html><body><div>Thanks, attached.</div><div>Rev C&nbsp;follows.</div>"
        '<div id="appendonsend"></div><hr><div id="divRplyFwdMsg">From: x</div>'
        "<div>Old text</div></body></html>"
    )
    assert outlook.sent_text(html) == "Thanks, attached.\nRev C follows."
    assert outlook.same_words("a  b\nc", "a b c")
    composed = outlook.compose_body(QUOTED, "Line one\nLine <two>")
    assert re.search(
        r"<body><div id=\"rivet-reply\"[^>]*>Line one<br>Line &lt;two&gt;", composed
    )


def test_plain_text_outlook_original_cannot_become_a_learned_edit():
    original = "________________________________________ From: Customer Sent: Friday To: PM Subject: Drawing\nPlease change the breaker."
    composed = outlook.compose_body(
        original, "Please confirm the governing trip unit.", "text"
    )
    assert outlook.sent_text(composed) == "Please confirm the governing trip unit."
    flattened = (
        '<div id="rivet-reply">Please confirm the governing trip unit.</div><br>'
        + original
    )
    assert outlook.sent_text(flattened) == "Please confirm the governing trip unit."
    assert outlook.same_words(
        outlook.sent_text(flattened), "Please confirm the governing trip unit."
    )
    assert (
        outlook.sent_text("<div>From: the scope record, this is still open.</div>")
        == "From: the scope record, this is still open."
    )


def due_again():
    with identity(), Session.begin() as s:
        row = s.scalar(select(OutlookDraft))
        row.next_attempt_at = now() - timedelta(minutes=1)
        row.checked_at = None


def test_retry_reuses_created_draft_and_preserves_human_edits(ready):
    class InterruptedMailbox(FakeMailbox):
        interrupted = False

        def handler(self, request):
            if request.method == "PATCH" and not self.interrupted:
                self.interrupted = True
                return httpx.Response(503, json={})
            return super().handler(request)

    graph = InterruptedMailbox()
    failed = service.deliver(client_factory=graph)[0]
    assert failed["status"] == "failed"
    edited = "The PM wrote this while Rivet was retrying."
    graph.draft()["body"] = {"content": outlook.compose_body(QUOTED, edited)}
    due_again()
    recovered = service.deliver(client_factory=graph)[0]
    assert recovered["status"] == "created"
    assert outlook.sent_text(graph.draft()["body"]["content"]) == edited
    assert sum(p.endswith("createReply") for _, p in graph.calls) == 1


def test_patch_retry_fills_same_empty_draft(ready):
    class InterruptedMailbox(FakeMailbox):
        interrupted = False

        def handler(self, request):
            if request.method == "PATCH" and not self.interrupted:
                self.interrupted = True
                return httpx.Response(503, json={})
            return super().handler(request)

    graph = InterruptedMailbox()
    assert service.deliver(client_factory=graph)[0]["status"] == "failed"
    due_again()
    assert service.deliver(client_factory=graph)[0]["status"] == "created"
    assert "cable entry location" in graph.draft()["body"]["content"]
    assert sum(p.endswith("createReply") for _, p in graph.calls) == 1


def test_unknown_create_outcome_is_not_blindly_retried(ready):
    class LostResponseMailbox(FakeMailbox):
        def handler(self, request):
            result = super().handler(request)
            if request.url.path.endswith("/createReply"):
                raise httpx.ReadTimeout("response lost", request=request)
            return result

    graph = LostResponseMailbox()
    result = service.deliver(client_factory=graph)[0]
    assert result["status"] == "failed" and "duplicate" in result["error"]
    due_again()
    assert service.deliver(client_factory=graph) == []
    assert sum(p.endswith("createReply") for _, p in graph.calls) == 1


def test_expired_creation_lease_is_visible_without_duplicate_write(ready):
    with identity(), Session.begin() as s:
        receipt = s.scalar(select(MailboxReceipt))
        reply = s.get(ReplyDraft, ready[0])
        s.add(
            OutlookDraft(
                reply_draft_id=reply.id,
                order_id=reply.order_id,
                receipt_id=receipt.id,
                connection_id=receipt.connection_id,
                status="creating",
                lease="interrupted",
                lease_until=now() - timedelta(minutes=1),
            )
        )
    graph = FakeMailbox()
    result = service.deliver(client_factory=graph)[0]
    assert result["status"] == "failed" and "interrupted" in result["error"]
    assert graph.calls == []


def test_new_prepared_version_does_not_duplicate_existing_outlook_draft(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    with identity(), Session.begin() as s:
        s.scalar(select(Span)).text += " The source was updated."
        automatic.reconcile_receipt(s, s.scalar(select(MailboxReceipt)))
    run()
    result = service.deliver(client_factory=graph)[0]
    assert result["status"] == "superseded"
    assert sum(p.endswith("createReply") for _, p in graph.calls) == 1
    with identity(), Session() as s:
        assert (
            service.draft_for_message(s, "<fixture@example.com>")["status"] == "created"
        )


def test_set_aside_after_preparation_stops_native_placement(ready):
    with identity(), Session.begin() as s:
        s.scalar(select(RecordIntake)).status = "set_aside"
    graph = FakeMailbox()
    assert service.deliver(client_factory=graph)[0]["status"] == "superseded"
    assert graph.calls == []


def test_older_email_backfilled_later_does_not_supersede_question(ready):
    with identity(), Session.begin() as s:
        receipt = s.scalar(select(MailboxReceipt))
        receipt.data = {
            **receipt.data,
            "conversation_id": "thread",
            "received_at": "2026-10-14T09:00:00Z",
        }
        s.add(
            MailboxReceipt(
                connection_id=receipt.connection_id,
                provider_message_id="older-recovered",
                internet_message_id="<older@example.com>",
                status="imported",
                intake_id=receipt.intake_id,
                data={
                    "conversation_id": "thread",
                    "received_at": "2026-10-13T09:00:00Z",
                },
            )
        )
    graph = FakeMailbox()
    assert service.deliver(client_factory=graph)[0]["status"] == "created"


def test_non_draft_without_sent_time_is_not_learned_as_sent(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    graph.draft()["isDraft"] = False
    assert service.sync_sent(client_factory=graph)[0]["status"] == "created"
    with identity(), Session() as s:
        assert s.scalar(select(CompanyFeedback)) is None


def test_paused_connection_is_not_read_for_learning(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    with identity(), Session.begin() as s:
        s.scalar(select(MailboxConnection)).enabled = False
    graph.calls.clear()
    service.sync_sent(client_factory=graph)
    assert graph.calls == []


def test_stale_sent_evidence_needs_review_before_reuse(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    with identity(), Session.begin() as s:
        s.scalar(select(Span)).text += " Superseding facts arrived before sending."
    graph.draft().update({"isDraft": False, "sentDateTime": "2026-10-14T09:00:00Z"})
    result = service.sync_sent(client_factory=graph)[0]
    assert result["status"] == "sent" and result["learning_status"] == "pending_review"
    with identity(), Session() as s:
        assert s.scalar(select(CompanyFeedback)).reuse_status == "unapproved"


def test_auto_learning_stays_in_original_order_and_does_not_redraft_sent_mail(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    graph.draft().update({"isDraft": False, "sentDateTime": "2026-10-14T09:00:00Z"})
    service.sync_sent(client_factory=graph)
    with identity(), Session() as s:
        assert s.scalar(select(CompanyFeedback)).reuse_scope == "order"
    automatic.schedule()
    with identity(), Session() as s:
        assert len(list(s.scalars(select(ReplyDraft)))) == 1


@pytest.mark.parametrize(
    "state,graph_id,attempts",
    [
        ("creating", "", 1),
        ("created", "native-1", 1),
        ("sent", "native-1", 1),
        ("failed", "native-1", 1),
        ("failed", "", service.MAX_ATTEMPTS),
    ],
)
def test_manual_compose_cannot_race_native_placement(ready, state, graph_id, attempts):
    with identity(), Session.begin() as s:
        reply = s.get(ReplyDraft, ready[0])
        reply.status = "approved"
        s.add(
            OutlookDraft(
                reply_draft_id=reply.id,
                order_id=reply.order_id,
                status=state,
                graph_draft_id=graph_id,
                attempts=attempts,
            )
        )
        s.flush()
        with pytest.raises(Exception) as exc:
            replies.compose(s, reply.id, SimpleNamespace())
        assert exc.value.status_code == 409
        assert "duplicate" in exc.value.detail


@pytest.mark.parametrize("state", ["pending", "permission_required", "failed"])
def test_safe_unclaimed_placement_allows_manual_compose(ready, state):
    with identity(), Session.begin() as s:
        reply = s.get(ReplyDraft, ready[0])
        s.add(
            OutlookDraft(
                reply_draft_id=reply.id,
                order_id=reply.order_id,
                status=state,
                attempts=1,
            )
        )
        s.flush()
        service.assert_manual_compose_available(s, reply)


def test_evidence_change_during_create_prevents_writing_old_reply(ready):
    class ChangedMailbox(FakeMailbox):
        def handler(self, request):
            result = super().handler(request)
            if request.url.path.endswith("/createReply"):
                with identity(), Session.begin() as s:
                    s.scalar(select(Span)).text += " Updated while creating."
            return result

    graph = ChangedMailbox()
    result = service.deliver(client_factory=graph)[0]
    assert result["status"] == "failed" and "changed" in result["error"]
    assert outlook.sent_text(graph.draft()["body"]["content"]) == ""
    assert not any(method == "PATCH" for method, _ in graph.calls)
    due_again()
    assert service.deliver(client_factory=graph) == []


def test_recover_known_draft_after_worker_crash(ready):
    class CrashMailbox(FakeMailbox):
        crash = True

        def handler(self, request):
            if request.method == "PATCH" and self.crash:
                self.crash = False
                raise RuntimeError("worker stopped unexpectedly")
            return super().handler(request)

    graph = CrashMailbox()
    assert service.deliver(client_factory=graph) == []
    with identity(), Session.begin() as s:
        row = s.scalar(select(OutlookDraft))
        assert row.status == "creating" and row.graph_draft_id
        row.lease_until = now() - timedelta(minutes=1)
    assert service.deliver(client_factory=graph)[0]["status"] == "created"
    assert sum(p.endswith("createReply") for _, p in graph.calls) == 1


def test_unavailable_check_interrupts_deleted_grace_period(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    graph.draft()["deleted"] = True
    service.sync_sent(client_factory=graph)
    with identity(), Session.begin() as s:
        row = s.scalar(select(OutlookDraft))
        row.missing_since -= timedelta(minutes=15)
        row.checked_at = None
    graph.deny = True
    service.sync_sent(client_factory=graph)
    with identity(), Session() as s:
        row = s.scalar(select(OutlookDraft))
        assert row.status == "created" and row.missing_since is None


def test_existing_native_draft_reports_later_evidence_change(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    with identity(), Session.begin() as s:
        s.scalar(select(Span)).text += " New accepted details."
    result = service.sync_sent(client_factory=graph)[0]
    assert result["status"] == "created" and "changed" in result["error"]
    with identity(), Session() as s:
        assert any(
            "changed" in item["detail"] for item in service.activity(s)["needs_you"]
        )


def test_soft_deleted_native_draft_is_discarded_without_learning(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    graph.draft()["parentFolderId"] = "folder-deleted"
    result = service.sync_sent(client_factory=graph)[0]
    assert result["status"] == "discarded"
    assert ("GET", "mailFolders/deleteditems") in graph.calls
    with identity(), Session() as s:
        assert s.scalar(select(CompanyFeedback)) is None


def test_open_existing_draft_returns_actual_link_and_normal_rest_id(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    graph.calls.clear()
    with identity():
        result = service.open_draft("<fixture@example.com>", client_factory=graph)[
            "draft"
        ]
    assert result["status"] == "created"
    assert result["rest_item_id"] == "rest-" + graph.draft()["id"]
    assert result["outlook_url"] == graph.draft()["webLink"]
    assert all(method == "GET" for method, _ in graph.calls)


def test_open_soft_deleted_draft_is_rejected(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    graph.draft()["parentFolderId"] = "folder-deleted"
    with identity(), pytest.raises(Exception) as exc:
        service.open_draft("<fixture@example.com>", client_factory=graph)
    assert exc.value.status_code == 409


def test_open_draft_never_returns_untrusted_link(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    graph.draft()["webLink"] = "https://outlook.office.com.evil.test/collect"
    with identity():
        result = service.open_draft("<fixture@example.com>", client_factory=graph)[
            "draft"
        ]
    assert result["outlook_url"] == "" and result["rest_item_id"]


def with_existing_manual_reply(graph, original_id):
    graph.messages[original_id] = {
        "id": original_id,
        "conversationId": "customer-thread",
        "categories": [],
    }
    graph.messages["manual-reply"] = {
        "id": "manual-reply",
        "conversationId": "customer-thread",
        "isDraft": True,
        "parentFolderId": "folder-drafts",
        "lastModifiedDateTime": "2026-10-14T09:00:00Z",
        "body": {"content": "<div>The PM already started this reply.</div>"},
        "attachments": ["PM-selected.pdf"],
        "webLink": "https://outlook.office.com/mail/deeplink/compose/manual-reply",
    }


def test_existing_manual_draft_is_preserved_and_not_attributed_to_rivet(ready):
    graph = FakeMailbox()
    with_existing_manual_reply(graph, ready[1])
    result = service.deliver(client_factory=graph)[0]
    assert result["status"] == "existing_draft"
    assert all(method == "GET" for method, _ in graph.calls)
    assert graph.messages["manual-reply"]["attachments"] == ["PM-selected.pdf"]
    assert "PM already started" in graph.messages["manual-reply"]["body"]["content"]
    with identity(), Session() as s:
        overview = service.activity(s)
        assert overview["summary"]["drafted_in_outlook"] == 0
        assert len(overview["waiting_in_outlook"]) == 1
    with identity():
        assert (
            service.open_draft("<fixture@example.com>", client_factory=graph)["draft"][
                "rest_item_id"
            ]
            == "rest-manual-reply"
        )


def test_sent_preserved_manual_draft_does_not_teach_an_ai_example(ready):
    graph = FakeMailbox()
    with_existing_manual_reply(graph, ready[1])
    service.deliver(client_factory=graph)
    graph.messages["manual-reply"].update(
        {"isDraft": False, "sentDateTime": "2026-10-14T09:00:00Z"}
    )
    result = service.sync_sent(client_factory=graph)[0]
    assert result["status"] == "sent" and result["outcome"] == "external"
    assert result["learning_status"] == "not_captured"
    with identity(), Session() as s:
        assert s.scalar(select(CompanyFeedback)) is None


def test_deleted_preserved_manual_draft_is_not_recreated(ready):
    graph = FakeMailbox()
    with_existing_manual_reply(graph, ready[1])
    service.deliver(client_factory=graph)
    graph.messages["manual-reply"]["parentFolderId"] = "folder-deleted"
    assert service.sync_sent(client_factory=graph)[0]["status"] == "discarded"
    assert service.deliver(client_factory=graph) == []


def test_unsorted_existing_drafts_select_newest_modified_without_changes(ready):
    graph = FakeMailbox()
    with_existing_manual_reply(graph, ready[1])
    old = graph.messages["manual-reply"]
    old["body"] = {"content": QUOTED}
    old["attachments"] = []
    graph.messages["newer-complete-reply"] = {
        **old,
        "id": "newer-complete-reply",
        "lastModifiedDateTime": "2026-10-14T05:00:00-07:00",
        "body": {"content": "<div>Reviewed reply with the released drawing.</div>"},
        "attachments": ["Released-E10.pdf"],
    }
    # Graph returns the older blank one first; ordering is deliberately unspecified.
    result = service.deliver(client_factory=graph)[0]
    assert result["status"] == "existing_draft"
    with identity(), Session() as s:
        assert s.scalar(select(OutlookDraft)).graph_draft_id == "newer-complete-reply"
    assert old["body"]["content"] == QUOTED
    assert graph.messages["newer-complete-reply"]["attachments"] == ["Released-E10.pdf"]
    assert all(method == "GET" for method, _ in graph.calls)


def test_newest_existing_draft_can_be_on_later_page(ready):
    class PagedMailbox(FakeMailbox):
        def handler(self, request):
            if request.url.path.endswith("/mailFolders/drafts/messages"):
                self.calls.append((request.method, "mailFolders/drafts/messages"))
                if request.url.params.get("page") == "2":
                    return httpx.Response(
                        200,
                        json={
                            "value": [
                                {
                                    **self.messages["manual-reply"],
                                    "id": "latest-page-two",
                                    "lastModifiedDateTime": "2026-10-14T12:00:00Z",
                                }
                            ]
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "value": [self.messages["manual-reply"]],
                        "@odata.nextLink": "https://graph.microsoft.com/v1.0/users/pm%40example.com/mailFolders/drafts/messages?page=2",
                    },
                )
            return super().handler(request)

    graph = PagedMailbox()
    with_existing_manual_reply(graph, ready[1])
    assert service.deliver(client_factory=graph)[0]["status"] == "existing_draft"
    with identity(), Session() as s:
        assert s.scalar(select(OutlookDraft)).graph_draft_id == "latest-page-two"


@pytest.mark.parametrize(
    "unsafe_link",
    [
        "https://other.example/collect",
        "https://graph.microsoft.com/v1.0/users/other@example.com/mailFolders/drafts/messages?page=2",
    ],
)
def test_draft_pagination_never_leaves_authorized_mailbox(ready, unsafe_link):
    class UnsafeCursorMailbox(FakeMailbox):
        def handler(self, request):
            if request.url.path.endswith("/mailFolders/drafts/messages"):
                self.calls.append((request.method, "mailFolders/drafts/messages"))
                return httpx.Response(
                    200, json={"value": [], "@odata.nextLink": unsafe_link}
                )
            return super().handler(request)

    graph = UnsafeCursorMailbox()
    with_existing_manual_reply(graph, ready[1])
    result = service.deliver(client_factory=graph)[0]
    assert result["status"] == "failed" and "unexpected" in result["error"]
    assert all(method == "GET" for method, _ in graph.calls)


def test_bounded_draft_lookup_fails_closed_when_pages_keep_coming(ready):
    class OverflowMailbox(FakeMailbox):
        def handler(self, request):
            if request.url.path.endswith("/mailFolders/drafts/messages"):
                self.calls.append((request.method, "mailFolders/drafts/messages"))
                page = int(request.url.params.get("page", "1"))
                return httpx.Response(
                    200,
                    json={
                        "value": [self.messages["manual-reply"]],
                        "@odata.nextLink": "https://graph.microsoft.com/v1.0/users/pm%40example.com/mailFolders/drafts/messages?page="
                        + str(page + 1),
                    },
                )
            return super().handler(request)

    graph = OverflowMailbox()
    with_existing_manual_reply(graph, ready[1])
    result = service.deliver(client_factory=graph)[0]
    assert result["status"] == "failed" and "too many" in result["error"]
    assert (
        sum(path == "mailFolders/drafts/messages" for _, path in graph.calls)
        == outlook.MAX_DRAFT_CHECK_PAGES
    )
    assert all(method == "GET" for method, _ in graph.calls)


def test_current_outlook_cloud_host_is_allowed_as_provider_link(ready):
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    graph.draft()["webLink"] = (
        "https://outlook.cloud.microsoft/mail/deeplink/read/existing-draft"
    )
    with identity():
        result = service.open_draft("<fixture@example.com>", client_factory=graph)[
            "draft"
        ]
    assert result["outlook_url"] == graph.draft()["webLink"]


def test_work_feed_links_one_receipt_to_its_order_and_native_draft(ready, mail):  # noqa: F811
    graph = FakeMailbox()
    service.deliver(client_factory=graph)
    with identity(), Session() as s:
        activity = service.activity(s)
        draft = activity["waiting_in_outlook"][0]
        assert draft["receipt_id"] == mail[3]
        assert draft["order_id"] == mail[0]
        assert draft["internet_message_id"] == "<fixture@example.com>"
        assert draft["intake_id"] is not None
        arrival = next(item for item in activity["feed"] if item["kind"] == "read")
        assert arrival["receipt_id"] == draft["receipt_id"]
        assert (
            arrival["preparation"]
            == s.get(MailboxReceipt, mail[3]).data["automatic_reply"]
        )
        preparation = replies.order_preparation(s, get(s, Order, mail[0]))
        assert preparation["outlook_draft"]["status"] == "created"
        assert preparation["internet_message_id"] == draft["internet_message_id"]
