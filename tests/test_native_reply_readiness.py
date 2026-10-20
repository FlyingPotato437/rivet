from backend.reply_assistant.service import _rank_sources, _source_processing


def test_parsed_native_cad_does_not_block_a_text_cited_reply():
    blocked, warnings = _source_processing(
        {
            "documents": [
                {"id": "cad", "name": "issued.dxf", "state": "parsed"},
                {"id": "spec", "name": "spec.pdf", "state": "ready"},
            ],
            "sources": [{"document_id": "spec", "text": "CB-01 trip: 125 A"}],
        }
    )
    assert blocked == []
    assert len(warnings) == 1
    assert warnings[0]["document_id"] == "cad"
    assert "not source-text evidence" in warnings[0]["warning"]


def test_unfinished_cad_is_still_blocking():
    blocked, warnings = _source_processing(
        {
            "documents": [{"id": "cad", "name": "issued.dxf", "state": "queued"}],
            "sources": [],
        }
    )
    assert len(blocked) == 1
    assert warnings == []


def test_reply_retrieves_for_the_extracted_question_after_drawing_boilerplate():
    docs = {"incoming": {"name": "returned.pdf"}, "spec": {"name": "spec.pdf"}}
    boilerplate = [
        {
            "id": str(i),
            "document_id": "incoming",
            "text": "Customer drawing requirements for the order",
        }
        for i in range(50)
    ]
    w = {
        "comments": [
            {
                "document_id": "incoming",
                "text": "C1: Confirm CB-01 trip 125 A instead of 150 A",
                "source_ids": ["note"],
            }
        ],
        "sources": [
            *boilerplate,
            {
                "id": "note",
                "document_id": "incoming",
                "text": "C1: Confirm CB-01 trip 125 A instead of 150 A",
            },
            {"id": "rating", "document_id": "spec", "text": "Trip: 125 A"},
        ],
    }
    ranked = _rank_sources("Prepare a customer response", w, {"incoming"}, docs)
    assert "rating" in {s["id"] for s in ranked[:40]}
