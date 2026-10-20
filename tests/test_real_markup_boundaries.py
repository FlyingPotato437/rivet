"""Failures observed on real public electrical review pages."""

from backend.ingestion.markup_classification import (
    actionability,
    annotation_actionability,
    atomic_findings,
)


def test_three_real_numbered_notes_remain_three_comments_with_honest_shared_area():
    text = (
        "1. Matt to review alternate Legrand Occupancy Sensor.\n"
        "2. Matt, TBIL & Lamarche Review Panel P1 Re & Re on site, c/w panel enclosure box size limitations.\n"
        "3. Lamarche to ensure panel cover (NC32S) is lockable."
    )
    area = [0.35, 0.82, 0.91, 0.92]
    findings = atomic_findings(
        [{"text": text, "kind": "review comment", "bbox": area, "confidence": "high"}]
    )
    assert len(findings) == 3
    assert [f["subcomment_index"] for f in findings] == [1, 2, 3]
    assert all(f["bbox"] == area and f["shared_parent_bbox"] for f in findings)
    assert all(f["parent_text"] == text for f in findings)


def test_real_stamps_and_product_selections_are_evidence_not_technical_requests():
    assert actionability({"kind": "review stamp", "text": "X REVIEWED"}) is False
    assert (
        actionability({"kind": "red overprint", "text": "BLR2X44L840UNVL15"}) is False
    )
    assert (
        actionability({"kind": "handwritten signature", "text": "Kevin Mcwade"})
        is False
    )
    assert (
        actionability(
            {"kind": "review comment", "text": "- Light Fixtures - No comment"}
        )
        is False
    )
    assert (
        actionability(
            {
                "kind": "review_comment",
                "text": "Electrical panel shall be 100A,120/208V,1PH",
            }
        )
        is True
    )
    assert (
        actionability({"kind": "ambiguous_mark", "text": "Illegible marked area"})
        is None
    )


def test_nonsequential_numbers_and_paragraphs_are_not_invented_subcomments():
    original = {"kind": "review_comment", "text": "1. First\n3. Third"}
    assert atomic_findings([original]) == [original]


def test_native_admin_notes_remain_metadata_and_short_marks_remain_unknown():
    assert (
        annotation_actionability(
            {"kind": "FreeText", "text": "Rec by Jacobs CA 8/4/20 EE-12"}
        )[0]
        is False
    )
    assert (
        annotation_actionability({"kind": "FreeText", "text": "1086M LOR trip"})[0]
        is True
    )
    assert annotation_actionability({"kind": "FreeText", "text": "LWS"})[0] is None
    assert annotation_actionability({"kind": "FreeText", "text": "X"})[0] is None
    original = {"kind": "review_stamp", "text": "1. Review terms\n2. Contract terms"}
    assert atomic_findings([original]) == [original]
