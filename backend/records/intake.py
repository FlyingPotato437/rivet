"""Private intake queue with conservative, reproducible order matching.

An explicit unique job number can open an order. Conflicting identifiers,
unknown revisions, and drawing-only hints are presented for confirmation.
"""

import re
from email import policy
from email.parser import BytesParser
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Form, Header, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import text

from backend.domain.service import fail, get, idempotent, scoped
from backend.identity import current_org
from backend.orders.schemas import StrictModel
from backend.orders.service import lock_order, new_order
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, InboundEmail, Order, Project, RecordIntake

router = APIRouter(prefix="/api/records/intake", tags=["Incoming documents"])
MAX_BYTES = 20 * 1024 * 1024


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
        re.findall(
            r"\b(?:PO|P\.O\.|purchase order)\s*(?:number|no\.?|#)?\s*[:#-]?\s*" + token,
            value,
            re.IGNORECASE,
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
    if suffix not in {".pdf", ".eml", ".txt"}:
        fail("Drop a returned PDF, an EML email, or a text comment log.")
    if not data or len(data) > MAX_BYTES:
        fail("Upload a nonempty file no larger than 20 MB.")
    stem = Path(name).stem.replace("_", " ")
    # Common file-description suffixes are not part of a labeled job number.
    stem = re.sub(
        r"[- ](?:return(?:ed)?|markups?|review|annotated|comments|final|package)\b.*$",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    parts = [stem]
    email = {}
    if suffix == ".pdf":
        if not data.startswith(b"%PDF"):
            fail("This file is not a valid PDF.")
        import pdfplumber

        try:
            with pdfplumber.open(BytesIO(data)) as pdf:
                if len(pdf.pages) > 150:
                    fail(
                        "PDFs are limited to 150 pages. Split the package before importing."
                    )
                parts.extend(
                    (page.extract_text() or "")[:10000] for page in pdf.pages[:3]
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
            ]
        }
        parts.append(email["subject"])
        body = message.get_body(preferencelist=("plain",))
        if body:
            try:
                parts.append(body.get_content()[:30000])
            except (LookupError, UnicodeError):
                pass
        parts.extend(
            Path(part.get_filename() or "").stem.replace("_", " ")
            for part in message.iter_attachments()
        )
    else:
        parts.append(data.decode("utf-8", errors="replace")[:40000])
    detected = identifiers("\n".join(parts))
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
        document.meta = {**document.meta, "intake_identifiers": row.data["detected"]}
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
    s.flush()


def receive(s, name, data, revision_label="", *, forwarded=False):
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
    revision = revision_label or detected["revision"]
    conflicting = (
        len(detected["job_numbers"]) > 1
        or len(detected["po_numbers"]) > 1
        or (len(detected["revisions"]) > 1 and not revision_label)
        or any(candidate["identifier_conflict"] for candidate in candidates)
    )
    strong = [
        candidate
        for candidate in candidates
        if any(
            reason != "Drawing number requires confirmation"
            for reason in candidate["reasons"]
        )
    ]
    if not conflicting and revision and len(candidates) == len(strong) == 1:
        row.status = "matched"
        row.data = {
            **row.data,
            "reason": "; ".join(
                reason
                for reason in strong[0]["reasons"]
                if reason != "Drawing number requires confirmation"
            ),
        }
        attach(s, row, lock_order(s, strong[0]["id"]), revision)
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


@router.get("")
def queue():
    with Session() as s:
        return {
            "items": [
                serialize(row)
                for row in s.scalars(
                    scoped(RecordIntake)
                    .order_by(RecordIntake.created_at.desc())
                    .limit(100)
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


@router.post("", status_code=202)
async def intake(
    file: UploadFile,
    revision_label: str = Form("", max_length=40),
    idempotency_key: str = Header(),
):
    data = await file.read(MAX_BYTES + 1)
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
            revision = body.revision_label or row.data["detected"]["revision"]
            if not revision:
                fail(
                    "Confirm the revision label, or enter Unspecified if it is not stated."
                )
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
