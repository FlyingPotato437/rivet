"""The coordination agent inspects original equipment data, not resolved claims."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.domain.service import get
from backend.equipment import service as equipment
from backend.equipment.demo_documents import build_demo_corpus
from backend.ingestion import parser
from backend.orders import service as orders
from backend.records import agent_service as agent
from backend.records import service as records
from backend.storage.db import Session
from backend.storage.models import (
    Document,
    Order,
    OrderEvent,
    OrderRevision,
    RecordAgentProposal,
)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("equipment-agent-originals")
    manifest = build_demo_corpus(root)
    return {
        item["id"]: {**item, "file": root / item["path"]}
        for item in manifest["sources"]
    }


@pytest.fixture
def prepared(corpus):
    with Session.begin() as session:
        order = orders.new_order(
            session,
            {
                "title": "Fictional closed-comment verification",
                "customer": "Equipment test",
                "synthetic": True,
            },
        )
        for key in [
            "01-baseline-C.drawing.pdf",
            "01-baseline-C.bom.xlsx",
            "02-partial-D.drawing.pdf",
            "02-partial-D.bom.xlsx",
            "requirements.pdf",
            "response.markup",
        ]:
            item = corpus[key]
            blob, checksum = parser.persist_blob(
                item["file"].read_bytes(), Path(item["name"]).suffix
            )
            document = Document(
                project_id=order.project_id,
                name=item["name"],
                kind=item["role"],
                blob=blob,
                sha256=checksum,
                meta={
                    "order_role": item["role"],
                    "revision_label": item["revision"],
                    "equipment_package": {
                        "role": item["role"],
                        "revision": item["revision"],
                    },
                },
            )
            session.add(document)
            session.flush()
            parser.parse(session, document)
        session.flush()
        orders.refresh(session, order)
        equipment.save_check(session, order, "C", "D")
        record = records.ensure(session, order)
        # A human can have marked a comment addressed; the independently
        # supplied actual revised BOM still contains TU-2500.
        data = deepcopy(record.data)
        for comment in data.get("comments", []):
            comment.update(
                status="closed",
                reviewed=True,
                response="All comments addressed. Drawing and BOM corrected.",
            )
        record.data = data
        oid = order.id
    return oid


def inspection(session, order, *, offset=0):
    w = records.view(session, order)
    # Include the explicit false completion claim even when the original PDF
    # parser correctly treats a cover response as context rather than a row.
    w["comments"] = [
        *w.get("comments", []),
        {
            "id": "fixed-cover-note",
            "number": "1",
            "text": "All comments addressed. Drawing and BOM corrected.",
            "status": "closed",
            "reviewed": True,
        },
    ]
    run = SimpleNamespace(state={"read_source_ids": []})
    result, read_ids, label = agent.execute_tool(
        session, order, run, w, "inspect_equipment_revision", {"offset": offset}
    )
    return result, read_ids, label, w, run


def test_agent_observes_actual_stale_bom_despite_closed_comment(prepared):
    with Session() as session:
        result, ids, _, _, _ = inspection(session, get(session, Order, prepared))
        trip = next(
            check
            for check in result["checks"]
            if check["device_tag"] == "CB-12" and check["field"] == "trip_unit"
        )
        assert trip["status"] == "mismatch"
        assert trip["target_drawing"]["value"] == "TU-3000"
        assert trip["target_bom"]["value"] == "TU-2500"
        assert result["summary"]["remaining"] == 2
        assert result["summary"]["revision_changes_verified"] is False
        assert result["coverage"]["complete_inventory_verified"] is False
        assert ids == []
        assert result["engineering_approval"] is False
        assert result["production_release"] is False


def test_inspection_does_not_mutate_order_record_or_stage_work(prepared):
    with Session.begin() as session:
        order = get(session, Order, prepared)
        record = records.ensure(session, order)
        before = (
            order.version,
            deepcopy(order.state),
            order.status,
            record.version,
            deepcopy(record.data),
            session.query(OrderEvent).count(),
            session.query(OrderRevision).count(),
            session.query(RecordAgentProposal).count(),
        )
        result, _, _, _, run = inspection(session, order)
        session.flush()
        after = (
            order.version,
            deepcopy(order.state),
            order.status,
            record.version,
            deepcopy(record.data),
            session.query(OrderEvent).count(),
            session.query(OrderRevision).count(),
            session.query(RecordAgentProposal).count(),
        )
        assert before == after
        assert result["read_only"] is True
        assert run.state == {"read_source_ids": []}


def test_citation_pointers_are_existing_span_ids_not_document_or_equipment_ids(
    prepared,
):
    with Session() as session:
        order = get(session, Order, prepared)
        result, ids, _, w, run = inspection(session, order)
        source_lookup = {item["id"]: item for item in w["sources"]}
        document_ids = {item["id"] for item in w["documents"]}
        observation_ids = {
            item["equipment_observation_id"] for item in result["checks"]
        }
        assert result["citation_source_ids"]
        assert set(result["citation_source_ids"]) <= source_lookup.keys()
        assert not set(result["citation_source_ids"]) & document_ids
        assert not set(result["citation_source_ids"]) & observation_ids
        assert ids == []  # Inspection points to anchors; it never marks them read.
        for check in result["checks"]:
            for role in (
                "baseline_drawing",
                "baseline_bom",
                "target_drawing",
                "target_bom",
                "required",
            ):
                for original in check[role]["evidence"]:
                    for sid in original["source_ids_to_read"]:
                        assert (
                            source_lookup[sid]["document_id"] == original["document_id"]
                        )
        trip = next(
            check
            for check in result["checks"]
            if check["device_tag"] == "CB-12" and check["field"] == "trip_unit"
        )
        bom_anchor = trip["target_bom"]["evidence"][0]["source_ids_to_read"][0]
        read, span_ids, _ = agent.execute_tool(
            session, order, run, w, "read_source", {"source_id": bom_anchor}
        )
        assert "TU-2500" in read["text"]
        assert span_ids == [bom_anchor]
        assert "path" not in json.dumps(result).casefold()


def test_bounded_pagination_preserves_all_actual_checks(prepared):
    with Session() as session:
        order = get(session, Order, prepared)
        expected = equipment.view(session, order)["comparison"]["checks"]
        combined, offset = [], 0
        while True:
            result, _, _, _, _ = inspection(session, order, offset=offset)
            assert len(result["checks"]) <= 40
            assert len(json.dumps(result)) < 36000
            combined.extend(
                item["equipment_observation_id"] for item in result["checks"]
            )
            if result["next_offset"] is None:
                break
            assert result["next_offset"] > offset
            offset = result["next_offset"]
        assert len(combined) == len(set(combined)) == len(expected)
        assert set(combined) == {item["id"] for item in expected}


def test_tool_and_saved_hint_are_visible_without_an_extra_model_call(
    prepared, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with Session.begin() as session:
        order = get(session, Order, prepared)
        run = agent.create_run(session, order, trigger_key="equipment-agent-hint")
        w = records.view(session, order)
        context = agent.summary_context(w, run.state["equipment_check_hint"])
        hint = context["equipment_revision"]["saved_check_hint"]
        assert hint["summary"]["remaining"] == 2
        assert hint["fingerprint"] == order.state["equipment_check"]["fingerprint"]
        assert context["equipment_revision"]["hint_is_not_current_verification"] is True
        assert run.usage["model_calls"] == 0
        assert "inspect_equipment_revision" in agent.READ_TOOLS
        schema = next(
            tool for tool in agent.TOOLS if tool["name"] == "inspect_equipment_revision"
        )
        assert schema["parameters"]["properties"]["offset"]["minimum"] == 0
        assert "inspect_equipment_revision" in agent.INSTRUCTIONS
        assert "not source span IDs" in agent.INSTRUCTIONS
        assert "inspect_equipment_revision" in {
            tool["name"]
            for tool in agent._available_tools(
                {"state": {"phase": "investigate"}, "steps": []}
            )
        }
        # Finalization reserves the remaining budget for saving the result;
        # equipment investigation must finish before this write-only phase.
        assert "inspect_equipment_revision" not in {
            tool["name"]
            for tool in agent._available_tools({"state": {"phase": "finalize"}})
        }
