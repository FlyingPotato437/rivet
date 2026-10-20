"""A confirmed reading must guide retrieval without erasing the raw OCR."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.reply_assistant import service as replies


def record(*, confirmed):
    return {
        "order": {"id": "order", "number": "PUBLIC-CONTROL"},
        "version": 2,
        "documents": [],
        "sources": [],
        "comments": [
            {
                "id": "note",
                "number": "1",
                "original_text": "SERVICE SHOULD ENTER LEFT",
                "text": "SERVICE SHOULD ENTER RIGHT, NOT LEFT",
                "transcription_reviewed": confirmed,
                "confidence": "PM corrected transcription"
                if confirmed
                else "Needs review",
                "source_ids": [],
                "document_id": "scan",
                "reviewed": False,
                "status": "open",
            }
        ],
        "changes": [],
        "approvals": [],
    }


def test_draft_payload_distinguishes_human_transcription_from_original_ocr():
    original = record(confirmed=True)
    before = deepcopy(original)
    result = replies.reply_context("Prepare a reply", original)
    comment = result["comments"][0]
    assert comment["original_text"] == "SERVICE SHOULD ENTER LEFT"
    assert comment["text"] == "SERVICE SHOULD ENTER RIGHT, NOT LEFT"
    assert comment["transcription_reviewed"] is True
    assert comment["confidence"] == "PM corrected transcription"
    assert comment["reviewed"] is False
    assert original == before


@pytest.mark.parametrize(
    ("confirmed", "expected"),
    [
        (False, "SERVICE SHOULD ENTER LEFT"),
        (True, "SERVICE SHOULD ENTER RIGHT, NOT LEFT"),
    ],
)
def test_company_retrieval_uses_confirmed_reading_only(
    monkeypatch, confirmed, expected
):
    w = record(confirmed=confirmed)
    captured = []
    monkeypatch.setattr(replies.records, "view", lambda *_: w)
    monkeypatch.setattr(
        replies.connected_sources,
        "summary",
        lambda *_: {"status": "current", "resources": [], "fingerprint": "connected"},
    )
    from backend.reply_assistant import prepared_comments

    monkeypatch.setattr(prepared_comments, "attach", lambda *_: "")

    def assemble(_session, _order, query):
        captured.append(query)
        return {"context_hash": "approved-memory"}

    monkeypatch.setattr(replies.learning, "assemble_context", assemble)
    _, payload, _, _ = replies.context(
        None, SimpleNamespace(id="order"), "Prepare a reply"
    )
    assert captured == ["Prepare a reply\n\n" + expected]
    assert payload["comments"][0]["original_text"] == w["comments"][0]["original_text"]
    assert payload["comments"][0]["transcription_reviewed"] is confirmed
