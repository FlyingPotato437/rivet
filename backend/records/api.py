from difflib import SequenceMatcher
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Form, Header, UploadFile
from fastapi.responses import FileResponse, Response

from backend.domain.service import fail, get, idempotent, scoped
from backend.orders.service import lock_order
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order, Project, RecordShare, Span

from . import service as svc
from .schemas import (
    ApprovalWrite,
    Ask,
    ChangeWrite,
    CommentWrite,
    ShareWrite,
    SubscriberWrite,
    Write,
)

router = APIRouter(prefix="/api", tags=["Comment records"])


@router.get("/records")
def feed():
    with Session.begin() as s:
        output = []
        for candidate in s.scalars(
            scoped(Order).order_by(Order.updated_at.desc())
        ).all():
            order = lock_order(s, candidate.id)
            w = svc.view(s, order)
            comments = w["comments"]
            output.append(
                {
                    **w["order"],
                    "version": w["version"],
                    "updated_at": order.updated_at.isoformat(),
                    "counts": {
                        "comments": len(comments),
                        "open": sum(c["status"] == "open" for c in comments),
                        "responded": sum(c["status"] == "responded" for c in comments),
                        "closed": sum(c["status"] == "closed" for c in comments),
                        "review": sum(not c["reviewed"] for c in comments),
                        "notices": len(w["notices"]),
                    },
                    "revision": next(
                        (
                            d["revision_label"]
                            for d in reversed(w["documents"])
                            if d["revision_label"]
                        ),
                        "Unspecified",
                    ),
                    "approval": (
                        {k: v for k, v in w["approvals"][-1].items() if k != "snapshot"}
                        if w["approvals"]
                        else None
                    ),
                }
            )
        return {"orders": output}


@router.get("/orders/{id}/record")
def record(id: str):
    with Session.begin() as s:
        return svc.view(s, lock_order(s, id))


def mutate(id, body, key, kind, item=""):
    with Session.begin() as s:
        order = lock_order(s, id)
        record = svc.ensure(s, order)
        return idempotent(
            s,
            f"record:{id}:{kind}:{item}:{key}",
            body.model_dump(),
            lambda: svc.change_record(s, order, record, body, kind, item),
        )


@router.post("/orders/{id}/record/comments")
def create_comment(id: str, body: CommentWrite, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "comment")


@router.post("/orders/{id}/record/comments/{item}")
def edit_comment(
    id: str, item: str, body: CommentWrite, idempotency_key: str = Header()
):
    return mutate(id, body, idempotency_key, "comment", item)


@router.post("/orders/{id}/record/changes")
def create_change(id: str, body: ChangeWrite, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "change")


@router.post("/orders/{id}/record/changes/{item}")
def edit_change(id: str, item: str, body: ChangeWrite, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "change", item)


@router.post("/orders/{id}/record/subscribers")
def subscribe(id: str, body: SubscriberWrite, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "subscriber")


@router.post("/orders/{id}/record/subscribers/{item}/remove")
def unsubscribe(id: str, item: str, body: Write, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "unsubscribe", item)


@router.post("/orders/{id}/record/approvals")
def approve(id: str, body: ApprovalWrite, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "approval")


@router.post("/orders/{id}/record/shares")
def share(id: str, body: ShareWrite, idempotency_key: str = Header()):
    with Session.begin() as s:
        order = lock_order(s, id)
        return idempotent(
            s,
            f"record-share:{id}:{idempotency_key}",
            body.model_dump(),
            lambda: svc.share(s, order, svc.ensure(s, order), body),
        )


@router.post("/orders/{id}/record/shares/{item}/revoke")
def revoke(id: str, item: str, body: Write, idempotency_key: str = Header()):
    with Session.begin() as s:
        order = lock_order(s, id)
        record = svc.ensure(s, order)

        def act():
            if record.version != body.expected_version:
                fail("Refresh the record before revoking.", 409)
            row = get(s, RecordShare, item)
            if row.order_id != id:
                fail("Link belongs to another order.", 403)
            row.revoked = True
            return svc.view(s, order, record)

        return idempotent(
            s, f"record-revoke:{id}:{item}:{idempotency_key}", body.model_dump(), act
        )


@router.get("/shared/{token}")
def shared(token: str):
    with Session() as s:
        return svc.shared(s, token).snapshot


@router.get("/shared/{token}/documents/{document_id}")
def shared_document(token: str, document_id: str):
    with Session() as s:
        row = svc.shared(s, token)
        if document_id not in {d["id"] for d in row.snapshot["documents"]}:
            fail("Source is not part of this shared record.", 404)
        from backend.identity import Identity, identity_scope

        with identity_scope(Identity(row.organization_id, "Shared record viewer")):
            d = get(s, Document, document_id)
        return FileResponse(
            db.BLOBS / d.blob, filename=d.name, content_disposition_type="inline"
        )


@router.get("/orders/{id}/record/compare")
def compare(id: str, before: str, after: str):
    with Session() as s:
        order = get(s, Order, id)
        docs = [get(s, Document, value) for value in (before, after)]
        if any(d.project_id != order.project_id for d in docs):
            fail("Both revisions must belong to this order.", 422)
        if before == after:
            fail("Select two different documents.")
        if any(d.state in {"queued", "failed", "processing"} for d in docs):
            fail("Both documents must finish processing.")
        groups = [
            list(
                s.scalars(
                    scoped(Span)
                    .where(Span.document_id == d.id)
                    .order_by(Span.created_at)
                )
            )
            for d in docs
        ]
        changes = []
        matcher = SequenceMatcher(
            a=[x.text for x in groups[0]], b=[x.text for x in groups[1]], autojunk=False
        )
        for op, i, j, k, l in matcher.get_opcodes():
            if op != "equal":
                changes.append(
                    {
                        "kind": op,
                        "before": "\n".join(x.text for x in groups[0][i:j]),
                        "after": "\n".join(x.text for x in groups[1][k:l]),
                        "before_source_ids": [x.id for x in groups[0][i:j]],
                        "after_source_ids": [x.id for x in groups[1][k:l]],
                    }
                )
        return {
            "changes": changes,
            "note": "Extracted text comparison. Drawing geometry and scanned content require manual review.",
        }


@router.get("/orders/{id}/record/export")
def export(id: str, format: Literal["xlsx", "pdf"] = "xlsx"):
    from .exports import pdf, workbook

    with Session.begin() as s:
        w = svc.view(s, lock_order(s, id))
    content = workbook(w) if format == "xlsx" else pdf(w)
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if format == "xlsx"
        else "application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="rivet-comment-log.{format}"'
        },
    )


@router.get("/orders/{id}/record/notices/{item}/draft")
def notice(id: str, item: str):
    with Session.begin() as s:
        w = svc.view(s, lock_order(s, id))
        draft = next((x for x in w["notices"] if x["id"] == item), None)
        if not draft:
            fail("Notice not found.", 404)
        msg = EmailMessage()
        msg["Subject"] = (
            w["order"]["number"]
            + " — "
            + draft["title"].replace("\n", " ").replace("\r", " ")
        )
        msg["To"] = ", ".join(x["email"] for x in draft["recipients"])
        msg["X-Unsent"] = "1"
        msg.set_content(
            draft["body"]
            + "\n\nAttach the reviewed comment log before sending.\nPrepared by Rivet; not sent."
        )
        return Response(
            msg.as_bytes(),
            media_type="message/rfc822",
            headers={
                "Content-Disposition": 'attachment; filename="rivet-change-notice.eml"'
            },
        )


@router.post("/orders/{id}/record/email", status_code=202)
async def email_import(
    id: str,
    file: UploadFile,
    revision_label: str = Form("", max_length=40),
    idempotency_key: str = Header(),
):
    from .email_intake import import_email

    data = await file.read(20 * 1024 * 1024 + 1)
    with Session.begin() as s:
        order = lock_order(s, id)
        return idempotent(
            s,
            f"email-import:{id}:{idempotency_key}",
            {"sha": sha256(data).hexdigest(), "revision": revision_label},
            lambda: import_email(s, order, file.filename or "", data, revision_label),
        )


@router.post("/orders/{id}/record/ask")
def ask(id: str, body: Ask):
    from .assistant import ask as answer

    with Session.begin() as s:
        w = svc.view(s, lock_order(s, id))
    # No database lock or mutation capability is held during the provider call.
    return answer(body.question, w, [m.model_dump() for m in body.history])
