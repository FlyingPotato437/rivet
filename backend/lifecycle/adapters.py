"""Executable local targets, deliberately distinct from connected enterprise APIs.

Preparation writes durable artifacts; approval can commit to this isolated local
store. These adapters never call SAP, SharePoint, Teams, or customer email.
"""

import json
from hashlib import sha256
from pathlib import Path

from backend.domain.service import fail
from backend.storage import db


def root(organization_id: str, order_id: str) -> Path:
    from uuid import UUID

    # Paths are derived from authenticated IDs, never an uploaded/browser path.
    UUID(organization_id)
    UUID(order_id)
    return db.DATA / "lifecycle" / organization_id / order_id


def write_json(organization_id, order_id, package_id, name, payload):
    if name not in {
        "work.json",
        "approved.json",
        "quote.json",
        "reply.json",
        "change-order.json",
        "drafting-list.json",
        "revision-check.json",
        "closeout.json",
    }:
        fail("Unsupported lifecycle artifact.")
    content = json.dumps(payload, indent=2, sort_keys=True, default=str).encode()
    return _write(
        organization_id, order_id, package_id, name, content, "application/json"
    )


def write_artifact(organization_id, order_id, package_id, name, content):
    if name not in {
        "quote.pdf",
        "change-order.pdf",
        "response-letter.pdf",
        "drafting-list.pdf",
        "handover-index.pdf",
        "handover-documents.zip",
    }:
        fail("Unsupported lifecycle document.")
    zipped = name == "handover-documents.zip"
    signature = b"PK\x03\x04" if zipped else b"%PDF-"
    if not isinstance(content, bytes) or not content.startswith(signature):
        fail("The prepared file does not match its supported format.")
    if len(content) > (101 if zipped else 32) * 1024 * 1024:
        fail("The prepared file exceeds the document size limit.", 413)
    return _write(
        organization_id, order_id, package_id, name, content,
        "application/zip" if zipped else "application/pdf",
    )


def _write(organization_id, order_id, package_id, name, content, media_type):
    from uuid import UUID

    UUID(package_id)
    destination = root(organization_id, order_id) / package_id
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / name
    if path.exists() and path.read_bytes() != content:
        fail("A prepared artifact is immutable. Prepare a new package.", 409)
    path.write_bytes(content)
    return {
        "name": name,
        "sha256": sha256(content).hexdigest(),
        "media_type": media_type,
        "mode": "local_demo",
        "url": f"/api/orders/{order_id}/lifecycle/artifacts/{package_id}/{name}",
    }


def read_artifact(organization_id, order_id, package_id, manifest):
    path = root(organization_id, order_id) / package_id / manifest["name"]
    if not path.is_file():
        fail("The prepared artifact is no longer available.", 410)
    content = path.read_bytes()
    if sha256(content).hexdigest() != manifest["sha256"]:
        fail(
            "The prepared artifact changed. Prepare it again before relying on it.", 409
        )
    return content


def prepare_receipt(system, action, payload):
    return {
        "system": system,
        "action": action,
        "status": "prepared_local_demo",
        "mode": "local_demo",
        "payload": payload,
        "notice": "Prepared in Rivet's local demo store. No external system was changed.",
    }
