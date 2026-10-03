"""Common intake for uploaded EML files and authenticated Resend deliveries."""

from email import policy
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path
from backend.domain.service import fail, get, scoped
from backend.storage.models import Document, Project


def import_email(s, order, name, data, revision_label="", *, forwarded=False):
    from backend.api.main import add_document
    from backend.ingestion.parser import ALLOWED

    if not data or len(data) > 20 * 1024 * 1024 or not name.lower().endswith(".eml"):
        fail("Import an EML email up to 20 MB.")
    message = BytesParser(policy=policy.default).parsebytes(data)
    attachments = [
        (Path(p.get_filename() or "attachment").name, p.get_payload(decode=True) or b"")
        for p in message.iter_attachments()
    ]
    if len(attachments) > 20:
        fail("Import at most 20 attachments per email.")
    unsupported = [
        name
        for name, _ in attachments
        if Path(name).suffix.lower() not in ALLOWED - {".eml"}
    ]
    if unsupported and not forwarded:
        fail(
            "Unsupported email attachments: "
            + ", ".join(unsupported)
            + ". Import supported files separately."
        )
    supported = [
        (name, payload) for name, payload in attachments if name not in unsupported
    ]
    for attachment_name, payload in supported:
        if not payload or len(payload) > 20 * 1024 * 1024:
            fail("An attachment is empty or too large.")
        if attachment_name.lower().endswith(".pdf") and not payload.startswith(b"%PDF"):
            fail("Invalid PDF attachment.")
    project = get(s, Project, order.project_id)
    existing = s.scalar(
        scoped(Document).where(
            Document.project_id == project.id,
            Document.sha256 == sha256(data).hexdigest(),
        )
    )
    if existing:
        return {"document_id": existing.id, "duplicate": True}
    original = add_document(
        s,
        project,
        name if name.lower().endswith(".eml") else "forwarded-email.eml",
        "auto",
        data,
        {
            "revision_label": revision_label,
            "unparsed_attachments": unsupported,
            "intake": "forwarded_email" if forwarded else "uploaded_email",
        },
    )
    for attachment_name, payload in supported:
        add_document(
            s,
            project,
            attachment_name,
            "auto",
            payload,
            {"email_document_id": original["id"], "revision_label": revision_label},
        )
    return {
        "document_id": original["id"],
        "attachments": len(supported),
        "unparsed_attachments": unsupported,
        "duplicate": False,
    }
