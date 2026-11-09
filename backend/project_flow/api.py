from fastapi import APIRouter
from pydantic import Field

from backend.autopilot.service import open_order_draft
from backend.domain.service import get
from backend.orders.schemas import StrictModel
from backend.storage.db import Session
from backend.storage.models import Order

from . import service

router = APIRouter(prefix="/api/project-work", tags=["Project communication"])


class ProjectName(StrictModel):
    name: str = Field(min_length=1, max_length=180)
    expected_version: int = Field(ge=0)


@router.get("")
def projects():
    with Session() as s:
        return service.list_projects(s)


@router.get("/{id}")
def project(id: str):
    with Session() as s:
        return service.project_view(s, get(s, Order, id), detail=True)


@router.patch("/{id}/name")
def rename(id: str, body: ProjectName):
    with Session.begin() as s:
        return service.rename(s, id, body.name, body.expected_version)


@router.get("/{id}/outlook-drafts/{draft_id}/open")
def open_preserved_draft(id: str, draft_id: str):
    return open_order_draft(id, draft_id)
