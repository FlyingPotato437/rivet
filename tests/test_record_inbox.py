from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select
from test_records import create, upload, view

from backend.identity import Identity, identity_scope
from backend.records.agent_service import input_hash
from backend.records.inbox import inbox, record_items
from backend.storage.db import Session
from backend.storage.models import OrderRecord, RecordAgentProposal, RecordAgentRun


def test_inbox_is_actionable_and_tenant_scoped(client):
    order = create(client)
    w = upload(client, order, "review.txt", b"Comment 1: Confirm the cable entry.")
    items = client.get("/api/records/inbox").json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "reference_review"
    assert items[0]["origin"] == "imported"
    assert items[0]["status"] == "needs_review"
    assert items[0]["comment_id"] == w["comments"][0]["id"]
    with identity_scope(Identity(str(uuid4()), "other-user")):
        assert inbox() == {"items": []}
    with Session.begin() as s:
        record = s.scalar(
            select(OrderRecord).where(OrderRecord.order_id == order["id"])
        )
        data = deepcopy(record.data)
        data["comments"][0].update(
            status="closed", reviewed=True, response="Recorded by engineer."
        )
        record.data = data
    assert client.get("/api/records/inbox").json()["items"] == []


def test_inbox_deduplicates_linked_tasks_and_does_not_invent_waiting():
    w = {
        "comments": [
            {
                "id": "c",
                "number": "1",
                "text": "Confirm finish.",
                "status": "open",
                "reviewed": True,
                "response": "",
                "origin": "manual",
            }
        ],
        "coordination": {
            "tasks": [
                {
                    "id": "t",
                    "comment_id": "c",
                    "title": "Review finish",
                    "owner": "Engineering",
                    "status": "open",
                }
            ]
        },
    }
    items = record_items(w, "2026-10-11T00:00:00Z")
    assert len(items) == 1
    assert items[0]["owner"] == "Engineering"
    assert items[0]["kind"] == "response"
    assert items[0]["status"] == "needs_review"
    w["comments"][0]["response"] = "Response is recorded."
    assert record_items(w, "2026-10-11")[0]["kind"] == "task"
    w["coordination"]["tasks"][0]["status"] = "done"
    assert record_items(w, "2026-10-11") == []


def test_inbox_shows_real_proposals_and_omits_stale_ones(client):
    o = create(client)
    w = view(client, o)
    with Session.begin() as s:
        run = RecordAgentRun(
            order_id=o["id"],
            goal="Context: comment 1 (comment id private-context-id).\nSource in view: review.pdf (document id private-document-id).\nReview sources",
            origin="user",
            mode="ai",
            status="waiting_for_review",
            base_version=w["version"],
            input_hash="a" * 64,
            trigger_key=str(uuid4()),
            actor="Local estimator",
        )
        s.add(run)
        s.flush()
        for version, title in [
            (w["version"], "Current proposal"),
            (w["version"] - 1, "Old proposal"),
        ]:
            s.add(
                RecordAgentProposal(
                    order_id=o["id"],
                    run_id=run.id,
                    kind="coordination",
                    title=title,
                    summary="Review prepared task",
                    status="pending",
                    origin="ai",
                    base_version=version,
                    input_hash=input_hash(w),
                    fingerprint=str(uuid4()),
                    operations=[],
                    evidence_ids=[],
                )
            )
    items = client.get("/api/records/inbox").json()["items"]
    assert [i["title"] for i in items] == ["Current proposal"]
    assert items[0]["origin"] == "ai" and items[0]["status"] == "ready"
    with Session.begin() as s:
        saved_run = s.get(RecordAgentRun, run.id)
        saved_run.status = "running"
    active = client.get("/api/records/inbox").json()["items"]
    assert len(active) == 1 and active[0]["kind"] == "run"
    assert active[0]["status"] == "waiting"
    assert active[0]["title"] == "Review sources"
    with Session.begin() as s:
        saved_run = s.get(RecordAgentRun, run.id)
        saved_run.status = "waiting_for_review"
        for proposal in s.scalars(select(RecordAgentProposal)):
            proposal.status = "stale"
    changed = client.get("/api/records/inbox").json()["items"]
    assert len(changed) == 1 and changed[0]["status"] == "needs_review"
    assert "evidence changed" in changed[0]["detail"]
