"""Returned-drawing intake, conservative revision checks, and useful exports."""

from email.message import EmailMessage
from io import BytesIO
from uuid import uuid4

import pdfplumber
import pytest
from fastapi import HTTPException
from openpyxl import load_workbook
from test_record_scenarios import drain
from test_records import create, edit_body, post, upload

from backend.identity import Identity, identity_scope
from backend.records.intake import (
    ConfirmIntake,
    confirm,
    identifiers,
    original,
    queue,
    receive,
)
from backend.records.revision_review import review_summary
from backend.storage.db import Session
from scripts.make_order_replay import write_pdf


def incoming(client, name, data, **fields):
    response = client.post(
        "/api/records/intake",
        files={"file": (name, data)},
        data=fields,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_clear_new_job_creates_order_and_repeat_file_does_not_duplicate(
    client, tmp_path
):
    path = tmp_path / "job-24-118-return.pdf"
    write_pdf(
        path,
        "Job 24-118 | Revision C",
        ["Drawing E-10"],
        [{"id": "c7", "text": "Comment 7: Confirm cable entry."}],
    )
    first = incoming(client, path.name, path.read_bytes())
    assert first["status"] == "created" and first["order_id"]
    assert first["detected"]["revision"] == "C"
    drain()
    record = client.get(f"/api/orders/{first['order_id']}/record").json()
    assert record["order"]["number"] == "24-118"
    assert record["order"]["customer"] == "Customer not yet confirmed"
    assert any(c["text"] == "Confirm cable entry." for c in record["comments"])
    second = incoming(client, path.name, path.read_bytes())
    assert second["duplicate"] and second["order_id"] == first["order_id"]
    assert len(client.get("/api/orders").json()["orders"]) == 1


def test_exact_existing_job_routes_without_project_setup(client):
    first = incoming(
        client, "returned.txt", b"Job 24-118 Revision B\nComment 1: Confirm finish."
    )
    drain()
    later = incoming(
        client,
        "returned-next.txt",
        b"Job 24-118 Revision C\nComment 2: Confirm cable entry.",
    )
    assert later["status"] == "matched" and later["order_id"] == first["order_id"]
    assert "Exact job number" in later["reason"]


@pytest.mark.parametrize(
    "content",
    [
        b"Job 24-118 and Job 24-119 Revision C\nComment 1: Confirm finish.",
        b"Job 24-118 Rev B and Rev C\nComment 1: Confirm finish.",
        b"Job 24-118\nComment 1: Confirm finish.",
        b"Drawing E-10 Revision C\nComment 1: Confirm finish.",
    ],
)
def test_uncertain_metadata_stays_private_until_one_confirmation(client, content):
    staged = incoming(client, "returned.txt", content)
    assert staged["status"] == "needs_confirmation" and not staged["order_id"]
    assert client.get("/api/orders").json()["orders"] == []
    saved = post(
        client,
        f"/records/intake/{staged['intake_id']}/confirm",
        {
            "job_number": "24-118",
            "revision_label": "C",
            "title": "East plant switchboard",
            "customer": "Sample contractor",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "created"
    assert saved.json()["detected"]["job_numbers"] == ["24-118"]
    assert saved.json()["revision_label"] == "C"
    assert len(client.get("/api/orders").json()["orders"]) == 1


def test_email_thread_never_overrides_a_conflicting_explicit_job(client):
    email = EmailMessage()
    email["Subject"] = "Job 24-118 Revision B"
    email["Message-ID"] = "<first@example.com>"
    email.set_content("Comment 1: Confirm the finish.")
    first = incoming(client, "first.eml", email.as_bytes())
    drain()
    reply = EmailMessage()
    reply["Subject"] = "Job 24-999 Revision C"
    reply["In-Reply-To"] = "<first@example.com>"
    reply.set_content("Different job, same conversation. Comment 2: Confirm location.")
    result = incoming(client, "reply.eml", reply.as_bytes())
    assert result["status"] == "needs_confirmation"
    assert result["candidates"][0]["id"] == first["order_id"]
    assert result["candidates"][0]["identifier_conflict"]


def test_known_po_disagreement_does_not_auto_match_even_with_same_job(client):
    first = incoming(
        client, "original.txt", b"Job 24-118 PO 77120 Rev B\nComment 1: Confirm finish."
    )
    second = incoming(
        client,
        "next.txt",
        b"Job 24-118 PO 88990 Rev C\nComment 2: Confirm cable entry.",
    )
    assert second["status"] == "needs_confirmation"
    assert second["candidates"][0]["id"] == first["order_id"]
    assert second["candidates"][0]["identifier_conflict"]


def test_intake_rows_and_confirmations_are_team_scoped(client):
    row = incoming(client, "pending.txt", b"Comment 1: Confirm cable entry.")
    source = client.get(f"/api/records/intake/{row['intake_id']}/source")
    assert source.content == b"Comment 1: Confirm cable entry."
    assert source.headers["cache-control"] == "no-store"
    with identity_scope(Identity(str(uuid4()), "Other PM")):
        assert queue() == {"items": []}
        with pytest.raises(HTTPException) as source_error:
            original(row["intake_id"])
        assert source_error.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            confirm(
                row["intake_id"],
                ConfirmIntake(job_number="24-118", revision_label="C"),
                str(uuid4()),
            )
        assert exc.value.status_code == 404
        with Session.begin() as session:
            other = receive(session, "pending.txt", b"Comment 1: Confirm cable entry.")
        assert other["intake_id"] != row["intake_id"]


def test_non_identifier_words_do_not_create_jobs_or_revisions():
    data = identifiers(
        "Project requirements review drawing schedule. Please preview comments."
    )
    assert all(not values for values in data.values())


@pytest.mark.parametrize(
    "phrase",
    [
        "which revision contains the latest dimensions",
        "revision is awaiting review",
        "revision of the drawing",
        "REVISION CONTAINS MORE COMMENTS",
        "REVISION THE CUSTOMER APPROVED",
    ],
)
def test_revision_prose_is_not_a_revision_identifier(phrase):
    assert identifiers(phrase)["revisions"] == []


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("Rev. C", "C"),
        ("revision b", "B"),
        ("Revision 02", "02"),
        ("REVISION: C2", "C2"),
        ("Rev. No. 1.2", "1.2"),
        ("Revision number B", "B"),
        ("Revision IFC", "IFC"),
    ],
)
def test_explicit_revision_codes_still_match(phrase, expected):
    assert identifiers(phrase)["revisions"] == [expected]


def test_known_job_with_revision_prose_still_requires_confirmation(client):
    first = incoming(
        client,
        "original.txt",
        b"Job 24-118 Revision A\nDrawing E-10\nComment 1: Confirm finish.",
    )
    second = incoming(
        client,
        "returned.txt",
        b"Job 24-118\nDrawing E-10\nComment 4: Please confirm which revision contains the latest dimensions.",
    )
    assert second["status"] == "needs_confirmation"
    assert second["detected"]["revision"] == ""
    assert second["candidates"][0]["id"] == first["order_id"]
    third = incoming(
        client,
        "confirmed.txt",
        b"Job 24-118 Revision B\nDrawing E-10\nComment 4: Confirm finish.",
    )
    assert third["status"] == "matched"
    assert "requires confirmation" not in third["reason"]


def test_revision_checks_distinguish_exact_repeat_open_prior_and_commercial_wording(
    client,
):
    order = create(client)
    first = upload(
        client,
        order,
        "rev-b.txt",
        b"Comment 1: Confirm cable entry.\nComment 2: Confirm paint finish.",
        rev="B",
    )
    reviewed = first
    for comment in first["comments"]:
        response = post(
            client,
            f"/orders/{order['id']}/record/comments/{comment['id']}",
            edit_body(reviewed, comment, reviewed=True),
        )
        assert response.status_code == 200, response.text
        reviewed = response.json()
    approved = post(
        client,
        f"/orders/{order['id']}/record/approvals",
        {
            "expected_version": reviewed["version"],
            "actor": "Review PM",
            "reason": "Reviewed the Rev B comment record.",
            "label": "Rev B reviewed",
        },
    )
    assert approved.status_code == 200, approved.text
    current = upload(
        client,
        order,
        "rev-c.txt",
        b"Comment 7: Confirm cable entry.\nComment 8: Add a spare label.\nComment 9: Additional cost for an enclosure outside original scope.",
        rev="C",
    )
    review = current["revision_review"]
    assert review["latest_revision"] == "C" and review["prior_revision"] == "B"
    assert review["counts"] == {
        "new": 2,
        "repeated": 1,
        "unresolved": 2,
        "late_change": 3,
        "scope_change": 1,
    }
    repeat = next(f for f in review["flags"] if f["kind"] == "repeated")
    assert len(repeat["source_ids"]) == 2 and len(repeat["evidence"]) == 2
    assert repeat["prior_comment_id"] in {c["id"] for c in first["comments"]}
    assert "does not prove" in next(
        f["detail"] for f in review["flags"] if f["kind"] == "late_change"
    )
    assert "Add a spare label." not in [
        f["evidence"][0]["text"] for f in review["flags"] if f["kind"] == "scope_change"
    ]


def test_similar_wording_and_scan_placeholder_do_not_become_repeated_or_scope_findings():
    w = {
        "documents": [{"id": "b", "created_at": "1"}, {"id": "c", "created_at": "2"}],
        "sources": [],
        "approvals": [],
        "comments": [
            {
                "id": "prior",
                "document_id": "b",
                "revision": "B",
                "text": "Increase quantity to 10",
                "status": "closed",
            },
            {
                "id": "current",
                "document_id": "c",
                "revision": "C",
                "text": "Decrease quantity to 10",
                "status": "open",
            },
            {
                "id": "scan",
                "document_id": "c",
                "revision": "C",
                "text": "Unreadable scan",
                "status": "open",
                "origin": "unreadable area",
            },
        ],
    }
    summary = review_summary(w)
    assert summary["counts"] == {
        "new": 1,
        "repeated": 0,
        "unresolved": 0,
        "late_change": 0,
        "scope_change": 0,
    }


def test_latest_return_uses_arrival_order_and_negated_commercial_words_are_not_flags():
    w = {
        "documents": [
            {"id": "c", "created_at": "1"},
            {"id": "b", "created_at": "2"},
        ],
        "comments": [
            {
                "id": "c1",
                "document_id": "c",
                "revision": "C",
                "text": "Confirm label.",
                "status": "closed",
            },
            {
                "id": "b1",
                "document_id": "b",
                "revision": "B",
                "text": "No additional cost. No change order required.",
                "status": "open",
            },
        ],
    }
    summary = review_summary(w)
    assert summary["latest_revision"] == "B"
    assert summary["prior_revision"] == "C"
    assert summary["counts"]["scope_change"] == 0


def test_returned_revision_without_extractable_comments_keeps_prior_items_visible():
    w = {
        "documents": [
            {
                "id": "b",
                "created_at": "2026-10-10T10:00:00Z",
                "role": "markups",
                "revision_label": "B",
                "state": "ready",
            },
            {
                "id": "c",
                "created_at": "2026-10-11T10:00:00Z",
                "role": "markups",
                "revision_label": "C",
                "state": "ready",
            },
            {
                "id": "d",
                "created_at": "2026-10-12T10:00:00Z",
                "role": "drawing",
                "revision_label": "D",
                "state": "ready",
            },
        ],
        "comments": [
            {
                "id": "b1",
                "document_id": "b",
                "revision": "B",
                "text": "Confirm finish.",
                "status": "open",
            }
        ],
    }
    summary = review_summary(w)
    assert summary["latest_revision"] == "C" and summary["current_document_ids"] == [
        "c"
    ]
    assert summary["counts"]["unresolved"] == 1


def test_workflow_moves_require_review_and_a_saved_response_and_keep_audit(client):
    order = create(client)
    w = upload(client, order, "review.txt", b"Comment 1: Confirm cable entry.")
    c = w["comments"][0]
    endpoint = f"/orders/{order['id']}/record/comments/{c['id']}/workflow"

    def move(status):
        return post(
            client,
            endpoint,
            {
                "expected_version": w["version"],
                "actor": "Review PM",
                "reason": "Recorded the coordination handoff.",
                "workflow_status": status,
            },
        )

    assert move("sent_to_drafting").status_code == 422
    saved = post(
        client,
        f"/orders/{order['id']}/record/comments/{c['id']}",
        edit_body(w, c, reviewed=True),
    )
    assert saved.status_code == 200, saved.text
    w = saved.json()
    moved = move("sent_to_drafting")
    assert moved.status_code == 200, moved.text
    w = moved.json()
    assert w["comments"][0]["workflow_status"] == "sent_to_drafting"
    assert w["events"][0]["after"]["workflow_status"] == "sent_to_drafting"
    assert move("resolved").status_code == 422
    draft = client.get(f"/api/orders/{order['id']}/record/export?format=drafting_xlsx")
    assert draft.status_code == 200
    rows = list(load_workbook(BytesIO(draft.content)).active.values)
    assert rows[2][2] == "Confirm cable entry."
    assert (
        client.get(f"/api/orders/{order['id']}/record/export?format=xlsx").status_code
        == 200
    )


def test_response_letter_omits_unreviewed_responses_and_contains_saved_reviewed_work(
    client,
):
    order = create(client)
    w = upload(
        client,
        order,
        "review.txt",
        b"Comment 1: Confirm cable entry.\nComment 2: Confirm paint finish.",
    )
    first, second = w["comments"]
    saved = post(
        client,
        f"/orders/{order['id']}/record/comments/{first['id']}",
        edit_body(
            w,
            first,
            reviewed=True,
            response="Cable entry confirmed by engineering.",
            responder="Casey Engineer",
            status="responded",
        ),
    )
    assert saved.status_code == 200, saved.text
    saved = post(
        client,
        f"/orders/{order['id']}/record/comments/{second['id']}",
        edit_body(
            saved.json(),
            second,
            response="UNREVIEWED_PROMISE_SHOULD_NOT_LEAK",
            responder="Unconfirmed person",
        ),
    )
    assert saved.status_code == 200, saved.text
    response = client.get(
        f"/api/orders/{order['id']}/record/export?format=response_pdf"
    )
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    with pdfplumber.open(BytesIO(response.content)) as pdf:
        text = " ".join(" ".join((p.extract_text() or "").split()) for p in pdf.pages)
    assert "Cable entry confirmed by engineering." in text
    assert "UNREVIEWED_PROMISE" not in text
    assert "1 reviewed responses included" in text and "DRAFT" in text
