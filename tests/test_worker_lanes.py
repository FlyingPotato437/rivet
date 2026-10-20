"""Mailbox work remains claimable while a coordination model run owns its lease."""

from datetime import timedelta

from backend.storage.db import Session
from backend.storage.models import Job, Project, now
from backend.worker import COORDINATION_KINDS, claim


def test_coordination_and_email_lanes_are_disjoint_and_keep_one_lease():
    with Session.begin() as s:
        project = Project(title="Lane fixture", customer="Fictional customer")
        s.add(project)
        s.flush()
        coordination = Job(
            project_id=project.id,
            kind="record_agent",
            payload={},
            key="coordination-fixture",
        )
        coordination.created_at = now() - timedelta(seconds=1)
        parsing = Job(
            project_id=project.id, kind="parse", payload={}, key="parse-fixture"
        )
        reply = Job(
            project_id=project.id, kind="reply_draft", payload={}, key="reply-fixture"
        )
        s.add_all([coordination, parsing, reply])
        s.flush()
        coordination_id, parsing_id, reply_id = coordination.id, parsing.id, reply.id

    slow = claim(kinds=COORDINATION_KINDS)
    assert slow[0] == coordination_id
    # The long model run still owns its lease when email prerequisites advance.
    assert claim(exclude_kinds=COORDINATION_KINDS)[0] == parsing_id
    assert claim(exclude_kinds=COORDINATION_KINDS)[0] == reply_id
    assert claim(kinds=COORDINATION_KINDS) is None
    assert claim(exclude_kinds=COORDINATION_KINDS) is None
    with Session() as s:
        assert all(
            s.get(Job, id).attempts == 1
            for id in (coordination_id, parsing_id, reply_id)
        )
        assert s.get(Job, coordination_id).lease == slow[1]


def test_empty_inclusion_never_claims_a_different_job():
    with Session.begin() as s:
        project = Project(title="Lane fixture", customer="Fictional customer")
        s.add(project)
        s.flush()
        s.add(
            Job(
                project_id=project.id,
                kind="parse",
                payload={},
                key="empty-filter-fixture",
            )
        )
    assert claim(kinds=()) is None
    assert claim()[2] == "parse"
