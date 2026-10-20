"""Gather exact released files into an inspectable, unissued handover package."""

import json
import re
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from backend.domain.service import digest, fail, get, scoped
from backend.storage import db
from backend.storage.models import Document, Span

MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
ALLOWED_SUFFIXES = {".pdf", ".txt", ".csv", ".xlsx", ".dxf", ".png", ".jpg", ".jpeg"}
INTERNAL_ROLES = {
    "erp_price",
    "erp_order",
    "accepted_exception",
    "engineering_approval",
    "customer_approval",
    "supplier_offer",
    "internal_note",
}


def _revision(value):
    return re.sub(
        r"^rev(?:ision)?\.?\s*", "", str(value or "").strip(), flags=re.IGNORECASE
    ).casefold()


def read_document(document):
    """Resolve only a stored blob and verify bytes, including during approvals."""
    blob = Path(document.blob)
    path = db.BLOBS / blob
    if (
        not document.blob
        or blob.name != document.blob
        or path.suffix.lower() not in ALLOWED_SUFFIXES
        or not path.is_file()
        or not path.resolve().is_relative_to(db.BLOBS.resolve())
    ):
        fail("A handover file is missing or has an unsupported format.", 409)
    if not 0 < path.stat().st_size <= MAX_FILE:
        fail("A handover file is empty or exceeds 20 MB.", 409)
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != document.sha256:
        fail(
            "A handover file differs from its saved source. Review the current file.",
            409,
        )
    return raw


def prepare_payload(order, reports, as_built, other, revision):
    if not _revision(revision):
        fail("Closeout needs the exact released revision.", 409)
    documents = []
    seen = set()
    total = 0
    for role, values in (
        ("test_report", reports),
        ("as_built", as_built),
        ("supporting_document", other),
    ):
        for document in values:
            if document.project_id != order.project_id or document.id in seen:
                fail("Choose each handover file once from this order.", 409)
            seen.add(document.id)
            source_role = document.meta.get("order_role") or document.kind
            if source_role in INTERNAL_ROLES:
                fail(
                    "Internal commercial and approval records are not customer handover files.",
                    409,
                )
            if document.meta.get("release_status") not in {"released", "approved"}:
                fail(
                    "Approve or release each handover source before preparing closeout.",
                    409,
                )
            if role == "as_built" and _revision(
                document.meta.get("revision_label")
            ) != _revision(revision):
                fail("The as-built drawing does not match the released revision.", 409)
            raw = read_document(document)
            total += len(raw)
            if total > MAX_TOTAL:
                fail(
                    "The handover package exceeds 100 MB. Split its reviewed documents.",
                    413,
                )
            # Preserve a useful filename while never accepting an archive path.
            safe_name = re.sub(
                r"[^\w .()\-]", "_", Path(document.name.replace("\\", "/")).name
            ).strip(" .")[:180]
            suffix = Path(document.blob).suffix.lower()
            if not safe_name.lower().endswith(suffix):
                safe_name = (safe_name or role) + suffix
            documents.append(
                {
                    "id": document.id,
                    "name": document.name,
                    "role": role,
                    "revision": document.meta.get("revision_label", ""),
                    "release_status": document.meta["release_status"],
                    "sha256": document.sha256,
                    "size_bytes": len(raw),
                    "archive_name": f"Documents/{len(documents) + 1:02d}-{safe_name}",
                }
            )
    return {
        "handover_schema_version": 1,
        "revision": revision,
        "documents": documents,
        "document_hashes": {
            d.id: digest([d.sha256, d.meta]) for d in reports + as_built + other
        },
        "handover": f"{order.number}: {len(documents)} released or approved source files gathered for handover review.",
        "email_draft": {
            "subject": f"{order.number} | Handover documents | Revision {revision}",
            "body": (
                f"The prepared handover for {order.number} contains the recorded test reports, "
                f"as-built revision {revision} and the supporting documents listed in the attached index. "
                "Please review the files before issue. This transmittal does not establish shipment, "
                "third-party certification or an additional production release."
            ),
            "status": "unsent_artifact",
        },
        "status": "unsent_artifact",
    }


def document_evidence(s, order, documents):
    """Expose file identity for review without claiming its engineering correctness."""
    source_ids = []
    for saved in documents:
        document = get(s, Document, saved["id"])
        if document.project_id != order.project_id:
            fail("Handover evidence belongs to a different order.", 409)
        text = json.dumps(
            {
                "document": document.name,
                "role": saved["role"],
                "revision": saved["revision"],
                "recorded_status": saved["release_status"],
                "size_bytes": saved["size_bytes"],
                "sha256": document.sha256,
                "review_scope": "File identity and recorded status only. Read the original file before approval.",
            },
            sort_keys=True,
        )
        location = {
            "kind": "handover_file_identity",
            "document_sha256": document.sha256,
        }
        span = next(
            (
                item
                for item in s.scalars(
                    scoped(Span).where(Span.document_id == document.id)
                )
                if item.text == text and item.location == location
            ),
            None,
        )
        if span is None:
            span = Span(document_id=document.id, text=text, location=location)
            s.add(span)
            s.flush()
        source_ids.append(span.id)
    return source_ids


def current(s, order, payload):
    if payload.get("handover_schema_version") != 1:
        return False
    for saved in payload["documents"]:
        document = get(s, Document, saved["id"])
        if (
            document.project_id != order.project_id
            or digest([document.sha256, document.meta])
            != payload["document_hashes"][document.id]
        ):
            return False
        if (
            document.sha256 != saved["sha256"]
            or len(read_document(document)) != saved["size_bytes"]
        ):
            return False
    return True


def render_index(context, package):
    from .exports import _font, _header, _p, _render, _styles

    _font()
    styles = _styles()
    payload = package["payload"]
    title = "Handover document index"
    story = _header(title, context, package, styles)
    story += [
        _p(f"As-built revision {payload['revision']}", styles, "heading"),
        _p(payload["handover"], styles),
        _p(
            "The package preserves the exact source files listed below. Document status is recorded from the order; this index does not certify engineering correctness, testing or shipment.",
            styles,
            "small",
        ),
    ]
    labels = {
        "test_report": "Test report",
        "as_built": "As-built drawing",
        "supporting_document": "Supporting document",
    }
    for index, document in enumerate(payload["documents"], 1):
        story += [
            _p(f"{index:02d} / {labels[document['role']]}", styles, "label"),
            _p(document["name"], styles, "heading"),
            _p(
                f"Revision: {document['revision'] or 'not recorded'} | Status: {document['release_status']} | {document['size_bytes']:,} bytes",
                styles,
                "small",
            ),
            _p(
                f"Archive entry: {document['archive_name']}\nSHA-256: {document['sha256']}",
                styles,
                "hash",
            ),
        ]
    story += [
        _p(
            f"Prepared package: {package['id']}\nFingerprint: {package['fingerprint']}",
            styles,
            "hash",
        )
    ]
    return _render(title, story, context, package)


def render_bundle(s, order, package, index_pdf):
    payload = package["payload"]
    files = {"Handover-index.pdf": index_pdf}
    for saved in payload["documents"]:
        document = get(s, Document, saved["id"])
        raw = read_document(document)
        if (
            document.project_id != order.project_id
            or sha256(raw).hexdigest() != saved["sha256"]
            or digest([document.sha256, document.meta])
            != payload["document_hashes"][document.id]
        ):
            fail("The handover source changed while the package was prepared.", 409)
        files[saved["archive_name"]] = raw
    files["manifest.json"] = json.dumps(
        {
            "schema_version": 1,
            "order_number": order.number,
            "revision": payload["revision"],
            "package_id": package["id"],
            "fingerprint": package["fingerprint"],
            "synthetic": package["synthetic"],
            "status": "prepared_for_review",
            "notice": "Prepared handover. No email has been sent by creation of this archive.",
            "documents": payload["documents"],
            "index_sha256": sha256(index_pdf).hexdigest(),
        },
        sort_keys=True,
        indent=2,
    ).encode()
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, raw)
    return output.getvalue()


def prepare_artifacts(s, order, package, context):
    from . import adapters

    index = render_index(context, package)
    bundle = render_bundle(s, order, package, index)
    return [
        adapters.write_artifact(
            order.organization_id, order.id, package["id"], name, raw
        )
        for name, raw in (
            ("handover-index.pdf", index),
            ("handover-documents.zip", bundle),
        )
    ]
