"""Persistent, loopback-only enterprise sandbox. It is not SAP, SharePoint or Teams.

Run with ``python -m backend.workflow_actions.local_server --database ...``.
Every reply and receipt says local_http_demo. The simulator writes its own SQLite
state and content-addressed objects; it never contacts a vendor service.
"""

import argparse
import base64
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException

from backend.domain.service import digest

from .client import intent_hash
from .schemas import OPERATIONS, RESOURCE, Command

GATES = {
    "kickoff": {"scope", "engineering"},
    "revision_review": {"money", "scope", "engineering", "customer_commitment"},
    "purchasing": {"money", "customer_commitment"},
    "release": {"scope", "engineering", "customer_commitment"},
}


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.objects = self.path.parent / (self.path.stem + "-objects")
        self.objects.mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS actions(
                    id TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
                    order_id TEXT NOT NULL, intent_sha256 TEXT NOT NULL,
                    receipt TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resources(
                    organization_id TEXT NOT NULL, order_id TEXT NOT NULL,
                    kind TEXT NOT NULL, version INTEGER NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY(organization_id, order_id, kind)
                );
                """
            )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def state(self, organization_id, order_id):
        with self.connect() as db:
            return {
                row["kind"]: {"version": row["version"], **json.loads(row["value"])}
                for row in db.execute(
                    "SELECT * FROM resources WHERE organization_id=? AND order_id=?",
                    (organization_id, order_id),
                )
            }

    def receipt(self, organization_id, order_id, action_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT receipt FROM actions WHERE id=? AND organization_id=? AND order_id=?",
                (action_id, organization_id, order_id),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def apply(self, action_id, command):
        organization_id, order_id = str(command.organization_id), str(command.order_id)
        intent = intent_hash(command)
        required = GATES[OPERATIONS[command.action]]
        if not required.issubset(command.approvals) or any(
            decision.get("decision") != "approve"
            or decision.get("fingerprint") != command.package_fingerprint
            or not decision.get("actor")
            for decision in command.approvals.values()
        ):
            raise HTTPException(409, "The exact required approvals are missing.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM actions WHERE id=?", (action_id,)
            ).fetchone()
            if old:
                if old["intent_sha256"] != intent:
                    raise HTTPException(
                        409, "The idempotency identity belongs to another intent."
                    )
                return json.loads(old["receipt"])
            state = {
                row["kind"]: {"version": row["version"], **json.loads(row["value"])}
                for row in db.execute(
                    "SELECT * FROM resources WHERE organization_id=? AND order_id=?",
                    (organization_id, order_id),
                )
            }
            kind = RESOURCE[command.action]
            previous = state.get(kind, {})
            version = previous.get("version", 0)
            if version != command.expected_remote_version:
                raise HTTPException(
                    409, "The remote record changed; reconcile before applying."
                )
            value = self._effect(command, state)
            value = {
                **value,
                "mode": "local_http_demo",
                "package_id": str(command.package_id),
                "package_fingerprint": command.package_fingerprint,
                "job_number": command.job_number,
            }
            db.execute(
                "INSERT INTO resources VALUES(?,?,?,?,?) "
                "ON CONFLICT(organization_id,order_id,kind) DO UPDATE SET version=excluded.version,value=excluded.value",
                (
                    organization_id,
                    order_id,
                    kind,
                    version + 1,
                    json.dumps(value, sort_keys=True),
                ),
            )
            receipt = {
                "mode": "local_http_demo",
                "status": "applied_local_simulation",
                "notice": "Applied to persistent local HTTP simulator only. No vendor system or customer mailbox was changed.",
                "action_id": action_id,
                "action": command.action,
                "organization_id": organization_id,
                "order_id": order_id,
                "package_id": str(command.package_id),
                "package_fingerprint": command.package_fingerprint,
                "intent_sha256": intent,
                "source_state_sha256": command.source_state_sha256,
                "remote_resource": kind,
                "remote_version": version + 1,
                "result_sha256": digest(value),
                "files": value.get("files", []),
                "applied_at": datetime.now(UTC).isoformat(),
            }
            db.execute(
                "INSERT INTO actions VALUES(?,?,?,?,?)",
                (
                    action_id,
                    organization_id,
                    order_id,
                    intent,
                    json.dumps(receipt, sort_keys=True),
                ),
            )
            return receipt

    def _effect(self, command, state):
        payload = command.payload
        action = command.action
        if action == "sap.draft_sales_order":
            if (
                state.get("sales_order", {}).get("production_status")
                == "released_local_simulation"
            ):
                raise HTTPException(
                    409, "A released sales order cannot be replaced by a kickoff draft."
                )
            return {
                "status": "draft_sales_order",
                "purchase_order_number": payload["purchase_order_number"],
                "lines": payload["lines"],
                "billing_authorized": False,
            }
        if action == "sharepoint.create_job_folder":
            return {"folder": command.job_number, "status": "created_local_simulation"}
        if action == "sap.propose_change":
            if "sales_order" not in state:
                raise HTTPException(
                    409, "Execute the approved kickoff before proposing its ERP change."
                )
            return {
                **{
                    key: value
                    for key, value in state["sales_order"].items()
                    if key != "version"
                },
                "proposed_change": payload,
                "commercial_change_status": "proposed_not_customer_accepted",
                "billing_authorized": False,
            }
        if action in {"sharepoint.publish_release", "vault.release_revision"}:
            if "job_folder" not in state:
                raise HTTPException(
                    409, "The approved job folder has not been created."
                )
            files = self._files(command)
            if not files:
                raise HTTPException(
                    409, "Release requires the exact approved document bytes."
                )
            return {
                "revision": payload["revision"],
                "files": files,
                "status": "released_local_simulation",
            }
        if action == "sap.release_order":
            if "sales_order" not in state:
                raise HTTPException(409, "No draft sales order exists.")
            for dependency in ("released_documents", "released_revision"):
                if state.get(dependency, {}).get("package_id") != str(
                    command.package_id
                ):
                    raise HTTPException(
                        409,
                        "Publish this exact release to SharePoint and the vault first.",
                    )
            if (
                state["released_documents"]["files"]
                != state["released_revision"]["files"]
            ):
                raise HTTPException(409, "The released file manifests disagree.")
            return {
                **{
                    key: value
                    for key, value in state["sales_order"].items()
                    if key != "version"
                },
                "production_status": "released_local_simulation",
                "released_revision": payload["revision"],
                "released_files": state["released_revision"]["files"],
                "billing_authorized": False,
            }
        if action in {"teams.buyer_handoff", "schedule.confirm_material_date"}:
            buyer = command.approvals["money"]["actor"]
            if payload.get("buyer_id") != buyer:
                raise HTTPException(
                    409, "The buyer has not approved this exact package."
                )
            earliest = date.fromisoformat(payload["order_date"]) + timedelta(
                days=payload["lead_time_days"]
            )
            expected = max(earliest, date.fromisoformat(payload["current_ship_date"]))
            if payload["approved_material_date"] != expected.isoformat():
                raise HTTPException(
                    409, "The material date disagrees with its trusted lead time."
                )
            if action == "teams.buyer_handoff":
                return {
                    "message": f"{command.job_number}: buyer approved {payload['part_number']}; {payload['lead_time_days']}-day lead time; earliest material date {expected}. Production, test and transit remain unconfirmed.",
                    "buyer_id": buyer,
                    "status": "posted_to_local_buyer_channel",
                }
            if state.get("buyer_channel", {}).get("package_id") != str(
                command.package_id
            ):
                raise HTTPException(
                    409,
                    "Deliver this exact buyer handoff before updating its schedule.",
                )
            return {
                "milestone": "earliest_material_availability",
                "date": expected.isoformat(),
                "buyer_id": buyer,
                "buyer_confirmed": True,
                "customer_ship_date_committed": False,
                "basis": payload["basis"],
            }
        raise HTTPException(422, "Unsupported action.")

    def _files(self, command):
        files, total = [], 0
        for item in command.files:
            try:
                content = base64.b64decode(item.content_base64, validate=True)
            except ValueError as exc:
                raise HTTPException(422, "Invalid file encoding.") from exc
            total += len(content)
            if (
                not content
                or total > 32 * 1024 * 1024
                or sha256(content).hexdigest() != item.sha256
            ):
                raise HTTPException(
                    409, "The released bytes do not match their manifest."
                )
            destination = (
                self.objects / str(command.organization_id) / str(command.order_id)
            )
            destination.mkdir(parents=True, exist_ok=True)
            path = destination / item.sha256
            if path.exists() and path.read_bytes() != content:
                raise HTTPException(409, "An immutable published object changed.")
            path.write_bytes(content)
            files.append(
                {
                    "document_id": str(item.document_id),
                    "name": item.name,
                    "sha256": item.sha256,
                    "bytes": len(content),
                }
            )
        return files


def create_app(database, *, token, organization_ids):
    if len(token) < 24 or not organization_ids:
        raise ValueError(
            "A private local token and organization allowlist are required."
        )
    allowed = {str(UUID(value)) for value in organization_ids}
    store = Store(database)
    app = FastAPI(title="Rivet local enterprise HTTP simulator")
    app.state.store = store
    # Fault injection is a server-side test harness capability, not an HTTP API.
    app.state.fail_before_once = set()
    app.state.fail_after_once = set()

    def authorize(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Local simulator authorization required.")

    def tenant(organization_id):
        if str(organization_id) not in allowed:
            raise HTTPException(
                403, "Organization is not enabled in this local simulator."
            )

    @app.get("/health")
    def health():
        return {
            "mode": "local_http_demo",
            "status": "ready",
            "vendor_connection": False,
        }

    @app.get(
        "/v1/state/{organization_id}/{order_id}", dependencies=[Depends(authorize)]
    )
    def state(organization_id: UUID, order_id: UUID):
        tenant(organization_id)
        return {
            "mode": "local_http_demo",
            "organization_id": str(organization_id),
            "order_id": str(order_id),
            "resources": store.state(str(organization_id), str(order_id)),
        }

    @app.get(
        "/v1/actions/{organization_id}/{order_id}/{action_id}",
        dependencies=[Depends(authorize)],
    )
    def receipt(organization_id: UUID, order_id: UUID, action_id: str):
        tenant(organization_id)
        value = store.receipt(str(organization_id), str(order_id), action_id)
        if not value:
            raise HTTPException(404, "No committed local receipt.")
        return value

    @app.post("/v1/actions/{action_id}", dependencies=[Depends(authorize)])
    def apply(action_id: str, command: Command):
        tenant(command.organization_id)
        expected_id = digest(
            [
                str(command.organization_id),
                str(command.order_id),
                str(command.package_id),
                command.package_fingerprint,
                command.action,
            ]
        )
        if action_id != expected_id:
            raise HTTPException(409, "Action identity does not match the package.")
        if command.action in app.state.fail_before_once:
            app.state.fail_before_once.remove(command.action)
            raise HTTPException(503, "Injected local failure before application.")
        result = store.apply(action_id, command)
        if command.action in app.state.fail_after_once:
            app.state.fail_after_once.remove(command.action)
            raise HTTPException(
                503, "Injected lost response after durable application."
            )
        return result

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5188)
    args = parser.parse_args()
    import uvicorn

    app = create_app(
        args.database,
        token=os.environ["RIVET_LOCAL_ACTIONS_TOKEN"],
        organization_ids=os.environ["RIVET_LOCAL_ACTIONS_ORGANIZATIONS"].split(","),
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
