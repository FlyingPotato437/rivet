"""A repeated delivery is new mail, not a second set of the same PDF markups."""

from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

import pytest
from test_company_context import ORG_B, identity
from test_company_context import order as base_order
from test_record_scenarios import drain
from test_reply_assistant import receipt

from backend.records import service as records
from backend.records.email_intake import email_document_ids, import_email
from backend.reply_assistant import automatic
from backend.reply_assistant import service as replies
from backend.reply_assistant.schemas import GenerateReply
from backend.storage.db import Session
from backend.storage.models import Document, Order, Project, Quote
from scripts.make_order_replay import write_pdf


def message(pdf):
    mail = EmailMessage()
    mail["From"] = "reviewer@example.com"
    mail["To"] = "pm@example.com"
    mail["Subject"] = "Returned comments on E-10"
    mail["Message-ID"] = f"<{uuid4()}@example.com>"
    mail.set_content("Please review the attached drawing.")
    mail.add_attachment(pdf, maintype="application", subtype="pdf", filename="E-10.pdf")
    return mail.as_bytes(), mail["Message-ID"]


def order(s):
    current, doc, span = base_order(s)
    s.add(Quote(project_id=current.project_id, number="FIXTURE-QUOTE"))
    s.flush()
    return current, doc, span


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "E-10.pdf"
    write_pdf(
        path,
        "Fictional switchboard | E-10",
        ["Revision C"],
        [
            {"id": f"markup-{i}", "text": f"Comment {i}: Please confirm item {i}."}
            for i in range(1, 4)
        ],
    )
    return path.read_bytes()


def ingest(s, current, pdf, revision="C"):
    raw, message_id = message(pdf)
    result = import_email(s, current, "returned.eml", raw, revision, forwarded=True)
    root = s.get(Document, result["document_id"])
    return result, root, message_id


def test_new_thread_reuses_same_revision_pdf_and_reply_can_read_its_markups(pdf):
    with identity():
        with Session.begin() as s:
            current, _, _ = order(s)
            oid = current.id
            _, root, _ = ingest(s, current, pdf)
            first_root_id = root.id
            attachment_id = root.meta["attachment_document_ids"][0]
        drain()
        with Session.begin() as s:
            current = s.get(Order, oid)
            initial = records.view(s, current)
            annotation_ids = {
                c["id"]
                for c in initial["comments"]
                if c["document_id"] == attachment_id
            }
            assert len(annotation_ids) == 3
            original_meta = dict(s.get(Document, attachment_id).meta)
            second, root, message_id = ingest(s, current, pdf, "Rev C")
            assert root.id != first_root_id
            assert second["attachments_reused"] == 1
            assert root.meta["attachment_document_ids"] == [attachment_id]
            assert root.meta["attachment_arrivals"] == [
                {"document_id": attachment_id, "name": "E-10.pdf", "reused": True}
            ]
            assert s.get(Document, attachment_id).meta == original_meta
            incoming = receipt(s, oid, root.id, message_id)
            incoming.status = "imported"
            assert set(automatic.focus_documents(s, current, incoming)) == {
                root.id,
                attachment_id,
            }
        drain()
        with Session.begin() as s:
            current = s.get(Order, oid)
            latest = records.view(s, current)
            assert {
                c["id"] for c in latest["comments"] if c["document_id"] == attachment_id
            } == annotation_ids
            assert len([d for d in latest["documents"] if d["name"] == "E-10.pdf"]) == 1
            # An Outlook-panel generation uses the same provenance as the worker.
            draft, created = replies.begin(
                s,
                GenerateReply(order_id=oid, internet_message_id=message_id),
                "new-thread",
            )
            assert created
            assert attachment_id in draft.context["focus_document_ids"]
            assert any(
                c["document_id"] == attachment_id
                for c in draft.context["current_order"]["comments"]
            )


@pytest.mark.parametrize("origin", ["direct_upload", "lifecycle_fixture"])
def test_same_confirmed_revision_reuses_preexisting_order_pdf_from_any_arrival(
    pdf, tmp_path, origin
):
    from backend.api.main import add_document
    from backend.lifecycle.replay import _pdf_source

    with identity():
        with Session.begin() as s:
            current, _, _ = order(s)
            oid = current.id
            if origin == "direct_upload":
                existing = add_document(
                    s,
                    s.get(Project, current.project_id),
                    "Customer-return.pdf",
                    "markups",
                    pdf,
                    {"revision_label": "C"},
                )
                attachment_id = existing["id"]
            else:
                source = tmp_path / "E-10-Returned.pdf"
                source.write_bytes(pdf)
                existing, _ = _pdf_source(s, current, source, "markup")
                # Older server-created fixtures lack the newer arrival metadata.
                existing.meta = {
                    k: v for k, v in existing.meta.items() if k != "intake_order_id"
                }
                attachment_id = existing.id
        drain()
        with Session.begin() as s:
            current = s.get(Order, oid)
            before = records.view(s, current)
            assert (
                len(
                    [c for c in before["comments"] if c["document_id"] == attachment_id]
                )
                == 3
            )
            original_meta = dict(s.get(Document, attachment_id).meta)
            incoming, root, _ = ingest(s, current, pdf, "Revision C")
            root_id = root.id
            assert incoming["attachments_reused"] == 1
            assert root.meta["attachment_document_ids"] == [attachment_id]
            assert s.get(Document, attachment_id).meta == original_meta
        drain()
        with Session.begin() as s:
            current = s.get(Order, oid)
            after = records.view(s, current)
            assert len(after["comments"]) == len(before["comments"])
            assert {c["id"] for c in after["comments"]} == {
                c["id"] for c in before["comments"]
            }
            assert root_id in {d["id"] for d in after["documents"]}
            assert any(
                span["document_id"] == root_id
                and "Please review the attached drawing" in span["text"]
                for span in after["sources"]
            )
            assert any(
                event["after"].get("context_only_reason")
                for event in after["events"]
                if event.get("after") and event["after"].get("document_id") == root_id
            )


def test_eleven_comment_lifecycle_pdf_in_new_email_keeps_exact_original_comment_ids(
    tmp_path, monkeypatch
):
    from test_email_comment_context import REHEARSAL_TRANSMITTAL

    from backend.ingestion import parser
    from backend.lifecycle import replay
    from backend.storage import db

    monkeypatch.setattr(db, "DATA", tmp_path / "lifecycle")
    with identity(str(uuid4())):
        result = replay.build(number="DEMO-LIFECYCLE-1015-081854")
        with Session.begin() as s:
            current = s.get(Order, result["order_id"])
            before = records.view(s, current)
            assert len(before["comments"]) == 11
            pdf = Path(result["returned_pdf"]).read_bytes()
            mail = EmailMessage()
            mail["From"] = "reviewer@example.com"
            mail["To"] = "pm@example.com"
            mail["Subject"] = "Job DEMO-LIFECYCLE-1015-081854 · Rev C returned"
            mail["Message-ID"] = f"<{uuid4()}@example.com>"
            mail.set_content(REHEARSAL_TRANSMITTAL)
            mail.add_attachment(
                pdf,
                maintype="application",
                subtype="pdf",
                filename="E-10-Rev-C-Returned-Comments.pdf",
            )
            arrival = import_email(
                s, current, "new-customer-thread.eml", mail.as_bytes(), "C"
            )
            assert arrival["attachments_reused"] == 1
            root = s.get(Document, arrival["document_id"])
            parser.parse(s, root)
            after = records.view(s, current)
            assert len(after["comments"]) == 11
            assert {c["id"] for c in after["comments"]} == {
                c["id"] for c in before["comments"]
            }
            assert root.id not in {c["document_id"] for c in after["comments"]}
            assert root.id in {d["id"] for d in after["documents"]}
            assert any(
                source["document_id"] == root.id
                and "Please prepare the responses" in source["text"]
                for source in after["sources"]
            )


@pytest.mark.parametrize("next_revision", ["D", "", "Unspecified"])
def test_changed_or_unconfirmed_revision_is_not_collapsed(pdf, next_revision):
    with identity(), Session.begin() as s:
        current, _, _ = order(s)
        _, first, _ = ingest(s, current, pdf)
        second, later, _ = ingest(s, current, pdf, next_revision)
        assert second["attachments_reused"] == 0
        assert (
            first.meta["attachment_document_ids"]
            != later.meta["attachment_document_ids"]
        )


def test_different_content_same_revision_and_other_orders_are_distinct(pdf):
    with identity(), Session.begin() as s:
        current, _, _ = order(s)
        _, first, _ = ingest(s, current, pdf)
        modified, _, _ = ingest(s, current, pdf + b"\n% altered delivery\n")
        assert modified["attachments_reused"] == 0
        another, _, _ = order(s)
        second, root, _ = ingest(s, another, pdf)
        assert second["attachments_reused"] == 0
        assert email_document_ids(s, current, root) == []
        root.meta = {
            **root.meta,
            "attachment_document_ids": first.meta["attachment_document_ids"],
        }
        assert not set(first.meta["attachment_document_ids"]) & set(
            email_document_ids(s, another, root)
        )
    with identity(ORG_B), Session.begin() as s:
        current, _, _ = order(s)
        other_tenant, _, _ = ingest(s, current, pdf)
        assert other_tenant["attachments_reused"] == 0
