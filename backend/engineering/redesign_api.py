"""Authenticated revision files, background preparation and final decision."""

from typing import Literal

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse
from pydantic import Field

from backend.domain.service import get, idempotent
from backend.orders import service as orders
from backend.orders.schemas import OrderVersion
from backend.storage.db import Session
from backend.storage.models import Order

from . import redesign_service as service

router = APIRouter(prefix="/api/orders", tags=["Drawing redesign"])


class Decision(OrderVersion):
    decision: Literal["approve", "request_changes"]
    note: str = Field(default="", max_length=3000)


@router.get("/{id}/engineering/redesign")
def view(id: str):
    with Session() as s:
        return service.view(s, get(s, Order, id))


@router.post("/{id}/engineering/redesign/prepare")
def prepare(id: str, body: OrderVersion, idempotency_key: str = Header()):
    with Session.begin() as s:
        order = orders.lock_order(s, id)

        def act():
            orders.check_version(order, body.expected_version)
            service.prepare(s, order)
            return service.view(s, order)

        return idempotent(
            s, f"redesign:{id}:prepare:{idempotency_key}", body.model_dump(), act
        )


@router.post("/{id}/engineering/redesign/{run_id}/decision")
def decision(id: str, run_id: str, body: Decision, idempotency_key: str = Header()):
    with Session.begin() as s:
        order = orders.lock_order(s, id)

        def act():
            orders.check_version(order, body.expected_version)
            service.decision(s, order, run_id, body.decision, body.note)
            return service.view(s, order)

        return idempotent(
            s,
            f"redesign:{id}:{run_id}:decision:{idempotency_key}",
            body.model_dump(),
            act,
        )


@router.get("/{id}/engineering/redesign/{run_id}/files/{name}")
def file(id: str, run_id: str, name: str):
    with Session() as s:
        path = service.artifact(s, get(s, Order, id), run_id, name)
        return FileResponse(
            path,
            filename=path.name,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
