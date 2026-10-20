"""Returned drawings produce current cited work without browser clicks or mail."""

from copy import deepcopy

import pytest
from sqlalchemy import select
from test_company_context import ORG_B, identity
from test_reply_assistant import fixture_order, output

from backend.agents import gateway
from backend.domain.service import get
from backend.reply_assistant import order_packet, service
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session
from backend.storage.models import (
    Document,
    Job,
    NoticeDelivery,
    Order,
    OrderRecord,
    Span,
)
from backend.worker import claim, process


@pytest.fixture
def returned(monkeypatch):
    monkeypatch.setattr(gateway, "configured", lambda: True)
    with identity():
        oid, did, sid = fixture_order()
        monkeypatch.setattr(service, "model_reply", lambda snapshot: output(sid))
    return oid, did, sid


def stage(returned):
    with identity(), Session.begin() as s:
        return order_packet.reconcile(s, get(s, Order, returned[0]))


def run():
    job = claim()
    assert job and job[2] == "reply_draft"
    process(job)


def test_document_response_is_automatic_deduplicated_and_visible(returned):
    first = stage(returned)
    assert first["status"] == "queued"
    assert stage(returned)["draft_id"] == first["draft_id"]
    run()
    with identity(), Session() as s:
        result = service.order_preparation(s, get(s, Order, returned[0]))
        assert result["delivery"] == "document"
        assert result["preparation"]["status"] == "ready"
        assert result["prepared_reply"]["body"] == output(returned[2])["body"]
        assert result["prepared_reply"]["prepared_automatically"]
        assert result["prepared_reply"]["source_refs"][0]["id"] == returned[2]
        draft = s.get(ReplyDraft, first["draft_id"])
        assert draft.context["focus_document_ids"] == [returned[1]]
        assert draft.internet_message_id == ""
        assert len(list(s.scalars(select(ReplyDraft)))) == 1
        assert len(list(s.scalars(select(Job)))) == 1
        assert not list(s.scalars(select(NoticeDelivery)))
    assert stage(returned)["draft_id"] == first["draft_id"]
    with identity(ORG_B), Session() as s:
        assert not list(
            s.scalars(
                order_packet.draft_query(type("OrderRef", (), {"id": returned[0]})())
            )
        )


def test_read_only_polling_does_not_start_work(returned):
    with identity(), Session() as s:
        for _ in range(3):
            service.order_preparation(s, get(s, Order, returned[0]))
        assert not list(s.scalars(select(ReplyDraft)))
        assert not list(s.scalars(select(Job)))


def test_reverted_evidence_reuses_the_matching_response_in_the_view(returned):
    first = stage(returned)
    run()
    with identity(), Session.begin() as s:
        source = get(s, Span, returned[2])
        original = source.text
        source.text += " Temporary additional requirement."
    second = stage(returned)
    run()
    assert second["draft_id"] != first["draft_id"]
    with identity(), Session.begin() as s:
        get(s, Span, returned[2]).text = original
    assert stage(returned)["draft_id"] == first["draft_id"]
    with identity(), Session() as s:
        result = service.order_preparation(s, get(s, Order, returned[0]))
        assert result["preparation"]["status"] == "ready"
        assert result["prepared_reply"]["id"] == first["draft_id"]
        assert len(list(s.scalars(select(ReplyDraft)))) == 2


def test_changed_evidence_invalidates_and_reprepares_without_overwriting(returned):
    first = stage(returned)
    run()
    with identity(), Session.begin() as s:
        original = s.get(ReplyDraft, first["draft_id"]).body
        source = get(s, Span, returned[2])
        source.text += " Also confirm the revised sheet E-11."
    with identity(), Session() as s:
        state = service.order_preparation(s, get(s, Order, returned[0]))
        assert state["preparation"]["status"] == "queued"
        assert state["prepared_reply"] is None
    next_result = stage(returned)
    assert next_result["draft_id"] != first["draft_id"]
    assert stage(returned)["draft_id"] == next_result["draft_id"]
    with Session() as s:
        assert s.get(ReplyDraft, first["draft_id"]).body == original


def test_opt_out_and_source_failure_do_not_create_false_ready_work(returned):
    with identity(), Session.begin() as s:
        order_packet.preparation(s, get(s, Order, returned[0]))
        record = s.scalar(
            select(OrderRecord).where(OrderRecord.order_id == returned[0])
        )
        data = deepcopy(record.data)
        data["agent_settings"] = {"enabled": False}
        record.data = data
    assert stage(returned)["status"] == "not_needed"
    with identity(), Session.begin() as s:
        record = s.scalar(
            select(OrderRecord).where(OrderRecord.order_id == returned[0])
        )
        record.data = {**record.data, "agent_settings": {"enabled": True}}
        get(s, Document, returned[1]).state = "failed"
    assert stage(returned)["status"] == "blocked"
    with Session() as s:
        assert not list(s.scalars(select(ReplyDraft)))


def test_failed_generation_is_retained_and_cannot_masquerade_as_finished(
    returned, monkeypatch
):
    monkeypatch.setattr(
        service,
        "model_reply",
        lambda snapshot: {**output(returned[2]), "source_ids": ["invented"]},
    )
    stage(returned)
    run()
    assert stage(returned)["status"] == "failed"
    with identity(), Session() as s:
        result = service.order_preparation(s, get(s, Order, returned[0]))
        assert result["prepared_reply"] is None
        assert result["preparation"]["status"] == "failed"
        assert len(list(s.scalars(select(ReplyDraft)))) == 1


def test_stale_result_with_failed_source_does_not_claim_work_is_running(returned):
    stage(returned)
    run()
    with identity(), Session.begin() as s:
        get(s, Document, returned[1]).state = "failed"
    with identity(), Session() as s:
        result = service.order_preparation(s, get(s, Order, returned[0]))
        assert result["prepared_reply"] is None
        assert result["preparation"]["status"] == "blocked"
