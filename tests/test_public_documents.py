"""Opt-in primary-source regression tests; originals stay out of the repository."""

import os
from pathlib import Path

import pytest
from test_records import create, upload, view

from backend.orders.extraction import parse_pdf_annotations

ROOT = Path(__file__).resolve().parents[1] / ".data" / "validation"
pytestmark = pytest.mark.skipif(
    os.environ.get("RIVET_PUBLIC_DOCUMENTS") != "1",
    reason="Run scripts/prepare_public_documents.py then RIVET_PUBLIC_DOCUMENTS=1 uv run pytest tests/test_public_documents.py -q",
)


def test_alachua_actual_drawing_callouts(client):
    path = ROOT / "alachua-addendum-excerpt.pdf"
    annotations = parse_pdf_annotations(path)
    assert len(annotations) == 13
    o = create(client)
    w = upload(client, o, path.name, path.read_bytes(), rev="Public review excerpt")
    # Drawing labels and numbered installation notes must not become customer comments.
    assert len(w["comments"]) == 13
    assert {c["original_text"] for c in w["comments"]} == {
        a["text"] for a in annotations
    }
    sources = {s["id"]: s for s in w["sources"]}
    for c in w["comments"]:
        sp = sources[c["source_ids"][0]]
        assert sp["location"]["bbox"]
        assert sp["location"]["annotation_id"]
        assert c["author"]
        assert c["authored_at"]
        assert c["status"] == "open" and not c["reviewed"]
    print(
        f"Alachua: {len(w['comments'])} original callouts with source areas, authors and dates"
    )


def test_avta_scanned_handwritten_review_is_not_silently_complete(client):
    path = ROOT / "avta-board-package-excerpt.pdf"
    o = create(client)
    w = upload(client, o, path.name, path.read_bytes(), rev="Public review excerpt")
    unreadable = [c for c in w["comments"] if c["origin"] == "unreadable area"]
    assert unreadable
    review = next(c for c in unreadable if c["source_page"] == 3)
    assert review["source_ids"] and not review["reviewed"]
    assert not review["original_text"] and not review["author"]
    source = next(s for s in w["sources"] if s["id"] == review["source_ids"][0])
    assert source["location"]["page"] == 3
    assert source["location"]["bbox"] == [0, 0, 1, 1]
    assert w["approvals"] == []
    print(
        f"AVTA: scanned handwritten review linked to excerpt p.3 (original p.79); {len(unreadable)} areas require transcription"
    )


def test_full_package_limits_are_explicit(client):
    o = create(client)
    path = ROOT / "alachua-addendum.pdf"
    response = client.post(
        f"/api/projects/{o['project_id']}/documents",
        files={"file": (path.name, path.read_bytes(), "application/pdf")},
        headers={"Idempotency-Key": "public-page-limit-test"},
    )
    assert response.status_code == 422 and "150 pages" in response.text
    assert view(client, o)["documents"] == []
    path = ROOT / "avta-board-package.pdf"
    response = client.post(
        f"/api/projects/{o['project_id']}/documents",
        files={"file": (path.name, path.read_bytes(), "application/pdf")},
        headers={"Idempotency-Key": "public-oversize-test"},
    )
    assert response.status_code in {413, 422}
    assert "20" in response.text


def test_real_document_review_response_change_approval_and_export(client):
    from io import BytesIO
    from openpyxl import load_workbook
    from test_records import post, edit_body

    o = create(client)
    path = ROOT / "alachua-addendum-excerpt.pdf"
    w = upload(client, o, path.name, path.read_bytes(), rev="Public review excerpt")
    base = f"/orders/{o['id']}/record"
    original = {c["id"]: c["original_text"] for c in w["comments"]}
    for c in list(w["comments"]):
        response = post(
            client,
            base + "/comments/" + c["id"],
            edit_body(
                w,
                c,
                reviewed=True,
                target_source_id=c["source_ids"][0],
                response="Automated workflow test only: source annotation logged; engineering disposition remains open.",
                responder="Test PM",
                status="responded",
            ),
        )
        assert response.status_code == 200, response.text
        w = response.json()
    first = w["comments"][0]
    result = post(
        client,
        base + "/changes",
        {
            "expected_version": w["version"],
            "actor": "Test PM",
            "reason": "Test traceability using the public source; no engineering change asserted.",
            "title": "Demo communication follow-up",
            "before": "Response not recorded",
            "after": "Comment assigned for follow-up",
            "comment_ids": [first["id"]],
            "requested_by": "Test PM",
        },
    )
    assert result.status_code == 200, result.text
    w = result.json()
    assert w["connections"]["comments"][first["id"]]["change_ids"] == [
        w["changes"][0]["id"]
    ]
    approval = post(
        client,
        base + "/approvals",
        {
            "expected_version": w["version"],
            "actor": "Test PM",
            "reason": "Test record approval, not equipment approval.",
            "label": "Automated test snapshot",
        },
    )
    assert approval.status_code == 200, approval.text
    w = approval.json()
    assert not any(w["connections"]["after_approval"].values())
    share = post(
        client,
        base + "/shares",
        {
            "expected_version": w["version"],
            "actor": "Test PM",
            "reason": "Test frozen record.",
            "approval_id": w["approvals"][-1]["id"],
        },
    )
    assert share.status_code == 200
    shared_path = "/api/shared/" + share.json()["token"]
    frozen = client.get(shared_path).json()
    assert len(frozen["comments"]) == 13
    excel = client.get("/api" + base + "/export?format=xlsx")
    assert excel.status_code == 200
    sheets = load_workbook(BytesIO(excel.content))
    assert any("Confirm 1087B" in row for row in sheets.active.values)
    pdf = client.get("/api" + base + "/export?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    updated = post(
        client,
        base + "/comments/" + first["id"],
        edit_body(w, first, response="Later test response"),
    )
    assert updated.status_code == 200
    changed = updated.json()
    assert changed["connections"]["after_approval"]["comment_ids"] == [first["id"]]
    assert not changed["connections"]["comments"][first["id"]][
        "matches_approved_record"
    ]
    assert {c["id"]: c["original_text"] for c in changed["comments"]} == original
    assert client.get(shared_path).json() == frozen
