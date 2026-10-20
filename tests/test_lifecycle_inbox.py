from uuid import uuid4

import test_lifecycle
from test_lifecycle import price_fixture

from backend.identity import Identity, identity_scope
from backend.records.inbox import inbox, lifecycle_item
from backend.storage.db import Session
from backend.storage.models import Span

fixture = test_lifecycle.fixture


def package(**patch):
    return {
        "id": "revision-a",
        "operation": "revision_review",
        "title": "Review prepared",
        "status": "awaiting_decision",
        "evidence_current": True,
        "fingerprint": "a" * 64,
        "pending_gates": ["engineering", "money", "scope"],
        "blockers": [],
        "payload": {
            "counts": {"answer": 7, "text_edit": 3, "layout_task": 1},
            "change_order": {"total": "1200.00", "lines": [{"currency": "USD"}]},
        },
        **patch,
    }


def project(packages):
    return lifecycle_item(
        {"version": 12, "mode": "local_demo", "packages": packages},
        "2026-10-15T12:00:00Z",
    )


def test_projection_reports_saved_outcomes_without_claiming_customer_send():
    result = project(
        [
            package(),
            package(
                id="quote-a", operation="quote", title="Quote prepared", payload={}
            ),
        ]
    )
    assert (
        result["detail"]
        == "7 answers drafted · 3 drawing/BOM fields edited · 1 located drafting task · $1,200.00 proposed scope addition."
    )
    assert result["prepared_outputs"] == 2
    assert result["lifecycle"]["version"] == 12
    assert result["lifecycle"]["packages"][0]["fingerprint"] == "a" * 64
    assert (
        result["lifecycle"]["prepared_label"] == "2 work packages · Local test adapters"
    )
    assert "Approval needed" in result["lifecycle"]["open_point"]
    assert "sent" not in result["detail"].lower()


def test_stale_work_cannot_keep_its_previous_ready_counts():
    result = project([package(status="stale", evidence_current=False)])
    assert result["status"] == "needs_review"
    assert result["review_required"] is True
    assert result["prepared_outputs"] == 0
    assert "needs refreshing" in result["detail"]
    assert "7 replies" not in result["detail"]


def test_mixed_work_preserves_specific_blockers_and_omits_rejected_replaced_packages():
    packages = [
        package(),
        package(
            id="po",
            operation="kickoff",
            payload={},
            status="blocked",
            blockers=["PO line CB-12 has a different price."],
        ),
        package(id="old", status="superseded"),
        package(id="rejected", status="rejected"),
    ]
    result = project(packages)
    assert result["prepared_outputs"] == 2
    assert "PO line CB-12" in result["lifecycle"]["open_point"]
    assert [p["id"] for p in result["lifecycle"]["packages"]] == ["revision-a", "po"]
    assert project([package(status="rejected")]) is None
    assert project([]) is None


def test_inbox_projects_real_saved_package_and_invalidates_changed_source(
    fixture, client
):
    prepared, source_id = price_fixture(fixture)
    order_id = fixture[0]
    items = client.get("/api/records/inbox").json()["items"]
    result = next(i for i in items if i["kind"] == "lifecycle")
    assert result["order_id"] == order_id
    assert result["prepared_outputs"] == 1
    assert (
        result["lifecycle"]["packages"][0]["fingerprint"]
        == prepared["packages"][0]["fingerprint"]
    )
    with Session.begin() as s:
        source = s.get(Span, source_id)
        source.text = "The recorded cost evidence was changed."
    changed = client.get("/api/records/inbox").json()["items"]
    result = next(i for i in changed if i["kind"] == "lifecycle")
    assert result["status"] == "needs_review"
    assert result["prepared_outputs"] == 0
    with identity_scope(Identity(str(uuid4()), "other-user")):
        assert inbox() == {"items": []}
