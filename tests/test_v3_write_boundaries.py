import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from test_record_agent import example, fetched, start

from backend.agents.context_budget import token_count
from backend.notifications import delivery
from backend.project_flow.followups import due_after
from backend.records import agent_service as agent
from backend.storage.db import Session
from backend.worker import claim, process
from backend.workflow_actions import publication


def test_rejected_generations_still_count_toward_usage(client, monkeypatch):
    o, w = example(client, monkeypatch, ai=True)
    run = start(client, o, w)

    def failed(*args):
        raise agent.ProviderOutputError(
            "Incomplete after regeneration",
            {
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
                "_model_calls": 3,
            },
        )

    monkeypatch.setattr(agent, "provider_call", failed)
    process(claim())
    result = fetched(client, o, run)
    assert result["status"] == "failed" and result["usage"]["model_calls"] == 3
    assert result["usage"]["total_tokens"] == 30 and not result["proposals"]


def test_sharepoint_upload_is_new_copy_and_does_not_forward_graph_token():
    content = b"Approved immutable artifact"
    row = SimpleNamespace(
        package_id="package-1",
        manifest={
            "sha256": __import__("hashlib").sha256(content).hexdigest(),
            "name": "letter.pdf",
        },
        status="queued",
    )
    expected = f"Rivet-package-1-{row.manifest['sha256'][:12]}-letter.pdf"

    def graph_request(request):
        assert request.method == "POST"
        assert (
            json.loads(request.content)["item"]["@microsoft.graph.conflictBehavior"]
            == "fail"
        )
        return httpx.Response(
            200,
            json={
                "uploadUrl": "https://approved.upload.example/session?private=opaque"
            },
        )

    def upload(request):
        assert request.method == "PUT" and request.headers.get("authorization") is None
        assert request.content == content
        return httpx.Response(
            201,
            json={
                "id": "drive-item-1",
                "name": expected,
                "size": len(content),
                "eTag": "v1",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(graph_request)) as client:
        graph = SimpleNamespace(
            client=client, headers=lambda: {"Authorization": "Bearer graph-token"}
        )
        result = publication.sharepoint(
            graph,
            {
                "drive_id": "drive-1",
                "parent_id": "folder-1",
                "upload_hosts": ["approved.upload.example"],
            },
            row,
            content,
            upload_transport=httpx.MockTransport(upload),
        )
    assert (
        result["status"] == "published_copy" and result["provider_id"] == "drive-item-1"
    )


def test_truncated_tool_json_is_regenerated_before_any_mutation(client, monkeypatch):
    _, w = example(client, monkeypatch, ai=True)
    calls = []

    class Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kw):
            calls.append(kw["json"].copy())
            incomplete = len(calls) == 1
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "status": "incomplete" if incomplete else "completed",
                    "usage": {"total_tokens": 100},
                    "output": [
                        {
                            "type": "function_call",
                            "name": "complete",
                            "call_id": "call-safe",
                            "arguments": '{"summary":"cut off'
                            if incomplete
                            else '{"summary":"Drafts ready","source_ids":[]}',
                        }
                    ],
                },
            )

    monkeypatch.setattr(agent.httpx, "Client", Client)
    run = {
        "goal": "Prepare a response",
        "model": "gpt-6-luna",
        "steps": [],
        "state": {},
        "budget": {
            "max_steps": 40,
            "max_output_tokens": 8000,
            "max_context_tokens": 64000,
            "max_total_tokens": 2000000,
        },
    }
    name, args, usage = agent.provider_call(run, w)
    assert len(calls) == 2 and name == "complete" and args["summary"] == "Drafts ready"
    assert (
        calls[0]["max_output_tokens"] == 8000 and calls[1]["max_output_tokens"] == 16000
    )
    assert usage["total_tokens"] == 200 and usage["_model_calls"] == 2
    assert (
        token_count(calls[-1]["input"]) + token_count(calls[-1]["tools"]) + 16000
        < 64000
    )


def test_history_compacts_whole_observations_without_erasing_originals(
    client, monkeypatch
):
    _, w = example(client, monkeypatch, ai=True)
    captured = {}

    class Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kw):
            captured.update(kw["json"])
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "output": [
                        {
                            "type": "function_call",
                            "name": "complete",
                            "call_id": "call-final",
                            "arguments": '{"summary":"Done","source_ids":[]}',
                        }
                    ]
                },
            )

    monkeypatch.setattr(agent.httpx, "Client", Client)
    turns = [
        agent.protocol_turn(
            "read_source",
            {"source_id": f"source-{i}"},
            {
                "id": f"source-{i}",
                "text": "线路图 revision 中文 cable 🔧 " * 2000,
                "coverage": {"complete": False},
            },
        )
        for i in range(12)
    ]
    original = json.dumps(turns)
    run = {
        "goal": "Review evidence",
        "model": "gpt-6-luna",
        "steps": [],
        "state": {"protocol_turns": turns},
        "budget": {
            "max_steps": 40,
            "max_output_tokens": 8000,
            "max_context_tokens": 64000,
            "max_total_tokens": 2000000,
        },
    }
    agent.provider_call(run, w)
    assert json.dumps(turns) == original
    continuation = json.loads(captured["input"][-1]["content"])
    assert continuation["compacted_observations"]
    assert "source-0" in json.dumps(continuation)
    assert token_count(captured["input"]) + 16000 < 64000


def test_followup_clock_skips_weekends():
    friday = datetime(2026, 10, 16, 15, tzinfo=UTC)
    assert due_after(friday).isoformat() == "2026-10-21T15:00:00+00:00"


def test_bridge_reconciles_existing_receipt_without_repeating_write(monkeypatch):
    monkeypatch.setenv("TEST_BRIDGE_TOKEN", "unit-test-only")
    row = SimpleNamespace(
        id="publication-1",
        status="needs_reconciliation",
        fingerprint="f" * 64,
        manifest={"sha256": "a" * 64},
    )
    order = SimpleNamespace(number="SWG-24-118", organization_id="test-org")
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(
            200,
            json={
                "status": "committed",
                "fingerprint": row.fingerprint,
                "job_number": order.number,
                "sha256": "a" * 64,
            },
        )

    result = publication.bridge(
        {
            "base_url": "https://manufacturer.example/rivet",
            "token_env": "TEST_BRIDGE_TOKEN",
        },
        row,
        order,
        b"test",
        httpx.MockTransport(handler),
    )
    assert result["status"] == "committed" and calls == ["GET"]


def test_uncertain_missing_bridge_receipt_never_repeats_put(monkeypatch):
    monkeypatch.setenv("TEST_BRIDGE_TOKEN", "unit-test-only")
    row = SimpleNamespace(
        id="publication-1",
        status="needs_reconciliation",
        fingerprint="f" * 64,
        manifest={"sha256": "a" * 64},
    )
    order = SimpleNamespace(number="SWG-24-118", organization_id="test-org")

    def handler(request):
        assert request.method == "GET"
        return httpx.Response(404)

    with pytest.raises(ValueError):
        publication.bridge(
            {
                "base_url": "https://manufacturer.example",
                "token_env": "TEST_BRIDGE_TOKEN",
            },
            row,
            order,
            b"test",
            httpx.MockTransport(handler),
        )


def test_sharepoint_unknown_outcome_never_opens_another_upload_session():
    row = SimpleNamespace(
        package_id="pkg",
        manifest={"sha256": "a" * 64, "name": "letter.pdf"},
        status="creating",
    )
    result = publication.sharepoint(
        None,
        {"drive_id": "allowed-drive", "parent_id": "approved-folder"},
        row,
        b"original",
    )
    assert result["status"] == "needs_reconciliation"


def test_unconfigured_teams_target_never_posts(client):
    with Session.begin() as s:
        row = delivery.InternalNotification(
            dedupe_key="brief:test", payload={"type": "AdaptiveCard"}
        )
        s.add(row)
        s.flush()
        id = row.id
    delivery.deliver(id, {})
    with Session() as s:
        assert (
            s.get(delivery.InternalNotification, id).status == "configuration_required"
        )
