from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from backend.company_context.service import require_writer
from backend.domain.service import get
from backend.storage.db import Session
from backend.storage.models import Order

from . import service

router = APIRouter(prefix="/api", tags=["Connected order sources"])


class SapCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=36)


@router.get("/integrations/sap")
def sap_connection_status():
    from .diagnostics import sap_status

    with Session() as s:
        return sap_status(s)


@router.post("/integrations/sap/check", status_code=202)
def sap_connection_check(body: SapCheck):
    from .diagnostics import check_sap

    with Session.begin() as s:
        return check_sap(s, body.order_id)


@router.get("/orders/{id}/connected-context")
def connected_context(id: str):
    with Session() as s:
        return service.summary(s, get(s, Order, id))


@router.post("/orders/{id}/connected-context/refresh", status_code=202)
def refresh(id: str):
    require_writer()
    with Session.begin() as s:
        order = get(s, Order, id)
        service.enqueue(s, order)
        return service.summary(s, order)
