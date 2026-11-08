"""Approved commercial PDFs use the same never-send native Outlook mechanism."""

from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_autopilot import FakeMailbox
from test_lifecycle import decision, prepare
from test_lifecycle import fixture as _lifecycle_fixture
from test_reply_assistant import receipt

from backend.autopilot import service as autopilot
from backend.lifecycle import adapters
from backend.lifecycle import outlook_handoff as handoff
from backend.lifecycle import service as lifecycle
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.orders.service import lock_order
from backend.records import service as records
from backend.reply_assistant import automatic
from backend.reply_assistant import service as replies
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session
from backend.storage.models import Document, Order, RecordIntake, Span, now

fixture = _lifecycle_fixture


def ready(fixture, *, link=False):
    oid, source = fixture
    doc_id, sid = source(
        {
            "tag": "CB-12",
            "quantity": "2",
            "unit_cost": "1200.00",
            "gross_margin_percent": "25",
            "currency": "USD",
        },
        kind="erp_price",
    )
    with Session.begin() as s:
        order = s.get(Order, oid)
        root = Document(
            project_id=order.project_id,
            name="request.eml",
            kind="email",
            blob="request.eml",
            sha256="a" * 64,
            state="ready",
            meta={"intake_order_id": oid},
        )
        s.add(root)
        s.flush()
        s.add(
            Span(
                document_id=root.id,
                text="Please send a proposal for CB-12.",
                location={"page": 1},
            )
        )
        mail = receipt(s, oid, root.id)
        mail.status = "imported"
        mail.data = {
            **mail.data,
            "conversation_id": "customer-thread",
            "received_at": now().isoformat(),
        }
        s.get(RecordIntake, mail.intake_id).status = "matched"
        if link:
            doc = s.get(Document, doc_id)
            doc.meta = {**doc.meta, "email_document_id": root.id}
        rid = mail.id
    result = prepare(
        oid,
        operation="quote",
        lines=[
            {
                "tag": "CB-12",
                "description": "Equipment package",
                "quantity": "2",
                "unit_cost": "1200.00",
                "gross_margin_percent": "25",
                "source_ids": [sid],
            }
        ],
    )
    return oid, result["packages"][0]["id"], rid, sid


def approve(values):
    oid, pid, _, sid = values
    for gate in ("money", "scope", "customer_commitment"):
        decision(oid, pid, gate, sid)


def queue(values):
    oid, pid, rid, _ = values
    with Session.begin() as s:
        return handoff.enqueue(s, lock_order(s, oid), pid, rid)


def test_quote_requires_all_gates_and_becomes_one_unsent_native_pdf_reply(fixture):
    values = ready(fixture)
    with pytest.raises(HTTPException, match="Complete the current"):
        queue(values)
    decision(values[0], values[1], "money", values[3])
    with pytest.raises(HTTPException, match="Complete the current"):
        queue(values)
    for gate in ("scope", "customer_commitment"):
        decision(values[0], values[1], gate, values[3])
    queued = queue(values)
    assert queue(values)["reply_id"] == queued["reply_id"]
    graph = FakeMailbox()
    result = autopilot.deliver(client_factory=graph)[0]
    assert result["status"] == "created"
    assert graph.draft()["isDraft"] is True
    assert graph.draft()["attachments"] == ["quote.pdf"]
    assert "DRAFT PROPOSAL" in graph.draft()["body"]["content"]
    assert "3200.00" in graph.draft()["body"]["content"]
    assert "1200.00" not in graph.draft()["body"]["content"]
    assert not autopilot.deliver(client_factory=graph)
    assert sum(path.endswith("createReply") for _, path in graph.calls) == 1
    with Session() as s:
        assert len(list(s.scalars(select(ReplyDraft)))) == 1
        projection = handoff.statuses_for_order(s, s.get(Order, values[0]))[values[1]]
        assert projection["status"] == "created"
        assert projection["draft"]["attachments"][0]["sha256"]


def test_human_reply_in_thread_is_preserved(fixture):
    values = ready(fixture)
    approve(values)
    queue(values)
    graph = FakeMailbox()
    with Session() as s:
        original_id = s.get(MailboxReceipt, values[2]).provider_message_id
    graph.messages[original_id] = {
        "id": original_id,
        "conversationId": "customer-thread",
    }
    original = {
        "id": "human-draft",
        "isDraft": True,
        "conversationId": "customer-thread",
        "parentFolderId": "folder-drafts",
        "body": {"content": "My commercial wording"},
    }
    graph.messages["human-draft"] = dict(original)
    assert autopilot.deliver(client_factory=graph)[0]["status"] == "existing_draft"
    assert graph.messages["human-draft"] == original
    assert all(method == "GET" for method, _ in graph.calls)


@pytest.mark.parametrize(
    "changed",
    [
        "source",
        "artifact",
        "assignment",
        "mailbox",
        "newer_email",
        "approval",
        "tenant",
    ],
)
def test_stale_handoff_never_creates_a_draft(fixture, changed):
    values = ready(fixture)
    approve(values)
    queue(values)
    oid, pid, rid, sid = values
    with Session.begin() as s:
        order = s.get(Order, oid)
        mail = s.get(MailboxReceipt, rid)
        if changed == "source":
            s.get(Span, sid).text += " changed"
        elif changed == "artifact":
            (adapters.root(order.organization_id, oid) / pid / "quote.pdf").write_bytes(
                b"%PDF-tampered"
            )
        elif changed == "assignment":
            s.get(RecordIntake, mail.intake_id).order_id = None
        elif changed == "mailbox":
            s.get(MailboxConnection, mail.connection_id).enabled = False
        elif changed == "tenant":
            s.get(MailboxConnection, mail.connection_id).microsoft_tenant_id = str(
                uuid4()
            )
        elif changed == "approval":
            from copy import deepcopy

            state = deepcopy(order.state)
            state["lifecycle"]["packages"][0]["decisions"][0]["fingerprint"] = "changed"
            order.state = state
        else:
            incoming = MailboxReceipt(
                connection_id=mail.connection_id,
                provider_message_id=str(uuid4()),
                internet_message_id="<new@example.com>",
                status="imported",
                data={
                    "conversation_id": "customer-thread",
                    "received_at": now().isoformat(),
                },
            )
            s.add(incoming)
    graph = FakeMailbox()
    result = autopilot.deliver(client_factory=graph)
    assert result and result[0]["status"] != "created"
    assert not graph.calls


def test_missing_required_pdf_does_not_claim_complete_or_retry_duplicate(fixture):
    values = ready(fixture)
    approve(values)
    queue(values)
    graph = FakeMailbox()
    original = graph.handler

    def failure(request):
        if request.method == "POST" and request.url.path.endswith("/attachments"):
            return httpx.Response(503, json={"error": {"code": "ServiceUnavailable"}})
        return original(request)

    graph.handler = failure
    result = autopilot.deliver(client_factory=graph)[0]
    assert result["status"] == "failed"
    assert "required PDF" in result["error"]
    assert graph.draft()["isDraft"] is True
    assert not autopilot.deliver(client_factory=graph)
    assert sum(path.endswith("createReply") for _, path in graph.calls) == 1


def test_final_approval_queues_only_an_unambiguous_provenance_linked_thread(fixture):
    values = ready(fixture, link=True)
    approve(values)
    with Session.begin() as s:
        # The last decision itself queued this, before any explicit handoff call.
        assert len(list(s.scalars(select(ReplyDraft)))) == 1
        order = lock_order(s, values[0])
        package = lifecycle.view(s, order)["packages"][0]
        result = handoff.enqueue_if_unambiguous(s, order, package)
        assert result["status"] == "queued"
        assert len(list(s.scalars(select(ReplyDraft)))) == 1
        original = s.get(MailboxReceipt, values[2])
        intake = s.get(RecordIntake, original.intake_id)
        another = receipt(
            s, order.id, intake.data["document_id"], "<another-thread@example.com>"
        )
        another.status = "imported"
        s.get(RecordIntake, another.intake_id).status = "matched"
        result = handoff.enqueue_if_unambiguous(s, order, package)
        assert result == {"status": "needs_thread", "candidate_count": 2}
        assert len(list(s.scalars(select(ReplyDraft)))) == 1


def revision_ready(fixture):
    values = ready(fixture)
    oid, _, rid, price = values
    _, source = fixture
    _, contract = source(
        {"billable_scope_tag": "CB-12", "billable": True}, kind="accepted_exception"
    )
    _, spec = source("Specification 4.1 requires top cable entry.")
    with Session.begin() as s:
        order = lock_order(s, oid)
        record = records.ensure(s, order)
        record.data = {
            **record.data,
            "comments": [
                {
                    "id": "c1",
                    "number": "1",
                    "revision": "C",
                    "status": "open",
                    "reviewed": False,
                    "text": "Confirm cable entry.",
                    "source_ids": [spec],
                }
            ],
        }
    result = prepare(
        oid,
        operation="revision_review",
        revision="C",
        comments=[
            {
                "comment_id": "c1",
                "kind": "answer",
                "response": "Specification 4.1 requires top cable entry.",
                "source_ids": [spec],
            }
        ],
        scope_additions=[
            {
                "tag": "CB-12",
                "description": "One additional equipment package",
                "baseline_quantity": "2",
                "requested_quantity": "3",
                "unit_cost": "1200.00",
                "gross_margin_percent": "25",
                "source_ids": [price],
                "billability": "contract_confirmed",
                "billability_source_ids": [contract],
            }
        ],
    )
    package = result["packages"][0]
    values = oid, package["id"], rid, spec
    with Session.begin() as s:
        mail = s.get(MailboxReceipt, rid)
        intake = s.get(RecordIntake, mail.intake_id)
        intake.data = {**intake.data, "revision_label": "C"}
    return values, package


def test_change_order_uses_only_customer_pdf_after_engineering_and_commercial_gates(
    fixture,
):
    values, package = revision_ready(fixture)
    oid, _, _, spec = values
    for gate in package["gates"]:
        decision(oid, package["id"], gate, spec)
    queue(values)
    graph = FakeMailbox()
    assert autopilot.deliver(client_factory=graph)[0]["status"] == "created"
    assert graph.draft()["attachments"] == ["response-letter.pdf", "change-order.pdf"]
    assert "USD 1600.00" in graph.draft()["body"]["content"]


def test_pending_revision_defers_generic_reply_then_approvals_queue_one_commercial_reply(
    fixture,
):
    values, package = revision_ready(fixture)
    oid, pid, rid, sid = values
    with Session.begin() as s:
        mail = s.get(MailboxReceipt, rid)
        state = automatic.reconcile_receipt(s, mail)
        assert state["status"] == "waiting_for_approval"
        assert state["package_ids"] == [pid]
        assert set(state["pending_gates"]) == set(package["gates"])
        assert not list(s.scalars(select(ReplyDraft)))
        prepared, projected = replies.prepared_for_receipt(s, s.get(Order, oid), mail)
        assert prepared is None and projected["status"] == "waiting_for_approval"
        feed = autopilot.activity(s)
        assert feed["needs_you"][0]["kind"] == "approval"
    graph = FakeMailbox()
    assert not autopilot.deliver(client_factory=graph)
    assert not graph.calls
    for gate in package["gates"]:
        decision(oid, pid, gate, sid)
    with Session.begin() as s:
        staged = automatic.reconcile_receipt(s, s.get(MailboxReceipt, rid))
        assert staged["status"] == "queued"
        saved = list(s.scalars(select(ReplyDraft)))
        assert len(saved) == 1
        assert saved[0].request_key.startswith("lifecycle-mail:")
    assert autopilot.deliver(client_factory=graph)[0]["status"] == "created"
    assert graph.draft()["attachments"] == ["response-letter.pdf", "change-order.pdf"]
    with Session.begin() as s:
        final = automatic.reconcile_receipt(s, s.get(MailboxReceipt, rid))
        assert final["status"] == "ready", final
        assert not autopilot.activity(s)["needs_you"]
        repeated = automatic.reconcile_receipt(s, s.get(MailboxReceipt, rid))
        assert repeated["status"] == "ready"
        assert len(list(s.scalars(select(ReplyDraft)))) == 1
    assert sum(path.endswith("/createReply") for _, path in graph.calls) == 1
    with Session.begin() as s:
        s.get(Span, sid).text += " changed after Outlook draft placement"
        stale = automatic.reconcile_receipt(s, s.get(MailboxReceipt, rid))
        assert stale["status"] == "blocked"
        assert stale["reason"] == "lifecycle_handoff_changed"


@pytest.mark.parametrize(
    "change",
    ["different_revision", "rejected", "superseded", "stale", "unknown_revision"],
)
def test_unrelated_or_inactive_commercial_work_does_not_hold_mail(fixture, change):
    from copy import deepcopy

    values, _ = revision_ready(fixture)
    with Session.begin() as s:
        order = s.get(Order, values[0])
        mail = s.get(MailboxReceipt, values[2])
        intake = s.get(RecordIntake, mail.intake_id)
        if change in {"different_revision", "unknown_revision"}:
            intake.data = {
                **intake.data,
                "revision_label": "D"
                if change == "different_revision"
                else "Unspecified",
            }
        elif change == "stale":
            s.get(Span, values[3]).text += " New requirement."
        else:
            state = deepcopy(order.state)
            next(p for p in state["lifecycle"]["packages"] if p["id"] == values[1])[
                "status"
            ] = change
            order.state = state
        assert handoff.commercial_work_for_receipt(s, order, mail) == []


@pytest.mark.parametrize("link", [False, True])
def test_pending_quote_only_holds_its_actual_source_linked_email(fixture, link):
    values = ready(fixture, link=link)
    with Session.begin() as s:
        order = s.get(Order, values[0])
        mail = s.get(MailboxReceipt, values[2])
        matched = handoff.commercial_work_for_receipt(s, order, mail)
        assert [p["id"] for p in matched] == ([values[1]] if link else [])


def test_ready_generic_reply_cannot_race_commercial_decisions_or_replace_native(
    fixture,
):
    from backend.autopilot.models import OutlookDraft

    values, package = revision_ready(fixture)
    oid, pid, rid, sid = values
    with Session.begin() as s:
        order, mail = s.get(Order, oid), s.get(MailboxReceipt, rid)
        focused = automatic.focus_documents(s, order, mail)
        evidence = replies.context(s, order, automatic.INSTRUCTION, focused)[-1]
        generic = ReplyDraft(
            order_id=oid,
            request_key="auto-mail:before-commercial",
            request_hash="old",
            internet_message_id=mail.internet_message_id,
            status="ready",
            evidence_hash=evidence,
            body="An ordinary response prepared before the commercial package.",
            context={
                "instruction": automatic.INSTRUCTION,
                "focus_document_ids": focused,
            },
        )
        s.add(generic)
        s.flush()
        generic_id = generic.id
    graph = FakeMailbox()
    deferred = autopilot.deliver_one(generic_id, client_factory=graph)
    assert deferred["status"] == "awaiting_approval"
    assert not graph.calls
    assert not autopilot._still_current(generic_id)
    for gate in package["gates"]:
        decision(oid, pid, gate, sid)
    # Approval does not release the obsolete generic reply ahead of the PDFs.
    assert (
        autopilot.deliver_one(generic_id, client_factory=graph)["status"]
        == "awaiting_approval"
    )
    assert autopilot.deliver(client_factory=graph)[0]["status"] == "created"
    native_body = graph.draft()["body"]["content"]
    assert (
        autopilot.deliver_one(generic_id, client_factory=graph)["status"]
        == "superseded"
    )
    assert graph.draft()["body"]["content"] == native_body
    assert sum(path.endswith("createReply") for _, path in graph.calls) == 1
    with Session() as s:
        placed = s.scalar(select(OutlookDraft).where(OutlookDraft.graph_draft_id != ""))
        assert placed.attempts == 1


def test_same_revision_multiple_threads_requires_explicit_selection(fixture):
    values, package = revision_ready(fixture)
    oid, pid, rid, sid = values
    with Session.begin() as s:
        first = s.get(MailboxReceipt, rid)
        intake = s.get(RecordIntake, first.intake_id)
        another = receipt(
            s, oid, intake.data["document_id"], "<another-revision-thread@example.com>"
        )
        another.status = "imported"
        other = s.get(RecordIntake, another.intake_id)
        other.status, other.data = "matched", {**other.data, "revision_label": "Rev C"}
    for gate in package["gates"]:
        decision(oid, pid, gate, sid)
    with Session.begin() as s:
        state = automatic.reconcile_receipt(s, s.get(MailboxReceipt, rid))
        assert state["status"] == "needs_thread"
        assert not list(s.scalars(select(ReplyDraft)))
    queue(values)
    graph = FakeMailbox()
    assert autopilot.deliver(client_factory=graph)[0]["status"] == "created"


def test_lost_body_response_preserves_draft_without_claiming_required_files(fixture):
    values = ready(fixture)
    approve(values)
    queue(values)
    graph = FakeMailbox()
    original = graph.handler

    def interrupted(request):
        response = original(request)
        if request.method == "PATCH" and "/draft-" in request.url.path:
            raise httpx.ReadTimeout("Lost response", request=request)
        return response

    graph.handler = interrupted
    assert autopilot.deliver(client_factory=graph)[0]["status"] == "failed"
    graph.handler = original
    from backend.autopilot.models import OutlookDraft

    with Session.begin() as s:
        row = s.scalar(select(OutlookDraft))
        row.next_attempt_at = now()
    retry = autopilot.deliver(client_factory=graph)[0]
    assert retry["status"] == "failed"
    assert "cannot confirm its required PDF" in retry["error"]
    assert sum(path.endswith("createReply") for _, path in graph.calls) == 1
    assert not graph.draft()["attachments"]


def test_explicit_endpoint_is_versioned_idempotent_and_rejects_recipient_injection(
    fixture, client
):
    values = ready(fixture)
    approve(values)
    with Session() as s:
        version = s.get(Order, values[0]).version
    url = f"/api/orders/{values[0]}/lifecycle/{values[1]}/outlook-draft"
    body = {"expected_version": version, "receipt_id": values[2]}
    headers = {"Idempotency-Key": "selected-thread"}
    assert (
        client.post(
            url, json={**body, "recipient": "wrong@example.com"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.post(
            url,
            json={**body, "expected_version": version - 1},
            headers={"Idempotency-Key": "old"},
        ).status_code
        == 409
    )
    first = client.post(url, json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert client.post(url, json=body, headers=headers).json() == first.json()
    with Session() as s:
        assert len(list(s.scalars(select(ReplyDraft)))) == 1
