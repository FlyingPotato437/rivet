from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from test_workflow import (
    addline,
    cmd,
    post,
    project,
    request,
    reviewed,
    upload,
    workspace,
)

from backend.agents.runner import run_agent
from backend.domain.coordination import sync_agent_clarifications
from backend.domain.service import get
from backend.storage.db import Session
from backend.storage.models import Clarification, Project, Quote, Run


def clarification(client, p, **values):
    response = post(
        client,
        f"/projects/{p['id']}/clarifications",
        {
            "title": "Confirm PDU-A supplier coverage",
            "question": "Does the supplier offer cover all ten PDU-A units?",
            **values,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def patch(client, issue, key=None, **values):
    return client.patch(
        f"/api/clarifications/{issue['id']}",
        json={
            "expected_revision": issue["revision"],
            **values,
        },
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def answer(client, issue, key=None, **values):
    return post(
        client,
        f"/clarifications/{issue['id']}/answers",
        {
            "expected_revision": issue["revision"],
            "answer": "Supplier contact reports that the existing price covers ten units.",
            **values,
        },
        key=key,
    )


def test_clarification_lifecycle_blocks_approval_until_reviewed(client):
    p = project(client)
    assert p["category"] == "Low-voltage switchgear"
    w = reviewed(client, p)
    issue = clarification(client, p, line_ids=[w["quote"]["lines"][0]["id"]])
    current = workspace(client, p)
    assert current["quote"]["status"] == "draft"
    assert current["clarifications"][0]["id"] == issue["id"]
    assert (
        post(
            client, "/quotes/" + current["quote"]["id"] + "/approve", request(current)
        ).status_code
        == 422
    )
    waiting = patch(
        client,
        issue,
        status="awaiting_reply",
        recipient="Supplier estimator",
        due_date="2026-10-15",
    )
    assert waiting.status_code == 200, waiting.text
    issue = waiting.json()
    response = answer(client, issue)
    assert response.status_code == 200, response.text
    issue = response.json()
    current = workspace(client, p)
    assert issue["status"] == "answered" and issue["answer_origin"] == "human entered"
    assert current["quote"]["input_revision"] == w["quote"]["input_revision"] + 1
    assert (
        patch(client, issue, status="resolved", resolution_note="Reviewed").status_code
        == 422
    )
    result = cmd(
        client,
        current,
        [
            {
                "type": "reconcile_inputs",
                "reason": "Reviewed recorded answer against current scope",
            }
        ],
    )
    assert result.status_code == 200, result.text
    assert (
        patch(client, issue, status="resolved", resolution_note="").status_code == 422
    )
    response = patch(
        client,
        issue,
        status="resolved",
        resolution_note="Confirmed answer has no commercial effect; current line reviewed.",
    )
    assert response.status_code == 200, response.text
    current = workspace(client, p)
    assert not current["quote"]["checks"]
    assert (
        post(
            client, "/quotes/" + current["quote"]["id"] + "/approve", request(current)
        ).status_code
        == 200
    )
    reopened = patch(client, response.json(), status="draft")
    assert reopened.status_code == 200
    assert workspace(client, p)["quote"]["status"] == "draft"
    assert reopened.json()["answer"] == issue["answer"]


def test_clarification_optimistic_lock_idempotency_and_transition_guards(client):
    p = project(client)
    issue = clarification(client, p)
    assert patch(client, issue, status="answered").status_code == 409
    assert (
        patch(
            client, issue, status="resolved", resolution_note="Skip answer"
        ).status_code
        == 409
    )
    assert patch(client, issue, due_date="tomorrow").status_code == 422
    key = str(uuid4())
    first = patch(client, issue, key=key, status="awaiting_reply")
    assert first.status_code == 200
    assert patch(client, issue, key=key, status="awaiting_reply").json() == first.json()
    assert (
        patch(client, issue, key=key, recipient="Different request").status_code == 409
    )
    assert answer(client, issue).status_code == 409
    issue = first.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda suffix: answer(client, issue, answer="Human answer " + suffix),
                ("A", "B"),
            )
        )
    assert sorted(response.status_code for response in results) == [200, 409]
    issue = next(response.json() for response in results if response.status_code == 200)
    assert (
        patch(
            client, issue, question="Replace the question after it was answered?"
        ).status_code
        == 422
    )


def test_clarification_references_are_project_and_tenant_scoped(client):
    p = project(client)
    other = project(client)
    other_workspace = addline(client, other)
    upload(
        client, other, "supplier.txt", b"Supplier offers ten units at USD 8000 each."
    )
    other_workspace = workspace(client, other)
    with Session.begin() as s:
        run = Run(
            quote_id=other_workspace["quote"]["id"],
            goal="Other project",
            base_version=1,
            input_revision=1,
        )
        s.add(run)
        s.flush()
        run_id = run.id
    for extra in (
        {"line_ids": [other_workspace["quote"]["lines"][0]["id"]]},
        {"source_ids": [other_workspace["sources"][0]["id"]]},
        {"run_id": run_id},
    ):
        response = post(
            client,
            f"/projects/{p['id']}/clarifications",
            {
                "title": "Cross-project attempt",
                "question": "Can this refer to other project evidence?",
                **extra,
            },
        )
        assert response.status_code == 403, response.text
    issue = clarification(client, p)
    assert (
        answer(
            client, issue, source_ids=[other_workspace["sources"][0]["id"]]
        ).status_code
        == 403
    )
    with Session.begin() as s:
        get(s, Clarification, issue["id"]).organization_id = str(uuid4())
    assert patch(client, issue, recipient="Hidden issue").status_code == 404
    assert client.get(f"/api/projects/{p['id']}/clarifications").json() == []


def test_recheck_after_new_offer_uses_current_baseline_and_empty_overlay(
    client, monkeypatch
):
    monkeypatch.setattr("backend.api.main.configured", lambda: True)
    p = project(client)
    w = addline(client, p)
    with Session.begin() as s:
        old = Run(
            quote_id=w["quote"]["id"],
            goal="Process addendum",
            base_version=w["quote"]["version"],
            input_revision=w["quote"]["input_revision"],
            status="waiting_for_input",
            overlay=[
                {
                    "type": "set_quantity",
                    "line_id": w["quote"]["lines"][0]["id"],
                    "value": "10",
                }
            ],
        )
        s.add(old)
        s.flush()
        old_id = old.id
    issue = clarification(
        client, p, run_id=old_id, line_ids=[w["quote"]["lines"][0]["id"]]
    )
    assert (
        post(
            client,
            f"/clarifications/{issue['id']}/resume",
            {"expected_revision": issue["revision"]},
        ).status_code
        == 409
    )
    issue = answer(client, issue).json()
    upload(
        client,
        p,
        "revised-offer.txt",
        b"Updated supplier offer covers PDU-A quantity 10 each at USD 8000.",
        "offer",
    )
    current = workspace(client, p)
    key = str(uuid4())
    body = {"expected_revision": issue["revision"]}
    response = post(client, f"/clarifications/{issue['id']}/resume", body, key=key)
    assert response.status_code == 202, response.text
    data = response.json()
    assert (
        post(client, f"/clarifications/{issue['id']}/resume", body, key=key).json()
        == data
    )
    assert data["run_id"] != old_id
    run = client.get("/api/agent-runs/" + data["run_id"]).json()
    assert run["base_version"] == current["quote"]["version"]
    assert run["input_revision"] == current["quote"]["input_revision"]
    assert run["overlay"] == [] and run["steps"] == []
    assert run["answers"][0]["origin"] == "human entered"
    assert data["clarification"]["status"] == "answered"
    assert client.get("/api/agent-runs/" + old_id).json()["status"] == "cancelled"
    assert (
        post(
            client,
            f"/clarifications/{issue['id']}/resume",
            {"expected_revision": data["clarification"]["revision"]},
        ).status_code
        == 409
    )
    queue = client.get("/api/work-queue").json()
    linked = [
        item for item in queue["items"] if item.get("clarification_id") == issue["id"]
    ]
    assert len(linked) == 1 and linked[0]["status"] == "processing"
    assert not any(
        item["id"] in ("run:" + old_id, "run:" + data["run_id"])
        for item in queue["items"]
    )
    assert workspace(client, p)["quote"]["lines"][0]["quantity"] == "2"


def test_agent_questions_are_durable_deduplicated_and_queue_tracks_real_states(client):
    p = project(client)
    w = addline(client, p)
    with Session.begin() as s:
        r = Run(
            quote_id=w["quote"]["id"],
            goal="Check scope",
            base_version=w["quote"]["version"],
            input_revision=w["quote"]["input_revision"],
        )
        s.add(r)
        s.flush()
        rid = r.id
    question = "Does the offer cover the PDU-A quantity change?"
    run_agent(
        rid,
        provider=lambda ctx: (
            "request_human_input",
            {"questions": [question, question]},
            {},
        ),
    )
    issues = client.get(f"/api/projects/{p['id']}/clarifications").json()
    assert len(issues) == 1
    assert issues[0]["line_ids"] == [w["quote"]["lines"][0]["id"]]
    with Session.begin() as s:
        r = get(s, Run, rid)
        q = get(s, Quote, r.quote_id)
        sync_agent_clarifications(
            s, r, q, get(s, Project, p["id"]), ["  " + question.upper() + " "]
        )
    assert len(client.get(f"/api/projects/{p['id']}/clarifications").json()) == 1
    queue = client.get("/api/work-queue").json()
    assert not any(item["kind"] == "run" for item in queue["items"])
    assert (
        len([item for item in queue["items"] if item["kind"] == "clarification"]) == 1
    )
    response = patch(client, issues[0], status="awaiting_reply", recipient="Supplier")
    assert response.status_code == 200
    queue = client.get("/api/work-queue").json()
    assert queue["counts"]["waiting"] == 1
    assert sum(queue["counts"].values()) == len(queue["items"])


def test_recheck_configuration_guard_and_unreviewed_lines(client, monkeypatch):
    p = project(client)
    w = addline(client, p)
    issue = clarification(client, p, line_ids=[w["quote"]["lines"][0]["id"]])
    issue = answer(client, issue).json()
    monkeypatch.setattr("backend.api.main.configured", lambda: False)
    assert (
        post(
            client,
            f"/clarifications/{issue['id']}/resume",
            {"expected_revision": issue["revision"]},
        ).status_code
        == 503
    )
    w = workspace(client, p)
    assert (
        cmd(
            client,
            w,
            [{"type": "reconcile_inputs", "reason": "Reviewed answer evidence"}],
        ).status_code
        == 200
    )
    response = patch(
        client,
        issue,
        status="resolved",
        resolution_note="Attempt without reviewing line",
    )
    assert response.status_code == 422 and "affected quote lines" in response.text


def test_answer_retry_does_not_double_increment_inputs(client):
    p = project(client)
    issue = clarification(client, p)
    before = workspace(client, p)["quote"]["input_revision"]
    key = str(uuid4())
    first = answer(client, issue, key=key)
    assert first.status_code == 200
    assert answer(client, issue, key=key).json() == first.json()
    assert workspace(client, p)["quote"]["input_revision"] == before + 1
    assert answer(client, issue, key=key, answer="Different answer").status_code == 409


def test_answer_recheck_proposal_review_resolution_and_approval(client, monkeypatch):
    """A revised supplier offer can move an eight-unit quote to ten with human approval."""
    monkeypatch.setattr("backend.api.main.configured", lambda: True)
    p = project(client)
    w = addline(client, p, quantity="8")
    line_id = w["quote"]["lines"][0]["id"]
    issue = clarification(client, p, line_ids=[line_id])
    d = upload(
        client,
        p,
        "revised-offer.txt",
        b"PDU-A: revised supplier offer covers 10 each at USD 8000 each. Delivery 20 weeks.",
        "offer",
    )
    w = workspace(client, p)
    source_id = w["sources"][0]["id"]
    issue = answer(
        client,
        issue,
        answer="I reviewed the revised offer for 10 units at USD 8000 each. Apply our 20 percent gross margin rule.",
        source_ids=[source_id],
    ).json()
    response = post(
        client,
        f"/clarifications/{issue['id']}/resume",
        {"expected_revision": issue["revision"]},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    issue = response.json()["clarification"]
    ops = [
        {
            "type": "set_quantity",
            "line_id": line_id,
            "value": "10",
            "expected_before": "8",
            "source_ids": [source_id],
        },
        {
            "type": "set_cost",
            "line_id": line_id,
            "value": "8000",
            "source_ids": [source_id],
            "reason": "Revised supplier offer for ten units",
        },
        {
            "type": "set_gross_margin",
            "line_id": line_id,
            "value": "20",
            "reason": "Human-confirmed team pricing rule",
        },
    ]
    calls = iter(
        [
            ("read_source_spans", {"document_id": d["id"]}, {}),
            (
                "stage_draft_changes",
                {
                    "operations": ops,
                    "summary": "Update covered quantity and apply confirmed pricing rule",
                },
                {},
            ),
            ("inspect_draft", {}, {}),
            (
                "finalize_proposal",
                {
                    "title": "PDU-A revised supplier coverage",
                    "summary": "Ten units, USD 10000 selling price each",
                    "clarifications": [],
                },
                {},
            ),
        ]
    )
    run_agent(run_id, provider=lambda ctx: next(calls))
    run = client.get("/api/agent-runs/" + run_id).json()
    assert run["status"] == "ready_for_review", run
    w = workspace(client, p)
    assert w["quote"]["lines"][0]["quantity"] == "8"
    assert w["clarifications"][0]["status"] == "answered"
    assert (
        post(
            client, "/proposals/" + run["result"]["proposal_id"] + "/accept", request(w)
        ).status_code
        == 200
    )
    w = workspace(client, p)
    assert w["quote"]["total"] == "100000.00"
    assert w["quote"]["status"] == "draft"
    assert (
        cmd(
            client,
            w,
            [
                {
                    "type": "reconcile_inputs",
                    "reason": "Reviewed updated offer and human-entered clarification",
                },
                {
                    "type": "review_line",
                    "line_id": line_id,
                    "reason": "Reviewed quantity, product, pricing and delivery",
                },
            ],
        ).status_code
        == 200
    )
    w = workspace(client, p)
    assert (
        post(client, "/quotes/" + w["quote"]["id"] + "/approve", request(w)).status_code
        == 422
    )
    response = patch(
        client,
        issue,
        status="resolved",
        resolution_note="Reviewed and accepted the revised supplier scope and pricing; quote reconciled.",
    )
    assert response.status_code == 200, response.text
    w = workspace(client, p)
    assert not w["quote"]["checks"]
    assert (
        post(client, "/quotes/" + w["quote"]["id"] + "/approve", request(w)).status_code
        == 200
    )
    queue = client.get("/api/work-queue").json()
    assert queue["counts"]["complete"] == 1
    assert queue["counts"]["waiting"] == queue["counts"]["attention"] == 0
