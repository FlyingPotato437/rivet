from fastapi import APIRouter, Header

from backend.domain.service import get, idempotent
from backend.mailbox.models import MailboxReceipt
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Order

from . import service
from .schemas import FactWrite, PolicyWrite, PrepareWrite

router = APIRouter(prefix="/api/routine-status", tags=["Routine status"])


@router.get("/policy")
def policy():
    with Session() as s:
        return service.policy(s)


@router.put("/policy")
def policy_write(body: PolicyWrite):
    with Session.begin() as s:
        return service.set_policy(s, body)


@router.get("/orders/{order_id}")
def order_status(order_id: str):
    with Session() as s:
        return service.view(s, get(s, Order, order_id))


@router.post("/orders/{order_id}/facts")
def fact(
    order_id: str,
    body: FactWrite,
    idempotency_key: str = Header(min_length=1, max_length=180),
):
    with Session.begin() as s:
        order = lock_order(s, order_id)
        return idempotent(
            s,
            f"routine-status-fact:{order.id}:{idempotency_key}",
            body.model_dump(mode="json"),
            lambda: service.approve_fact(s, order, body),
        )


@router.post("/orders/{order_id}/prepare")
def prepare(order_id: str, body: PrepareWrite):
    from backend.company_context.service import require_writer

    require_writer()
    with Session.begin() as s:
        order = lock_order(s, order_id)
        result = service.prepare_for_receipt(
            s, order, get(s, MailboxReceipt, body.receipt_id)
        )
        return result or {
            "status": "not_applicable",
            "detail": "This is not a routine order-status question.",
        }
