"""Execute current approved packages through a configured local HTTP boundary.

The package remains immutable. Operational attempts and reconciled receipts are
append-only OrderEvents, so a lost process/HTTP response cannot create a second
ERP proposal or buyer notification. No function here sends customer email.
"""

import base64
import mimetypes
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from backend.domain.service import digest, fail, get, scoped
from backend.identity import current_actor, current_identity
from backend.lifecycle import adapters
from backend.lifecycle import service as lifecycle
from backend.orders import service as orders
from backend.storage import db
from backend.storage.models import Document, OrderEvent, OrderRevision, Project

from .client import DeliveryError, LocalClient, configuration
from .schemas import RESOURCE, Command, Execute


def _physical_document(document):
    path = db.BLOBS / document.blob
    if (
        Path(document.blob).name != document.blob
        or not path.resolve().is_relative_to(db.BLOBS.resolve())
        or not path.is_file()
    ):
        fail("An approved source file is unavailable; no action was executed.", 409)
    content = path.read_bytes()
    if (
        len(content) > 32 * 1024 * 1024
        or sha256(content).hexdigest() != document.sha256
    ):
        fail("An approved source file changed; prepare and approve it again.", 409)
    return content


def _package(s, order, package_id):
    state = lifecycle._state(order)
    package = next(
        (item for item in state["packages"] if item["id"] == package_id), None
    )
    if not package:
        fail("Prepared lifecycle package not found.", 404)
    if package["status"] != "approved" or package["blockers"]:
        fail("Execute only fully approved work with no unresolved blockers.", 409)
    approvals = {
        d["gate"]: d for d in package["decisions"] if d["decision"] == "approve"
    }
    if set(approvals) != set(package["gates"]) or any(
        d["fingerprint"] != package["fingerprint"] for d in approvals.values()
    ):
        fail("Every exact package gate needs its authenticated approval.", 409)
    if digest(
        {
            key: package[key]
            for key in (
                "operation",
                "payload",
                "gates",
                "sources",
                "dependencies",
                "synthetic",
            )
        }
    ) != package["fingerprint"] or not lifecycle._current(s, order, state, package):
        fail("The approved package or source evidence is stale.", 409)
    signed = False
    for revision in s.scalars(
        scoped(OrderRevision)
        .where(OrderRevision.order_id == order.id)
        .order_by(OrderRevision.version.desc())
        .limit(100)
    ):
        snapshot = revision.snapshot
        if digest(snapshot) != revision.checksum:
            fail("The order audit snapshot no longer matches its checksum.", 409)
        if package in snapshot.get("state", {}).get("lifecycle", {}).get(
            "packages", []
        ):
            signed = True
            break
    if not signed:
        fail("The approved package is not present in an immutable order snapshot.", 409)
    for approval in approvals.values():
        if (
            lifecycle._sources(s, order, approval["source_ids"])
            != approval["evidence_hashes"]
        ):
            fail("The signatory's supporting evidence changed.", 409)
    for item in package["sources"].values():
        _physical_document(get(s, Document, item["document_id"]))
    for artifact in package["artifacts"]:
        adapters.read_artifact(order.organization_id, order.id, package_id, artifact)
    return package, approvals


def _released_files(s, order, package):
    files, total = [], 0
    for document_id in package["payload"]["outbound_document_ids"]:
        document = get(s, Document, document_id)
        if document.project_id != order.project_id or document.meta.get(
            "release_status"
        ) not in {"released", "approved_for_customer"}:
            fail("Only this order's exact released documents can be published.", 409)
        content = _physical_document(document)
        total += len(content)
        if total > 32 * 1024 * 1024:
            fail("Split the release publication into a bounded package.", 413)
        files.append(
            {
                "document_id": document.id,
                "name": document.name,
                "sha256": document.sha256,
                "media_type": mimetypes.guess_type(document.name)[0]
                or "application/octet-stream",
                "content_base64": base64.b64encode(content).decode(),
            }
        )
    return files


def _plan(s, order, package, approvals, config):
    operation, payload = package["operation"], package["payload"]
    if operation == "kickoff":
        return [
            (
                "sap.draft_sales_order",
                {key: payload[key] for key in ("purchase_order_number", "lines")},
                [],
            ),
            ("sharepoint.create_job_folder", {"name": order.number}, []),
        ]
    if operation == "revision_review":
        change = payload["change_order"]
        if not change["lines"]:
            return []
        return [
            (
                "sap.propose_change",
                {
                    **change,
                    "status": "proposed_not_customer_accepted",
                    "billing_authorized": False,
                },
                [],
            )
        ]
    if operation == "purchasing":
        buyer = approvals["money"]["actor"]
        if buyer not in config.buyer_ids.get(order.organization_id, []):
            fail(
                "A configured named buyer must approve this exact purchasing package before its handoff or schedule update.",
                403,
            )
        values = {
            **deepcopy(payload),
            "buyer_id": buyer,
            "approved_material_date": payload["proposed_ship_date"],
            "customer_ship_date_committed": False,
        }
        return [
            ("teams.buyer_handoff", values, []),
            ("schedule.confirm_material_date", values, []),
        ]
    if operation == "release":
        state = lifecycle._state(order)
        check = next(
            item
            for item in state["packages"]
            if item["id"] == payload["revision_check_id"]
        )
        # Recursively revalidate the actual revision proof, not merely its label.
        _package(s, order, check["id"])
        files = _released_files(s, order, package)
        values = {
            "revision": check["payload"]["revision"],
            "revision_check_id": check["id"],
            "files_sha256": [item["sha256"] for item in files],
        }
        return [
            ("sharepoint.publish_release", values, files),
            ("vault.release_revision", values, files),
            ("sap.release_order", values, []),
        ]
    fail(
        "This package has no enterprise execution actions. Customer mail remains unsent.",
        409,
    )


def execute(s, order, package_id, body: Execute, *, config=None, leased=None):
    orders.check_version(order, body.expected_version)
    if current_identity().role != "org:admin":
        fail(
            "A team administrator must run the isolated enterprise action harness.", 403
        )
    if not get(s, Project, order.project_id).synthetic:
        fail("This local harness only executes explicitly synthetic demo orders.", 409)
    config = config or configuration()
    if order.organization_id not in config.organization_ids:
        fail("This organization is not enabled for local enterprise actions.", 403)
    package, approvals = _package(s, order, package_id)
    plan = _plan(s, order, package, approvals, config)
    client = LocalClient(config)
    actions, failures = [], []
    for action, payload, files in plan:
        if leased is not None and not leased():
            fail(
                "The execution worker lost its lease; saved receipts can be reconciled.",
                409,
            )
        action_id = digest(
            [
                order.organization_id,
                order.id,
                package_id,
                package["fingerprint"],
                action,
            ]
        )
        try:
            # The order is locked by the caller; recheck evidence before every
            # remote side effect, including retries after a partial application.
            _package(s, order, package_id)
            remote = client.state(order.organization_id, order.id)
            remote_version = (
                remote["resources"].get(RESOURCE[action], {}).get("version", 0)
            )
            command = Command(
                organization_id=order.organization_id,
                order_id=order.id,
                job_number=order.number,
                action=action,
                package_id=package_id,
                package_fingerprint=package["fingerprint"],
                order_version=order.version,
                source_state_sha256=digest(package["sources"]),
                approvals=approvals,
                requested_by=current_actor(),
                reason=body.reason,
                payload=payload,
                files=files,
                expected_remote_version=remote_version,
            )
            if leased is not None and not leased():
                fail("The execution worker lost its lease before delivery.", 409)
            receipt, reconciled = client.deliver(command, action_id)
            result = {
                "action_id": action_id,
                "action": action,
                "status": receipt["status"],
                "reconciled": reconciled,
                "receipt": receipt,
            }
            actions.append(result)
        except DeliveryError as exc:
            result = {
                "action_id": action_id,
                "action": action,
                "status": "receipt_unknown" if exc.retryable else "blocked",
                "message": str(exc),
                "retryable": exc.retryable,
            }
            failures.append(result)
        s.add(
            OrderEvent(
                order_id=order.id,
                version=order.version,
                kind="workflow_action",
                summary=f"{action}: {result['status']} (local HTTP simulation)",
                actor=current_actor(),
                data={
                    "mode": "local_http_demo",
                    "package_id": package_id,
                    "fingerprint": package["fingerprint"],
                    "reason": body.reason,
                    **result,
                },
            )
        )
        s.flush()
    return {
        "mode": "local_http_demo",
        "order_id": order.id,
        "package_id": package_id,
        "status": "complete"
        if not failures
        else "partial"
        if actions
        else "incomplete",
        "actions": actions,
        "failures": failures,
        "vendor_writes": False,
        "customer_mail_sent": False,
        "notice": "Actual HTTP effects in the persistent local enterprise simulator only; no production vendor system was changed.",
    }


def view(s, order):
    events = list(
        s.scalars(
            scoped(OrderEvent)
            .where(
                OrderEvent.order_id == order.id, OrderEvent.kind == "workflow_action"
            )
            .order_by(OrderEvent.created_at.desc(), OrderEvent.id.desc())
            .limit(100)
        )
    )
    return {
        "mode": "local_http_demo",
        "order_id": order.id,
        "attempts": [
            {"id": event.id, "at": event.created_at.isoformat(), **event.data}
            for event in events
        ],
        "vendor_writes": False,
    }
