"""Reproduce approved actions over real HTTP in an isolated test database.

Explicit fictional human decisions are required. The loopback simulator persists
its own enterprise state and published bytes. No live database, vendor system
or customer mailbox is used.
"""

import argparse
import json
import os
import secrets
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import uvicorn

from backend.api import (
    main as _models_loaded,  # noqa: F401 - register all schema models
)
from backend.domain.service import get, scoped
from backend.identity import Identity, current_org, identity_scope
from backend.lifecycle.completion_replay import complete
from backend.storage.db import Session, engine
from backend.storage.models import Base, Job, Order
from backend.worker import claim, process

from .client import LocalClient, configuration
from .local_server import create_app

BUYER = "Fictional buyer and customer-schedule reviewer - isolated simulation"


@contextmanager
def local_environment(database, organization_id):
    token = secrets.token_urlsafe(40)
    app = create_app(database, token=token, organization_ids={organization_id})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("The isolated local HTTP simulator did not start.")
    values = {
        "RIVET_LOCAL_ACTIONS_AUTOMATIC": "true",
        "RIVET_LOCAL_ACTIONS_URL": f"http://127.0.0.1:{sock.getsockname()[1]}",
        "RIVET_LOCAL_ACTIONS_TOKEN": token,
        "RIVET_LOCAL_ACTIONS_ORGANIZATIONS": organization_id,
        "RIVET_LOCAL_ACTIONS_BUYERS": json.dumps({organization_id: [BUYER]}),
    }
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield app
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        server.should_exit = True
        thread.join(5)
        sock.close()


def run(database):
    if not (engine.url.database or "").startswith("rivet_test_"):
        raise RuntimeError(
            "HTTP completion replay requires a dedicated rivet_test_* database."
        )
    Base.metadata.create_all(engine)
    with local_environment(database, current_org()) as app:
        completed = complete()
        with Session() as s:
            order = get(s, Order, completed["order_id"])
            snapshot_hash, version = order.snapshot_hash, order.version
        # This is the ordinary durable worker, not direct endpoint execution.
        # Approval queued the outbox; the replay supplies no new approval powers.
        processed = 0
        while task := claim(kinds=("workflow_actions",)):
            process(task)
            processed += 1
            if processed > 20:
                raise AssertionError("Unexpected unbounded action replay.")
        with Session() as s:
            order = get(s, Order, completed["order_id"])
            if order.snapshot_hash != snapshot_hash or order.version != version:
                raise AssertionError(
                    "Operational execution modified the signed package snapshot."
                )
            jobs = list(
                s.scalars(
                    scoped(Job).where(
                        Job.project_id == order.project_id,
                        Job.kind == "workflow_actions",
                    )
                )
            )
            if len(jobs) != 4 or any(job.status != "succeeded" for job in jobs):
                raise AssertionError(
                    "The four approved stages did not finish their local HTTP work."
                )
        remote = LocalClient(configuration()).state(
            current_org(), completed["order_id"]
        )
        with app.state.store.connect() as connection:
            receipts = [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT receipt FROM actions WHERE order_id=? ORDER BY rowid",
                    (completed["order_id"],),
                )
            ]
        if len(receipts) != 8:
            raise AssertionError(
                "The exact eight enterprise actions were not observed over HTTP."
            )
        return {
            "mode": "isolated_local_http_simulation",
            "order_id": completed["order_id"],
            "organization_id": current_org(),
            "job": completed["job"],
            "database": engine.url.database,
            "simulator_database": str(app.state.store.path),
            "published_object_directory": str(app.state.store.objects),
            "verified_comments": completed["verified_comments"],
            "automatic_approval_outbox": True,
            "worker_jobs_succeeded": len(jobs),
            "http_effects": len(receipts),
            "receipts": receipts,
            "resources": remote["resources"],
            "signed_snapshot_unchanged": True,
            "vendor_writes": False,
            "customer_mail_sent": False,
            "server_stopped_when_cli_returns": True,
            "notice": "Actual network requests and durable local enterprise changes using explicit fictional human approvals. This is not a live SAP, SharePoint, vault or Teams deployment.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=UUID)
    parser.add_argument("--server-database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--simulate-human-decisions", action="store_true", required=True
    )
    args = parser.parse_args()
    with identity_scope(
        Identity(
            str(args.organization_id) if args.organization_id else current_org(),
            "Isolated HTTP replay operator",
        )
    ):
        result = run(args.server_database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "mode",
                    "order_id",
                    "worker_jobs_succeeded",
                    "http_effects",
                    "signed_snapshot_unchanged",
                    "notice",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
