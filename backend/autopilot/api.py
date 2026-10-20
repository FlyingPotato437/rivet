"""Read-only autopilot view plus the team's autonomy settings."""

from typing import Literal

from fastapi import APIRouter, Query

from backend.orders.schemas import StrictModel
from backend.storage.db import Session

from . import service

router = APIRouter(prefix="/api/autopilot", tags=["Autopilot"])


class AutopilotSettingsWrite(StrictModel):
    draft_in_outlook: bool | None = None
    tag_in_outlook: bool | None = None
    learn_from_sent: Literal["auto", "review"] | None = None


@router.get("/activity")
def activity(days: int = Query(default=7, ge=1, le=30)):
    with Session() as s:
        return service.activity(s, days)


@router.get("/settings")
def get_settings():
    with Session() as s:
        return service.settings(s)


@router.put("/settings")
def put_settings(body: AutopilotSettingsWrite):
    with Session.begin() as s:
        return service.update_settings(s, body)


@router.get("/outlook-draft")
def outlook_draft(internet_message_id: str = Query(min_length=3, max_length=1024)):
    with Session() as s:
        return {"draft": service.draft_for_message(s, internet_message_id)}


@router.get("/outlook-draft/open")
def open_outlook_draft(internet_message_id: str = Query(min_length=3, max_length=1024)):
    return service.open_draft(internet_message_id)
