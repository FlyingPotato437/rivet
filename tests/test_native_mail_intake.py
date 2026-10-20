"""Returned mail must trigger real native file work through ordinary ingestion."""

import base64
from email.message import EmailMessage
from hashlib import sha256
from uuid import uuid4

import pytest
import test_native_outlook_workflow as native_workflow
from test_equipment_api import new_order, upload
from test_equipment_outlook import DraftMailbox

from backend.domain.service import get
from backend.equipment import outlook, workflow
from backend.equipment.native_demo import write_requests
from backend.equipment.native_model import inspect_native
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.records.email_intake import import_email
from backend.storage.db import Session
from backend.storage.models import Document, Job, Order, RecordIntake
from backend.worker import claim, process

corpus = native_workflow.corpus


@pytest.mark.parametrize("attachment_name", ["CR-01-returned-changes.pdf", "attachment-00023.pdf"])
def test_returned_email_attachment_automatically_changes_native_files_and_outlook_draft(
    client, corpus, attachment_name
):
    manifest, sources = corpus
    case = manifest["cases"][0]
    assert case["id"] == "01-trip-rewire"
    oid = new_order(client)
    # The engineering source files are already on the order. The customer
    # return below arrives through the same EML ingestion used by the mailbox.
    for side in ("baseline", "target"):
        for key in case[side]:
            upload(client, oid, sources, [key])
    with Session() as session:
        order = get(session, Order, oid)
        assert not session.query(Job).filter_by(
            project_id=order.project_id, kind="equipment_corrections"
        ).count()

    request = sources[case["requirements"][0]]
    mail = EmailMessage()
    mail["From"] = "reviewer@example.com"
    mail["To"] = "pm@example.com"
    mail["Subject"] = f"{manifest['job_number']} - Rev D returned changes"
    mail["Message-ID"] = f"<{uuid4()}@example.com>"
    mail.set_content(
        "Fictional integration test. Rev D is revise and resubmit. "
        "Please implement the two attached trip-circuit changes and return "
        "the draft drawing and matching bill of materials for review."
    )
    mail.add_attachment(
        request["file"].read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=attachment_name,
    )
    with Session.begin() as session:
        order = get(session, Order, oid)
        arrival = import_email(
            session, order, "customer-return.eml", mail.as_bytes(), "D", forwarded=True
        )
        original = get(session, Document, arrival["document_id"])
        attachment_id = original.meta["attachment_document_ids"][0]
        attachment = get(session, Document, attachment_id)
        assert "equipment_package" not in attachment.meta
        assert "order_role" not in attachment.meta
        connection = MailboxConnection(
            mailbox="pm@example.com", microsoft_tenant_id=str(uuid4())
        )
        session.add(connection)
        session.flush()
        intake = RecordIntake(
            name=original.name,
            blob=original.blob,
            sha256=original.sha256,
            order_id=oid,
            status="imported",
            data={"document_id": original.id},
        )
        session.add(intake)
        session.flush()
        session.add(
            MailboxReceipt(
                connection_id=connection.id,
                provider_message_id="genuine-order-email",
                internet_message_id=str(mail["Message-ID"]),
                intake_id=intake.id,
                status="imported",
                data={"subject": str(mail["Subject"]), "sender": "reviewer@example.com"},
            )
        )

    # Run only real parse jobs. Their normal refresh_project_orders call must
    # discover the returned requests, reconcile and queue durable native work.
    while task := claim(kinds=("parse",)):
        process(task)
    with Session() as session:
        order = get(session, Order, oid)
        document = get(session, Document, attachment_id)
        assert document.state == "ready", document.coverage
        report, _, _, _, _ = workflow.plan(session, order)
        assert report["status"] == "ready", {
            "plan_status": report["status"],
            "blockers": report["blockers"],
            "attachment": next(
                row for row in order.state["documents"] if row["id"] == attachment_id
            ),
        }
        assert report["geometry_changed"] and len(report["native_plan"]["requests"]) == 2
        jobs = session.query(Job).filter_by(
            project_id=order.project_id, kind="equipment_corrections", status="queued"
        ).all()
        assert len(jobs) == 1
        payload = jobs[0].payload

    graph = DraftMailbox()
    result = workflow.process(payload, client_factory=graph)
    assert result["draft"]["status"] == "created"
    assert result["draft"]["threaded"] is True
    assert any(call.url.path.endswith("/createReply") for call in graph.calls)
    assert not any("/send" in call.url.path for call in graph.calls)
    assert graph.messages["native-correction"]["isDraft"]
    with Session() as session:
        order = get(session, Order, oid)
        bundle = order.state["equipment_corrections"]["bundles"][0]
        drawing = next(item for item in bundle["artifacts"] if item["role"] == "drawing")
        path, _ = workflow.artifact(session, order, bundle["id"], drawing["name"])
        actual = inspect_native(path)
        assert actual["status"] == "verified", actual["checks"]
        wires = {wire["id"]: wire for wire in actual["wires"]}
        assert wires["W102"]["to"] == "CB-13:TC1"
        assert wires["W104"]["to"] == "CB-12:TC1"
        attached = graph.messages["native-correction"]["attachments"]
        assert len(attached) == 3
        assert {
            item["name"]: sha256(base64.b64decode(item["contentBytes"])).hexdigest()
            for item in attached
        } == {
            item["name"]: item["sha256"]
            for item in bundle["artifacts"]
            if item["role"] in {"drawing", "bom", "preview"}
        }


def test_unsupported_native_change_in_email_is_visible_and_cannot_queue_file_work(
    client, corpus, tmp_path, monkeypatch
):
    manifest, sources = corpus
    case = manifest["cases"][0]
    oid = new_order(client)
    for side in ("baseline", "target"):
        for key in case[side]:
            upload(client, oid, sources, [key])
    returned = tmp_path / "attachment-unknown-operation.pdf"
    write_requests(returned, "Fictional rotation request", ["Rotate KA-1 by 90 degrees."])
    mail = EmailMessage()
    mail["From"] = "reviewer@example.com"
    mail["To"] = "pm@example.com"
    mail["Subject"] = f"{manifest['job_number']} - Rev D returned changes"
    mail.set_content("Fictional test: please implement the attached returned change.")
    mail.add_attachment(
        returned.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename="attachment-00024.pdf",
    )
    graph = DraftMailbox()
    monkeypatch.setattr(outlook, "_client", graph)
    with Session.begin() as session:
        order = get(session, Order, oid)
        imported = import_email(
            session, order, "unsupported-return.eml", mail.as_bytes(), "D", forwarded=True
        )
        original = get(session, Document, imported["document_id"])
        attachment_id = original.meta["attachment_document_ids"][0]
    while task := claim(kinds=("parse",)):
        process(task)
    with Session() as session:
        order = get(session, Order, oid)
        projected = next(row for row in order.state["documents"] if row["id"] == attachment_id)
        assert projected["role"] == "markup"
        assert get(session, Document, attachment_id).state == "ready"
        assert not session.query(Job).filter_by(
            project_id=order.project_id, kind="equipment_corrections"
        ).count()
    public = client.get(f"/api/orders/{oid}/equipment/corrections")
    assert public.status_code == 200, public.text
    view = public.json()
    assert view["plan"]["status"] == "blocked"
    assert any(
        item["code"] == "unsupported_geometry_request"
        for item in view["plan"]["blockers"]
    )
    refused = client.post(
        f"/api/orders/{oid}/equipment/corrections/prepare",
        json={
            "expected_version": view["version"],
            "expected_plan_id": view["plan"]["plan_id"],
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert refused.status_code == 409, refused.text
    assert not view["bundles"]
    assert graph.calls == [] and graph.messages == {}
