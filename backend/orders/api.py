from typing import Literal

from fastapi import APIRouter, Header
from fastapi.responses import Response
from sqlalchemy import text

from backend.domain.service import get, idempotent, scoped
from backend.storage.db import Session
from backend.storage.models import Order

from . import service as svc
from .schemas import (
    ClassifyDocument,
    OrderCommand,
    OrderCreate,
    OrderDecision,
    OrderReleaseRequest,
    OrderVersion,
    OrderWaive,
    ProposeAction,
    VerifyTask,
)

router = APIRouter(prefix="/api/orders", tags=["Engineering orders"])


@router.get("")
def orders():
    with Session() as s:
        return {
            "orders": [
                svc.summary(s, order)
                for order in s.scalars(scoped(Order).order_by(Order.updated_at.desc()))
            ]
        }


@router.post("")
def create(body: OrderCreate, idempotency_key: str = Header()):
    with Session.begin() as s:
        s.execute(text("SELECT pg_advisory_xact_lock(777003)"))
        return idempotent(
            s,
            "order:create:" + idempotency_key,
            body.model_dump(),
            lambda: svc.workspace(s, svc.new_order(s, body.model_dump())),
        )


@router.get("/{id}")
def workspace(id: str):
    with Session() as s:
        return svc.workspace(s, get(s, Order, id))


def mutate(id, body, key, scope, operation):
    with Session.begin() as s:
        order = svc.lock_order(s, id)

        def act():
            svc.check_version(order, body.expected_version)
            operation(s, order)
            return svc.workspace(s, order)

        return idempotent(s, order.id + ":" + scope + ":" + key, body.model_dump(), act)


@router.post("/{id}/refresh")
def refresh(id: str, body: OrderVersion, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "refresh", lambda s, o: svc.refresh(s, o))


@router.post("/{id}/documents/{document_id}/classify")
def classify_document(
    id: str,
    document_id: str,
    body: ClassifyDocument,
    idempotency_key: str = Header(),
):
    return mutate(
        id,
        body,
        idempotency_key,
        "classify:" + document_id,
        lambda s, o: svc.classify_document(
            s, o, document_id, body.model_dump(exclude_unset=True)
        ),
    )


@router.post("/{id}/actions")
def propose(id: str, body: ProposeAction, idempotency_key: str = Header()):
    def action(s, order):
        a = svc.make_action(
            s, order, body.type, body.title, body.summary, body.after, body.source_ids
        )
        svc.commit(s, order, "Prepared: " + a.title, "draft", {"action_id": a.id})

    return mutate(id, body, idempotency_key, "propose", action)


@router.post("/{id}/actions/{action_id}/accept")
def accept(
    id: str, action_id: str, body: OrderDecision, idempotency_key: str = Header()
):
    return mutate(
        id,
        body,
        idempotency_key,
        "accept:" + action_id,
        lambda s, o: svc.accept_action(s, o, action_id, body.reason, body.edited_text),
    )


@router.post("/{id}/actions/{action_id}/reject")
def reject(
    id: str, action_id: str, body: OrderDecision, idempotency_key: str = Header()
):
    return mutate(
        id,
        body,
        idempotency_key,
        "reject:" + action_id,
        lambda s, o: svc.reject_action(s, o, action_id, body.reason),
    )


@router.post("/{id}/waivers")
def waive(id: str, body: OrderWaive, idempotency_key: str = Header()):
    return mutate(
        id,
        body,
        idempotency_key,
        "waive",
        lambda s, o: svc.waive(s, o, body.model_dump()),
    )


@router.post("/{id}/tasks/{task_id}/verify")
def verify(id: str, task_id: str, body: VerifyTask, idempotency_key: str = Header()):
    return mutate(
        id,
        body,
        idempotency_key,
        "verify:" + task_id,
        lambda s, o: svc.verify_task(s, o, task_id, body.source_ids),
    )


@router.post("/{id}/release")
def release(id: str, body: OrderReleaseRequest, idempotency_key: str = Header()):
    return mutate(
        id,
        body,
        idempotency_key,
        "release",
        lambda s, o: svc.release(s, o, body.model_dump()),
    )


@router.post("/{id}/commands")
def commands(id: str, body: OrderCommand, idempotency_key: str = Header()):
    from .commands import execute

    with Session.begin() as s:
        order = svc.lock_order(s, id)

        def action():
            svc.check_version(order, body.expected_version)
            return execute(s, order, body.prompt)

        return idempotent(
            s, order.id + ":command:" + idempotency_key, body.model_dump(), action
        )


@router.get("/{id}/revisions/{version}")
def revision(id: str, version: int):
    with Session() as s:
        row = svc.get_revision(s, get(s, Order, id), version)
        return {
            "version": row.version,
            "revision_label": row.revision_label,
            "summary": row.summary,
            "actor": row.actor,
            "at": row.created_at.isoformat(),
            "checksum": row.checksum,
            "snapshot": row.snapshot,
        }


@router.get("/{id}/diff")
def compare(id: str, from_version: int, to_version: int | None = None):
    with Session() as s:
        return svc.diff(s, get(s, Order, id), from_version, to_version)


def export_response(data, output, format, kind):
    media = (
        "application/pdf"
        if format == "pdf"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    import re

    number = re.sub(r"[^A-Za-z0-9._-]", "_", data["order"]["number"])
    name = f"{number}-v{data['order']['version']}-{kind}.{format}"
    return Response(
        output,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/{id}/exports/response-matrix")
def matrix(id: str, format: Literal["pdf", "xlsx"] = "pdf"):
    from .exports import response_matrix_pdf, response_matrix_xlsx

    with Session() as s:
        data = svc.workspace(s, get(s, Order, id))
        return export_response(
            data,
            (response_matrix_pdf if format == "pdf" else response_matrix_xlsx)(data),
            format,
            "response-matrix",
        )


@router.get("/{id}/exports/bom-delta")
def bom_delta(id: str, from_version: int = 1, to_version: int | None = None):
    from .exports import bom_delta_xlsx

    with Session() as s:
        order = get(s, Order, id)
        data = svc.workspace(s, order)
        data["diff"] = svc.diff(s, order, from_version, to_version)
        target = svc.get_revision(s, order, data["diff"]["to_version"])
        data["order"]["version"] = target.version
        data["order"]["revision_label"] = target.revision_label
        return export_response(data, bom_delta_xlsx(data), "xlsx", "bom-delta")
