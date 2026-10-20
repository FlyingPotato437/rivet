"""Opt-in durable outbox. An approved package, never a model, authorizes execution."""

import os

from fastapi import HTTPException

from backend.domain.service import digest, get, scoped
from backend.identity import current_actor
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Job, OrderEvent, Project

from . import service
from .client import configuration
from .schemas import Execute

OPERATIONS = {"kickoff", "revision_review", "purchasing", "release"}


def automatic_enabled():
    return os.getenv("RIVET_LOCAL_ACTIONS_AUTOMATIC", "false").lower() == "true"


def enqueue(s, order, package):
    if not automatic_enabled() or package["operation"] not in OPERATIONS:
        return {"status": "disabled"}
    if not get(s, Project, order.project_id).synthetic:
        return {"status": "unsupported_production_order"}
    try:
        config = configuration()
        if order.organization_id not in config.organization_ids:
            return {"status": "organization_not_enabled"}
        current, approvals = service._package(s, order, package["id"])
        if not service._plan(s, order, current, approvals, config):
            return {"status": "no_enterprise_actions"}
    except HTTPException as exc:
        s.add(
            OrderEvent(
                order_id=order.id,
                version=order.version,
                kind="workflow_action_queue",
                summary="Approved work could not enter the local execution queue",
                actor="Rivet outbox",
                data={
                    "mode": "local_http_demo",
                    "package_id": package["id"],
                    "status": "blocked",
                    "message": str(exc.detail),
                },
            )
        )
        return {"status": "blocked"}
    key = "workflow-actions:" + digest(
        [order.organization_id, order.id, package["id"], package["fingerprint"]]
    )
    existing = s.scalar(scoped(Job).where(Job.key == key))
    if existing:
        return {"status": existing.status, "job_id": existing.id}
    job = Job(
        project_id=order.project_id,
        kind="workflow_actions",
        key=key,
        payload={
            "order_id": order.id,
            "package_id": package["id"],
            "fingerprint": package["fingerprint"],
            "authorized_by": current_actor(),
            "mode": "local_http_demo",
        },
    )
    s.add(job)
    s.flush()
    s.add(
        OrderEvent(
            order_id=order.id,
            version=order.version,
            kind="workflow_action_queue",
            summary="Approved work queued for the local enterprise HTTP simulator",
            actor="Rivet outbox",
            data={
                "mode": "local_http_demo",
                "package_id": package["id"],
                "status": "queued",
                "job_id": job.id,
            },
        )
    )
    return {"status": "queued", "job_id": job.id}


def process_job(payload, leased):
    with Session.begin() as s:
        order = lock_order(s, payload["order_id"])
        if not automatic_enabled():
            return {"status": "disabled"}
        package, approvals = service._package(s, order, payload["package_id"])
        if (
            payload.get("mode") != "local_http_demo"
            or payload.get("fingerprint") != package["fingerprint"]
            or payload.get("authorized_by")
            not in {item["actor"] for item in approvals.values()}
        ):
            raise HTTPException(
                409, "The queued work does not match its authenticated approval."
            )
        return service.execute(
            s,
            order,
            package["id"],
            Execute(
                expected_version=order.version,
                reason="Automatically execute this exact human-approved package through the opt-in local enterprise simulator; reconcile every prior receipt.",
            ),
            leased=leased,
        )
