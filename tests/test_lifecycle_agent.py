"""Lifecycle preparation through scheduled agent jobs, not replay-only calls."""

import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_lifecycle import fixture as _lifecycle_fixture
from test_lifecycle import prepare

from backend.lifecycle import service as lifecycle
from backend.orders.service import lock_order
from backend.records import agent_service as agent
from backend.records import lifecycle_preparation as bridge
from backend.records import service as records
from backend.storage.db import Session
from backend.storage.models import NoticeDelivery, Order, RecordAgentRun
from backend.worker import claim, process

fixture = _lifecycle_fixture

QUOTE = {
    "tag": "CB-12",
    "description": "Recorded equipment package",
    "quantity": "2",
    "unit_cost": "1200.00",
    "gross_margin_percent": "25",
    "currency": "USD",
    "exceptions": ["Delivery date remains subject to written confirmation."],
}


def enable(order_id):
    with Session.begin() as s:
        order = lock_order(s, order_id)
        record = records.ensure(s, order)
        record.data = {**record.data, "agent_settings": {"enabled": True}}


def test_event_worker_prepares_quote_then_po_without_manual_lifecycle_call(
    fixture, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    source(QUOTE, kind="erp_price")
    source(
        {
            "tag": "CB-12",
            "quantity": "2",
            "unit_price": "1600.00",
            "purchase_order": "PO-442",
        },
        kind="purchase_order",
    )
    enable(oid)
    assert agent.scheduled_pass() == 1
    task = claim(kinds=("record_agent",))
    assert task and task[2] == "record_agent"
    process(task)
    with Session() as s:
        order = s.get(Order, oid)
        packages = lifecycle.view(s, order)["packages"]
        assert {p["operation"] for p in packages} == {"quote", "kickoff"}, s.scalar(
            select(RecordAgentRun)
        ).steps
        quote = next(p for p in packages if p["operation"] == "quote")
        kickoff = next(p for p in packages if p["operation"] == "kickoff")
        assert quote["payload"]["total"] == "3200.00"
        assert quote["payload"]["exceptions"][0]["text"] == QUOTE["exceptions"][0]
        assert quote["status"] == "awaiting_decision" and not quote["decisions"]
        assert kickoff["status"] == "blocked"
        assert "Sales has not approved" in " ".join(kickoff["blockers"])
        run = s.scalar(select(RecordAgentRun))
        assert [step["name"] for step in run.steps].count("prepare_lifecycle_work") == 2
        assert "Read structured" in run.steps[1]["label"]
        assert not list(s.scalars(select(NoticeDelivery)))
    assert agent.scheduled_pass() == 0
    # An unrelated file must not replace the prepared quote or create duplicates.
    source("General correspondence only.", kind="email")
    assert agent.scheduled_pass() == 1
    process(claim(kinds=("record_agent",)))
    with Session() as s:
        assert len(lifecycle.view(s, s.get(Order, oid))["packages"]) == 2


@pytest.mark.parametrize(
    "missing", ["quantity", "unit_cost", "gross_margin_percent", "exceptions"]
)
def test_incomplete_structured_quote_is_not_automatically_invented(
    fixture, monkeypatch, missing
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    source({k: v for k, v in QUOTE.items() if k != missing}, kind="erp_price")
    enable(oid)
    agent.scheduled_pass()
    process(claim(kinds=("record_agent",)))
    with Session() as s:
        assert not lifecycle.view(s, s.get(Order, oid))["packages"]


def test_model_tool_is_available_and_saves_a_logged_package(fixture, monkeypatch):
    oid, source = fixture
    _, sid = source(QUOTE, kind="erp_price")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-real")
    with Session.begin() as s:
        order = lock_order(s, oid)
        run = agent.create_run(s, order)
        run.origin = "user"
        rid = run.id
    calls = []
    payload = {
        "operation": "quote",
        "currency": "USD",
        "lines": [
            {
                k: QUOTE[k]
                for k in (
                    "tag",
                    "description",
                    "quantity",
                    "unit_cost",
                    "gross_margin_percent",
                )
            }
            | {"source_ids": [sid]}
        ],
        "exceptions": [],
    }

    def provider(run, w):
        calls.append(1)
        assert "prepare_lifecycle_work" in {
            item["name"] for item in agent.available_tools(run, w)
        }
        if len(calls) == 1:
            return "read_lifecycle_context", {}, {"total_tokens": 100}
        if len(calls) == 2:
            return (
                "prepare_lifecycle_work",
                {"payload_json": json.dumps(payload)},
                {"total_tokens": 100},
            )
        return (
            "complete",
            {
                "summary": "Prepared the quotation for commercial review.",
                "source_ids": [sid],
            },
            {"total_tokens": 100},
        )

    monkeypatch.setattr(agent, "provider_call", provider)
    agent.process_run(rid)
    with Session() as s:
        run = s.get(RecordAgentRun, rid)
        assert run.status == "completed", (run.result, run.steps)
        assert run.result["lifecycle_preparations"][0]["status"] == "awaiting_decision"
        assert any(
            step["name"] == "prepare_lifecycle_work" and step["status"] == "completed"
            for step in run.steps
        )
        assert not lifecycle.view(s, s.get(Order, oid))["packages"][0]["decisions"]


def bridge_fixture(fixture):
    oid, source = fixture
    _, sid = source(QUOTE, kind="erp_price")
    payload = {
        "operation": "quote",
        "currency": "USD",
        "lines": [
            {
                k: QUOTE[k]
                for k in (
                    "tag",
                    "description",
                    "quantity",
                    "unit_cost",
                    "gross_margin_percent",
                )
            }
            | {"source_ids": [sid]}
        ],
    }
    return oid, sid, payload


def test_tool_rejects_unread_sources_and_invented_quantity(fixture, monkeypatch):
    oid, sid, payload = bridge_fixture(fixture)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with Session.begin() as s:
        order = lock_order(s, oid)
        run = agent.create_run(s, order)
        w = records.view(s, order)
        with pytest.raises(HTTPException, match="Read every cited source"):
            bridge.prepare(s, order, run, w, payload, read_source_ids=[])
        payload["lines"][0]["quantity"] = "7"
        with pytest.raises(HTTPException, match="quantity requires"):
            bridge.prepare(s, order, run, w, payload, read_source_ids=[sid])
        assert not lifecycle.view(s, order)["packages"]


@pytest.mark.parametrize("operation", ["approve", "send_email"])
def test_agent_has_no_release_approval_or_sending_capability(fixture, operation):
    oid, _, _ = bridge_fixture(fixture)
    with Session.begin() as s:
        order = lock_order(s, oid)
        run = agent.create_run(s, order)
        with pytest.raises(HTTPException) as error:
            bridge.prepare(
                s,
                order,
                run,
                records.view(s, order),
                {"operation": operation},
                read_source_ids=[],
            )
        assert error.value.status_code == 403


@pytest.mark.parametrize("operation", ["revision_check", "release", "closeout"])
def test_late_stage_preparation_requires_actual_prerequisites(fixture, operation):
    oid, _, _ = bridge_fixture(fixture)
    with Session.begin() as s:
        order = lock_order(s, oid)
        run = agent.create_run(s, order)
        inspected = bridge.inspect(s, order, records.view(s, order))
        assert operation in inspected["schemas"]
        with pytest.raises(HTTPException) as error:
            bridge.prepare(
                s,
                order,
                run,
                records.view(s, order),
                {"operation": operation},
                read_source_ids=[],
            )
        assert error.value.status_code == 422
        assert not lifecycle.view(s, order)["packages"]


def test_current_revision_answers_are_visible_and_not_replaced_with_questions(
    fixture, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    _, sid = source(
        "Specification 4.1: Cable entry is at the top.", kind="specification"
    )
    with Session.begin() as s:
        order = lock_order(s, oid)
        record = records.ensure(s, order)
        record.data = {
            **record.data,
            "comments": [
                {
                    "id": "comment-1",
                    "number": "1",
                    "revision": "C",
                    "text": "Confirm cable entry.",
                    "reviewed": False,
                    "source_ids": [sid],
                    "status": "open",
                }
            ],
        }
    prepare(
        oid,
        operation="revision_review",
        revision="C",
        comments=[
            {
                "comment_id": "comment-1",
                "kind": "answer",
                "source_ids": [sid],
                "response": "Specification 4.1 records top cable entry.",
            }
        ],
    )
    enable(oid)
    agent.scheduled_pass()
    process(claim(kinds=("record_agent",)))
    with Session.begin() as s:
        order = lock_order(s, oid)
        w = records.view(s, order)
        run = s.scalar(select(RecordAgentRun))
        context = bridge.inspect(s, order, w)
        assert context["existing_revision_work"][0]["response"].startswith(
            "Specification 4.1"
        )
        assert run.result["existing_lifecycle_work"]
        assert "Current prepared work retained" in run.result["summary"]
        assert not agent.proposals_for(s, oid, run.id)
        result = agent.stage(
            s,
            order,
            run,
            w,
            [
                {
                    "type": "draft_clarification",
                    "comment_id": "comment-1",
                    "text": "Please confirm the cable entry.",
                    "evidence_ids": [sid],
                    "title": "Clarify",
                }
            ],
        )
        assert result["staged"] == 0
        assert not agent.proposals_for(s, oid, run.id)


def test_customer_uploaded_json_cannot_authorize_prices(fixture, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    source(QUOTE, kind="quote")
    enable(oid)
    agent.scheduled_pass()
    process(claim(kinds=("record_agent",)))
    with Session() as s:
        assert not lifecycle.view(s, s.get(Order, oid))["packages"]


def test_lifecycle_change_after_inspection_rejects_preparation(fixture):
    oid, sid, payload = bridge_fixture(fixture)
    with Session.begin() as s:
        order = lock_order(s, oid)
        run = agent.create_run(s, order)
        run.state = {**run.state, "lifecycle_order_version": order.version - 1}
        with pytest.raises(HTTPException, match="changed after inspection"):
            bridge.prepare(
                s, order, run, records.view(s, order), payload, read_source_ids=[sid]
            )


def test_automatic_open_questions_are_one_cited_draft(fixture, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    _, sid = source(
        "Returned questions: clarify red item and identify governing drawing.",
        kind="markup",
    )
    with Session.begin() as s:
        order = lock_order(s, oid)
        record = records.ensure(s, order)
        record.data = {
            **record.data,
            "comments": [
                {
                    "id": f"c{i}",
                    "number": str(i),
                    "revision": "C",
                    "text": question,
                    "reviewed": False,
                    "source_ids": [sid],
                    "status": "open",
                }
                for i, question in enumerate(
                    ["Identify red item.", "Which drawing governs?"], 1
                )
            ],
        }
        run = agent.create_run(s, order)
        w = records.view(s, order)
        result = agent.stage(
            s,
            order,
            run,
            w,
            [
                {
                    "type": "draft_clarification",
                    "comment_id": f"c{i}",
                    "text": question,
                    "title": "Clarification",
                    "evidence_ids": [sid],
                }
                for i, question in enumerate(
                    [
                        "Please identify the red item.",
                        "Please confirm the governing drawing.",
                    ],
                    1,
                )
            ],
        )
        assert result["staged"] == 1
        proposal = agent.proposals_for(s, oid, run.id)[0]
        assert proposal.operations[0]["type"] == "draft_summary"
        assert "red item" in proposal.operations[0]["text"]
        assert "governing drawing" in proposal.operations[0]["text"]
        assert proposal.evidence_ids == [sid]
