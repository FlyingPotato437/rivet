"""Private intake queue with conservative, reproducible order matching.

An explicit unique job number can open an order. Existing orders and threads
route without inventing a revision. Conflicting identifiers and drawing-only
hints are presented for confirmation.
"""

import re
from email import policy
from email.parser import BytesParser
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Form, Header, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import Field
from sqlalchemy import text

from backend.domain.service import fail, get, idempotent, scoped
from backend.identity import current_actor, current_org
from backend.ingestion.limits import DWG_REQUEST, upload_max_bytes, validate_pdf_upload
from backend.orders.schemas import StrictModel
from backend.orders.service import lock_order, new_order
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import (
    Document,
    InboundEmail,
    Order,
    Project,
    RecordIntake,
    now,
)

router = APIRouter(prefix="/api/records/intake", tags=["Incoming documents"])
MAX_BYTES = 20 * 1024 * 1024
INSPECTION_VERSION = 4


class ConfirmIntake(StrictModel):
    order_id: str = Field(default="", max_length=100)
    job_number: str = Field(default="", max_length=80)
    revision_label: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=180)
    customer: str = Field(default="", max_length=180)


def unique(values):
    return list(
        dict.fromkeys(value.strip().upper() for value in values if value.strip())
    )


def identifiers(value):
    token = r"([A-Z0-9]+(?:[-./][A-Z0-9]+)*)"
    jobs = unique(
        re.findall(
            r"\b(?:job|project)\s*(?:number|no\.?|#)?\s*[:#-]?\s*" + token,
            value,
            re.IGNORECASE,
        )
    )
    pos = unique(
        match[1]
        for match in re.finditer(
            r"\b(?:PO|P\.O\.|purchase order)\s*(?:number|no\.?|#)?\s*[:#-]?\s*" + token,
            value,
            re.IGNORECASE,
        )
        if not (
            re.fullmatch(r"REV(?:ISION)?[-./]\d+", match[1], re.IGNORECASE)
            and not re.search(
                r"[:#]|\b(?:number|no)\b",
                match[0][: match.start(1) - match.start()],
                re.IGNORECASE,
            )
        )
    )
    drawings = unique(
        re.findall(
            r"\b(?:drawing|dwg\.?|sheet)\s*(?:number|no\.?|#)?\s*[:#-]?\s*" + token,
            value,
            re.IGNORECASE,
        )
    )
    candidates = re.findall(
        r"\b(?:rev|revision)\b\.?\s*(?:(?:number\b|no\b\.?)\s*)?[:#-]?\s*([A-Z0-9]+(?:[._-][A-Z0-9]+)*)\b",
        value,
        re.IGNORECASE,
    )
    prose = {
        "THE",
        "AND",
        "FOR",
        "TO",
        "OF",
        "IS",
        "IN",
        "ON",
        "BY",
        "OR",
        "AS",
        "AT",
        "NO",
        "NOT",
        "HAS",
        "HAD",
        "WAS",
        "ARE",
        "CAN",
        "MAY",
        "BUT",
    }
    revisions = unique(
        code
        for code in candidates
        if len(code) <= 10
        and (
            any(character.isdigit() for character in code)
            or (code.isalpha() and len(code) == 1)
            or (
                code.isalpha()
                and code.isupper()
                and len(code) <= 3
                and code not in prose
            )
        )
    )
    # A normal sentence such as 'project requirements' is not an identifier.
    numeric = lambda items: [item for item in items if any(c.isdigit() for c in item)]
    return {
        "job_numbers": numeric(jobs),
        "po_numbers": numeric(pos),
        "drawing_numbers": numeric(drawings),
        "revisions": revisions,
    }


def inspect_file(name, data):
    suffix = Path(name).suffix.lower()
    if suffix == ".dwg":
        fail(DWG_REQUEST)
    if suffix not in {".pdf", ".eml", ".txt"}:
        fail("Drop a returned PDF, an EML email, or a text comment log.")
    limit = upload_max_bytes(name)
    if not data or len(data) > limit:
        fail(f"Upload a nonempty file no larger than {limit // (1024 * 1024)} MB.")
    stem = Path(name).stem.replace("_", " ")
    # Common file-description suffixes are not part of a labeled job number.
    stem = re.sub(
        r"[- ](?:return(?:ed)?|markups?|review|annotated|comments|final|package)\b.*$",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    parts = [stem]
    revision_evidence = []
    filename_revisions = identifiers(stem)["revisions"]
    if filename_revisions:
        revision_evidence.append(
            {"source": "filename", "revisions": filename_revisions}
        )

    def header_revisions(value):
        # A drawing's own job/sheet header is distinct from a note mentioning a
        # specification or previous revision. Keep conflicting headers visible.
        result = []
        for line in value.splitlines():
            found = identifiers(line)
            if found["job_numbers"] or found["drawing_numbers"]:
                result.extend(found["revisions"])
        return unique(result)

    email = {}
    if suffix == ".pdf":
        if not data.startswith(b"%PDF"):
            fail("This file is not a valid PDF.")
        import pdfplumber

        try:
            with pdfplumber.open(BytesIO(data)) as pdf:
                validate_pdf_upload(data)
                excerpts = [
                    (page.extract_text() or "")[:10000] for page in pdf.pages[:3]
                ]
                parts.extend(excerpts)
                primary = header_revisions("\n".join(excerpts))
                if primary:
                    revision_evidence.append(
                        {"source": "drawing_header", "revisions": primary}
                    )
        except Exception as exc:
            from fastapi import HTTPException

            if isinstance(exc, HTTPException):
                raise
            fail("This PDF cannot be opened. Export an unlocked PDF and try again.")
    elif suffix == ".eml":
        message = BytesParser(policy=policy.default).parsebytes(data)
        email = {
            key: str(message.get(header, ""))
            for key, header in [
                ("message_id", "Message-ID"),
                ("in_reply_to", "In-Reply-To"),
                ("references", "References"),
                ("subject", "Subject"),
                ("from", "From"),
                ("date", "Date"),
            ]
        }
        parts.append(email["subject"])
        subject_revisions = identifiers(email["subject"])["revisions"]
        if subject_revisions:
            revision_evidence.append(
                {"source": "email_subject", "revisions": subject_revisions}
            )
        from .intake_preview import email_preview

        email_body = email_preview(data)["body"]
        parts.append(email_body)
        primary = header_revisions(email_body)
        if primary:
            revision_evidence.append({"source": "email_job_line", "revisions": primary})
        parts.extend(
            Path(part.get_filename() or "").stem.replace("_", " ")
            for part in message.iter_attachments()
        )
        # Customers often write only 'see attached'. Read a bounded PDF header
        # sample for explicit identifiers; never infer an order from a filename
        # number alone or pretend a scanned page was read. Conflicting labels
        # across the message and these files remain separate detections.
        from fastapi import HTTPException

        pdfs = [
            p
            for p in message.iter_attachments()
            if Path(p.get_filename() or "").suffix.lower() == ".pdf"
        ]
        for attachment in pdfs[:3]:
            try:
                attached, _ = inspect_file(
                    attachment.get_filename(),
                    attachment.get_payload(decode=True) or b"",
                )
            except HTTPException:
                continue
            revision_evidence.extend(
                {
                    **evidence,
                    "source": "attachment:" + evidence["source"],
                }
                for evidence in attached.get("revision_evidence", [])
            )
            for field, label in (
                ("job_numbers", "Job"),
                ("po_numbers", "PO"),
                ("drawing_numbers", "Drawing"),
                ("revisions", "Revision"),
            ):
                values = attached[field]
                if field == "revisions":
                    values = [*values, *attached.get("referenced_revisions", [])]
                parts.extend(f"{label} {value}" for value in values)
    else:
        parts.append(data.decode("utf-8", errors="replace")[:40000])
    detected = identifiers("\n".join(parts))
    primary = unique(
        revision for evidence in revision_evidence for revision in evidence["revisions"]
    )
    # Require corroboration, not a subject-line override. A matching subject,
    # filename and drawing header can identify the returned revision while the
    # drawing cites a specification with its own revision. Disagreeing primary
    # labels still require confirmation.
    if len(revision_evidence) >= 2 and len(primary) == 1:
        detected["referenced_revisions"] = [
            revision for revision in detected["revisions"] if revision not in primary
        ]
        detected["revisions"] = primary
    detected["revision_evidence"] = revision_evidence
    detected["job_number"] = (
        detected["job_numbers"][0] if len(detected["job_numbers"]) == 1 else ""
    )
    detected["revision"] = (
        detected["revisions"][0] if len(detected["revisions"]) == 1 else ""
    )
    return detected, email


def match_orders(s, detected, email):
    orders = list(s.scalars(scoped(Order)))
    jobs, pos = set(detected["job_numbers"]), set(detected["po_numbers"])
    threads = set(
        re.findall(
            r"<[^>]+>", email.get("in_reply_to", "") + " " + email.get("references", "")
        )
    )
    candidates = []
    for order in orders:
        reasons = []
        known_jobs = {order.number.upper()}
        known_pos = set()
        if order.number.upper() in jobs:
            reasons.append("Exact job number")
        if order.number.upper() in pos:
            reasons.append("Exact order / PO number")
        for doc in s.scalars(
            scoped(Document).where(Document.project_id == order.project_id)
        ):
            known = doc.meta.get("intake_identifiers", {})
            known_jobs.update(known.get("job_numbers", []))
            known_pos.update(known.get("po_numbers", []))
            if jobs.intersection(known.get("job_numbers", [])):
                reasons.append("Previously confirmed job number")
            if pos.intersection(known.get("po_numbers", [])):
                reasons.append("Previously confirmed PO number")
            if doc.meta.get("email", {}).get("message_id") in threads:
                reasons.append("Existing email thread")
            if set(detected["drawing_numbers"]).intersection(
                known.get("drawing_numbers", [])
            ):
                reasons.append("Drawing number requires confirmation")
        if reasons:
            project = get(s, Project, order.project_id)
            candidates.append(
                {
                    "id": order.id,
                    "number": order.number,
                    "title": project.title,
                    "reasons": list(dict.fromkeys(reasons)),
                    "identifier_conflict": bool(jobs - known_jobs)
                    or bool(pos and known_pos and pos - known_pos),
                }
            )
    return candidates


def serialize(row):
    return {
        "intake_id": row.id,
        "filename": row.name,
        "status": row.status,
        "order_id": row.order_id,
        "created_at": row.created_at.isoformat(),
        **row.data,
    }


def attach(s, row, order, revision):
    from backend.api.main import add_document

    from .email_intake import import_email

    payload = (db.BLOBS / row.blob).read_bytes()
    if row.name.lower().endswith(".eml"):
        result = import_email(
            s,
            order,
            row.name,
            payload,
            revision,
            forwarded=bool(row.data.get("forwarded")),
        )
        document = get(s, Document, result["document_id"])
        document.meta = {
            **document.meta,
            "intake_identifiers": row.data["detected"],
            # Make a subsequent thread reply routable before parsing finishes.
            "email": row.data.get("email", {}),
        }
    else:
        result = add_document(
            s,
            get(s, Project, order.project_id),
            row.name,
            "markups",
            payload,
            {"revision_label": revision, "intake_identifiers": row.data["detected"]},
        )
    row.order_id = order.id
    row.data = {
        **row.data,
        "document_id": result.get("id") or result.get("document_id"),
        "revision_label": revision,
    }
    # An email awaiting confirmation becomes imported in the same transaction as
    # the user's confirmed attachment to an order.
    for email in s.scalars(
        scoped(InboundEmail).where(InboundEmail.intake_id == row.id)
    ):
        email.order_id = order.id
        email.document_id = row.data["document_id"]
        email.status = "imported"
        email.error = ""
    from backend.mailbox.models import MailboxReceipt

    for receipt in s.scalars(
        scoped(MailboxReceipt).where(
            MailboxReceipt.intake_id == row.id,
            MailboxReceipt.status == "needs_confirmation",
        )
    ):
        receipt.status = "imported"
        receipt.processed_at, receipt.error = now(), ""
    s.flush()

    # Both automatic matches and a PM's later confirmation use this path.
    from backend.connected_context.service import enqueue

    enqueue(s, order)


def receive(
    s, name, data, revision_label="", *, forwarded=False, mailbox_capture=False
):
    from backend.ingestion.parser import persist_blob

    # Prevent duplicate files and duplicate new orders even across retry keys.
    s.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "record-intake:" + current_org()},
    )
    digest = sha256(data).hexdigest()
    name = Path(name).name[:240]
    existing = next(
        (
            row
            for row in s.scalars(
                scoped(RecordIntake).where(
                    RecordIntake.sha256 == digest, RecordIntake.name == name
                )
            )
            if row.data.get("requested_revision", "") == revision_label
        ),
        None,
    )
    if existing:
        return {**serialize(existing), "duplicate": True}
    detected, email = inspect_file(name, data)
    candidates = match_orders(s, detected, email)
    blob, _ = persist_blob(data, Path(name).suffix.lower())
    row = RecordIntake(
        name=name,
        blob=blob,
        sha256=digest,
        data={
            "inspection_version": INSPECTION_VERSION,
            "detected": detected,
            "candidates": candidates,
            "reason": "Confirm the order and revision before importing.",
            "email": email,
            "requested_revision": revision_label,
            "forwarded": forwarded,
        },
    )
    s.add(row)
    s.flush()
    if mailbox_capture:
        from .mail_relevance import classify

        relevance = classify(data, detected, candidates)
        row.data = {**row.data, "mail_relevance": relevance}
        if not relevance["relevant"]:
            row.status = "not_order_related"
            row.data = {**row.data, "reason": relevance["reason"]}
            return serialize(row)
    return route_inspected(s, row, revision_label)


def route_inspected(s, row, revision_label=""):
    """Apply the same deterministic routing to new and re-inspected originals."""
    detected, candidates = row.data["detected"], row.data["candidates"]
    revision = revision_label or detected["revision"]
    strong = [
        candidate
        for candidate in candidates
        if any(
            reason != "Drawing number requires confirmation"
            for reason in candidate["reasons"]
        )
    ]
    # Sheet names such as E-01 (or "sheet 1") recur across unrelated orders.
    # Those weak matches must not defeat one unambiguous job/PO/thread match.
    # Conflicting explicit identifiers or a conflicting thread still stop us.
    conflicting = (
        len(detected["job_numbers"]) > 1
        or len(detected["po_numbers"]) > 1
        or (len(detected["revisions"]) > 1 and not revision_label)
        or any(candidate["identifier_conflict"] for candidate in strong)
    )
    if not conflicting and len(strong) == 1:
        row.status = "matched"
        row.data = {
            **row.data,
            "reason": "; ".join(
                reason
                for reason in strong[0]["reasons"]
                if reason != "Drawing number requires confirmation"
            ),
        }
        # The order is known. Missing revision text should not make the PM do
        # clerical work or silently reuse the previous drawing revision.
        attach(s, row, lock_order(s, strong[0]["id"]), revision or "Unspecified")
    elif not conflicting and revision and detected["job_number"] and not candidates:
        order = new_order(
            s,
            {
                "number": detected["job_number"],
                "title": "Job " + detected["job_number"],
                "customer": "Customer not yet confirmed",
                "category": "Custom equipment",
            },
        )
        row.status = "created"
        row.data = {
            **row.data,
            "reason": "Opened an order from the explicit job number. Confirm the customer details when convenient.",
        }
        attach(s, row, order, revision)
    return serialize(row)


def reinspect_pending(s, intake_id):
    """Replay a saved undecided original through the current intake rules.

    This does not override a person's dismissal, assignment or chosen revision,
    and retains the prior detections for the audit trail.
    """
    s.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "record-intake:" + current_org()},
    )
    row = get(s, RecordIntake, intake_id, True)
    if row.order_id or row.status != "needs_confirmation":
        return serialize(row)
    payload = (db.BLOBS / row.blob).read_bytes()
    detected, email = inspect_file(row.name, payload)
    candidates = match_orders(s, detected, email)
    row.data = {
        **row.data,
        "previous_detected": row.data.get("detected", {}),
        "detected": detected,
        "email": email,
        "candidates": candidates,
        "inspection_version": INSPECTION_VERSION,
        "reinspected_at": now().isoformat(),
    }
    return route_inspected(s, row, row.data.get("requested_revision", ""))


@router.get("")
def queue(
    status: Literal[
        "needs_confirmation", "matched", "created", "not_order_related", "set_aside"
    ]
    | None = None,
):
    with Session() as s:
        query = scoped(RecordIntake)
        if status is not None:
            query = query.where(RecordIntake.status == status)
        return {
            "items": [
                serialize(row)
                for row in s.scalars(
                    query.order_by(RecordIntake.created_at.desc()).limit(100)
                )
            ]
        }


@router.get("/{id}/source")
def original(id: str):
    with Session() as s:
        row = get(s, RecordIntake, id)
        suffix = Path(row.name).suffix.lower()
        return FileResponse(
            db.BLOBS / row.blob,
            filename=row.name,
            media_type={
                ".pdf": "application/pdf",
                ".txt": "text/plain",
                ".eml": "message/rfc822",
            }[suffix],
            content_disposition_type="attachment" if suffix == ".eml" else "inline",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )


@router.get("/{id}/preview")
def intake_preview(id: str):
    from .intake_preview import preview

    with Session() as s:
        return preview(s, get(s, RecordIntake, id))


@router.get("/{id}/attachments/{index}")
def intake_attachment(id: str, index: int):
    from urllib.parse import quote

    from .intake_preview import attachment_bytes

    with Session() as s:
        row = get(s, RecordIntake, id)
        if not row.name.lower().endswith(".eml"):
            fail("This file is not an email.", 404)
        try:
            message = BytesParser(policy=policy.default).parsebytes(
                (db.BLOBS / row.blob).read_bytes()
            )
        except OSError:
            fail("The original email is unavailable.", 404)
        parts = list(message.iter_attachments())
        if index < 0 or index >= len(parts):
            fail("Attachment not found.", 404)
        part = parts[index]
        name = Path(part.get_filename() or "attachment").name
        return Response(
            attachment_bytes(part),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(name, safe=""),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


@router.get("/{id}")
def detail(id: str):
    """Resolve a notification link independently of the latest intake page."""
    with Session() as s:
        return serialize(get(s, RecordIntake, id))


@router.post("", status_code=202)
async def intake(
    file: UploadFile,
    revision_label: str = Form("", max_length=40),
    idempotency_key: str = Header(),
):
    data = await file.read(upload_max_bytes(file.filename or "document") + 1)
    with Session.begin() as s:
        return idempotent(
            s,
            "record-intake:" + idempotency_key,
            {
                "sha": sha256(data).hexdigest(),
                "name": file.filename or "",
                "revision": revision_label,
            },
            lambda: receive(s, file.filename or "", data, revision_label),
        )


@router.post("/{id}/set-aside")
def set_aside(id: str, idempotency_key: str = Header()):
    return change_disposition(id, "set_aside", idempotency_key)


@router.post("/{id}/restore")
def restore_intake(id: str, idempotency_key: str = Header()):
    return change_disposition(id, "needs_confirmation", idempotency_key)


def change_disposition(id, status, key):
    with Session.begin() as s:
        row = get(s, RecordIntake, id, True)

        def act():
            if row.order_id:
                fail("This message already belongs to an order.", 409)
            row.status = status
            row.data = {
                **row.data,
                "disposition_history": [
                    *row.data.get("disposition_history", []),
                    {
                        "status": status,
                        "actor": current_actor(),
                        "at": now().isoformat(),
                    },
                ],
            }
            return serialize(row)

        return idempotent(
            s, f"record-intake:{id}:disposition:{key}", {"status": status}, act
        )


@router.post("/{id}/confirm")
def confirm(id: str, body: ConfirmIntake, idempotency_key: str = Header()):
    with Session.begin() as s:
        s.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": "record-intake:" + current_org()},
        )
        row = get(s, RecordIntake, id, True)

        def act():
            if row.order_id:
                fail("This file has already been imported. Open its order.", 409)
            if row.status in {"set_aside", "not_order_related"}:
                fail(
                    "Restore this message to review before assigning it to an order.",
                    409,
                )
            revision = body.revision_label or row.data["detected"]["revision"]
            if not revision and len(row.data["detected"].get("revisions", [])) > 1:
                fail(
                    "The message references multiple revisions. Select the returned revision, or enter Unspecified."
                )
            revision = revision or "Unspecified"
            if body.order_id:
                order = lock_order(s, body.order_id)
                row.status = "matched"
            else:
                job = body.job_number or row.data["detected"]["job_number"]
                if not job:
                    fail(
                        "Enter a job number to create an order, or choose an existing order."
                    )
                matches = [
                    o
                    for o in s.scalars(scoped(Order))
                    if o.number.casefold() == job.casefold()
                ]
                if matches:
                    fail(
                        "That job number already exists. Choose the existing order.",
                        409,
                    )
                order = new_order(
                    s,
                    {
                        "number": job,
                        "title": body.title or "Job " + job,
                        "customer": body.customer or "Customer not yet confirmed",
                        "category": "Custom equipment",
                    },
                )
                row.status = "created"
            # A PM confirmation supersedes ambiguous text. Retain the original
            # detections separately without teaching incorrect aliases.
            original = row.data["detected"]
            row.data = {
                **row.data,
                "original_detected": original,
                "detected": {
                    **original,
                    "job_numbers": [body.job_number.upper()]
                    if body.job_number
                    else [order.number.upper()],
                    "job_number": body.job_number or order.number,
                    "po_numbers": []
                    if len(original["po_numbers"]) > 1
                    else original["po_numbers"],
                    "revision": revision,
                },
                "reason": "Order and revision confirmed by a person.",
            }
            attach(s, row, order, revision)
            return serialize(row)

        return idempotent(
            s, f"record-intake:{id}:confirm:{idempotency_key}", body.model_dump(), act
        )
