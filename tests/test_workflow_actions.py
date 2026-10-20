"""Real loopback HTTP integration, native artifacts and recoverable partial effects."""

import os
import socket
import threading
import time
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from fastapi import HTTPException
from sqlalchemy import select

from backend.domain.service import get
from backend.identity import Identity, current_org, identity_scope
from backend.lifecycle.completion_replay import complete
from backend.orders.service import lock_order
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order, OrderRevision
from backend.workflow_actions import service
from backend.workflow_actions.client import Configuration, LocalClient
from backend.workflow_actions.local_server import Store, create_app
from backend.workflow_actions.schemas import Execute

BUYER = "Fictional buyer and customer-schedule reviewer - isolated simulation"


@pytest.fixture
def local_server(tmp_path):
    organization_id = current_org()
    token = "fixture-local-only-token-not-a-real-credential"
    app = create_app(
        tmp_path / "enterprise.sqlite", token=token, organization_ids={organization_id}
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    config = Configuration(
        f"http://127.0.0.1:{sock.getsockname()[1]}",
        token,
        frozenset({organization_id}),
        {organization_id: [BUYER]},
    )
    yield app, config
    server.should_exit = True
    thread.join(5)
    sock.close()


@pytest.fixture
def approved(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "order-work")
    if not os.getenv("RIVET_PDF_PYTHON"):
        candidate = (
            Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
        )
        if candidate.is_file():
            monkeypatch.setenv("RIVET_PDF_PYTHON", str(candidate))
    return complete(number="DEMO-HTTP-ACTIONS")


def execute(
    approved,
    config,
    operation,
    *,
    reason="Execute the exact approved package in the isolated HTTP enterprise simulator.",
):
    with Session.begin() as s:
        order = lock_order(s, approved["order_id"])
        package = next(
            item
            for item in order.state["lifecycle"]["packages"]
            if item["operation"] == operation and item["status"] == "approved"
        )
        return service.execute(
            s,
            order,
            package["id"],
            Execute(expected_version=order.version, reason=reason),
            config=config,
        )


def test_full_http_order_actions_publish_real_bytes_and_retry_without_duplicate_effects(
    local_server, approved
):
    app, config = local_server
    with Session() as s:
        order = get(s, Order, approved["order_id"])
        original_state, original_version, original_hash = (
            deepcopy(order.state),
            order.version,
            order.snapshot_hash,
        )
        original_audit = [
            (r.id, r.checksum)
            for r in s.scalars(
                select(OrderRevision).where(OrderRevision.order_id == order.id)
            )
        ]
        purchasing = next(
            p
            for p in order.state["lifecycle"]["packages"]
            if p["operation"] == "purchasing"
        )
        expected_date = purchasing["payload"]["proposed_ship_date"]
    operations = ("kickoff", "revision_review", "purchasing", "release")
    results = [execute(approved, config, operation) for operation in operations]
    assert all(result["status"] == "complete" for result in results)
    assert sum(len(result["actions"]) for result in results) == 8
    client = LocalClient(config)
    state = client.state(current_org(), approved["order_id"])["resources"]
    assert state["sales_order"]["production_status"] == "released_local_simulation"
    assert state["sales_order"]["billing_authorized"] is False
    assert (
        state["sales_order"]["commercial_change_status"]
        == "proposed_not_customer_accepted"
    )
    assert state["material_schedule"]["buyer_confirmed"] is True
    assert state["material_schedule"]["customer_ship_date_committed"] is False
    assert state["material_schedule"]["date"] == expected_date
    assert state["released_documents"]["files"] == state["released_revision"]["files"]
    for item in state["released_documents"]["files"]:
        path = (
            app.state.store.objects
            / current_org()
            / approved["order_id"]
            / item["sha256"]
        )
        assert sha256(path.read_bytes()).hexdigest() == item["sha256"]
        assert path.stat().st_size == item["bytes"]
    retried = [
        execute(
            approved,
            config,
            operation,
            reason="Reconcile the approved action identities after restarting the orchestration client.",
        )
        for operation in operations
    ]
    assert all(row["reconciled"] for result in retried for row in result["actions"])
    assert client.state(current_org(), approved["order_id"])["resources"] == state
    with app.state.store.connect() as database:
        assert database.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 8
    with Session() as s:
        order = get(s, Order, approved["order_id"])
        assert order.state == original_state
        assert (
            order.version == original_version and order.snapshot_hash == original_hash
        )
        assert [
            (r.id, r.checksum)
            for r in s.scalars(
                select(OrderRevision).where(OrderRevision.order_id == order.id)
            )
        ] == original_audit
        assert order.status != "released"
        assert len(service.view(s, order)["attempts"]) == 16


def test_partial_failure_reconciles_prior_sap_write_before_retry(
    local_server, approved
):
    app, config = local_server
    app.state.fail_before_once.add("sharepoint.create_job_folder")
    first = execute(approved, config, "kickoff")
    assert first["status"] == "partial"
    assert [row["action"] for row in first["actions"]] == ["sap.draft_sales_order"]
    again = execute(
        approved,
        config,
        "kickoff",
        reason="Retry the incomplete folder creation and reconcile the existing ERP receipt.",
    )
    assert again["status"] == "complete"
    assert again["actions"][0]["reconciled"] is True
    assert again["actions"][0]["receipt"] == first["actions"][0]["receipt"]
    state = LocalClient(config).state(current_org(), approved["order_id"])["resources"]
    assert state["sales_order"]["version"] == state["job_folder"]["version"] == 1


def test_lost_teams_response_is_reconciled_and_schedule_requires_exact_buyer(
    local_server, approved
):
    app, config = local_server
    with pytest.raises(HTTPException) as error:
        execute(approved, replace(config, buyer_ids={}), "purchasing")
    assert error.value.status_code == 403
    assert app.state.store.state(current_org(), approved["order_id"]) == {}
    app.state.fail_after_once.add("teams.buyer_handoff")
    result = execute(approved, config, "purchasing")
    assert result["status"] == "complete"
    assert result["actions"][0]["reconciled"] is True
    state = app.state.store.state(current_org(), approved["order_id"])
    assert (
        state["buyer_channel"]["version"] == state["material_schedule"]["version"] == 1
    )


def test_unapproved_tampered_stale_and_ordinary_writer_cannot_execute(
    local_server, approved
):
    app, config = local_server
    with identity_scope(Identity(current_org(), "ordinary-writer", "org:member")):
        with pytest.raises(HTTPException) as error:
            execute(approved, config, "kickoff")
        assert error.value.status_code == 403
    with Session.begin() as s:
        order = lock_order(s, approved["order_id"])
        package = next(
            item
            for item in order.state["lifecycle"]["packages"]
            if item["operation"] == "kickoff" and item["status"] == "approved"
        )
        original = deepcopy(order.state)
        altered = deepcopy(order.state)
        next(
            item
            for item in altered["lifecycle"]["packages"]
            if item["id"] == package["id"]
        )["payload"]["purchase_order_number"] = "SPOOFED-PO"
        order.state = altered
        with pytest.raises(HTTPException) as error:
            service.execute(
                s,
                order,
                package["id"],
                Execute(
                    expected_version=order.version,
                    reason="Attempt to execute changed state without a matching signature.",
                ),
                config=config,
            )
        assert error.value.status_code == 409
        order.state = original
        unapproved = deepcopy(order.state)
        next(
            item
            for item in unapproved["lifecycle"]["packages"]
            if item["id"] == package["id"]
        )["status"] = "awaiting_decision"
        order.state = unapproved
        with pytest.raises(HTTPException) as error:
            service.execute(
                s,
                order,
                package["id"],
                Execute(
                    expected_version=order.version,
                    reason="An unapproved package cannot cross an enterprise boundary.",
                ),
                config=config,
            )
        assert error.value.status_code == 409
        order.state = original
        source = next(iter(package["sources"].values()))
        document = get(s, Document, source["document_id"])
        path = db.BLOBS / document.blob
        original_bytes = path.read_bytes()
        path.write_bytes(b"changed after the approving human signed")
        with pytest.raises(HTTPException) as error:
            service.execute(
                s,
                order,
                package["id"],
                Execute(
                    expected_version=order.version,
                    reason="Attempt to execute an approved package whose physical evidence changed.",
                ),
                config=config,
            )
        assert error.value.status_code == 409
        path.write_bytes(original_bytes)
    assert app.state.store.state(current_org(), approved["order_id"]) == {}


def test_remote_state_survives_restart_and_wrong_tenant_cannot_use_client(
    local_server, approved
):
    app, config = local_server
    execute(approved, config, "kickoff")
    reloaded = Store(app.state.store.path)
    assert (
        reloaded.state(current_org(), approved["order_id"])["sales_order"]["version"]
        == 1
    )
    with Session.begin() as s:
        order = lock_order(s, approved["order_id"])
        package = next(
            item
            for item in order.state["lifecycle"]["packages"]
            if item["operation"] == "kickoff" and item["status"] == "approved"
        )
        with pytest.raises(HTTPException) as error:
            service.execute(
                s,
                order,
                package["id"],
                Execute(
                    expected_version=order.version + 1,
                    reason="A stale browser version must not execute this package.",
                ),
                config=config,
            )
        assert error.value.status_code == 409
    with identity_scope(Identity(str(uuid4()), "other-tenant")), Session.begin() as s:
        with pytest.raises(HTTPException) as error:
            lock_order(s, approved["order_id"])
        assert error.value.status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://localhost:5188",
        "http://127.0.0.1:5188/path",
        "http://127.0.0.1:5188?token=secret",
    ],
)
def test_configuration_does_not_allow_accidental_live_vendor_writes(url):
    with pytest.raises(ValueError):
        Configuration(
            url, "fixture-only-token-long-enough", frozenset({current_org()}), {}
        )


def test_last_human_gate_queues_background_execution_and_worker_reconciles_partial_retry(
    local_server, approved, monkeypatch
):
    import json
    from datetime import timedelta

    from pydantic import TypeAdapter

    from backend.lifecycle import service as lifecycle
    from backend.lifecycle.schemas import DecisionWrite, Prepare
    from backend.storage.models import Job, now
    from backend.worker import claim, process

    app, config = local_server
    monkeypatch.setenv("RIVET_LOCAL_ACTIONS_AUTOMATIC", "true")
    monkeypatch.setenv("RIVET_LOCAL_ACTIONS_URL", config.base_url)
    monkeypatch.setenv("RIVET_LOCAL_ACTIONS_TOKEN", config.token)
    monkeypatch.setenv("RIVET_LOCAL_ACTIONS_ORGANIZATIONS", current_org())
    monkeypatch.setenv("RIVET_LOCAL_ACTIONS_BUYERS", json.dumps(config.buyer_ids))
    with Session.begin() as s:
        order = lock_order(s, approved["order_id"])
        prior = next(
            p
            for p in order.state["lifecycle"]["packages"]
            if p["operation"] == "kickoff" and p["status"] == "approved"
        )
        payload = prior["payload"]
        prepared = lifecycle.prepare(
            s,
            order,
            TypeAdapter(Prepare).validate_python(
                {
                    "operation": "kickoff",
                    "expected_version": order.version,
                    "purchase_order_number": payload["purchase_order_number"],
                    "lines": payload["lines"],
                }
            ),
        )
        package = next(
            p
            for p in prepared["packages"]
            if p["operation"] == "kickoff" and p["status"] == "awaiting_decision"
        )
        package_id = package["id"]
        source_id = payload["lines"][0]["source_ids"][0]
    for index, gate in enumerate(("scope", "engineering")):
        with Session.begin() as s:
            order = lock_order(s, approved["order_id"])
            lifecycle.decide(
                s,
                order,
                package_id,
                DecisionWrite(
                    expected_version=order.version,
                    gate=gate,
                    decision="approve",
                    reason="The authenticated fictional human reviewed the exact kickoff package; execute only the configured local simulation.",
                    source_ids=[source_id],
                ),
            )
            jobs = list(s.scalars(select(Job).where(Job.kind == "workflow_actions")))
            assert len(jobs) == index
    assert app.state.store.state(current_org(), approved["order_id"]) == {}
    with Session() as s:
        order = get(s, Order, approved["order_id"])
        state, version, checksum = (
            deepcopy(order.state),
            order.version,
            order.snapshot_hash,
        )
    app.state.fail_before_once.add("sharepoint.create_job_folder")
    task = claim(kinds=("workflow_actions",))
    assert task is not None
    process(task)
    with Session.begin() as s:
        job = s.get(Job, task[0])
        assert job.status == "running" and job.attempts == 1
        job.lease_until = now() - timedelta(seconds=1)
    next_task = claim(kinds=("workflow_actions",))
    assert next_task is not None
    process(next_task)
    with Session() as s:
        job = s.get(Job, task[0])
        assert job.status == "succeeded" and job.attempts == 2
        order = get(s, Order, approved["order_id"])
        assert (
            order.state == state
            and order.version == version
            and order.snapshot_hash == checksum
        )
    remote = app.state.store.state(current_org(), approved["order_id"])
    assert remote["sales_order"]["version"] == remote["job_folder"]["version"] == 1


def test_remote_optimistic_version_rejects_a_stale_erp_write(local_server, approved):
    from backend.domain.service import digest
    from backend.workflow_actions.client import DeliveryError
    from backend.workflow_actions.schemas import Command

    app, config = local_server
    execute(approved, config, "kickoff")
    with Session() as s:
        order = get(s, Order, approved["order_id"])
        package = next(
            item
            for item in order.state["lifecycle"]["packages"]
            if item["operation"] == "revision_review" and item["status"] == "approved"
        )
        package, approvals = service._package(s, order, package["id"])
        action, payload, files = service._plan(s, order, package, approvals, config)[0]
        command = Command(
            organization_id=order.organization_id,
            order_id=order.id,
            job_number=order.number,
            action=action,
            package_id=package["id"],
            package_fingerprint=package["fingerprint"],
            order_version=order.version,
            source_state_sha256=digest(package["sources"]),
            approvals=approvals,
            requested_by="Isolated HTTP fixture",
            reason="Test an obsolete remote version without overwriting current ERP state.",
            payload=payload,
            files=files,
            expected_remote_version=0,
        )
        action_id = digest(
            [
                order.organization_id,
                order.id,
                package["id"],
                package["fingerprint"],
                action,
            ]
        )
    with pytest.raises(DeliveryError) as error:
        LocalClient(config).deliver(command, action_id)
    assert error.value.retryable is False
    assert (
        app.state.store.state(current_org(), approved["order_id"])["sales_order"][
            "version"
        ]
        == 1
    )
    assert execute(approved, config, "revision_review")["status"] == "complete"
    assert (
        app.state.store.state(current_org(), approved["order_id"])["sales_order"][
            "version"
        ]
        == 2
    )


def test_a_hash_matching_symlink_outside_the_blob_store_cannot_be_published(
    local_server, approved, tmp_path
):
    app, config = local_server
    with Session() as s:
        order = get(s, Order, approved["order_id"])
        package = next(
            item
            for item in order.state["lifecycle"]["packages"]
            if item["operation"] == "kickoff" and item["status"] == "approved"
        )
        source = next(iter(package["sources"].values()))
        document = get(s, Document, source["document_id"])
        path = db.BLOBS / document.blob
        content = path.read_bytes()
    outside = tmp_path / "hash-matching-source-outside-blob-store.json"
    outside.write_bytes(content)
    path.unlink()
    path.symlink_to(outside)
    try:
        with pytest.raises(HTTPException) as error:
            execute(approved, config, "kickoff")
        assert error.value.status_code == 409
        assert "unavailable" in error.value.detail
        assert app.state.store.state(current_org(), approved["order_id"]) == {}
    finally:
        path.unlink()
        path.write_bytes(content)
