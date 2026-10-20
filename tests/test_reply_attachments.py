"""Authenticated draft preparation only; no real Microsoft or mail-provider calls."""

import base64

import pytest
from fastapi import HTTPException
from test_company_context import ORG_B, identity, order
from test_reply_assistant import fixture_order, prepare, receipt

from backend.ingestion.parser import persist_blob
from backend.reply_assistant import attachments, service
from backend.reply_assistant.models import ReplyDraft
from backend.reply_assistant.schemas import ComposeReply, ReviewReply
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document
from scripts.make_order_replay import write_pdf


def pdf(text="Fictional supporting drawing E-10. Not an engineering design."):
    path = db.BLOBS / "attachment-test-generated.pdf"
    write_pdf(path, "Fictional supporting document", [text])
    return path.read_bytes()


def original(s, document_id, payload=None, name="Drawing E-10 Rev C.pdf"):
    payload = payload or pdf()
    row = s.get(Document, document_id)
    row.name = name
    row.blob, row.sha256 = persist_blob(payload, ".pdf")
    return payload


@pytest.fixture
def draft(monkeypatch):
    with identity():
        order_id, document_id, source_id = fixture_order()
        with Session.begin() as s:
            payload = original(s, document_id)
            receipt(s, order_id, document_id)
        result = prepare(
            monkeypatch,
            order_id,
            source_id,
            internet_message_id="<fixture@example.com>",
        )
        assert result["status"] == "ready"
        return result, document_id, payload


def review(result, document_ids=()):
    with Session.begin() as s:
        return service.review(
            s,
            result["id"],
            ReviewReply(
                body=result["body"],
                decision="approved",
                expected_evidence_hash=result["evidence_hash"],
                attachment_ids=list(document_ids),
            ),
        )


def compose(result, message_id="<fixture@example.com>"):
    with Session.begin() as s:
        return service.compose(
            s,
            result["id"],
            ComposeReply(
                expected_evidence_hash=result["evidence_hash"],
                internet_message_id=message_id,
            ),
        )


def test_supporting_original_is_not_selected_until_pm_reviews_and_bytes_are_private(
    draft,
):
    result, document_id, payload = draft
    with identity():
        assert result["selected_attachments"] == []
        candidate = result["attachment_candidates"][0]
        assert candidate["document_id"] == document_id and candidate["eligible"]
        assert "url" not in candidate and "base64" not in candidate
        reviewed = review(result, [document_id])
        assert reviewed["selected_attachments"][0]["sha256"] == candidate["sha256"]
        composed = compose(result)
        assert composed["body"] == result["body"] and composed["sent"] is False
        assert base64.b64decode(composed["attachments"][0]["base64"]) == payload
        assert composed["attachments"][0]["size_bytes"] == len(payload)


def test_body_only_reply_does_not_attach_any_candidate(draft):
    with identity():
        review(draft[0])
        assert compose(draft[0])["attachments"] == []


def test_unreviewed_or_different_message_cannot_receive_compose_bundle(draft):
    with identity():
        with pytest.raises(HTTPException) as error:
            compose(draft[0])
        assert error.value.status_code == 409
        review(draft[0], [draft[1]])
        with pytest.raises(HTTPException) as error:
            compose(draft[0], "<different-message@example.com>")
        assert error.value.status_code == 409


def test_attachment_order_and_tenant_isolation(draft):
    with identity():
        with Session.begin() as s:
            _, foreign, _ = order(s)
            original(s, foreign.id)
            foreign_id = foreign.id
        with pytest.raises(HTTPException) as error:
            review(draft[0], [foreign_id])
        assert error.value.status_code == 403
        review(draft[0], [draft[1]])
    with identity(ORG_B):
        with pytest.raises(HTTPException) as error:
            compose(draft[0])
        assert error.value.status_code == 404


def test_changed_original_blocks_review_and_later_compose(draft):
    with identity():
        with Session() as s:
            file = db.BLOBS / s.get(Document, draft[1]).blob
        file.write_bytes(pdf("A different original appeared after draft generation."))
        with pytest.raises(HTTPException) as error:
            review(draft[0], [draft[1]])
        assert error.value.status_code == 409
        file.write_bytes(draft[2])
        review(draft[0], [draft[1]])
        file.write_bytes(pdf("A different original appeared after PM approval."))
        with pytest.raises(HTTPException) as error:
            compose(draft[0])
        assert error.value.status_code == 409


def test_internal_and_historical_files_are_explicitly_labeled_never_preselected(
    monkeypatch,
):
    with identity():
        order_id, document_id, source_id = fixture_order()
        with Session.begin() as s:
            original(s, document_id, name="Internal supplier quote Rev B.pdf")
            doc = s.get(Document, document_id)
            doc.meta = {**doc.meta, "order_role": "quote", "revision_label": "B"}
        result = prepare(monkeypatch, order_id, source_id)
        candidate = result["attachment_candidates"][0]
        assert any("commercial" in w for w in candidate["warnings"])
        assert result["selected_attachments"] == []


@pytest.mark.parametrize(
    "name,payload",
    [
        ("malware.exe", b"MZ fake program"),
        ("disguised.pdf", b"<html>not a PDF</html>"),
        ("spreadsheet.xlsx", b"not an office package"),
    ],
)
def test_unsupported_or_disguised_file_is_not_approved(monkeypatch, name, payload):
    with identity():
        order_id, document_id, source_id = fixture_order()
        with Session.begin() as s:
            original(s, document_id, payload, name)
        result = prepare(monkeypatch, order_id, source_id)
        with pytest.raises(HTTPException) as error:
            review(result, [document_id])
        assert error.value.status_code == 409


def test_duplicate_selection_and_combined_size_are_bounded(draft, monkeypatch):
    with identity():
        with pytest.raises(HTTPException):
            review(draft[0], [draft[1], draft[1]])
        monkeypatch.setattr(attachments, "MAX_TOTAL_BYTES", len(draft[2]) - 1)
        with pytest.raises(HTTPException):
            review(draft[0], [draft[1]])


def test_removed_original_and_path_escape_cannot_be_attached(draft):
    with identity():
        with Session.begin() as s:
            doc = s.get(Document, draft[1])
            doc.blob = "../outside.pdf"
        with pytest.raises(HTTPException) as error:
            review(draft[0], [draft[1]])
        assert error.value.status_code == 409


def test_reviewed_attachment_selection_is_immutable(draft):
    with identity():
        review(draft[0], [draft[1]])
        with pytest.raises(HTTPException) as error:
            review(draft[0], [])
        assert error.value.status_code == 409
        with Session() as s:
            assert len(s.get(ReplyDraft, draft[0]["id"]).review["attachments"]) == 1


def test_captured_email_question_retrieves_related_history_over_generic_newer_text():
    documents = [
        {
            "id": "email",
            "name": "current.eml",
            "role": "email",
            "state": "ready",
            "created_at": "2026-10-13",
            "revision_label": "C",
        }
    ]
    sources = [
        {
            "id": "incoming",
            "document_id": "email",
            "text": "Can you confirm the E-10 interlock selector?",
            "location": {},
        }
    ]
    for i in range(100):
        documents.append(
            {
                "id": f"irrelevant-{i}",
                "name": "package.pdf",
                "role": "drawing",
                "state": "ready",
                "created_at": "2026-10-14",
                "revision_label": "D",
            }
        )
        sources.append(
            {
                "id": f"source-{i}",
                "document_id": f"irrelevant-{i}",
                "text": "Please prepare a response to returned comments about finish color.",
                "location": {},
            }
        )
    documents.append(
        {
            "id": "earlier",
            "name": "E-10 Rev B.pdf",
            "role": "drawing",
            "state": "ready",
            "created_at": "2026-09-01",
            "revision_label": "B",
        }
    )
    sources.append(
        {
            "id": "relevant-history",
            "document_id": "earlier",
            "text": "E-10 interlock selector contact layout. Revision B source drawing.",
            "location": {"page": 10},
        }
    )
    w = {
        "order": {},
        "version": 1,
        "documents": documents,
        "sources": sources,
        "comments": [],
        "changes": [],
        "approvals": [],
    }
    context = service.reply_context(
        "Prepare a response to the returned comments.", w, ["email"]
    )
    assert context["sources"][0]["id"] == "incoming"
    assert "relevant-history" in {x["id"] for x in context["sources"]}
    assert context["coverage"]["included_sources"] < context["coverage"]["source_count"]


def test_earlier_record_approval_does_not_silently_approve_later_amendments():
    w = {
        "order": {},
        "version": 9,
        "documents": [],
        "sources": [],
        "changes": [],
        "comments": [
            {"id": "changed", "original_text": "Change the voltage", "source_ids": []},
            {"id": "same", "original_text": "Finish agreed", "source_ids": []},
        ],
        "approvals": [
            {"id": "approval-b", "label": "Record Rev B", "at": "2026-10-01"}
        ],
        "connections": {
            "latest_approval_id": "approval-b",
            "after_approval": {"comment_ids": ["changed"]},
            "comments": {
                "changed": {
                    "in_approved_record": True,
                    "matches_approved_record": False,
                },
                "same": {"in_approved_record": True, "matches_approved_record": True},
            },
        },
    }
    context = service.reply_context("What was approved?", w)
    status = {c["id"]: c["approval_scope"] for c in context["comments"]}
    assert status == {
        "changed": "amended_since_record_approval",
        "same": "unchanged_in_approved_record",
    }
    assert context["approval_state"]["changes_since_record_approval"][
        "comment_ids"
    ] == ["changed"]


def test_long_page_retrieves_matching_passage_and_discloses_truncation():
    source = {
        "id": "large-source",
        "document_id": "large",
        "text": "General arrangement. " * 200
        + "The unique interlock ZX204 uses a keyed selector."
        + " Detail." * 100,
        "location": {"page": 4},
    }
    w = {
        "order": {},
        "version": 1,
        "documents": [{"id": "large", "name": "drawing.pdf"}],
        "sources": [source],
        "comments": [],
        "changes": [],
        "approvals": [],
    }
    context = service.reply_context("What does ZX204 specify?", w)
    selected = context["sources"][0]
    assert "ZX204" in selected["text"] and selected["excerpt_start"] > 0
    assert selected["text_truncated"] and context["coverage"]["truncated_sources"] == 1


def test_recommends_only_cited_released_drawing_and_still_requires_pm_approval(
    monkeypatch,
):
    from backend.domain.service import get
    from backend.storage.models import Order

    with identity():
        order_id, document_id, source_id = fixture_order()
        with Session.begin() as s:
            original(s, document_id, name="E-10 released.pdf")
            doc = s.get(Document, document_id)
            doc.meta = {
                **doc.meta,
                "connected_source": {
                    "kind": "vault",
                    "lifecycle": "released",
                    "family": "E-10",
                },
                "order_role": "drawing",
            }
            o = get(s, Order, order_id)
            snapshot = {
                "current_order": {
                    "sources": [{"id": source_id, "document_id": document_id}],
                    "connected_context": {"resources": [{"document_id": document_id}]},
                }
            }
            snapshot["attachment_candidates"] = attachments.candidates(
                s, o, snapshot, [{"id": source_id}]
            )
            assert attachments.recommend(s, o, snapshot) == [document_id]
            # Retrieval of a draft, internal commercial original or uncited history
            # never silently widens the proposed outgoing attachment set.
            doc.meta = {
                **doc.meta,
                "connected_source": {"kind": "vault", "lifecycle": "draft"},
            }
            snapshot["attachment_candidates"] = attachments.candidates(
                s, o, snapshot, [{"id": source_id}]
            )
            assert attachments.recommend(s, o, snapshot) == []
            doc.meta = {
                **doc.meta,
                "connected_source": {"kind": "vault", "lifecycle": "released"},
            }
            doc.name = "Internal quote E-10.pdf"
            snapshot["attachment_candidates"] = attachments.candidates(
                s, o, snapshot, [{"id": source_id}]
            )
            assert attachments.recommend(s, o, snapshot) == []
            doc.name = "E-10 released.pdf"
            snapshot["attachment_candidates"] = attachments.candidates(
                s, o, snapshot, []
            )
            assert attachments.recommend(s, o, snapshot) == []
