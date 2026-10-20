from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import Field

from backend.domain.service import fail, get, idempotent
from backend.lifecycle.schemas import DecisionWrite
from backend.orders.schemas import StrictModel
from backend.storage.db import Session
from backend.storage.models import Order

from . import service
from .models import ReplyDraft
from .schemas import ComposeReply, GenerateReply, ReviewReply

router = APIRouter(prefix="/api", tags=["Outlook reply assistant"])


class DocumentOutlookDraftWrite(StrictModel):
    reply_id: str = Field(min_length=36, max_length=36)
    connection_id: str | None = Field(default=None, min_length=36, max_length=36)


@router.get("/orders/{id}/document-outlook-draft")
def document_outlook_draft(id: str):
    from .document_outlook import metadata

    with Session() as s:
        return metadata(s, id)


@router.post("/orders/{id}/document-outlook-draft")
def save_document_outlook_draft(id: str, body: DocumentOutlookDraftWrite):
    from .document_outlook import save

    return save(id, body.reply_id, body.connection_id)


class LifecycleEmailDecision(DecisionWrite):
    internet_message_id: str = Field(min_length=1, max_length=1024)
    receipt_id: str = Field(min_length=36, max_length=36)
    package_fingerprint: str = Field(min_length=64, max_length=64)


@router.post("/reply-assistant/lifecycle/decisions/{package_id}")
def lifecycle_decision(
    package_id: str,
    body: LifecycleEmailDecision,
    idempotency_key: str = Header(min_length=1, max_length=180),
):
    from backend.lifecycle import service as lifecycle
    from backend.lifecycle.mail_context import selected_message
    from backend.mailbox.models import MailboxReceipt
    from backend.orders.service import check_version, lock_order
    from backend.storage.models import RecordIntake

    with Session.begin() as s:
        receipt = get(s, MailboxReceipt, body.receipt_id)
        intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
        if (
            receipt.internet_message_id != body.internet_message_id
            or not intake
            or not intake.order_id
        ):
            fail(
                "The selected email changed. Open the current customer message again.",
                409,
            )
        order = lock_order(s, intake.order_id)

        def decide():
            check_version(order, body.expected_version)
            context = selected_message(s, body.internet_message_id)
            package = next(
                (p for p in context["packages"] if p["id"] == package_id), None
            )
            if (
                context["receipt_id"] != receipt.id
                or context["order_id"] != order.id
                or not package
                or package["fingerprint"] != body.package_fingerprint
                or not set(body.source_ids) <= set(package["sources"])
            ):
                fail(
                    "This work or its evidence no longer belongs to the selected email. Refresh before deciding.",
                    409,
                )
            if body.gate not in package["allowed_gates"]:
                fail(
                    "This decision is unavailable for your role or the current source evidence.",
                    403,
                )
            decision = DecisionWrite.model_validate(
                body.model_dump(
                    exclude={"internet_message_id", "receipt_id", "package_fingerprint"}
                )
            )
            return lifecycle.decide(s, order, package_id, decision)

        return idempotent(
            s,
            f"{order.id}:outlook-lifecycle:{idempotency_key}",
            {"package_id": package_id, **body.model_dump(mode="json")},
            decide,
        )


@router.get("/reply-assistant/context")
def context(internet_message_id: str = Query(default="", max_length=1024)):
    with Session() as s:
        return service.outlook_context(s, internet_message_id)


@router.get("/reply-assistant/lifecycle")
def lifecycle_context(internet_message_id: str = Query(min_length=1, max_length=1024)):
    from backend.lifecycle.mail_context import selected_message

    with Session() as s:
        return JSONResponse(
            selected_message(s, internet_message_id),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )


@router.post("/reply-drafts", status_code=202)
def generate(body: GenerateReply, idempotency_key: str = Header()):
    return service.generate(body, idempotency_key)


@router.get("/reply-drafts/{id}")
def draft(id: str):
    with Session() as s:
        return service.current_view(s, get(s, ReplyDraft, id))


@router.post("/reply-drafts/{id}/review")
def review(id: str, body: ReviewReply):
    with Session.begin() as s:
        return service.review(s, id, body)


@router.post("/reply-drafts/{id}/compose")
def compose(id: str, body: ComposeReply):
    with Session.begin() as s:
        return JSONResponse(
            service.compose(s, id, body),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )


@router.get("/orders/{id}/reply-preparation")
def order_preparation(id: str):
    with Session() as s:
        return service.order_preparation(s, get(s, Order, id))
