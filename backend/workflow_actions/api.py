from fastapi import APIRouter

from backend.domain.service import get
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Order

from . import service
from .schemas import Execute

router = APIRouter(prefix="/api/orders", tags=["Local enterprise action harness"])


@router.get("/{id}/workflow-actions")
def status(id: str):
    with Session() as s:
        return service.view(s, get(s, Order, id))


@router.post("/{id}/workflow-actions/{package_id}/execute")
def execute(id: str, package_id: str, body: Execute):
    # Stable remote action identities provide idempotency even if this process
    # crashes before its local transaction records the response.
    with Session.begin() as s:
        return service.execute(s, lock_order(s, id), package_id, body)
