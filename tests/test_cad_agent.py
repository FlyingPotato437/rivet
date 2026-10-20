"""Returned email/PDF -> normal event-agent tools -> actual native draft copies."""

import json
from copy import deepcopy
from email.message import EmailMessage
from hashlib import sha256

import ezdxf
import pytest
from fastapi import HTTPException
from openpyxl import load_workbook
from sqlalchemy import select

from backend.domain.service import get
from backend.orders.service import lock_order, new_order
from backend.records import agent_service as agent
from backend.records import cad_preparation as cad
from backend.records import service as records
from backend.records.email_intake import import_email
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order, OrderEvent, RecordAgentRun, Span
from backend.worker import claim, process
from scripts.build_drafting_demo import make_sources
from scripts.make_order_replay import write_pdf

COMMENTS = [
    "Please change the trip unit on CB-12 to TU-3000-DEMO in the draft drawing.",
    "Please change the CB-12 nameplate to CB-12 / DATA HALL A.",
    "Please update BOM line 10 part_number to TU-3000-DEMO.",
    "Please move CB-12 lower in the compartment; a drafter must check clearances.",
]


def native_source(s, order, path, role):
    data = path.read_bytes()
    blob = path.name
    (db.BLOBS / blob).write_bytes(data)
    document = Document(
        project_id=order.project_id,
        name=path.name,
        kind=role,
        blob=blob,
        sha256=sha256(data).hexdigest(),
        state="ready",
        meta={
            "order_role": role,
            "revision_label": "C",
            "release_status": "draft",
            "drawing_number": "E-10",
        }
        if role == "drawing"
        else {"order_role": role, "revision_label": "C", "release_status": "draft"},
        coverage=[],
    )
    s.add(document)
    s.flush()
    span = Span(
        document_id=document.id,
        text="Registered native manufacturer source, exact bytes retained for controlled draft preparation.",
        location={"native_source": True},
    )
    s.add(span)
    s.flush()
    return document.id, span.id


@pytest.fixture
def incoming(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(db, "DATA", tmp_path / "work")
    drawing, bom, handle = make_sources(
        tmp_path / "manufacturer-vault", job_number="JOB-CAD-24-118"
    )
    returned = tmp_path / "E-10-Rev-C-returned.pdf"
    write_pdf(
        returned,
        "Job JOB-CAD-24-118 | E-10 | Revision C",
        ["Returned engineering comments"],
        [
            {"id": f"customer-{i}", "text": f"Comment {i}: {text}"}
            for i, text in enumerate(COMMENTS, 1)
        ],
    )
    with Session.begin() as s:
        order = new_order(
            s,
            {
                "title": "Data Hall A - native preparation",
                "customer": "Fictional data center",
                "number": "JOB-CAD-24-118",
                "synthetic": True,
            },
        )
        did, dsid = native_source(s, order, drawing, "drawing")
        bid, bsid = native_source(s, order, bom, "bom")
        message = EmailMessage()
        message["From"] = "reviewer@example.com"
        message["To"] = "pm@example.com"
        message["Subject"] = "Job JOB-CAD-24-118 | Rev C drawing comments"
        message["Message-ID"] = "<normal-native-comment-event@example.com>"
        message.set_content("Please review the attached drawing.")
        message.add_attachment(
            returned.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=returned.name,
        )
        result = import_email(s, order, "returned.eml", message.as_bytes(), "C")
        oid, email_document_id = order.id, result["document_id"]
    while task := claim(kinds=("parse",)):
        process(task)
    with Session.begin() as s:
        order = lock_order(s, oid)
        w = records.view(s, order)
        assert len(w["comments"]) == 4
        by_number = {c["number"]: c for c in w["comments"]}

        def context(index):
            comment = by_number[str(index)]
            return {
                "id": f"edit-{index}",
                "comment_id": comment["id"],
                "evidence_id": comment["source_ids"][0],
                "reason": "Apply the exact explicitly requested value to a review-only native copy.",
            }

        payload = {
            "drawing_document_id": did,
            "bom_document_id": bid,
            "drawing_sha256": s.get(Document, did).sha256,
            "bom_sha256": s.get(Document, bid).sha256,
            "drawing_number": "E-10",
            "source_revision": "C",
            "target_revision": "D",
            "returned_revision": "C",
            "edits": [
                {
                    **context(1),
                    "kind": "cad_attribute",
                    "entity_handle": handle,
                    "block_name": "RIVET_BREAKER",
                    "equipment_tag": "CB-12",
                    "attribute_tag": "TRIP_UNIT",
                    "expected_value": "TU-2500-DEMO",
                    "replacement_value": "TU-3000-DEMO",
                },
                {
                    **context(2),
                    "kind": "cad_attribute",
                    "entity_handle": handle,
                    "block_name": "RIVET_BREAKER",
                    "equipment_tag": "CB-12",
                    "attribute_tag": "NAMEPLATE",
                    "expected_value": "CB-12 / CUSTOMER NAME PENDING",
                    "replacement_value": "CB-12 / DATA HALL A",
                },
                {
                    **context(3),
                    "kind": "bom_cell",
                    "row_key_column": "line_id",
                    "row_key": "10",
                    "column": "part_number",
                    "expected_value": "TU-2500-DEMO",
                    "replacement_value": "TU-3000-DEMO",
                    "worksheet": "BOM",
                },
            ],
            "layout_tasks": [
                {
                    **context(4),
                    "id": "locate-breaker",
                    "entity_handle": handle,
                    "block_name": "RIVET_BREAKER",
                    "equipment_tag": "CB-12",
                }
            ],
        }
    return {
        "order_id": oid,
        "payload": payload,
        "native_ids": [did, bid],
        "native_source_ids": [dsid, bsid],
        "email_document_id": email_document_id,
        "handle": handle,
        "originals": [drawing.read_bytes(), bom.read_bytes()],
    }


def test_ordinary_email_event_agent_prepares_native_files_then_revision_package(
    incoming, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key-no-provider-call")
    counter = 0

    def provider(data, w):
        nonlocal counter
        counter += 1
        if counter == 1:
            return (
                "read_document",
                {"document_id": next(c["document_id"] for c in w["comments"])},
                {},
            )
        if counter == 2:
            return "inspect_order_cad", {}, {}
        if counter == 3:
            return (
                "prepare_order_cad",
                {"payload_json": json.dumps(incoming["payload"])},
                {},
            )
        if counter == 4:
            return "read_lifecycle_context", {}, {}
        if counter == 5:
            bundle = next(
                step["result"]
                for step in reversed(data["steps"])
                if step["name"] == "prepare_order_cad" and step["status"] == "completed"
            )
            return (
                "prepare_lifecycle_work",
                {
                    "payload_json": json.dumps(
                        {
                            "operation": "revision_review",
                            "revision": "C",
                            "comments": bundle["comment_plans"],
                        }
                    )
                },
                {},
            )
        return (
            "complete",
            {
                "summary": "Actual native DXF/BOM draft copies and one located layout task are prepared for engineering review. No geometry moved, approval or release was issued.",
                "source_ids": [],
            },
            {},
        )

    monkeypatch.setattr(agent, "provider_call", provider)
    assert agent.scheduled_pass() == 1
    task = claim(kinds=("record_agent",))
    assert task
    process(task)
    with Session() as s:
        order = get(s, Order, incoming["order_id"])
        run = s.scalar(
            select(RecordAgentRun).where(RecordAgentRun.order_id == order.id)
        )
        assert run.status == "completed", run.steps
        assert any(
            step["name"] == "prepare_order_cad" and step["status"] == "completed"
            for step in run.steps
        )
        saved = cad.saved_bundles(s, order)
        assert len(saved) == 1
        bundle = saved[0]["draft_bundle"]
        assert (
            len(bundle["changes"]) == 3
            and bundle["layout_tasks"][0]["geometry_changed"] is False
        )
        report = (
            db.DATA
            / "drafting"
            / order.organization_id
            / order.id
            / saved[0]["bundle_id"]
        )
        native_drawing = next(
            a for a in bundle["artifacts"] if a["role"] == "draft_drawing"
        )
        native_bom = next(a for a in bundle["artifacts"] if a["role"] == "draft_bom")
        output = ezdxf.readfile(report / native_drawing["name"])
        entity = output.entitydb.get(incoming["handle"])
        values = {a.dxf.tag: a.dxf.text for a in entity.attribs}
        assert (
            values["TRIP_UNIT"] == "TU-3000-DEMO"
            and values["NAMEPLATE"] == "CB-12 / DATA HALL A"
        )
        assert list(entity.dxf.insert) == bundle["layout_tasks"][0]["anchor_xyz"]
        workbook = load_workbook(report / native_bom["name"])
        assert workbook["BOM"]["C2"].value == "TU-3000-DEMO"
        workbook.close()
        for index, document_id in enumerate(incoming["native_ids"]):
            document = s.get(Document, document_id)
            assert (db.BLOBS / document.blob).read_bytes() == incoming["originals"][
                index
            ]
        review = order.state["lifecycle"]["packages"][-1]
        assert (
            review["operation"] == "revision_review"
            and review["status"] == "awaiting_decision"
        )
        assert review["payload"]["counts"] == {
            "answer": 0,
            "text_edit": 3,
            "layout_task": 1,
        }
        assert review["decisions"] == []
        assert all(
            c["status"] == "open" and not c["reviewed"]
            for c in records.view(s, order)["comments"]
        )


@pytest.mark.parametrize(
    "failure",
    [
        "unread",
        "invented_value",
        "partial_value",
        "forged_approval",
        "stale_hash",
        "geometry_key",
        "wrong_comment",
        "same_revision",
        "other_order",
        "outside_symlink",
    ],
)
def test_native_tool_rejects_untrusted_or_unsupported_changes_without_outputs(
    incoming, failure, tmp_path
):
    with Session.begin() as s:
        order = lock_order(s, incoming["order_id"])
        w = records.view(s, order)
        run = agent.create_run(s, order, trigger_key="cad-security-case")
        inspection, _native_ids, _ = agent.execute_tool(
            s, order, run, w, "inspect_order_cad", {}
        )
        run.state = {
            **run.state,
            "read_source_ids": [
                *inspection["source_ids"],
                *[sid for c in w["comments"] for sid in c["source_ids"]],
            ],
        }
        payload = deepcopy(incoming["payload"])
        if failure == "unread":
            run.state = {**run.state, "read_source_ids": []}
        elif failure == "invented_value":
            payload["edits"][0]["replacement_value"] = "TU-9000-INVENTED"
        elif failure == "partial_value":
            payload["edits"][0]["replacement_value"] = "3000"
        elif failure == "forged_approval":
            payload["edits"][0]["decision_status"] = "confirmed_for_draft"
        elif failure == "stale_hash":
            payload["drawing_sha256"] = "0" * 64
        elif failure == "geometry_key":
            payload["layout_tasks"][0]["new_position"] = [100, 200, 0]
        elif failure == "wrong_comment":
            payload["edits"][0]["comment_id"] = w["comments"][-1]["id"]
        elif failure == "same_revision":
            payload["target_revision"] = "c"
        elif failure == "other_order":
            other = new_order(
                s,
                {
                    "title": "Other order",
                    "customer": "Other fictional customer",
                    "number": "OTHER-CAD",
                    "synthetic": True,
                },
            )
            s.get(Document, incoming["native_ids"][0]).project_id = other.project_id
            s.flush()
        elif failure == "outside_symlink":
            document = s.get(Document, incoming["native_ids"][0])
            path = db.BLOBS / document.blob
            outside = tmp_path / "outside-native.dxf"
            outside.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(outside)
        with pytest.raises(HTTPException) as error:
            agent.execute_tool(
                s,
                order,
                run,
                w,
                "prepare_order_cad",
                {"payload_json": json.dumps(payload)},
            )
        assert error.value.status_code in {404, 409, 422}
        assert not list(
            s.scalars(
                select(OrderEvent).where(
                    OrderEvent.order_id == order.id, OrderEvent.kind == "cad_draft"
                )
            )
        )
        assert not (db.DATA / "drafting" / order.organization_id / order.id).exists()


def test_native_bundle_reuse_is_idempotent_and_changed_evidence_is_withheld(incoming):
    with Session.begin() as s:
        order = lock_order(s, incoming["order_id"])
        w = records.view(s, order)
        run = agent.create_run(s, order, trigger_key="cad-idempotency")
        inspection, _, _ = agent.execute_tool(s, order, run, w, "inspect_order_cad", {})
        run.state = {
            **run.state,
            "read_source_ids": [
                *inspection["source_ids"],
                *[sid for c in w["comments"] for sid in c["source_ids"]],
            ],
        }
        payload = {"payload_json": json.dumps(incoming["payload"])}
        first, _, _ = agent.execute_tool(s, order, run, w, "prepare_order_cad", payload)
        repeated, _, _ = agent.execute_tool(
            s, order, run, w, "prepare_order_cad", payload
        )
        assert first["bundle_id"] == repeated["bundle_id"] and repeated["replayed"]
        assert len(cad.saved_bundles(s, order)) == 1
        event_count = len(
            list(
                s.scalars(
                    select(OrderEvent).where(
                        OrderEvent.order_id == order.id, OrderEvent.kind == "cad_draft"
                    )
                )
            )
        )
        assert event_count == 1
        source = s.get(Span, incoming["payload"]["edits"][0]["evidence_id"])
        source.text = "Corrected returned comment: retain the original trip unit pending clarification."
        s.flush()
        assert cad.saved_bundles(s, order) == []


def test_malformed_native_bom_is_an_explicit_blocker_not_a_crash():
    with pytest.raises(HTTPException) as error:
        cad._bom_inventory(b"not a spreadsheet zip", ".xlsx")
    assert error.value.status_code == 409
