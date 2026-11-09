"""Native-file work wins over a concurrent generic Outlook text reply."""

from datetime import timedelta
from email.message import EmailMessage
from hashlib import sha256
from uuid import uuid4

import pytest
import test_autopilot as autopilot_tests
import test_native_outlook_workflow as native_workflow
import test_reply_automatic as automatic_tests
from reportlab.pdfgen import canvas
from test_company_context import identity
from test_equipment_api import new_order, upload

from backend.api.main import add_document
from backend.autopilot import service as autopilot
from backend.domain.service import get
from backend.equipment import ownership
from backend.equipment import service as equipment
from backend.equipment.native_demo import write_requests
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.records.email_intake import import_email
from backend.reply_assistant import automatic, order_packet
from backend.reply_assistant.models import ReplyDraft
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import (
    Document,
    Job,
    Order,
    Project,
    Quote,
    RecordIntake,
    now,
)
from backend.worker import claim, process

mail = automatic_tests.mail
ready = autopilot_tests.ready
corpus = native_workflow.corpus


def _native_arrival(ready, corpus, *, unsupported=False):
    """Add actual native originals and an actual captured customer return."""
    manifest, sources = corpus
    case = manifest["cases"][0]
    with identity(), Session.begin() as session:
        reply = get(session, ReplyDraft, ready[0])
        order = get(session, Order, reply.order_id)
        receipt = autopilot._receipt_for_reply(session, reply)
        intake = get(session, RecordIntake, receipt.intake_id)
        original = get(session, Document, intake.data["document_id"])
        # The generic-reply fixture intentionally starts from persisted text.
        # Give that legacy original real matching bytes before including it in
        # an original-file comparison; no source hash is bypassed in this test.
        path = db.BLOBS / original.blob
        document = canvas.Canvas(str(path))
        document.drawString(40, 800, "Fictional earlier customer review")
        document.drawString(40, 775, "Please confirm cable entry on E-10.")
        document.save()
        original.sha256 = sha256(path.read_bytes()).hexdigest()
        project = get(session, Project, order.project_id)
        session.add(Quote(project_id=project.id, number="FICTIONAL-NATIVE-RACE"))
        session.flush()
        for side in ("baseline", "target"):
            for key in case[side]:
                source = sources[key]
                metadata = {
                    "order_role": source["role"],
                    "revision_label": source["revision"],
                    "equipment_package": {
                        "role": source["role"],
                        "revision": source["revision"],
                    },
                }
                if source["name"].endswith(".dxf"):
                    equipment.add_native_dxf(
                        session,
                        order,
                        source["name"],
                        source["file"].read_bytes(),
                        metadata,
                    )
                else:
                    add_document(
                        session,
                        project,
                        source["name"],
                        source["role"],
                        source["file"].read_bytes(),
                        metadata,
                    )
        request = sources[case["requirements"][0]]["file"]
        if unsupported:
            request = db.BLOBS / "fictional-unsupported-return.pdf"
            write_requests(
                request, "Fictional unsupported change", ["Rotate KA-1 by 90 degrees."]
            )
        email = EmailMessage()
        email["Subject"] = f"{manifest['job_number']} returned Rev D changes"
        email["Message-ID"] = receipt.internet_message_id
        email["From"] = "customer@example.com"
        email["To"] = "pm@example.com"
        email.set_content(
            "Fictional test: implement the attached changes and return draft files."
        )
        email.add_attachment(
            request.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename="attachment-007.pdf",
        )
        result = import_email(
            session, order, "captured-return.eml", email.as_bytes(), "D", forwarded=True
        )
        root = get(session, Document, result["document_id"])
        intake.name, intake.blob, intake.sha256 = root.name, root.blob, root.sha256
        intake.data = {"document_id": root.id}
        receipt_id, oid = receipt.id, order.id
    return oid, receipt_id


def _parse_returns():
    while task := claim(kinds=("parse",)):
        process(task)


@pytest.mark.parametrize("unsupported", [False, True])
def test_existing_ready_generic_reply_defers_to_pending_and_checked_native_work(
    ready, corpus, unsupported
):
    oid, receipt_id = _native_arrival(ready, corpus, unsupported=unsupported)
    graph = autopilot_tests.FakeMailbox()
    with identity(), Session.begin() as session:
        state = automatic.reconcile_receipt(
            session, get(session, MailboxReceipt, receipt_id)
        )
        assert state["reason"] == "native_equipment" and state["status"] == "queued"
    with identity():
        waiting = autopilot.deliver_one(ready[0], client_factory=graph)
    assert waiting["status"] == "awaiting_preparation"
    assert graph.calls == []
    _parse_returns()
    with identity(), Session.begin() as session:
        state = automatic.reconcile_receipt(
            session, get(session, MailboxReceipt, receipt_id)
        )
        assert state["reason"] == "native_equipment"
        assert state["status"] == ("blocked" if unsupported else "queued")
        assert session.query(ReplyDraft).filter_by(order_id=oid).count() == 1
    with identity():
        result = autopilot.deliver_one(ready[0], client_factory=graph)
    assert result["status"] == "awaiting_preparation"
    assert graph.calls == [] and graph.messages == {}


def test_mail_reconciliation_enqueues_ready_native_correction_once(ready, corpus):
    """A native email owner starts the existing durable correction worker."""
    oid, receipt_id = _native_arrival(ready, corpus)
    with identity(), Session.begin() as session:
        state = automatic.reconcile_receipt(
            session, get(session, MailboxReceipt, receipt_id)
        )
        assert state["reason"] == "native_equipment"
        assert state["status"] == "queued"
        project_id = get(session, Order, oid).project_id
        assert (
            session.query(Job)
            .filter_by(project_id=project_id, kind="equipment_corrections")
            .count()
            == 0
        )

    _parse_returns()
    with identity(), Session.begin() as session:
        receipt = get(session, MailboxReceipt, receipt_id)
        order = get(session, Order, oid)
        read_only = ownership.for_receipt(session, order, receipt)
        assert read_only and read_only["status"] == "queued"
        assert (
            session.query(Job)
            .filter_by(project_id=order.project_id, kind="equipment_corrections")
            .count()
            == 0
        )
        prior_unbound = native_workflow.workflow.schedule(session, order)
        assert prior_unbound and "receipt_id" not in prior_unbound.payload
        legacy_payload = dict(prior_unbound.payload)
        legacy_key = prior_unbound.key
        state = automatic.reconcile_receipt(session, receipt)
        assert state["reason"] == "native_equipment"
        assert state["status"] == "queued"
        jobs = (
            session.query(Job)
            .filter_by(project_id=order.project_id, kind="equipment_corrections")
            .all()
        )
        assert len(jobs) == 1
        bound_job = jobs[0]
        assert bound_job.id == prior_unbound.id
        assert bound_job.payload["receipt_id"] == receipt_id
        assert set(bound_job.payload) == {
            "order_id",
            "source_fingerprint",
            "plan_id",
            "receipt_id",
        }
        # Simulate an older duplicate arriving during a rolling upgrade. An
        # occupied bound key must win without renaming this legacy queued row.
        session.add(
            Job(
                project_id=order.project_id,
                kind="equipment_corrections",
                key=legacy_key,
                payload=legacy_payload,
            )
        )
        session.flush()
        assert (
            native_workflow.workflow.schedule(
                session, order, receipt_id=receipt_id
            ).id
            == bound_job.id
        )

    # The recurring receipt reconciliation is idempotent and does not enqueue
    # another correction while the first worker job is pending.
    with identity(), Session.begin() as session:
        automatic.reconcile_receipt(session, get(session, MailboxReceipt, receipt_id))
        assert (
            session.query(Job)
            .filter_by(
                project_id=get(session, Order, oid).project_id,
                kind="equipment_corrections",
            )
            .count()
            == 2
        )

    # A later unrelated customer message on the same order must not become the
    # target thread for this attachment package. The originating receipt stays
    # bound to its durable job and the resulting reply remains unsent.
    with identity(), Session.begin() as session:
        order = get(session, Order, oid)
        original = get(session, MailboxReceipt, receipt_id)
        email = EmailMessage()
        email["Subject"] = "Please confirm next week's review meeting"
        email["Message-ID"] = f"<{uuid4()}@example.com>"
        email["From"] = "customer@example.com"
        email["To"] = "pm@example.com"
        email.set_content("Please confirm next week's review meeting.")
        imported = import_email(
            session, order, "later-unrelated.eml", email.as_bytes(), "D", forwarded=True
        )
        later_doc = get(session, Document, imported["document_id"])
        later_intake = RecordIntake(
            order_id=oid,
            name=later_doc.name,
            blob=later_doc.blob,
            sha256=later_doc.sha256,
            status="imported",
            data={"document_id": later_doc.id},
        )
        session.add(later_intake)
        session.flush()
        session.add(
            MailboxReceipt(
                connection_id=original.connection_id,
                intake_id=later_intake.id,
                provider_message_id="newer-unrelated-email",
                internet_message_id=str(email["Message-ID"]),
                status="imported",
                data={"received_at": (now() + timedelta(seconds=1)).isoformat()},
            )
        )
        payload = next(
            job.payload for job in jobs if job.payload.get("receipt_id") == receipt_id
        )

    legacy_graph = native_workflow.DraftMailbox()
    legacy_graph.original_id = ready[1]
    with identity():
        legacy_result = native_workflow.workflow.process(
            legacy_payload, client_factory=legacy_graph
        )
    assert legacy_result["reason"] == "receipt_binding_required"
    assert legacy_graph.calls == []

    graph = native_workflow.DraftMailbox()
    graph.original_id = ready[1]
    with identity():
        result = native_workflow.workflow.process(payload, client_factory=graph)
    assert result["draft"]["status"] == "created"
    assert graph.messages["native-correction"]["isDraft"]
    assert any(
        request.method == "POST"
        and request.url.path.endswith(f"/{ready[1]}/createReply")
        for request in graph.calls
    )
    assert not any("/send" in str(request.url) for request in graph.calls)


def test_native_work_appearing_during_graph_setup_stops_generic_provider_writes(
    ready, corpus, monkeypatch
):
    graph = autopilot_tests.FakeMailbox()
    checked_hashes = []
    current_hash = autopilot._current_hash

    def measured_hash(session, reply):
        checked_hashes.append(reply.id)
        return current_hash(session, reply)

    def connect(snapshot):
        _native_arrival(ready, corpus)
        return graph(snapshot)

    monkeypatch.setattr(autopilot, "_current_hash", measured_hash)
    with identity():
        result = autopilot.deliver_one(ready[0], client_factory=connect)
    assert result["status"] == "superseded"
    # The native ownership gate acts before the second evidence-hash check;
    # this is not merely the ordinary stale-reply protection passing by chance.
    assert checked_hashes == [ready[0]]
    assert graph.calls and all(method == "GET" for method, _ in graph.calls)
    assert not any(key.startswith("draft-") for key in graph.messages)


def test_native_reply_already_handled_finishes_obsolete_generic_delivery(ready, corpus):
    oid, receipt_id = _native_arrival(ready, corpus)
    with identity(), Session.begin() as session:
        order = get(session, Order, oid)
        order.state = {
            **order.state,
            "equipment_outlook": {
                "checked-package": {"receipt_id": receipt_id, "status": "sent"}
            },
        }
    graph = autopilot_tests.FakeMailbox()
    with identity():
        result = autopilot.deliver_one(ready[0], client_factory=graph)
    assert result["status"] == "superseded"
    assert graph.calls == []


def test_later_plain_status_email_does_not_inherit_native_work_from_the_order(
    ready, corpus
):
    oid, old_receipt_id = _native_arrival(ready, corpus)
    _parse_returns()
    with identity(), Session.begin() as session:
        order = get(session, Order, oid)
        old = get(session, MailboxReceipt, old_receipt_id)
        email = EmailMessage()
        email["Subject"] = "When is our next review meeting?"
        email["Message-ID"] = f"<{uuid4()}@example.com>"
        email.set_content("Please confirm the date of our next review meeting.")
        imported = import_email(
            session, order, "status-question.eml", email.as_bytes(), "D"
        )
        original = get(session, Document, imported["document_id"])
        intake = RecordIntake(
            order_id=oid,
            name=original.name,
            blob=original.blob,
            sha256=original.sha256,
            status="imported",
            data={"document_id": original.id},
        )
        session.add(intake)
        session.flush()
        status = MailboxReceipt(
            connection_id=old.connection_id,
            intake_id=intake.id,
            provider_message_id="later-status-question",
            internet_message_id=str(email["Message-ID"]),
            status="imported",
        )
        session.add(status)
        session.flush()
        assert ownership.for_receipt(session, order, status) is None
        assert ownership.for_receipt(session, order, old) is not None


def test_file_only_native_order_does_not_schedule_a_redundant_generic_packet(
    client, corpus, monkeypatch
):
    oid, _ = native_workflow.register(client, corpus, 1)

    def reject_generic(*args, **kwargs):
        pytest.fail(
            "Native corrections must not start a redundant generic reply packet"
        )

    monkeypatch.setattr(order_packet.service, "begin", reject_generic)
    with Session.begin() as session:
        state = order_packet.reconcile(session, get(session, Order, oid))
        assert state["reason"] == "native_equipment"


def test_incomplete_native_package_keeps_pending_and_parsed_request_owned(
    client, corpus
):
    manifest, sources = corpus
    case = manifest["cases"][0]
    oid = new_order(client)
    drawing_id = next(
        key for key in case["target"] if sources[key]["name"].endswith(".dxf")
    )
    request_id = case["requirements"][0]
    sources = {**sources, request_id: {**sources[request_id], "revision": "D"}}
    upload(client, oid, sources, [drawing_id])
    upload(client, oid, sources, [request_id])
    # Neither a baseline nor a target BOM exists. The ordinary workflow plan
    # therefore includes package-wide blockers alongside source-specific ones.
    email = EmailMessage()
    email["Subject"] = "Fictional returned changes; source package incomplete"
    email["Message-ID"] = f"<{uuid4()}@example.com>"
    email["From"] = "reviewer@example.com"
    email["To"] = "pm@example.com"
    email.set_content("Please implement the attached returned changes.")
    request = sources[request_id]
    email.add_attachment(
        request["file"].read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=request["name"],
    )
    with Session.begin() as session:
        order = get(session, Order, oid)
        imported = import_email(
            session, order, "incomplete-native-return.eml", email.as_bytes(), "D"
        )
        assert imported["attachments_reused"] == 1
        original = get(session, Document, imported["document_id"])
        connection = MailboxConnection(
            mailbox="pm@example.com", microsoft_tenant_id=str(uuid4())
        )
        session.add(connection)
        session.flush()
        intake = RecordIntake(
            order_id=oid,
            name=original.name,
            blob=original.blob,
            sha256=original.sha256,
            status="imported",
            data={"document_id": original.id},
        )
        session.add(intake)
        session.flush()
        receipt = MailboxReceipt(
            connection_id=connection.id,
            intake_id=intake.id,
            provider_message_id="incomplete-native-message",
            internet_message_id=str(email["Message-ID"]),
            status="imported",
            data={"sender": "reviewer@example.com"},
        )
        session.add(receipt)
        session.flush()
        receipt_id = receipt.id
        assert ownership.for_order(session, order)["status"] == "queued"
        state = automatic.reconcile_receipt(session, receipt)
        assert state["reason"] == "native_equipment" and state["status"] == "queued"
    _parse_returns()
    with Session.begin() as session:
        order = get(session, Order, oid)
        state = ownership.for_order(session, order)
        assert state["status"] == "blocked" and state["reason"] == "native_equipment"
        state = automatic.reconcile_receipt(
            session, get(session, MailboxReceipt, receipt_id)
        )
        assert state["status"] == "blocked" and state["reason"] == "native_equipment"
        assert session.query(ReplyDraft).filter_by(order_id=oid).count() == 0
