"""Typed order lifecycle tools, using the same tenant and optimistic locks as orders."""

from fastapi import APIRouter, Header
from fastapi.responses import Response
from pydantic import Field

from backend.domain.service import get, idempotent
from backend.orders.schemas import StrictModel
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Order

from . import adapters, service
from .schemas import DecisionWrite, PolicyWrite, Prepare

router = APIRouter(prefix="/api/orders", tags=["Order lifecycle"])


class OutlookHandoffWrite(StrictModel):
    expected_version: int = Field(ge=1)
    receipt_id: str = Field(min_length=36, max_length=36)


@router.post("/{id}/lifecycle/{package_id}/outlook-draft")
def outlook_draft(
    id: str,
    package_id: str,
    body: OutlookHandoffWrite,
    idempotency_key: str = Header(min_length=1, max_length=180),
):
    from backend.orders.service import check_version

    from .outlook_handoff import enqueue

    with Session.begin() as s:
        order = lock_order(s, id)

        def queue():
            check_version(order, body.expected_version)
            return enqueue(s, order, package_id, body.receipt_id)

        return idempotent(
            s,
            f"{order.id}:lifecycle:outlook:{idempotency_key}",
            {"package_id": package_id, **body.model_dump()},
            queue,
        )


@router.get("/{id}/lifecycle")
def workspace(id: str):
    with Session() as s:
        return service.view(s, get(s, Order, id))


@router.post("/{id}/lifecycle/prepare")
def prepare(
    id: str, body: Prepare, idempotency_key: str = Header(min_length=1, max_length=180)
):
    with Session.begin() as s:
        order = lock_order(s, id)
        return idempotent(
            s,
            f"{order.id}:lifecycle:prepare:{idempotency_key}",
            body.model_dump(mode="json"),
            lambda: service.prepare(s, order, body),
        )


@router.post("/{id}/lifecycle/decisions/{package_id}")
def decide(
    id: str,
    package_id: str,
    body: DecisionWrite,
    idempotency_key: str = Header(min_length=1, max_length=180),
):
    with Session.begin() as s:
        order = lock_order(s, id)
        return idempotent(
            s,
            f"{order.id}:lifecycle:decision:{idempotency_key}",
            body.model_dump(mode="json"),
            lambda: service.decide(s, order, package_id, body),
        )


@router.post("/{id}/lifecycle/policy")
def policy(id: str, body: PolicyWrite):
    with Session.begin() as s:
        get(s, Order, id)
        return service.set_policy(s, body)


@router.get("/{id}/lifecycle/artifacts/{package_id}/{name}")
def artifact(id: str, package_id: str, name: str):
    with Session() as s:
        order = get(s, Order, id)
        package = next(
            (
                p
                for p in order.state.get("lifecycle", {}).get("packages", [])
                if p["id"] == package_id
            ),
            None,
        )
        manifest = (
            next((a for a in package["artifacts"] if a["name"] == name), None)
            if package
            else None
        )
        if not manifest:
            from backend.domain.service import fail

            fail("Prepared artifact not found.", 404)
        content = adapters.read_artifact(
            order.organization_id, order.id, package_id, manifest
        )
        return Response(
            content,
            media_type=manifest["media_type"],
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )


@router.get("/{id}/lifecycle/drafting/{bundle_id}/{name}")
def drafting_artifact(id: str, bundle_id: str, name: str):
    from backend.drafting import read_bundle_artifact
    from backend.storage import db

    with Session() as s:
        order = get(s, Order, id)
        bundle = service._draft_bundle(s, order, bundle_id)
        report = (
            db.DATA
            / "drafting"
            / order.organization_id
            / order.id
            / bundle_id
            / "report.json"
        )
        content, artifact = read_bundle_artifact(
            report,
            artifact_name=name,
            organization_id=order.organization_id,
            order_id=order.id,
            expected_report_sha256=bundle["report_sha256"],
        )
        disposition = (
            "inline"
            if artifact.media_type in {"text/html", "image/svg+xml"}
            else "attachment"
        )
        return Response(
            content,
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="{artifact.name}"',
                "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'",
            },
        )
