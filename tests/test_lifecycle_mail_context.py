"""The Outlook pane can decide only current work tied to its captured email."""

from uuid import uuid4

import pytest
from test_commercial_mail import CATALOG, incoming, prepare_worker
from test_lifecycle import fixture as _fixture

from backend.domain.service import digest
from backend.identity import LOCAL_ORG, Identity, identity_scope
from backend.lifecycle import service
from backend.lifecycle.mail_context import selected_message
from backend.lifecycle.schemas import PolicyWrite
from backend.mailbox.models import MailboxReceipt
from backend.storage.db import Session
from backend.storage.models import Document, Order, RecordIntake, Span

fixture = _fixture


@pytest.fixture(autouse=True)
def no_external_model(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def context_fixture(fixture, tmp_path):
    oid, source = fixture
    docid, sid = source(CATALOG, kind="erp_price")
    rid = incoming(oid, tmp_path)
    quote = next(p for p in prepare_worker(oid) if p["operation"] == "quote")
    with Session() as s:
        message_id = s.get(MailboxReceipt, rid).internet_message_id
    return oid, rid, message_id, quote, docid, sid


def test_selected_email_endpoint_owns_evidence_and_is_not_cached(
    fixture, tmp_path, client
):
    oid, rid, message_id, quote, _, _ = context_fixture(fixture, tmp_path)
    response = client.get(
        "/api/reply-assistant/lifecycle", params={"internet_message_id": message_id}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    view = response.json()
    assert view["status"] == "matched" and view["receipt_id"] == rid
    assert view["order_id"] == oid and view["packages"][0]["id"] == quote["id"]
    assert view["packages"][0]["allowed_gates"] == quote["gates"]
    with Session() as s:
        assert view["version"] == s.get(Order, oid).version
        for evidence in view["sources"]:
            span = s.get(Span, evidence["id"])
            doc = s.get(Document, span.document_id)
            assert evidence["hash"] == digest(
                [doc.sha256, doc.meta, span.text, span.location]
            )
            assert evidence["document_sha256"] == doc.sha256
            assert evidence["current"]


def test_other_tenant_or_unknown_email_exposes_no_order(fixture, tmp_path):
    _, _, message_id, _, _, _ = context_fixture(fixture, tmp_path)
    with identity_scope(Identity(str(uuid4()), "Other administrator")), Session() as s:
        result = selected_message(s, message_id)
        assert result["status"] == "not_captured" and not result["packages"]
        assert result["order_id"] is None and not result["sources"]
    with Session() as s:
        result = selected_message(s, "<not-a-captured-message@example.com>")
        assert result["order_id"] is None and result["receipt_id"] is None


def test_only_named_gate_approver_can_decide(fixture, tmp_path):
    _, _, message_id, quote, _, _ = context_fixture(fixture, tmp_path)
    with Session.begin() as s:
        service.set_policy(
            s,
            PolicyWrite(
                level=1,
                reason="Allow a named sales reviewer only.",
                gate_approvers={"money": ["Sales reviewer"]},
            ),
        )
    for actor, expected in [
        ("Unassigned colleague", []),
        ("Sales reviewer", ["money"]),
    ]:
        with identity_scope(Identity(LOCAL_ORG, actor, "org:member")), Session() as s:
            result = selected_message(s, message_id)
            assert result["packages"][0]["id"] == quote["id"]
            assert result["packages"][0]["allowed_gates"] == expected


@pytest.mark.parametrize("changed", ["text", "document_hash", "processing_state"])
def test_changed_evidence_disables_gate_and_pane_remains_readable(
    fixture, tmp_path, changed
):
    _, _, message_id, _, docid, sid = context_fixture(fixture, tmp_path)
    with Session.begin() as s:
        if changed == "text":
            s.get(Span, sid).text += "\nCorrected price requires review."
        elif changed == "document_hash":
            s.get(Document, docid).sha256 = "e" * 64
        else:
            s.get(Document, docid).state = "failed"
    with Session() as s:
        result = selected_message(s, message_id)
        assert result["packages"][0]["status"] == "stale"
        assert result["packages"][0]["allowed_gates"] == []
        assert (
            next(row for row in result["sources"] if row["id"] == sid)["current"]
            is False
        )


def test_pending_or_misbound_intake_cannot_expose_package(fixture, tmp_path):
    _, rid, message_id, _, _, _ = context_fixture(fixture, tmp_path)
    with Session.begin() as s:
        mail = s.get(MailboxReceipt, rid)
        intake = s.get(RecordIntake, mail.intake_id)
        intake.status = "needs_confirmation"
    with Session() as s:
        result = selected_message(s, message_id)
        assert not result["packages"] and not result["sources"]


def test_handoff_on_another_thread_is_not_shown_as_selected_native_draft(
    fixture, tmp_path, monkeypatch
):
    _, _, message_id, quote, _, _ = context_fixture(fixture, tmp_path)
    from backend.lifecycle import outlook_handoff

    monkeypatch.setattr(
        outlook_handoff,
        "statuses_for_order",
        lambda *_: {
            quote["id"]: {
                "status": "created",
                "receipt_id": str(uuid4()),
                "outlook_url": "https://outlook.office.com/mail/other-thread",
            }
        },
    )
    with Session() as s:
        result = selected_message(s, message_id)
        assert result["packages"][0]["outlook_handoff"] == {"status": "other_thread"}


def test_same_order_unrelated_captured_message_does_not_show_quote(fixture, tmp_path):
    oid, _, _, _, _, _ = context_fixture(fixture, tmp_path)
    rid = incoming(
        oid, tmp_path, rfq=["A completely different question about project status."]
    )
    with Session() as s:
        mail = s.get(MailboxReceipt, rid)
        result = selected_message(s, mail.internet_message_id)
        assert result["status"] == "matched" and not result["packages"]
        assert not result["sources"]


def test_outlook_decision_mutation_revalidates_email_package_and_version(
    fixture, tmp_path, client
):
    oid, rid, message_id, quote, _, sid = context_fixture(fixture, tmp_path)
    with Session() as s:
        version = s.get(Order, oid).version
    body = {
        "expected_version": version,
        "internet_message_id": message_id,
        "receipt_id": rid,
        "package_fingerprint": quote["fingerprint"],
        "gate": "money",
        "decision": "approve",
        "reason": "Checked this fictional quote against its exact price source.",
        "source_ids": [sid],
    }
    endpoint = f"/api/reply-assistant/lifecycle/decisions/{quote['id']}"
    for key, value in [
        ("internet_message_id", "<another-email@example.com>"),
        ("package_fingerprint", "f" * 64),
        ("expected_version", version - 1),
        ("source_ids", [str(uuid4())]),
    ]:
        result = client.post(
            endpoint,
            json={**body, key: value},
            headers={"Idempotency-Key": "wrong-" + key},
        )
        assert result.status_code == 409, result.text
    approved = client.post(
        endpoint, json=body, headers={"Idempotency-Key": "exact-selected-decision"}
    )
    assert approved.status_code == 200, approved.text
    current = next(p for p in approved.json()["packages"] if p["id"] == quote["id"])
    assert current["pending_gates"] == ["scope", "customer_commitment"]
    repeated = client.post(
        endpoint, json=body, headers={"Idempotency-Key": "exact-selected-decision"}
    )
    assert repeated.status_code == 200 and repeated.json() == approved.json()
    with Session.begin() as s:
        intake = s.get(RecordIntake, s.get(MailboxReceipt, rid).intake_id)
        intake.order_id = None
    refused = client.post(
        endpoint,
        json={**body, "gate": "scope", "expected_version": approved.json()["version"]},
        headers={"Idempotency-Key": "email-detached"},
    )
    assert refused.status_code == 409
