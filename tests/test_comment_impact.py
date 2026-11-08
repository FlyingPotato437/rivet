from io import BytesIO
from zipfile import ZipFile

from openpyxl import load_workbook

from backend.records.comment_impact import _estimate, classify
from tests.test_records import create, upload


def test_comment_kinds_follow_what_the_customer_asked():
    assert classify("CB-01 trip must be 125 A, not 150 A. Update drawing and BOM.") == {
        "kind": "change",
        "engineer": True,
        "reasons": ["requests a drawing or BOM change", "protection setting"],
        "tags": ["CB-01"],
    }
    assert (
        classify("Confirm the cable entry location on drawing E-10.")["kind"]
        == "question"
    )
    assert classify("NO EXCEPTION TAKEN")["kind"] == "no_action"
    assert (
        classify("A graphical annotation has no extractable text.")["kind"] == "review"
    )
    scope = classify("Add a second 24 VDC power supply for redundancy per UL 508A.")
    assert scope["kind"] == "scope" and scope["engineer"]
    assert classify("Substitute Allen-Bradley 1489-M for the specified breaker.") == {
        "kind": "change",
        "engineer": True,
        "reasons": ["requests a drawing or BOM change", "component substitution"],
        "tags": [],
    }


def test_documentation_additions_do_not_establish_added_equipment_scope():
    for text in (
        "Add transformer weights and gallons of oil",
        "Add tap positions for tertiary winding. Add details of internal connections.",
        "Add symbols for HV & TV tap changer locations. Add additional info for CTs.",
        "Add notation that the transformer must be de-energized.",
        'Add: "automatically controlled by contacts on the hot-spot temperature gauge."',
        "Add TV tap changer location",
        "Missing additional BCT: 1000/2000:5 ampere, C800 accuracy",
    ):
        assert classify(text)["kind"] == "change", text
    # A mixed request must still retain its distinct equipment addition.
    assert classify("Add a safety note and add one spare breaker.")["kind"] == "scope"


BOOK = {
    "prices": [
        {"tag": "CB-12", "unit_cost": "1200.00", "gross_margin_percent": "25.00", "currency": "USD", "source_id": "p1", "document": "ERP-price-CB-12.json"},
        {"tag": "CB-EXTRA", "unit_cost": "900.00", "gross_margin_percent": "25.00", "currency": "USD", "source_id": "p2", "document": "ERP-price-CB-EXTRA.json"},
    ],
    "scope": [
        {"scope_tag": "CB-EXTRA", "billable_scope_tag": "CB-EXTRA", "billable": True, "notice": "Contract states extra breaker is excluded.", "source_id": "s1", "document": "Accepted-scope.json"},
    ],
}  # fmt: skip


def test_added_scope_is_priced_only_from_the_sentence_that_adds_it():
    moved_and_added = _estimate(
        "Move CB-12 lower in the compartment. Also add one extra breaker; exception EX-02 excludes it.",
        BOOK,
    )
    # CB-12 is only moved; the added breaker is the excluded item on the accepted-scope record.
    assert moved_and_added["tag"] == "CB-EXTRA"
    assert moved_and_added["estimate"] == "1200.00"
    assert moved_and_added["billable"] == "per accepted scope"
    quoted = _estimate("Please quote the two CB-12 circuit-breaker packages.", BOOK)
    assert (quoted["tag"], quoted["quantity"], quoted["estimate"]) == (
        "CB-12",
        "2",
        "3200.00",
    )
    assert quoted["billable"] == "unconfirmed"
    # No equipment in common with the scope record and no tag: never borrow a price.
    assert _estimate(
        "Please distinguish the base scope from an extra-price option.", BOOK
    ) == {"status": "needs_pricing"}
    assert _estimate("Add a spare 20 A breaker.", {"prices": [], "scope": []}) == {
        "status": "needs_pricing"
    }


def test_record_view_and_exports_carry_comment_types(client):
    o = create(client)
    w = upload(
        client,
        o,
        "review.txt",
        b"Comment 1: Confirm the paint finish.\nComment 2: Add one spare 20 A breaker in panel LP-1.\nComment 3: CB-4 trip must be 60 A.",
    )
    kinds = {c["number"]: c["impact"]["kind"] for c in w["comments"]}
    assert kinds == {"1": "question", "2": "scope", "3": "change"}
    summary = w["comment_impact"]
    assert summary["counts"]["scope"] == 1 and summary["needs_pricing"] == 1
    assert summary["estimated_total"] is None  # no trusted ERP price, no number
    assert summary["engineer"] == ["3"]

    excel = client.get(f"/api/orders/{o['id']}/record/export")
    rows = list(load_workbook(BytesIO(excel.content)).active.values)
    assert rows[1][-4:] == (
        "Type",
        "Engineer review required",
        "Cost impact",
        "Recorded status",
    )
    spare = next(r for r in rows if r[0] == "2")
    assert spare[-4:] == (
        "Added scope",
        None,
        "Adds scope; needs pricing",
        "open",
    )

    word = client.get(f"/api/orders/{o['id']}/record/export?format=docx")
    assert word.status_code == 200
    assert word.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert 'filename="rivet-comment-matrix.docx"' in word.headers["content-disposition"]
    with ZipFile(BytesIO(word.content)) as z:
        document = z.read("word/document.xml").decode()
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(
            z.namelist()
        )
    assert "Comment response matrix" in document
    assert "Add one spare 20 A breaker" in document and "Added scope" in document
    assert "DRAFT" in document
