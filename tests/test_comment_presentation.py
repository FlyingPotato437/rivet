from copy import deepcopy

from backend.records.presentation import comment_presentation


def item(id, document, text, **kw):
    return {
        "id": id,
        "document_id": document,
        "text": text,
        "original_text": text,
        "number": "1",
        "origin": "email",
        "reviewed": False,
        "response": "",
        **kw,
    }


def test_cover_is_email_context_and_original_source_numbers_are_retained():
    comments = [
        item(
            "cover",
            "email",
            "Please review the attached comments. Fictional product demonstration.",
        ),
        item("pdf", "drawing", "Confirm the cable entry.", origin="annotation"),
    ]
    original = deepcopy(comments)
    visible, context = comment_presentation(
        comments, [{"id": "email", "attachment_document_ids": ["drawing"]}]
    )
    assert [c["id"] for c in visible] == ["pdf"]
    assert context[0]["id"] == "cover"
    assert visible[0]["log_reference"] == "C-002"
    assert visible[0]["number"] == "1"
    assert comments == original


def test_log_ids_do_not_reset_for_different_sources_or_revisions():
    comments = [
        item("a", "doc-a", "First.", revision="B"),
        item("b", "doc-b", "Second.", revision="C"),
        item("c", "doc-c", "Third.", revision="C"),
    ]
    visible, _ = comment_presentation(comments, [])
    assert [c["log_reference"] for c in visible] == ["C-001", "C-002", "C-003"]
    assert [c["number"] for c in visible] == ["1", "1", "1"]


def test_independent_requests_and_human_decisions_are_never_hidden():
    comments = [
        item(
            "request",
            "email",
            "Please review the attached drawing. Confirm production release.",
        ),
        item("reviewed", "email", "Please review the attached drawing.", reviewed=True),
        item(
            "answered",
            "email",
            "Please review the attached drawing.",
            response="Noted by the PM.",
        ),
        item(
            "edited",
            "email",
            "Please review the attached drawing.",
            original_text="Old wording",
        ),
    ]
    visible, context = comment_presentation(
        comments, [{"id": "email", "attachment_document_ids": ["drawing"]}]
    )
    assert len(visible) == 4
    assert not context


def test_display_ids_do_not_invalidate_prepared_work():
    from backend.records.agent_service import input_hash
    from backend.reply_assistant.service import fingerprint

    w = {
        "comments": [item("a", "doc", "Confirm the location.")],
        "documents": [],
        "sources": [],
        "approvals": [],
        "changes": [],
    }
    before = fingerprint(w), input_hash(w)
    w["comments"][0]["log_reference"] = "C-001"
    assert before == (fingerprint(w), input_hash(w))
    w["comments"][0]["text"] = "The customer changed the requirement."
    assert before != (fingerprint(w), input_hash(w))
