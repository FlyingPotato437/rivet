"""Private originals selected by a PM, never automatically disclosed by a model.

The browser receives bytes only after approval through an authenticated compose
request. Office's Base64 reply attachment support avoids public download URLs.
"""

import base64
import hashlib
import re
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException

from backend.domain.service import digest, fail, get, scoped
from backend.storage import db
from backend.storage.models import Document

MAX_FILES = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def limits():
    return {
        "max_files": MAX_FILES,
        "max_file_bytes": MAX_FILE_BYTES,
        "max_total_bytes": MAX_TOTAL_BYTES,
    }


def _path(doc):
    root = db.BLOBS.resolve()
    path = (root / doc.blob).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        fail(
            "The original document is unavailable. Restore it before attaching it.", 409
        )
    return path


def _version(doc):
    return digest(
        [
            doc.id,
            doc.project_id,
            doc.name,
            doc.sha256,
            doc.state,
            doc.kind,
            doc.meta.get("order_role"),
            doc.meta.get("revision_label"),
            doc.meta.get("active", True),
            doc.meta.get("email_document_id"),
            doc.meta.get("connected_identity"),
        ]
    )


def _descriptor(doc, focused, cited, latest_revision):
    name = Path(doc.name.replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    reason, size = "", 0
    if not name or len(name) > 255 or any(ord(c) < 32 for c in name):
        reason = "The document filename needs correction."
    elif suffix not in TYPES:
        reason = "This file type cannot be attached through Rivet."
    elif doc.state not in {"ready", "mapped"}:
        reason = "Source processing must finish before this file can be attached."
    elif doc.meta.get("active", True) is False:
        reason = "This document is marked inactive."
    elif doc.meta.get("connected_source", {}).get("kind") == "erp":
        reason = "Internal ERP scope is context only; it is not a customer attachment."
    elif (
        doc.meta.get("connected_source", {}).get("kind") == "vault"
        and doc.meta["connected_source"].get("lifecycle") != "released"
    ):
        reason = "This vault revision is not released. Select the released drawing."
    else:
        try:
            size = _path(doc).stat().st_size
        except (OSError, HTTPException):
            # Do not expose filesystem paths or exception details to the pane.
            reason = "The original file is unavailable."
        if not reason and not 0 < size <= MAX_FILE_BYTES:
            reason = "Choose a nonempty original no larger than 10 MB."
    role = doc.meta.get("order_role", doc.kind)
    revision = doc.meta.get("revision_label", "")
    warnings = []
    if re.search(
        r"\b(internal|confidential|pricing|cost|supplier|quote|quotation|margin)\b",
        role + " " + name,
        re.IGNORECASE,
    ):
        warnings.append(
            "May contain internal or commercial information. Confirm it is appropriate for this recipient."
        )
    connected = doc.meta.get("connected_source")
    if (
        connected
        and connected.get("kind") == "vault"
        and connected.get("lifecycle") == "released"
    ):
        warnings.append(
            "Released vault revision. Newer drafts do not supersede this file."
        )
    elif not connected and revision and latest_revision and revision != latest_revision:
        warnings.append(
            "A different revision appears in the latest review. Check that this is the intended version."
        )
    relationship = (
        "Current email"
        if doc.id in focused
        else "Cited in this reply"
        if doc.id in cited
        else "Order history"
    )
    if relationship == "Order history":
        warnings.append(
            "Historical context is not automatically part of this response."
        )
    return {
        "document_id": doc.id,
        "name": name,
        "content_type": TYPES.get(suffix, ""),
        "size_bytes": size,
        "sha256": doc.sha256,
        "revision": revision,
        "role": role,
        "relationship": relationship,
        "warnings": warnings,
        "eligible": not bool(reason),
        "unavailable_reason": reason,
        "version": _version(doc),
        "connected_source": doc.meta.get("connected_source"),
    }


def candidates(s, order, snapshot, source_refs=()):
    focused = set(snapshot.get("focus_document_ids", []))
    sources = {x["id"]: x for x in snapshot["current_order"]["sources"]}
    cited = {sources[r["id"]]["document_id"] for r in source_refs if r["id"] in sources}
    latest_revision = snapshot["current_order"].get("latest_review_revision", "")
    docs = list(
        s.scalars(scoped(Document).where(Document.project_id == order.project_id))
    )
    allowed = {
        r["document_id"]
        for r in snapshot["current_order"]
        .get("connected_context", {})
        .get("resources", [])
    }
    docs = [d for d in docs if not d.meta.get("connected_source") or d.id in allowed]
    docs.sort(
        key=lambda d: (d.id in cited, d.id in focused, d.created_at, d.id), reverse=True
    )
    return [_descriptor(d, focused, cited, latest_revision) for d in docs]


def _validate_content(name, payload):
    suffix = Path(name).suffix.lower()
    valid = False
    if suffix == ".pdf":
        valid = payload.startswith(b"%PDF-")
    elif suffix == ".png":
        valid = payload.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in {".jpg", ".jpeg"}:
        valid = payload.startswith(b"\xff\xd8\xff")
    elif suffix in {".txt", ".csv"}:
        try:
            valid = "\x00" not in payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            valid = False
    elif suffix in {".xlsx", ".docx"}:
        try:
            with ZipFile(BytesIO(payload)) as archive:
                names = archive.namelist()
                required = (
                    "xl/workbook.xml" if suffix == ".xlsx" else "word/document.xml"
                )
                valid = (
                    required in names
                    and "[Content_Types].xml" in names
                    and not any("vbaproject" in n.lower() for n in names)
                )
        except BadZipFile:
            valid = False
    if not valid:
        fail(
            "The selected original does not match its supported file type. Review it before attaching.",
            409,
        )


def selected(s, order, snapshot, document_ids, *, frozen=None, include_bytes=False):
    if len(document_ids) > MAX_FILES or len(set(document_ids)) != len(document_ids):
        fail("Choose up to five different supporting documents.")
    known = {c["document_id"]: c for c in snapshot.get("attachment_candidates", [])}
    frozen_by_id = {c["document_id"]: c for c in frozen or []}
    result, total = [], 0
    for id in document_ids:
        doc = get(s, Document, id)
        if doc.project_id != order.project_id:
            fail("Attachments must belong to the reply's current order.", 403)
        expected = frozen_by_id.get(id) if frozen is not None else known.get(id)
        if not expected or id not in known:
            fail(
                "This document was not available for the reviewed reply. Generate a fresh draft.",
                409,
            )
        if not expected.get("eligible") or expected.get("version") != _version(doc):
            fail(
                "A selected document changed or is unavailable. Generate a fresh draft before attaching it.",
                409,
            )
        path = _path(doc)
        if not 0 < path.stat().st_size <= MAX_FILE_BYTES:
            fail("Each supporting original must be nonempty and at most 10 MB.")
        # Bounded read prevents a concurrently replaced file from exhausting memory.
        with path.open("rb") as handle:
            payload = handle.read(MAX_FILE_BYTES + 1)
        if len(payload) > MAX_FILE_BYTES:
            fail("A supporting document exceeds the 10 MB limit.")
        if hashlib.sha256(payload).hexdigest() != expected["sha256"]:
            fail(
                "An original file changed since this draft was prepared. Restore or re-import it before attaching.",
                409,
            )
        if len(payload) != expected["size_bytes"]:
            fail(
                "An original file's size changed. Generate a fresh draft before attaching it.",
                409,
            )
        _validate_content(expected["name"], payload)
        total += len(payload)
        if total > MAX_TOTAL_BYTES:
            fail("Supporting documents must total no more than 20 MB.")
        result.append(
            {
                **expected,
                **(
                    {"base64": base64.b64encode(payload).decode("ascii")}
                    if include_bytes
                    else {}
                ),
            }
        )
    return result


def recommend(s, order, snapshot):
    """Propose cited released vault drawings; never disclose them.

    SharePoint and internal commercial records require explicit selection because
    retrieval access is not recipient disclosure consent. The PM sees suggested
    filenames and approves them through the normal compose path.
    """
    result, total = [], 0
    for item in snapshot.get("attachment_candidates", []):
        connected = item.get("connected_source") or {}
        released = (
            connected.get("kind") == "vault"
            and connected.get("lifecycle") == "released"
        )
        if (
            not item.get("eligible")
            or item.get("relationship") != "Cited in this reply"
            or not released
        ):
            continue
        if any(
            "internal or commercial" in warning.lower()
            for warning in item.get("warnings", [])
        ):
            continue
        size = item["size_bytes"]
        if len(result) == MAX_FILES or total + size > MAX_TOTAL_BYTES:
            continue
        try:
            selected(s, order, snapshot, [item["document_id"]])
        except (HTTPException, OSError):
            continue
        result.append(item["document_id"])
        total += size
    return result
