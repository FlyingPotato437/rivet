from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import Field

from backend.domain.service import fail, get, idempotent
from backend.ingestion.limits import upload_max_bytes, DWG_REQUEST
from backend.orders import service as orders
from backend.orders.schemas import StrictModel
from backend.storage.db import Session
from backend.storage.models import Document, Order, Project

from . import service

router = APIRouter(prefix="/api/orders", tags=["Equipment revision checks"])


class CheckRevision(StrictModel):
    expected_version: int = Field(ge=1)
    baseline_revision: str = Field(min_length=1, max_length=40)
    target_revision: str = Field(min_length=1, max_length=40)


class PrepareCorrections(StrictModel):
    expected_version: int = Field(ge=1)
    expected_plan_id: str = Field(min_length=1, max_length=180)


class CorrectionOutlook(StrictModel):
    expected_version: int = Field(ge=1)
    connection_id: str | None = Field(default=None, max_length=80)


@router.get("/{id}/equipment")
def equipment(id: str):
    with Session() as s:
        return service.view(s, get(s, Order, id))


@router.get("/{id}/equipment/model")
def equipment_model(id: str):
    from .model_service import current

    with Session() as s:
        return current(s, get(s, Order, id))


@router.get("/{id}/equipment/corrections")
def corrections(id: str):
    from . import workflow

    with Session() as s:
        return workflow.view(s, get(s, Order, id))


@router.post("/{id}/equipment/corrections/prepare")
def prepare_corrections(
    id: str, body: PrepareCorrections, idempotency_key: str = Header()
):
    from . import workflow

    with Session.begin() as s:
        order = orders.lock_order(s, id)

        def act():
            orders.check_version(order, body.expected_version)
            workflow.prepare(s, order, body.expected_plan_id)
            return workflow.view(s, order)

        return idempotent(
            s, f"equipment:{id}:prepare:{idempotency_key}", body.model_dump(), act
        )


@router.post("/{id}/equipment/corrections/{bundle_id}/outlook")
def correction_outlook(
    id: str, bundle_id: str, body: CorrectionOutlook, idempotency_key: str = Header()
):
    from . import outlook, workflow

    # Native draft creation owns its short reserve/checkpoint transactions so
    # an uncertain Graph response cannot be erased by a request rollback.
    with Session() as s:
        order = get(s, Order, id)
        orders.check_version(order, body.expected_version)
        workflow.outlook_bundle(s, order, bundle_id)
    outlook.save(id, bundle_id, body.connection_id)
    with Session() as s:
        return workflow.view(s, get(s, Order, id))


@router.get("/{id}/equipment/corrections/{bundle_id}/files/{name}")
def correction_file(id: str, bundle_id: str, name: str):
    from . import workflow

    with Session() as s:
        path, item = workflow.artifact(s, get(s, Order, id), bundle_id, name)
        return FileResponse(
            path,
            media_type=item["media_type"],
            filename=item["name"],
            headers={"Cache-Control": "no-store"},
        )


@router.post("/{id}/equipment/check")
def check(id: str, body: CheckRevision, idempotency_key: str = Header()):
    with Session.begin() as s:
        order = orders.lock_order(s, id)

        def act():
            orders.check_version(order, body.expected_version)
            return service.save_check(
                s, order, body.baseline_revision, body.target_revision
            )

        return idempotent(
            s, f"equipment:{id}:check:{idempotency_key}", body.model_dump(), act
        )


@router.post("/{id}/equipment/packages")
async def upload_package(
    id: str,
    role: Literal["drawing", "bom", "specification", "markup"] = Form(),
    revision: str = Form(min_length=1, max_length=40),
    files: Annotated[list[UploadFile] | None, File()] = None,
    document_ids: str = Form("[]"),
    idempotency_key: str = Header(),
):
    files = files or []
    try:
        ids = json.loads(document_ids)
        if (
            not isinstance(ids, list)
            or len(ids) > 20
            or not all(isinstance(i, str) for i in ids)
        ):
            raise ValueError()
    except (ValueError, TypeError):
        fail("Existing source IDs must be a list of document IDs.")
    if not files and not ids:
        fail("Select a drawing or BOM original.")
    if len(files) + len(ids) > 20:
        fail("Add up to 20 files at a time.")
    incoming = []
    for file in files:
        name = Path(file.filename or "document").name[:240]
        suffix = Path(name).suffix.lower()
        allowed = {
            "drawing": {".pdf", ".dxf"},
            "bom": {".xlsx", ".csv", ".pdf"},
            "specification": {".pdf"},
            "markup": {".pdf"},
        }[role]
        if suffix == ".dwg":
            fail(DWG_REQUEST)
        if suffix not in allowed:
            fail(
                f"This {role} file type is unsupported. Choose {', '.join(sorted(allowed))}."
            )
        limit = upload_max_bytes(name)
        data = await file.read(limit + 1)
        if not data or len(data) > limit:
            fail(f"Each equipment file must be nonempty and no larger than {limit // (1024 * 1024)} MB.")
        incoming.append((name, data))
    if sum(len(data) for _, data in incoming) > 400 * 1024 * 1024:
        fail("Add no more than 400 MB of equipment sources in one request.")
    signature = {
        "role": role,
        "revision": revision,
        "document_ids": ids,
        "files": [[name, hashlib.sha256(data).hexdigest()] for name, data in incoming],
    }
    with Session.begin() as s:
        order = orders.lock_order(s, id)

        def act():
            from backend.api.main import add_document

            service.register_existing(s, order, ids, role, revision)
            kind = {"drawing": "approval_drawing", "markup": "markups"}.get(role, role)
            metadata = {
                "order_role": kind,
                "revision_label": revision,
                "equipment_package": {"role": role, "revision": revision},
            }
            for name, data in incoming:
                if Path(name).suffix.lower() == ".dxf":
                    service.add_native_dxf(s, order, name, data, metadata)
                else:
                    add_document(
                        s, get(s, Project, order.project_id), name, kind, data, metadata
                    )
            # Record a check of the raw drawing/BOM bytes immediately. Ordinary
            # ingestion and later mailbox uploads remain independent consumers.
            result = service.view(s, order)
            report = result.get("comparison")
            if report and report["baseline_revision"]:
                return service.save_check(
                    s, order, report["baseline_revision"], report["target_revision"]
                )
            orders.commit(
                s,
                order,
                f"Added equipment {role} originals for Rev {revision}",
                "equipment_sources",
            )
            result["version"] = order.version
            return result

        return idempotent(s, f"equipment:{id}:upload:{idempotency_key}", signature, act)


def source(s, id, document_id):
    order = get(s, Order, id)
    document = get(s, Document, document_id)
    if document.project_id != order.project_id:
        fail("This source belongs to another order.", 403)
    path = service.original_path(document)
    if hashlib.sha256(path.read_bytes()).hexdigest() != document.sha256:
        fail("The original file changed. This evidence is no longer current.", 409)
    return order, document, path


@router.get("/{id}/equipment/files/{document_id}")
def original(id: str, document_id: str):
    with Session() as s:
        _, document, path = source(s, id, document_id)
        return FileResponse(
            path,
            media_type=mimetypes.guess_type(document.name)[0]
            or "application/octet-stream",
            filename=document.name,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


@router.get("/{id}/equipment/evidence")
def evidence_image(id: str, document_id: str, fact_id: str = "", page: int = 1):
    import pymupdf as fitz

    with Session() as s:
        order, document, path = source(s, id, document_id)
        if path.suffix.lower() != ".pdf":
            fail("A page image is only available for PDF originals.")
        bbox = None
        if fact_id:
            info = service.document_info(document, order)
            from .comparison import analyze_package

            analysis = analyze_package([service.source_input(document, info)])
            fact = next(
                (
                    f
                    for f in [
                        *analysis.get("facts", []),
                        *analysis.get("requested_changes", []),
                    ]
                    if f["id"] == fact_id
                ),
                None,
            )
            if not fact:
                fail(
                    "This equipment observation is no longer present in the original.",
                    409,
                )
            location = fact.get("evidence", {})
            page = location.get("page", page)
            bbox = location.get("bbox")
        try:
            with fitz.open(path) as pdf:
                if page < 1 or page > len(pdf):
                    fail("The source page is unavailable.", 404)
                original_page = pdf[page - 1]
                if bbox and len(bbox) == 4 and all(0 <= float(v) <= 1 for v in bbox):
                    rect = original_page.rect
                    region = fitz.Rect(
                        float(bbox[0]) * rect.width,
                        float(bbox[1]) * rect.height,
                        float(bbox[2]) * rect.width,
                        float(bbox[3]) * rect.height,
                    )
                    original_page.draw_rect(
                        region * original_page.derotation_matrix,
                        color=(0.64, 0.52, 0.86),
                        fill=(0.64, 0.52, 0.86),
                        fill_opacity=0.12,
                        width=1.5,
                        overlay=True,
                    )
                scale = min(
                    1.6, 1800 / max(original_page.rect.width, original_page.rect.height)
                )
                image = original_page.get_pixmap(
                    matrix=fitz.Matrix(scale, scale), alpha=False
                ).tobytes("png")
        except (RuntimeError, ValueError):
            fail("This PDF page could not be rendered.", 422)
        return Response(
            image,
            media_type="image/png",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
