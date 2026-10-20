"""Restart-safe parallel preparation uses the real durable job queue."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from test_record_agent import fetched, start
from test_records import create, upload

from backend.records import agent_service as agent
from backend.records import agent_tasks
from backend.storage.db import Session
from backend.storage.models import Job, now
from backend.worker import claim, process


def setup(client, monkeypatch, count=6):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    order = create(client)
    record = upload(
        client,
        order,
        "returned.txt",
        "\n".join(
            f"Comment {i}: Confirm CB-{i} trip unit on E-10."
            for i in range(1, count + 1)
        ).encode(),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setenv("RIVET_AGENT_COMMENT_TASKS", "true")
    monkeypatch.setattr(
        agent,
        "provider_call",
        lambda run, w: (
            "complete",
            {"summary": "Model text is not the coverage ledger", "source_ids": []},
            {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        ),
    )
    monkeypatch.setattr(
        agent_tasks,
        "generate",
        lambda data, model: (
            {
                "title": "Trip unit clarification",
                "text": "Please supply the released sheet for confirmation.",
                "source_ids": data["comment"]["source_ids"],
                "limitations": ["Released drawing not verified"],
            },
            {
                "input_tokens": 10,
                "output_tokens": 10,
                "total_tokens": 20,
                "model_calls": 1,
            },
        ),
    )
    run = start(client, order, record)
    process(claim(kinds=("record_agent",)))
    return order, run


@pytest.mark.parametrize("count", [6, 40])
def test_every_comment_is_prepared_once_and_merged_in_small_batches(
    client, monkeypatch, count
):
    order, run = setup(client, monkeypatch, count)
    with Session() as s:
        tasks = list(s.scalars(select(agent_tasks.CommentPreparation)))
        assert len(tasks) == count
    while task := claim(kinds=("comment_prepare",)):
        process(task)
    # Re-delivery cannot create another response or another merge job.
    agent_tasks.process(tasks[0].id)
    process(claim(kinds=("record_agent",)))
    done = fetched(client, order, run)
    assert done["status"] == "completed", done
    assert done["result"]["coverage"]["comments_prepared"] == count
    assert f"{count} of {count}" in done["result"]["summary"]
    assert sum(len(p["operations"]) for p in done["proposals"]) == count
    assert all(
        len(step["result"].get("operations", [])) <= 5
        for step in done["steps"]
        if step["name"] == "stage_changes"
    )
    assert done["usage"]["total_tokens"] == count * 20 + 2
    # Finished sub-task work must reach the Outlook composition path, rather
    # than remain visible only in a coordination log.
    from backend.reply_assistant.service import context
    from backend.storage.models import Order

    with Session() as s:
        _, data, _, _ = context(
            s, s.get(Order, order["id"]), "Prepare the returned drawing response"
        )
        assert len(data["prepared_comment_responses"]) == count
        assert data["coverage"]["parallel_responses_included"] == count


def test_child_failure_preserves_other_drafts_and_no_false_completion(
    client, monkeypatch
):
    order, run = setup(client, monkeypatch)
    original = agent_tasks.generate
    calls = []

    def one_fails(data, model):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("incomplete output")
        return original(data, model)

    monkeypatch.setattr(agent_tasks, "generate", one_fails)
    while task := claim(kinds=("comment_prepare",)):
        process(task)
    process(claim(kinds=("record_agent",)))
    done = fetched(client, order, run)
    assert done["status"] == "failed"
    assert done["result"]["coverage"]["comments_prepared"] == 5
    assert len(done["result"]["coverage"]["comments_failed"]) == 1
    assert sum(len(p["operations"]) for p in done["proposals"]) == 5


def test_exhausted_child_wakes_coordinator_instead_of_leaving_it_running(
    client, monkeypatch
):
    order, run = setup(client, monkeypatch)
    child = claim(kinds=("comment_prepare",))
    with Session.begin() as s:
        j = s.get(Job, child[0])
        j.attempts = 3
        j.lease_until = now() - timedelta(seconds=1)
    while task := claim(kinds=("comment_prepare",)):
        process(task)
    process(claim(kinds=("record_agent",)))
    assert fetched(client, order, run)["status"] == "failed"


def test_unrelated_citation_cannot_be_merged(client, monkeypatch):
    order, run = setup(client, monkeypatch)
    monkeypatch.setattr(
        agent_tasks,
        "generate",
        lambda *args: (
            {
                "title": "bad",
                "text": "Unsupported answer",
                "source_ids": ["other-order-span"],
                "limitations": [],
            },
            {},
        ),
    )
    while task := claim(kinds=("comment_prepare",)):
        process(task)
    process(claim(kinds=("record_agent",)))
    done = fetched(client, order, run)
    assert done["status"] == "failed" and not done["proposals"]
