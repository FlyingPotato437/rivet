"""Completed native files resolve only their own redundant event investigation."""

from uuid import uuid4

import test_native_outlook_workflow as native_workflow

from backend.domain.service import get
from backend.equipment import workflow
from backend.project_flow.activity import timeline
from backend.storage.db import Session
from backend.storage.models import Order, RecordAgentRun

corpus = native_workflow.corpus


def test_native_success_keeps_failed_history_without_a_false_current_warning(
    client, corpus
):
    oid, _ = native_workflow.register(client, corpus, 1)
    with Session.begin() as session:
        order = get(session, Order, oid)
        workflow.prepare(session, order)
        bundle = order.state["equipment_corrections"]["bundles"][0]
        fingerprint = bundle["source_fingerprint"]
        runs = []
        for automatic, hint in (
            (True, fingerprint),
            (False, fingerprint),
            (True, "0" * 64),
        ):
            run = RecordAgentRun(
                order_id=oid,
                goal="Inspect returned changes",
                actor="Rivet event coordinator" if automatic else "Review PM",
                status="failed",
                trigger_key=(f"record-evidence:{oid}:" if automatic else "manual:")
                + str(uuid4()),
                base_version=order.version,
                input_hash="1" * 64,
                state={"equipment_check_hint": {"fingerprint": hint}},
                result={
                    "error": "Original investigation failed; retained for inspection."
                },
            )
            session.add(run)
            session.flush()
            runs.append(run.id)
        bundle_id = bundle["id"]
    with Session() as session:
        events = {event["id"]: event for event in timeline(session, oid)}
        resolved = events[runs[0]]
        assert resolved["kind"] == "event"
        assert resolved["facts"]["superseded_bundle_id"] == bundle_id
        assert "superseded" in resolved["title"]
        assert "failed" in resolved["detail"]
        assert events[runs[1]]["kind"] == events[runs[2]]["kind"] == "attention"
        # No state was rewritten into success and no history was removed.
        assert get(session, RecordAgentRun, runs[0]).status == "failed"
        order = get(session, Order, oid)
        raw = order.state["equipment_corrections"]["bundles"][0]
        artifact = next(item for item in raw["artifacts"] if item["role"] == "drawing")
        path, _ = workflow.artifact(session, order, bundle_id, artifact["name"])
        path.write_bytes(path.read_bytes() + b"\nchanged after check\n")
        changed = {event["id"]: event for event in timeline(session, oid)}
        assert changed[runs[0]]["kind"] == "attention"
        assert "superseded_bundle_id" not in changed[runs[0]]["facts"]
