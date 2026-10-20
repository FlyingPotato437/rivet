"""Only the approved physical handover may enter its captured Outlook thread."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from test_autopilot import FakeMailbox
from test_lifecycle import decision, prepare
from test_reply_assistant import receipt

from backend.autopilot import service as autopilot
from backend.lifecycle import completion_replay
from backend.lifecycle import outlook_handoff as handoff
from backend.lifecycle import service as lifecycle
from backend.lifecycle.mail_context import selected_message
from backend.reply_assistant.models import ReplyDraft
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order, RecordIntake, Span, now


def test_actual_approved_handover_becomes_native_index_and_zip_then_blocks_tampering(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "DATA", tmp_path / "work")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    python = (
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    )
    if python.is_file():
        monkeypatch.setenv("RIVET_PDF_PYTHON", str(python))
    result = completion_replay.complete(number="DEMO-HANDOVER-MAIL")
    oid = result["order_id"]
    with Session.begin() as s:
        order = s.get(Order, oid)
        package = next(
            p
            for p in lifecycle.view(s, order)["packages"]
            if p["operation"] == "closeout"
        )
        document = Document(
            project_id=order.project_id,
            name="handover-request.eml",
            kind="email",
            blob="request.eml",
            sha256="a" * 64,
            state="ready",
            meta={"intake_order_id": oid},
        )
        s.add(document)
        s.flush()
        source = Span(
            document_id=document.id,
            text="Please provide the handover documents for this order, including the as-built drawings and test report.",
            location={"page": 1},
        )
        s.add(source)
        s.flush()
        mail = receipt(s, oid, document.id, "<handover-request@example.com>")
        mail.status = "imported"
        mail.data = {
            **mail.data,
            "received_at": now().isoformat(),
            "conversation_id": "handover-thread",
        }
        s.get(RecordIntake, mail.intake_id).status = "matched"
        rid, sid = mail.id, source.id
        documents = package["payload"]["documents"]
    prepared = prepare(
        oid,
        operation="closeout",
        handover_request_source_ids=[sid],
        test_report_document_ids=[
            d["id"] for d in documents if d["role"] == "test_report"
        ],
        as_built_document_ids=[d["id"] for d in documents if d["role"] == "as_built"],
        other_document_ids=[
            d["id"] for d in documents if d["role"] == "supporting_document"
        ],
    )["packages"][0]
    assert sid in prepared["sources"] and len(prepared["sources"]) == 4
    with Session.begin() as s:
        order = s.get(Order, oid)
        view = selected_message(s, "<handover-request@example.com>")
        assert view["packages"][0]["id"] == prepared["id"]
        assert view["packages"][0]["allowed_gates"] == [
            "engineering",
            "customer_commitment",
        ]
        with pytest.raises(HTTPException, match="Complete the current"):
            handoff.enqueue(s, order, prepared["id"], rid)
    decision(oid, prepared["id"], "engineering", sid)
    decision(oid, prepared["id"], "customer_commitment", sid)
    graph = FakeMailbox()
    delivered = autopilot.deliver(client_factory=graph)
    assert len(delivered) == 1 and delivered[0]["status"] == "created"
    assert graph.draft()["isDraft"]
    assert graph.draft()["attachments"] == [
        "handover-index.pdf",
        "handover-documents.zip",
    ]
    assert "DRAFT HANDOVER" in graph.draft()["body"]["content"]
    assert "does not establish shipment" in graph.draft()["body"]["content"]
    assert not autopilot.deliver(client_factory=graph)
    assert sum(path.endswith("createReply") for _, path in graph.calls) == 1
    with Session.begin() as s:
        order = s.get(Order, oid)
        queued = handoff.enqueue(s, order, prepared["id"], rid)
        reply = s.get(ReplyDraft, queued["reply_id"])
        drawing = s.get(
            Document, next(d["id"] for d in documents if d["role"] == "as_built")
        )
        (db.BLOBS / drawing.blob).write_bytes(
            b"Unreviewed drawing changed after handover approval"
        )
        with pytest.raises(HTTPException):
            handoff.validate(s, order, reply)
