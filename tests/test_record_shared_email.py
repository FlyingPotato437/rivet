"""Public email sources expose approved excerpts, never an original MIME inbox."""

from copy import deepcopy
from email.message import EmailMessage

from sqlalchemy import select
from test_records import create, edit_body, post, upload, view

from backend.domain.service import get
from backend.records.email_intake import import_email
from backend.storage.db import Session
from backend.storage.models import Order, RecordShare, Span
from backend.worker import claim, process


def reviewed_email_share(client):
    order = create(client)
    message = EmailMessage()
    message["From"] = "Review author <author@example.com>"
    message["To"] = "private-recipient@example.com"
    message["Cc"] = "private-observer@example.com"
    message["Bcc"] = "private-internal@example.com"
    message["Subject"] = "Customer review"
    message["Message-ID"] = "<private-internal-routing@example.com>"
    message.set_content("Comment 7: Please confirm cable entry on drawing E-10.")
    message.add_attachment(
        b"tag,cost,internal_margin\nCB-12,9000,PRIVATE_MARGIN_47\n",
        maintype="text",
        subtype="csv",
        filename="private-pricing.csv",
    )
    message.add_attachment(
        b"PRIVATE_CAD_EMBEDDED_CONTENT",
        maintype="application",
        subtype="octet-stream",
        filename="private-design.dwg",
    )
    original = message.as_bytes()
    with Session.begin() as s:
        imported = import_email(
            s,
            get(s, Order, order["id"]),
            "customer-review.EML",
            original,
            "B",
            forwarded=True,
        )
    while job := claim():
        process(job)
    workspace = view(client, order)
    assert len(workspace["comments"]) == 1
    comment = workspace["comments"][0]
    base = f"/orders/{order['id']}/record"
    reviewed = post(
        client,
        base + "/comments/" + comment["id"],
        edit_body(workspace, comment, reviewed=True),
    )
    assert reviewed.status_code == 200, reviewed.text
    workspace = reviewed.json()
    approved = post(
        client,
        base + "/approvals",
        {
            "expected_version": workspace["version"],
            "actor": "Review PM",
            "reason": "The customer comment has been checked against its email source.",
            "label": "Reviewed Rev B record",
        },
    )
    assert approved.status_code == 200, approved.text
    workspace = approved.json()
    shared = post(
        client,
        base + "/shares",
        {
            "expected_version": workspace["version"],
            "actor": "Review PM",
            "reason": "Share the approved comment record.",
            "approval_id": workspace["approvals"][0]["id"],
        },
    )
    assert shared.status_code == 200, shared.text
    return order, workspace, imported["document_id"], original, shared.json()


def test_public_email_download_is_frozen_approved_text_without_private_mime(client):
    order, workspace, document_id, original, share = reviewed_email_share(client)
    public_url = f"/api/shared/{share['token']}/documents/{document_id}"
    response = client.get(public_url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "shared-email-excerpts.txt" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "Please confirm cable entry on drawing E-10." in response.text
    for private in (
        "private-recipient@example.com",
        "private-observer@example.com",
        "private-internal@example.com",
        "private-internal-routing@example.com",
        "PRIVATE_CAD_EMBEDDED_CONTENT",
        "PRIVATE_MARGIN_47",
        "multipart/mixed",
        "Content-Transfer-Encoding",
        "private-pricing.csv",
        "private-design.dwg",
    ):
        assert private not in response.text
    authenticated = client.get(f"/api/documents/{document_id}/content")
    assert authenticated.status_code == 200 and authenticated.content == original
    attachment = next(
        d for d in workspace["documents"] if d["name"] == "private-pricing.csv"
    )
    assert (
        client.get(
            f"/api/shared/{share['token']}/documents/{attachment['id']}"
        ).status_code
        == 404
    )

    # Later working changes and another source must not enter an earlier approval.
    comment = workspace["comments"][0]
    changed = post(
        client,
        f"/orders/{order['id']}/record/comments/{comment['id']}",
        edit_body(workspace, comment, text="Later working wording PRIVATE_LATER_TEXT"),
    )
    assert changed.status_code == 200
    later = upload(
        client, order, "later-private.txt", b"Comment 8: PRIVATE_LATER_DOCUMENT"
    )
    later_id = next(
        d["id"] for d in later["documents"] if d["name"] == "later-private.txt"
    )
    assert (
        client.get(f"/api/shared/{share['token']}/documents/{later_id}").status_code
        == 404
    )
    # A parser correction to live spans cannot silently rewrite shared evidence.
    with Session.begin() as s:
        source = s.get(Span, comment["source_ids"][0])
        source.text = "PRIVATE_REPARSED_CONTENT"
    assert client.get(public_url).content == response.content

    current = view(client, order)
    revoked = post(
        client,
        f"/orders/{order['id']}/record/shares/{share['id']}/revoke",
        {
            "expected_version": current["version"],
            "actor": "Review PM",
            "reason": "End access to this shared record.",
        },
    )
    assert revoked.status_code == 200
    assert client.get(public_url).status_code == 404


def test_shared_email_without_included_excerpts_never_falls_back_to_original(client):
    _, _, document_id, _, share = reviewed_email_share(client)
    # Legacy/incomplete shares may list a document without any included source.
    with Session.begin() as s:
        row = s.scalar(select(RecordShare).where(RecordShare.id == share["id"]))
        snapshot = deepcopy(row.snapshot)
        snapshot["sources"] = []
        row.snapshot = snapshot
    response = client.get(f"/api/shared/{share['token']}/documents/{document_id}")
    assert response.status_code == 404
    assert "private-recipient" not in response.text and "multipart" not in response.text
