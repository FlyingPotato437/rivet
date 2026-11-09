"""Authenticated revision files, background preparation and final decision."""

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field

from backend.company_context.service import require_writer
from backend.domain.service import fail, get, idempotent
from backend.orders import service as orders
from backend.orders.schemas import OrderVersion
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Order, uid

from . import redesign_service as service

router = APIRouter(prefix="/api/orders", tags=["Drawing redesign"])


class Decision(OrderVersion):
    decision: Literal["approve", "request_changes"]
    note: str = Field(default="", max_length=3000)


class ChangeReview(OrderVersion):
    change_id: str = Field(min_length=1, max_length=100)
    decision: Literal["accepted", "needs_changes"]
    note: str = Field(default="", max_length=3000)


class Recheck(OrderVersion):
    document_id: str = Field(min_length=1, max_length=100)


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


@router.post("/{id}/engineering/redesign/{run_id}/review")
def review(id: str, run_id: str, body: ChangeReview, idempotency_key: str = Header()):
    require_writer()
    with Session.begin() as s:
        order = orders.lock_order(s, id)

        def act():
            orders.check_version(order, body.expected_version)
            service.review_change(
                s, order, run_id, body.change_id, body.decision, body.note.strip()
            )
            return service.view(s, order)

        return idempotent(
            s,
            f"redesign:{id}:{run_id}:review:{idempotency_key}",
            body.model_dump(),
            act,
        )


@router.post("/{id}/engineering/redesign/{run_id}/recheck")
def recheck(id: str, run_id: str, body: Recheck, idempotency_key: str = Header()):
    require_writer()
    with Session.begin() as s:
        order = orders.lock_order(s, id)

        def act():
            orders.check_version(order, body.expected_version)
            prior, native = service.validate_recheck(s, order, run_id)
            service.queue_recheck(s, order, prior, native, body.document_id)
            return service.view(s, order)

        return idempotent(
            s,
            f"redesign:{id}:{run_id}:recheck:{idempotency_key}",
            body.model_dump(),
            act,
        )


@router.post("/{id}/engineering/redesign/{run_id}/recheck/upload")
def upload_revision(
    id: str,
    run_id: str,
    file: Annotated[UploadFile, File()],
    revision: Annotated[str, Form(min_length=1, max_length=40)],
    expected_version: Annotated[int, Form(ge=1)],
    idempotency_key: str = Header(),
):
    require_writer()
    name = Path((file.filename or "").replace("\\", "/")).name
    revision = revision.strip()
    if Path(name).suffix.lower() != ".dxf" or len(name) > 240 or not revision:
        fail("Choose a DXF and enter its drawing revision.", 422)
    if file.size is not None and file.size > service.REVISION_MAX_BYTES:
        fail("Keep the revised DXF within 200 MB.", 413)
    blob = db.BLOBS / (uid() + ".dxf")
    registered = False
    try:
        with Session.begin() as s:
            order = orders.lock_order(s, id)
            checksum = service.stage_revision(file.file, blob)

            def act():
                nonlocal registered
                orders.check_version(order, expected_version)
                prior, native = service.validate_recheck(s, order, run_id)
                if checksum == native["sha256"]:
                    fail(
                        "The revised drawing matches the prior original byte for byte.",
                        409,
                    )
                document = service.register_revision(
                    s, order, blob, name, checksum, revision
                )
                service.queue_recheck(s, order, prior, native, document.id)
                registered = True
                return service.view(s, order)

            result = idempotent(
                s,
                f"redesign:{id}:{run_id}:upload:{idempotency_key}",
                {
                    "expected_version": expected_version,
                    "name": name,
                    "revision": revision,
                    "sha256": checksum,
                },
                act,
            )
    except Exception:
        blob.unlink(missing_ok=True)
        raise
    if not registered:
        blob.unlink(missing_ok=True)
    return result


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
