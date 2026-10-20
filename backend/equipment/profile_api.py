"""Explicit company review and order-level profile assignment."""

from fastapi import APIRouter, Header
from pydantic import Field

from backend.domain.service import get, idempotent
from backend.orders.schemas import StrictModel
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Order

from . import profile_service as svc

router = APIRouter(prefix="/api", tags=["Company equipment conventions"])


class ProfileProposal(StrictModel):
    manifest: dict
    parent_id: str | None = Field(default=None, max_length=80)


class ProfileReview(StrictModel):
    expected_version_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ProfileRevocation(StrictModel):
    reason: str = Field(min_length=8, max_length=1000)


class ProfileBinding(ProfileRevocation):
    expected_version: int = Field(ge=1)
    profile_version_id: str | None = Field(default=None, max_length=80)


@router.get("/equipment/profiles")
def profiles():
    with Session() as s:
        return svc.overview(s)


@router.post("/equipment/profiles")
def propose(body: ProfileProposal, idempotency_key: str = Header()):
    svc.require_admin()
    with Session.begin() as s:
        return idempotent(
            s,
            "equipment:profile:propose:" + idempotency_key,
            body.model_dump(),
            lambda: svc.propose(s, body.manifest, body.parent_id),
        )


@router.post("/equipment/profiles/{id}/review")
def review(id: str, body: ProfileReview, idempotency_key: str = Header()):
    svc.require_admin()
    with Session.begin() as s:
        return idempotent(
            s,
            f"equipment:profile:{id}:review:{idempotency_key}",
            body.model_dump(),
            lambda: svc.review(s, id, body.expected_version_hash),
        )


@router.post("/equipment/profiles/{id}/revoke")
def revoke(id: str, body: ProfileRevocation, idempotency_key: str = Header()):
    svc.require_admin()
    with Session.begin() as s:
        return idempotent(
            s,
            f"equipment:profile:{id}:revoke:{idempotency_key}",
            body.model_dump(),
            lambda: svc.revoke(s, id, body.reason),
        )


@router.get("/orders/{id}/equipment/profile")
def order_profile(id: str):
    with Session() as s:
        return svc.binding_view(s, get(s, Order, id))


@router.put("/orders/{id}/equipment/profile")
def bind_profile(id: str, body: ProfileBinding, idempotency_key: str = Header()):
    svc.require_admin()
    with Session.begin() as s:
        order = lock_order(s, id)
        return idempotent(
            s,
            f"equipment:profile:{id}:bind:{idempotency_key}",
            body.model_dump(),
            lambda: svc.bind(
                s, order, body.expected_version, body.profile_version_id, body.reason
            ),
        )
