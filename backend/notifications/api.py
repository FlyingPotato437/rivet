import os
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.domain.service import get, scoped
from backend.identity import current_org, identity_scope
from backend.mailbox.notifications import GraphSubscription
from backend.orders.schemas import StrictModel
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Order, now

from . import auth, cards
from .delivery import InternalNotification
from .delivery import configuration as channel_config
from .models import ApprovalCard

router = APIRouter(prefix="/api", tags=["Approval cards"])


@router.get("/automation-readiness")
def readiness():
    with Session() as s:
        subscriptions = list(s.scalars(scoped(GraphSubscription)))
        recent = list(
            s.scalars(
                scoped(InternalNotification)
                .order_by(InternalNotification.created_at.desc())
                .limit(10)
            )
        )
        channels = channel_config().get(current_org(), {})
        return {
            "features": [
                {
                    "name": "Order agent",
                    "status": "available",
                    "detail": "40 steps, small validated batches and durable per-comment preparation.",
                },
                {
                    "name": "Scanned drawing transcription",
                    "status": "available"
                    if os.getenv("OPENAI_API_KEY")
                    else "setup_required",
                    "detail": "Located AI transcription, with unreadable areas retained. Model confidence is not engineering approval.",
                },
                {
                    "name": "Instant Microsoft events",
                    "status": "connected"
                    if any(r.expires_at > now() for r in subscriptions)
                    else "setup_required",
                    "detail": "Requires a public HTTPS callback and registered subscription. Delta polling remains the recovery path.",
                },
                {
                    "name": "Teams decisions and morning brief",
                    "status": "configured"
                    if channels.get("enabled")
                    else "setup_required",
                    "detail": "Internal Teams workflow delivery with a signed-in decision page. Native bot approvals require bot registration.",
                },
                {
                    "name": "Outlook actionable cards",
                    "status": "configured"
                    if cards.actionable_enabled()
                    else "setup_required",
                    "detail": "Requires Microsoft originator registration, Entra audience and named approver mappings.",
                },
                {
                    "name": "Waiting timers",
                    "status": "drafts_enabled"
                    if os.getenv("RIVET_FOLLOWUP_DRAFTS_ENABLED") == "true"
                    else "timers_only",
                    "detail": "Three business days after a confirmed send. Follow-up drafts are optional; Rivet does not send them.",
                },
                {
                    "name": "Enterprise publication",
                    "status": "configured"
                    if os.getenv("RIVET_WRITE_TARGETS_CONFIG")
                    else "setup_required",
                    "detail": "Approved artifacts only. SharePoint needs selected-site write access; SAP and vault need an installed manufacturer bridge.",
                },
            ],
            "recent_internal_notices": [
                {"status": r.status, "at": r.created_at.isoformat()} for r in recent
            ],
        }


class CardRequest(StrictModel):
    package_id: str
    gate: Literal["money", "scope", "engineering", "customer_commitment"]
    channel: Literal["outlook", "teams"] = "outlook"


class Decision(StrictModel):
    decision: Literal["approve", "reject"]


@router.get("/approval-cards/{id}")
def read(id: str):
    with Session() as s:
        row = get(s, ApprovalCard, id)
        order = get(s, Order, row.order_id)
        package = next(
            p for p in order.state["lifecycle"]["packages"] if p["id"] == row.package_id
        )
        return {
            **cards.render(s, row),
            "order_id": order.id,
            "number": order.number,
            "gate": row.gate,
            "title": package["title"],
            "source_ids": row.source_ids,
            "result": row.result,
        }


@router.post("/orders/{id}/approval-cards")
def create(id: str, body: CardRequest):
    with Session.begin() as s:
        order = lock_order(s, id)
        return cards.render(
            s, cards.create(s, order, body.package_id, body.gate), body.channel
        )


@router.post("/approval-cards/{id}/decision")
def decide(id: str, body: Decision):
    with Session.begin() as s:
        return cards.decide(s, id, body.decision)


@router.post("/actions/outlook/{id}")
def outlook(id: str, body: Decision, request: Request):
    with identity_scope(auth.verify(request)), Session.begin() as s:
        result = cards.decide(s, id, body.decision)
        return JSONResponse(
            result,
            headers={
                "CARD-ACTION-STATUS": "Decision recorded against the reviewed evidence."
            },
        )
