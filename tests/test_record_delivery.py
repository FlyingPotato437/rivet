from copy import deepcopy
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_records import create, upload, view

from backend.identity import LOCAL_ORG, Identity, identity_scope
from backend.records.delivery import (
    adoption_status,
    digest,
    email_bytes,
    get_digest,
    workspace_url,
)
from backend.storage.db import Session
from backend.storage.models import TeamInbox


def record():
    return {
        "version": 4,
        "order": {
            "id": "order-a",
            "number": "24-118",
            "title": "Westbridge switchgear",
        },
        "comments": [
            {
                "id": "a",
                "number": "1",
                "text": "Confirm cable entry",
                "document_id": "old",
                "revision": "B",
                "status": "open",
                "reviewed": False,
                "response": "",
            },
            {
                "id": "b",
                "number": "2",
                "text": "Confirm cable entry",
                "document_id": "current",
                "revision": "C",
                "status": "open",
                "reviewed": False,
                "response": "",
                "source_page": 2,
            },
            {
                "id": "c",
                "number": "3",
                "text": "Added compartment",
                "document_id": "current",
                "revision": "C",
                "status": "responded",
                "reviewed": True,
                "response": "Please confirm updated scope.",
                "source_page": 4,
            },
        ],
        "revision_review": {
            "latest_revision": "C",
            "current_document_ids": ["current"],
            "flags": [
                {
                    "id": "f1",
                    "kind": "repeated",
                    "comment_id": "b",
                    "title": "Cable entry repeated",
                    "detail": "Also recorded in Rev B.",
                },
                {
                    "id": "f2",
                    "kind": "scope_change",
                    "comment_id": "c",
                    "title": "Possible additional scope",
                    "detail": "Customer requested an additional compartment.",
                },
                {
                    "id": "f3",
                    "kind": "late_change",
                    "comment_id": "c",
                    "title": "After an approved record",
                    "detail": "Check against the prior approved record.",
                },
            ],
        },
    }


def test_digest_counts_current_revision_and_flags_without_double_counting_comments():
    w = record()
    original = deepcopy(w)
    result = digest(w, "https://workspace.example.com")
    assert result["comment_count"] == 2
    assert result["needs_response"] == result["needs_review"] == 1
    assert result["counts"] == {
        "new": 0,
        "repeated": 1,
        "unresolved": 0,
        "late_change": 1,
        "scope_change": 1,
    }
    assert len(result["items"]) == 3
    assert result["items"][0]["label"] == "Possible scope changes"
    assert result["sent"] is False and result["requires_review"] is True
    assert "not an engineering or commercial determination" in result["text"]
    assert "Rev C" in result["subject"]
    assert w == original


def test_digest_escapes_html_and_does_not_create_foreign_comment_links():
    w = record()
    w["order"]["number"] = "24-118\r\nBcc: other@example.com"
    w["order"]["title"] = '<img src=x onerror="alert(1)">'
    w["revision_review"]["flags"][0]["title"] = "<script>alert(1)</script>"
    w["revision_review"]["flags"][0]["detail"] = "Smith & Sons <review>"
    w["comments"][1]["id"] = 'b&tab=sharing"'
    w["revision_review"]["flags"][0]["comment_id"] = 'b&tab=sharing"'
    w["revision_review"]["flags"].append(
        {
            "id": "evil",
            "kind": "late_change",
            "comment_id": "foreign",
            "title": "Other order",
            "detail": "private",
        }
    )
    result = digest(w, "https://workspace.example.com")
    assert "<script>" not in result["html"] and "<img" not in result["html"]
    assert (
        "&lt;script&gt;" in result["html"]
        and "Smith &amp; Sons &lt;review&gt;" in result["html"]
    )
    assert "foreign" not in result["text"] and result["counts"]["late_change"] == 1
    assert "comment=b%26tab%3Dsharing%22" in result["html"]
    message = BytesParser(policy=policy.default).parsebytes(email_bytes(result))
    assert message["To"] is None and message["Cc"] is None and message["Bcc"] is None
    assert message["From"] is None and message["X-Unsent"] == "1"
    assert len(message.get_all("Subject")) == 1
    assert message.get_body(preferencelist=("plain",)) is not None
    assert message.get_body(preferencelist=("html",)) is not None


def test_workspace_links_only_use_trusted_configured_origin(monkeypatch):
    monkeypatch.setenv("RIVET_LOCAL_ONLY", "false")
    monkeypatch.delenv("RIVET_WORKSPACE_URL", raising=False)
    monkeypatch.setenv("RIVET_ALLOWED_ORIGINS", "https://marketing.example.com")
    assert workspace_url() == ""
    for unsafe in (
        "javascript:alert(1)",
        "https://user:secret@example.com",
        "https://example.com/#orders",
        "https://example.com/path",
        "http://example.com",
        "https://example.com\r\nX: value",
    ):
        monkeypatch.setenv("RIVET_WORKSPACE_URL", unsafe)
        assert workspace_url() == ""
    monkeypatch.setenv("RIVET_WORKSPACE_URL", "https://app.example.com/")
    assert workspace_url() == "https://app.example.com"
    monkeypatch.setenv("RIVET_WORKSPACE_URL", "http://127.0.0.1:5178")
    assert workspace_url() == ""
    monkeypatch.setenv("RIVET_LOCAL_ONLY", "true")
    assert workspace_url() == "http://127.0.0.1:5178"


def test_digest_without_link_config_never_invents_a_public_url():
    result = digest(record())
    assert result["order_url"] == ""
    assert all(item["url"] == "" for item in result["items"])
    assert "A workspace URL is not configured" in result["text"]
    assert "rivet-usa.com" not in result["text"]


def test_digest_revision_counts_include_manual_comments_but_not_prior_rows():
    w = record()
    for comment in w["comments"]:
        comment["document_id"] = ""
    w["revision_review"]["current_document_ids"] = []
    assert digest(w)["comment_count"] == 2
    w["revision_review"]["latest_revision"] = "D"
    w["revision_review"]["current_document_ids"] = ["new-empty-return"]
    assert digest(w)["comment_count"] == 0


def test_digest_endpoints_are_read_only_and_tenant_scoped(client):
    order = create(client)
    w = upload(client, order, "review.txt", b"Comment 1: Confirm the cable entry.")
    result = client.get(f"/api/orders/{order['id']}/record/digest")
    assert result.status_code == 200, result.text
    assert result.json()["comment_count"] == 1
    eml = client.get(
        f"/api/orders/{order['id']}/record/digest.eml",
        params={"expected_version": w["version"]},
    )
    assert eml.status_code == 200
    assert eml.headers["content-type"].startswith("message/rfc822")
    assert "no-store" in eml.headers["cache-control"]
    stale = client.get(
        f"/api/orders/{order['id']}/record/digest.eml",
        params={"expected_version": w["version"] + 1},
    )
    assert stale.status_code == 409
    assert client.get(f"/api/orders/{order['id']}/record/digest.eml").status_code == 422
    assert view(client, order) == w
    with identity_scope(Identity(str(uuid4()), "other-user")):
        with pytest.raises(HTTPException) as exc:
            get_digest(order["id"])
        assert exc.value.status_code == 404


def test_adoption_does_not_claim_external_connections_or_fabricate_address(
    client, monkeypatch
):
    for key in ("RESEND_API_KEY", "RESEND_RECEIVING_DOMAIN", "RESEND_WEBHOOK_SECRET"):
        monkeypatch.delenv(key, raising=False)
    state = adoption_status()
    assert state["forwarding"]["status"] == "setup_needed"
    assert state["forwarding"]["address_count"] == 0
    assert state["forwarding"]["team_address"] == ""
    assert state["forwarding"]["team_address_enabled"] is False
    assert state["forwarding"]["automatic_delivery_configured"] is False
    states = {item["id"]: item["status"] for item in state["integrations"]}
    assert states["outlook"] == states["sharepoint"] == states["sap"] == "not_connected"
    assert states["excel"] == "export_available"
    assert state["outbound"]["automatic_sending"] is False
    monkeypatch.setenv("RESEND_API_KEY", "configured-in-test")
    monkeypatch.setenv("RESEND_RECEIVING_DOMAIN", "intake.example.com")
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "configured-in-test")
    monkeypatch.setenv("RIVET_LOCAL_ONLY", "false")
    with identity_scope(Identity(LOCAL_ORG, "Test administrator")):
        state = adoption_status()
    assert state["forwarding"]["status"] == "configured"
    assert state["forwarding"]["automatic_delivery_configured"] is True
    assert state["forwarding"]["address_count"] == 0


def test_adoption_team_address_is_scoped_and_only_shown_while_enabled(client):
    with Session.begin() as s:
        inbox = TeamInbox(address="orders-unique@intake.example.com")
        s.add(inbox)
        s.flush()
        inbox_id = inbox.id
    state = adoption_status()
    assert state["forwarding"]["team_address"] == "orders-unique@intake.example.com"
    assert state["forwarding"]["team_address_enabled"] is True
    with identity_scope(Identity(str(uuid4()), "Other team")):
        assert adoption_status()["forwarding"]["team_address"] == ""
    with Session.begin() as s:
        s.get(TeamInbox, inbox_id).enabled = False
    assert adoption_status()["forwarding"]["team_address"] == ""
