"""Read-only, tenant-scoped adapter for the transformer phase prototype."""

from backend.domain.service import scoped
from backend.equipment.schemas import SourceInput
from backend.records import service as records
from backend.storage import db
from backend.storage.models import Document

from .phase_change import analyze, read_claims


def for_order(s, order, scenario=None):
    workspace = records.view(s, order)
    documents = {
        doc.id: doc
        for doc in s.scalars(
            scoped(Document).where(Document.project_id == order.project_id)
        )
    }
    sources, excluded = [], []
    for item in workspace["documents"]:
        document = documents.get(item["id"])
        if not document or item.get("role") not in {
            "drawing",
            "markup",
            "specification",
            "bom",
        }:
            excluded.append({"id": item["id"], "reason": "No applicable source role"})
            continue
        # Paths are resolved from the authorized order, never accepted from HTTP.
        sources.append(
            SourceInput(
                id=document.id,
                name=document.name,
                path=str(db.BLOBS / document.blob),
                sha256=document.sha256,
                role=item["role"],
                revision=item.get("revision_label") or "Unspecified",
            )
        )
    report = analyze(read_claims(sources), scenario)
    return {
        **report,
        "order_id": order.id,
        "order_number": order.number,
        "order_version": order.version,
        "excluded_documents": excluded,
    }
