"""Common intake for uploaded EML files and authenticated Resend deliveries."""

from email import policy
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path

from backend.domain.service import fail, get, scoped
from backend.storage.models import Document, Project


def _revision(value):
    return (
        str(value or "")
        .strip()
        .lower()
        .removeprefix("revision ")
        .removeprefix("rev ")
        .strip()
    )


def email_document_ids(s, order, root):
    """The original arrival plus both new and reused, same-order attachments."""
    if root.project_id != order.project_id:
        return []
    linked = set(root.meta.get("attachment_document_ids", []))
    return [
        root.id,
        *[
            document.id
            for document in s.scalars(
                scoped(Document).where(Document.project_id == order.project_id)
            )
            if document.id != root.id
            and (
                document.meta.get("email_document_id") == root.id
                or document.id in linked
            )
        ],
    ]


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
    # Serialize arrival + attachment identities across concurrent mailbox jobs.
    project = get(s, Project, order.project_id, True)
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
            "intake_order_id": order.id,
        },
    )
    attachment_links = []
    for attachment_name, payload in supported:
        revision = _revision(revision_label)
        matches = (
            list(
                s.scalars(
                    scoped(Document).where(
                        Document.project_id == project.id,
                        Document.sha256 == sha256(payload).hexdigest(),
                    )
                )
            )
            if revision and revision != "unspecified"
            else []
        )
        existing_attachment = next(
            (
                document
                for document in matches
                if document.state != "failed"
                and _revision(document.meta.get("revision_label")) == revision
            ),
            None,
        )
        # A project belongs to exactly one order (Order.project_id is unique).
        # Its existing confirmed document identity is valid regardless of whether
        # it originally arrived by upload, an email or a server-created fixture.
        # Parent-email IDs are arrival provenance, not a reason to duplicate the
        # same revision's annotations. Keep every new arrival on its own EML.
        attachment = (
            {"id": existing_attachment.id}
            if existing_attachment
            else add_document(
                s,
                project,
                attachment_name,
                "auto",
                payload,
                {
                    "email_document_id": original["id"],
                    "revision_label": revision_label,
                    "intake_order_id": order.id,
                },
            )
        )
        attachment_links.append(
            {
                "document_id": attachment["id"],
                "name": attachment_name,
                "reused": existing_attachment is not None,
            }
        )
    original_row = get(s, Document, original["id"])
    original_row.meta = {
        **original_row.meta,
        "attachment_document_ids": list(
            dict.fromkeys(item["document_id"] for item in attachment_links)
        ),
        "attachment_arrivals": attachment_links,
    }
    return {
        "document_id": original["id"],
        "attachments": len(supported),
        "unparsed_attachments": unsupported,
        "duplicate": False,
        "attachments_reused": sum(item["reused"] for item in attachment_links),
    }
