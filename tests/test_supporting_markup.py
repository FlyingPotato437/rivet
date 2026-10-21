from backend.records.supporting_markup import separate


def test_only_no_text_support_types_are_separated_and_all_evidence_is_retained():
    sources = [
        {
            "id": kind,
            "document_id": "pdf",
            "text": "",
            "location": {"annotation_id": kind, "annotation_type": kind},
        }
        for kind in ("Line", "Highlight", "StrikeOut", "Square", "Ink")
    ]
    comments = [
        {
            "id": s["id"],
            "source_ids": [s["id"]],
            "document_id": "pdf",
            "original_text": "",
            "text": "Graphical mark",
        }
        for s in sources
    ]
    visible, supporting = separate(comments, [], sources)
    assert [c["id"] for c in visible] == ["Square", "Ink"]
    assert [c["id"] for c in supporting] == ["Line", "Highlight", "StrikeOut"]
    assert {c["id"] for c in visible + supporting} == {c["id"] for c in comments}
    # An engineer's own decision is never hidden by classification.
    visible, _ = separate([{**comments[0], "reviewed": True}], [], sources)
    assert len(visible) == 1
    visible, _ = separate(
        [{**comments[0], "response": "Keep for engineering review"}], [], sources
    )
    assert len(visible) == 1


def test_numeric_drawing_dimension_is_context_but_voltage_request_is_not():
    sources = [
        {
            "id": "native",
            "document_id": "pdf",
            "text": "Change enclosure",
            "location": {"annotation_id": "a"},
        },
        {
            "id": "body",
            "document_id": "pdf",
            "text": "29.\n5 13.8 29.5",
            "location": {"page": 6},
        },
    ]
    c = {
        "id": "c",
        "document_id": "pdf",
        "source_ids": ["body"],
        "origin": "numbered text",
        "text": "5 13.8 29.5\nPRELIMINARY DIMENSIONS",
    }
    docs = [{"id": "pdf", "name": "drawing.pdf", "role": "markup"}]
    assert len(separate([c], docs, sources)[1]) == 1
    assert (
        len(separate([{**c, "text": "13660 V\nUpdate tap voltages"}], docs, sources)[0])
        == 1
    )
    assert len(separate([c], [{**docs[0], "name": "notes.txt"}], sources)[0]) == 1
