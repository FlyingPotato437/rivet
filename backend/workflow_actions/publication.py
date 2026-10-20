"""Opt-in enterprise publication, separate from the loopback demo executor.

SharePoint uses Graph upload sessions with conflict=fail. SAP/vault writes use
an explicitly installed manufacturer bridge, not the read-only public SAP API.
No endpoints, destinations or credentials are chosen by a model.
"""

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.domain.service import digest, fail, get, scoped
from backend.lifecycle import adapters
from backend.lifecycle import service as lifecycle
from backend.mailbox.models import MailboxConnection
from backend.orders.schemas import StrictModel
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Base, Job, Order, OrderEvent, Project, Row


class Publication(Row, Base):
    __tablename__ = "enterprise_publications"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    package_id: Mapped[str] = mapped_column(String(36))
    target_key: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSONB)
    target_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    receipt: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (
        UniqueConstraint("order_id", "package_id", "target_key", "fingerprint"),
    )


def targets(order):
    path = os.getenv("RIVET_WRITE_TARGETS_CONFIG", "")
    if not path:
        return {}
    return (
        json.loads(Path(path).read_text())
        .get("organizations", {})
        .get(order.organization_id, {})
        .get("jobs", {})
        .get(order.number, {})
    )


def approved(s, order, package_id):
    state = lifecycle._state(order)
    p = next((p for p in state["packages"] if p["id"] == package_id), None)
    if not p or p["status"] != "approved" or not lifecycle._current(s, order, state, p):
        fail("Publication requires the current, fully approved package.", 409)
    return p


def enqueue_approved(s, order, package):
    try:
        configured = targets(order)
        for key, target in configured.items():
            if not target.get("enabled") or not target.get("auto_publish"):
                continue
            for artifact in target.get("artifacts", []):
                if not any(a["name"] == artifact for a in package["artifacts"]):
                    continue
                with s.begin_nested():
                    enqueue(s, order, package["id"], key, artifact)
    except (HTTPException, ValueError, OSError, KeyError):
        s.add(
            OrderEvent(
                order_id=order.id,
                version=order.version,
                kind="publication_setup",
                summary="The decision is recorded; enterprise publication needs configuration review",
                actor="Rivet",
                data={"package_id": package["id"], "status": "blocked"},
            )
        )


def enqueue(s, order, package_id, target_key, artifact):
    p = approved(s, order, package_id)
    target = targets(order).get(target_key)
    if not target or not target.get("enabled"):
        fail("This enterprise write target is not enabled by the administrator.", 503)
    if get(s, Project, order.project_id).synthetic and not target.get(
        "allow_synthetic", False
    ):
        fail(
            "A fictional order cannot publish to this enterprise target. Explicitly bind a test destination first.",
            403,
        )
    manifest = next((a for a in p["artifacts"] if a["name"] == artifact), None)
    if not manifest:
        fail("This file is not an artifact of the approved package.", 404)
    if artifact not in target.get("artifacts", []):
        fail("This target does not accept that kind of artifact.", 403)
    fingerprint = digest([p["fingerprint"], manifest, target_key, digest(target)])
    existing = s.scalar(
        scoped(Publication).where(
            Publication.order_id == order.id,
            Publication.package_id == package_id,
            Publication.target_key == target_key,
            Publication.fingerprint == fingerprint,
        )
    )
    if existing:
        return existing
    unresolved = s.scalar(
        scoped(Publication).where(
            Publication.order_id == order.id,
            Publication.package_id == package_id,
            Publication.target_key == target_key,
            Publication.status.in_(["creating", "needs_reconciliation"]),
        )
    )
    if unresolved:
        fail(
            "Reconcile the existing publication attempt before changing its destination.",
            409,
        )
    row = Publication(
        order_id=order.id,
        package_id=package_id,
        target_key=target_key,
        fingerprint=fingerprint,
        manifest=manifest,
        target_hash=digest(target),
    )
    s.add(row)
    s.flush()
    s.add(
        Job(
            project_id=order.project_id,
            kind="enterprise_publication",
            key="publication:" + row.id,
            payload={"publication_id": row.id},
        )
    )
    return row


def sharepoint(
    graph, target, row, content, *, upload_transport=None, still_current=lambda: True
):
    drive = quote(target["drive_id"], safe="")
    parent = quote(target["parent_id"], safe="")
    name = (
        f"Rivet-{row.package_id}-{row.manifest['sha256'][:12]}-{row.manifest['name']}"
    )
    endpoint = f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{parent}:/{quote(name, safe='')}"
    # A deterministic path is a reconciliation key. An existing different file
    # is never overwritten, renamed, or interpreted as a completed receipt.
    if row.status in {"creating", "needs_reconciliation"}:
        response = (
            graph.client.get(endpoint, headers=graph.headers()) if graph else None
        )
        if response is not None and response.status_code == 200:
            item = response.json()
            if item.get("size") != len(content):
                raise ValueError(
                    "The SharePoint destination does not have the approved file size."
                )
            original = graph.client.get(endpoint + ":/content", headers=graph.headers())
            if original.status_code in {301, 302, 303, 307, 308}:
                url = original.headers.get("Location", "")
                location = urlsplit(url)
                if (
                    location.scheme != "https"
                    or location.hostname not in target.get("download_hosts", [])
                    or location.username
                    or location.password
                ):
                    raise ValueError(
                        "SharePoint reconciliation download host is not authorized."
                    )
                with httpx.Client(
                    timeout=30, follow_redirects=False, transport=upload_transport
                ) as client:
                    original = client.get(url)
            if (
                original.status_code == 200
                and hashlib.sha256(original.content).hexdigest()
                == row.manifest["sha256"]
                and item.get("id")
            ):
                return {
                    "status": "published_copy",
                    "provider": "sharepoint",
                    "provider_id": item["id"],
                    "sha256": row.manifest["sha256"],
                    "reconciled": True,
                    "name": name,
                }
        return {
            "status": "needs_reconciliation",
            "detail": "No matching completed file was confirmed at the deterministic destination. Check the pending upload before retrying.",
            "destination_name": name,
        }
    response = graph.client.post(
        endpoint + ":/createUploadSession",
        headers=graph.headers(),
        json={"item": {"@microsoft.graph.conflictBehavior": "fail", "name": name}},
    )
    if response.status_code not in {200, 201}:
        raise ValueError(
            f"SharePoint upload session was rejected (HTTP {response.status_code})."
        )
    upload_url = response.json()["uploadUrl"]
    p = urlsplit(upload_url)
    if (
        p.scheme != "https"
        or p.hostname not in target.get("upload_hosts", [])
        or p.username
        or p.password
        or p.fragment
    ):
        raise ValueError(
            "Microsoft upload host is not in this target's administrator allowlist."
        )
    # The upload URL authenticates the request itself. Never forward Graph's
    # application bearer token to an upload host.
    if not still_current():
        raise ValueError("The approval or source changed before publication.")
    with httpx.Client(
        timeout=60, follow_redirects=False, transport=upload_transport
    ) as client:
        uploaded = client.put(
            upload_url,
            headers={
                "Content-Length": str(len(content)),
                "Content-Range": f"bytes 0-{len(content) - 1}/{len(content)}",
            },
            content=content,
        )
    if uploaded.status_code not in {200, 201}:
        raise ValueError(
            f"SharePoint did not confirm the completed upload (HTTP {uploaded.status_code})."
        )
    item = uploaded.json()
    if (
        not item.get("id")
        or item.get("name") != name
        or item.get("size") != len(content)
    ):
        raise ValueError("SharePoint's receipt does not identify the expected file.")
    return {
        "status": "published_copy",
        "provider": "sharepoint",
        "provider_id": item["id"],
        "name": name,
        "sha256": row.manifest["sha256"],
        "eTag": item.get("eTag", ""),
    }


def bridge(target, row, order, content, transport=None):
    base = target["base_url"].rstrip("/")
    p = urlsplit(base)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
    ):
        raise ValueError(
            "The installed manufacturer bridge requires an explicit HTTPS URL."
        )
    token = os.getenv(target.get("token_env", ""), "")
    if not token:
        raise ValueError("The manufacturer write bridge credential is missing.")
    headers = {"Authorization": "Bearer " + token, "Idempotency-Key": row.id}
    url = base + "/v1/orders/" + quote(order.number, safe="") + "/actions/" + row.id
    with httpx.Client(
        timeout=30, follow_redirects=False, transport=transport
    ) as client:
        receipt = client.get(url, headers=headers)
        if receipt.status_code == 404 and row.status == "queued":
            import base64

            receipt = client.put(
                url,
                headers=headers,
                json={
                    "organization_id": order.organization_id,
                    "job_number": order.number,
                    "fingerprint": row.fingerprint,
                    "artifact": row.manifest,
                    "content_base64": base64.b64encode(content).decode(),
                },
            )
        if receipt.status_code not in {200, 201}:
            raise ValueError(
                "The manufacturer bridge did not confirm its receipt; reconcile before retrying."
            )
        payload = receipt.json()
        if (
            payload.get("fingerprint") != row.fingerprint
            or payload.get("job_number") != order.number
            or payload.get("sha256") != row.manifest["sha256"]
            or payload.get("status") != "committed"
        ):
            raise ValueError(
                "The write receipt does not match the approved artifact and order."
            )
        return {
            k: payload[k] for k in ("fingerprint", "job_number", "sha256", "status")
        }


def process(id, leased=lambda: True):
    from backend.mailbox.graph import GraphError
    from backend.mailbox.service import client_for

    with Session.begin() as s:
        row = get(s, Publication, id, True)
        order = lock_order(s, row.order_id)
        if row.status in {"published_copy", "committed"} or not leased():
            return
        approved(s, order, row.package_id)
        target = targets(order).get(row.target_key, {})
        if not target.get("enabled") or digest(target) != row.target_hash:
            row.status = "blocked"
            row.error = "The approved destination configuration changed."
            return
        content = adapters.read_artifact(
            order.organization_id, order.id, row.package_id, row.manifest
        )
        if hashlib.sha256(content).hexdigest() != row.manifest["sha256"]:
            fail("The approved artifact changed.", 409)
        # Preserve the prior attempt state for reconciliation; then commit the
        # intent before any external write.
        prior = row.status
        row.status = "creating"
        snapshot = Publication(
            id=row.id,
            package_id=row.package_id,
            manifest=row.manifest,
            status=prior,
            fingerprint=row.fingerprint,
        )
        connection = s.scalar(
            scoped(MailboxConnection).where(MailboxConnection.enabled.is_(True))
        )
    graph = None

    def still_current():
        with Session() as s:
            active = get(s, Publication, id)
            current = get(s, Order, active.order_id)
            approved(s, current, active.package_id)
            return (
                leased()
                and digest(targets(current).get(active.target_key, {}))
                == active.target_hash
            )

    try:
        if target["kind"] == "sharepoint":
            if (
                not connection
                or target.get("tenant_id") != connection.microsoft_tenant_id
            ):
                raise ValueError("This SharePoint tenant is not authorized.")
            graph = client_for(connection)
            result = sharepoint(
                graph, target, snapshot, content, still_current=still_current
            )
        elif target["kind"] in {"sap_bridge", "vault_bridge"}:
            result = bridge(target, snapshot, order, content)
        else:
            raise ValueError("Unsupported enterprise publication target.")
    except (ValueError, KeyError, httpx.HTTPError, GraphError, HTTPException):
        result = {
            "status": "needs_reconciliation",
            "detail": "The external result is uncertain. No blind re-upload or duplicate business action will be attempted.",
        }
    finally:
        if graph:
            graph.close()
    with Session.begin() as s:
        row = get(s, Publication, id, True)
        row.status = result["status"]
        row.receipt = result
        row.error = result.get("detail", "")
        order = get(s, Order, row.order_id)
        s.add(
            OrderEvent(
                order_id=order.id,
                version=order.version,
                kind="enterprise_publication",
                summary="Enterprise publication: " + row.status,
                actor="Rivet",
                data={
                    "publication_id": row.id,
                    "target": row.target_key,
                    "receipt": result,
                },
            )
        )


router = APIRouter(prefix="/api/orders", tags=["Enterprise publication"])


class Publish(StrictModel):
    package_id: str
    target_key: str
    artifact: str


@router.post("/{id}/publish")
def publish(id: str, body: Publish):
    with Session.begin() as s:
        row = enqueue(
            s, lock_order(s, id), body.package_id, body.target_key, body.artifact
        )
        return {"id": row.id, "status": row.status, "receipt": row.receipt}
