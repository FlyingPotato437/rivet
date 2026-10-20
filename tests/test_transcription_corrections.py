"""Correction storage and approval boundaries; controls, not document accuracy."""

from types import SimpleNamespace

import pytest

from backend.records.revision_review import source_wording
from backend.records.transcription_corrections import correction_example


def sample():
    return (
        {
            "id": "comment",
            "text": "LINE UP",
            "original_text": "LINE UP",
            "source_ids": ["span"],
            "source_page": 79,
        },
        SimpleNamespace(id="document", sha256="a" * 64),
        [
            {
                "id": "span",
                "document_id": "document",
                "location": {"page": 79, "bbox": [0.1, 0.2, 0.5, 0.3]},
            }
        ],
    )


def test_human_label_keeps_original_text_and_source_but_does_not_approve_engineering():
    comment, document, sources = sample()
    result = correction_example(
        comment, document, sources, "LINEUP", "PM", "2026-10-18T16:00:00-07:00"
    )
    assert result["original_extracted_text"] == comment["text"] == "LINE UP"
    assert result["human_text"] == "LINEUP"
    assert result["source_sha256"] == document.sha256
    assert result["source_locations"][0]["location"]["page"] == 79
    assert result["engineering_approved"] is False
    assert result["cross_company_training_allowed"] is False
    assert "reviewed" not in comment


def test_label_cannot_silently_rebind_to_another_document_or_empty_edit():
    comment, document, sources = sample()
    with pytest.raises(ValueError, match="original order source"):
        correction_example(
            comment,
            SimpleNamespace(id="other", sha256="b" * 64),
            sources,
            "changed",
            "PM",
            "now",
        )
    with pytest.raises(ValueError, match="corrected transcription"):
        correction_example(comment, document, sources, " LINE UP ", "PM", "now")


def test_only_a_confirmed_transcription_changes_revision_matching_wording():
    comment = {"text": "LINEUP", "original_text": "LINE UP"}
    assert source_wording(comment) == "LINE UP"
    assert source_wording({**comment, "transcription_reviewed": True}) == "LINEUP"
