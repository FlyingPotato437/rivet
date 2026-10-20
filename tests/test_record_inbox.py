from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from test_records import create, upload, view

from backend.identity import Identity, identity_scope
from backend.records.agent_service import input_hash
from backend.records.automation import key as automation_key
from backend.records.inbox import inbox, record_items
from backend.storage.db import Session
from backend.storage.models import OrderRecord, RecordAgentProposal, RecordAgentRun, now


def test_inbox_is_actionable_and_tenant_scoped(client):
    order = create(client)
    w = upload(client, order, "review.txt", b"Comment 1: Confirm the cable entry.")
    items = client.get("/api/records/inbox").json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "reference_review"
    assert items[0]["origin"] == "imported"
    assert items[0]["status"] == "ready"
    assert items[0]["review_required"] is False
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
    assert items[0]["origin"] == "ai" and items[0]["status"] == "needs_review"
    assert items[0]["review_required"] is True
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


def test_only_superseded_automatic_reviews_leave_the_action_queue(client):
    o = create(client)
    w = view(client, o)
    with Session.begin() as s:
        old_ids = []
        for origin in ("event", "user"):
            old = RecordAgentRun(
                order_id=o["id"],
                goal="Review this order",
                origin=origin,
                mode="rules",
                status="waiting_for_review",
                base_version=w["version"] - 1,
                input_hash="old",
                trigger_key=str(uuid4()),
                actor="Fixture reviewer",
                created_at=now() - timedelta(minutes=10),
            )
            s.add(old)
            s.flush()
            old_ids.append(old.id)
            s.add(
                RecordAgentProposal(
                    order_id=o["id"],
                    run_id=old.id,
                    kind="coordination",
                    title="Earlier proposal",
                    summary="A saved old review",
                    status="stale",
                    origin="rules",
                    base_version=old.base_version,
                    input_hash="old",
                    fingerprint=str(uuid4()),
                    operations=[],
                    evidence_ids=[],
                )
            )
        successor = RecordAgentRun(
            order_id=o["id"],
            goal="Review this order",
            origin="event",
            mode="rules",
            status="completed",
            base_version=w["version"],
            input_hash=input_hash(w),
            trigger_key=str(uuid4()),
            actor="Fixture reviewer",
        )
        s.add(successor)
        s.flush()
    items = client.get("/api/records/inbox").json()["items"]
    by_run = {i["run_id"]: i for i in items}
    assert by_run[old_ids[0]]["superseded_by"] == successor.id
    assert by_run[old_ids[1]]["superseded_by"] == ""
    assert by_run[old_ids[1]]["run_status"] == "waiting_for_review"
    assert by_run[old_ids[1]]["current_version"] == w["version"]


def test_imported_comment_with_outstanding_coordination_task_stays_actionable():
    w = {
        "comments": [
            {
                "id": "c",
                "number": "1",
                "text": "Confirm breaker rating.",
                "status": "open",
                "reviewed": False,
                "origin": "PDF markup",
            }
        ],
        "coordination": {
            "tasks": [
                {
                    "id": "t",
                    "comment_id": "c",
                    "title": "Ask engineering",
                    "status": "open",
                    "origin": "ai",
                    "managed": False,
                    "note": "Ask engineer to confirm breaker rating.",
                }
            ]
        },
    }
    items = record_items(w, "2026-10-13")
    assert len(items) == 1
    assert items[0]["origin"] == "ai"
    assert items[0]["task_id"] == "t"
    assert items[0]["status"] == "needs_review"
    assert items[0]["detail"] == "Ask engineer to confirm breaker rating."
    w["coordination"]["tasks"][0]["origin"] = "automatic"
    assert record_items(w, "2026-10-13")[0]["origin"] == "automatic"
    w["coordination"]["tasks"][0]["status"] = "done"
    assert record_items(w, "2026-10-13")[0]["origin"] == "imported"


def test_only_untouched_generated_comment_bookkeeping_is_quiet():
    w = {
        "comments": [
            {
                "id": "c",
                "number": "1",
                "text": "Finish requested.",
                "status": "open",
                "reviewed": False,
                "origin": "PDF markup",
            }
        ],
        "coordination": {
            "tasks": [
                {
                    "id": automation_key("comment-task", "c"),
                    "comment_id": "c",
                    "title": "Confirm context",
                    "status": "open",
                    "managed": True,
                    "note": "",
                }
            ]
        },
    }
    assert record_items(w, "2026-10-13")[0]["origin"] == "imported"
    w["coordination"]["tasks"][0]["managed"] = False
    assert record_items(w, "2026-10-13")[0]["origin"] == "human"


def test_reviewed_import_does_not_leave_a_second_generated_pm_task():
    w = {
        "comments": [
            {
                "id": "c",
                "number": "1",
                "text": "Confirm finish.",
                "status": "open",
                "reviewed": True,
                "response": "Noted",
                "origin": "PDF markup",
            }
        ],
        "coordination": {
            "tasks": [
                {
                    "id": automation_key("comment-task", "c"),
                    "comment_id": "c",
                    "title": "Review comment",
                    "status": "open",
                    "managed": True,
                    "note": "",
                }
            ]
        },
    }
    assert record_items(w, "2026-10-14") == []
    w["coordination"]["tasks"][0]["owner"] = "Engineering"
    assert record_items(w, "2026-10-14")[0]["kind"] == "task"
