"""Native file work avoids redundant event agents without disabling manual work."""

from datetime import timedelta
from email.message import EmailMessage
from uuid import uuid4

import pymupdf
import pytest
import test_native_outlook_workflow as native_workflow
import test_native_ownership as ownership_tests
from reportlab.pdfgen import canvas
from test_company_context import identity
from test_equipment_api import upload

from backend.domain.service import get
from backend.equipment import ownership
from backend.mailbox.models import MailboxReceipt
from backend.records import agent_service as agent
from backend.records import agent_tasks
from backend.records import service as records
from backend.records.agent_schemas import RunWrite
from backend.records.email_intake import import_email
from backend.storage.db import Session
from backend.storage.models import (
    Document,
    Job,
    Order,
    RecordAgentRun,
    RecordIntake,
    now,
)

corpus = native_workflow.corpus
mail = ownership_tests.mail
ready = ownership_tests.ready


def _enable(session, order):
    record = records.ensure(session, order)
    record.data = {**record.data, "agent_settings": {"enabled": True}}
    return record


def _manual_body(record):
    return RunWrite(
        expected_version=record.version,
        actor="Fixture PM",
        reason="Inspect these sources explicitly.",
        goal="Review the customer evidence and prepare clarification questions.",
    )


def test_file_only_native_work_skips_event_autoruns_but_manual_ask_remains_available(
    client, corpus, monkeypatch
):
    monkeypatch.setenv("RIVET_AGENT_AUTORUN", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, _ = native_workflow.register(client, corpus, 1)
    ownership_tests._parse_returns()
    with Session.begin() as session:
        order = get(session, Order, oid)
        _enable(session, order)
        assert ownership.for_autorun(session, order) is not None
    assert agent.scheduled_pass() == 0
    assert agent.scheduled_pass() == 0
    with Session.begin() as session:
        order = get(session, Order, oid)
        record = records.ensure(session, order)
        assert session.query(RecordAgentRun).filter_by(order_id=oid).count() == 0
        manual = agent.create_run(session, order, _manual_body(record))
        assert manual.origin == "user" and manual.status == "queued"
        assert (
            session.query(Job)
            .filter_by(project_id=order.project_id, kind="record_agent")
            .count()
            == 1
        )


def test_later_ordinary_email_releases_native_event_deferral(
    ready, corpus, monkeypatch
):
    monkeypatch.setenv("RIVET_AGENT_AUTORUN", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, previous_id = ownership_tests._native_arrival(ready, corpus)
    ownership_tests._parse_returns()
    with identity(), Session.begin() as session:
        order = get(session, Order, oid)
        _enable(session, order)
        assert ownership.for_autorun(session, order) is not None
    assert agent.scheduled_pass() == 0
    with identity(), Session.begin() as session:
        order = get(session, Order, oid)
        previous = get(session, MailboxReceipt, previous_id)
        email = EmailMessage()
        email["Message-ID"] = f"<{uuid4()}@example.com>"
        email["Subject"] = "Confirm the next review meeting"
        email.set_content("Please confirm when our next review meeting is scheduled.")
        imported = import_email(
            session, order, "next-meeting.eml", email.as_bytes(), "D"
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
        session.add(
            MailboxReceipt(
                connection_id=previous.connection_id,
                intake_id=intake.id,
                provider_message_id="ordinary-later-question",
                internet_message_id=str(email["Message-ID"]),
                status="imported",
                data={"received_at": (now() + timedelta(seconds=1)).isoformat()},
            )
        )
    ownership_tests._parse_returns()
    with identity(), Session() as session:
        assert ownership.for_autorun(session, get(session, Order, oid)) is None
    assert agent.scheduled_pass() == 1
    with identity(), Session() as session:
        runs = session.query(RecordAgentRun).filter_by(order_id=oid).all()
        assert len(runs) == 1 and runs[0].origin == "event"


@pytest.mark.parametrize("manual", [False, True])
def test_event_comment_children_exclude_native_sources_and_preserve_other_comments(
    client, corpus, tmp_path, monkeypatch, manual
):
    monkeypatch.setenv("RIVET_AGENT_COMMENT_TASKS", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    manifest, sources = corpus
    case = manifest["cases"][0]
    request_id = case["requirements"][0]
    marked_up = tmp_path / "returned-native-annotations.pdf"
    with pymupdf.open(sources[request_id]["file"]) as pdf:
        for index in range(6):
            note = pdf[0].add_text_annot(
                (90 + index * 30, 300), case["expected"]["request_actions"][0]
            )
            note.update()
        pdf.save(marked_up)
    sources = {**sources, request_id: {**sources[request_id], "file": marked_up}}
    oid, _ = native_workflow.register(client, (manifest, sources), 1)
    other = tmp_path / "ordinary-review-comments.pdf"
    pdf = canvas.Canvas(str(other))
    for index in range(1, 7):
        pdf.drawString(
            40,
            800 - index * 25,
            f"Comment {index}: Please confirm the shipping contact for site {index}.",
        )
    pdf.save()
    upload(
        client,
        oid,
        {
            "ordinary": {
                "role": "markup",
                "revision": "D",
                "name": other.name,
                "file": other,
            }
        },
        ["ordinary"],
    )
    ownership_tests._parse_returns()
    with Session.begin() as session:
        order = get(session, Order, oid)
        record = records.ensure(session, order)
        native = ownership.for_order(session, order)
        assert native is not None and len(native["source_ids"]) == 1
        workspace = records.view(session, order, record)
        comments = [
            item for item in workspace["comments"] if item.get("revision") == "D"
        ]
        assert len(comments) == 12
        body = _manual_body(record) if manual else None
        run = agent.create_run(
            session, order, body, trigger_key=None if manual else "event-test"
        )
        assert agent_tasks.coordinate(session, order, run, workspace) is True
        tasks = agent_tasks.rows(session, run)
        assert len(tasks) == (12 if manual else 6)
        assert all(
            task.snapshot["comment"]["document_id"] not in set(native["source_ids"])
            for task in tasks
        ) is (not manual)
