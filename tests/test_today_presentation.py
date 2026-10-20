"""Today exposes existing facts without changing timer or approval behavior."""

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select
from test_company_context import identity, order
from test_project_flow import incoming

from backend.project_flow import activity, followups
from backend.project_flow.models import ProjectSentMessage
from backend.storage.db import Session
from backend.storage.models import now


def test_daily_brief_exposes_due_timers_and_keeps_sent_count_numeric():
    with identity(), Session.begin() as s:
        o, doc, _ = order(s)
        original = incoming(s, o, doc, at=now() - timedelta(days=8))
        sent = ProjectSentMessage(
            order_id=o.id,
            connection_id=original.connection_id,
            provider_id="confirmed-send",
            conversation_id="project-thread",
            subject="Review response",
            sent_at=now() - timedelta(days=7),
            recipients=["customer@example.com"],
        )
        s.add(sent)
        s.flush()
        followups.schedule(s, o)
        s.flush()
        timer = s.scalar(select(followups.FollowUp))
        timer.status = "drafted"
        timer.outlook_url = "https://outlook.office.com/mail/drafts/id/fixture"
        timer_id, order_id = timer.id, o.id
    with identity(), Session() as s:
        report = activity.brief(s)
        assert type(report["confirmed_sent_replies"]) is int
        assert report["confirmed_sent_replies"] == 0
        assert len(report["waiting_items"]) == 1
        visible = report["waiting_items"][0]
        assert visible["id"] == timer_id
        assert visible["order_id"] == order_id
        assert visible["status"] == "drafted"
        assert s.get(followups.FollowUp, timer_id).status == "drafted"
        assert "0 replies sent" in report["text"]
    with identity("22222222-2222-2222-2222-222222222222"), Session() as s:
        assert activity.brief(s)["waiting_items"] == []
        assert activity.timeline(s) == []


def test_activity_facts_are_plain_values_without_hashes():
    pkg = {
        "id": "p1",
        "operation": "revision_review",
        "status": "awaiting_decision",
        "fingerprint": "f" * 64,
        "payload": {
            "counts": {"answer": 7, "text_edit": 3, "layout_task": 1},
            "change_order": {"total": "1200.00", "lines": []},
            "comments": [{"text": "raw"}],
        },
    }
    facts = activity.package_facts(pkg)
    assert facts == {
        "operation": "revision_review",
        "status": "awaiting_decision",
        "counts": {"answer": 7, "text_edit": 3, "layout_task": 1},
        "change_order_total": "1200.00",
    }

    run = SimpleNamespace(
        result={
            "coverage": {
                "comments_total": 11,
                "comments_prepared": 10,
                "comments_failed": ["c9"],
            },
            "source_ids": ["a", "b", "c"],
            "lifecycle_preparations": [{"summary": "Quote prepared"}],
        }
    )

    assert activity.run_facts(run) == {
        "comments_total": 11,
        "comments_prepared": 10,
        "comments_failed": 1,
        "sources": 3,
        "prepared": ["Quote prepared"],
    }


def test_daily_brief_lists_live_work():
    with identity(), Session() as s:
        report = activity.brief(s)
        assert report["working"] == []
        assert all("facts" in item for item in report["items"])
