"""Minimal, public pilot intake. Customer workspaces never expose these records."""

import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from backend.storage.db import Session
from backend.storage.models import PilotRequest

router = APIRouter(prefix="/api")


class PilotInquiry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    company: str = Field(min_length=2, max_length=180)
    workflow: str = Field(default="", max_length=1200)
    consent: bool
    website: str = Field(default="", max_length=200)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise ValueError("Enter a valid email address.")
        return value.lower()


@router.post("/pilot-requests", status_code=202)
def request_pilot(body: PilotInquiry):
    if not body.consent:
        raise HTTPException(422, "Please agree to be contacted about the pilot.")
    if body.website:
        return {"accepted": True}
    with Session.begin() as session:
        # Serialize the small public intake to enforce its global hourly cap.
        session.execute(text("SELECT pg_advisory_xact_lock(824763190)"))
        since = datetime.now(UTC) - timedelta(hours=1)
        recent = session.scalar(
            select(func.count())
            .select_from(PilotRequest)
            .where(PilotRequest.created_at >= since)
        )
        if recent >= 100:
            raise HTTPException(
                429, "We are receiving many requests. Please try again later."
            )
        session.execute(
            insert(PilotRequest)
            .values(
                name=body.name,
                email=body.email,
                company=body.company,
                workflow=body.workflow,
                consent=True,
            )
            .on_conflict_do_nothing(index_elements=["email"])
        )
    # Same response for a repeated email; never reveal an existing inquiry.
    return {"accepted": True}
