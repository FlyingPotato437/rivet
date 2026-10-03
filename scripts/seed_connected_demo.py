"""Seed a repeatable connected-review example in the existing LOCAL demo team.

No account creation, auth bypass, preapproved records, or sent messages.
The normal durable worker processes these files. Reruns preserve user edits.
"""

import json
import os

from sqlalchemy import text

from backend.api.main import add_document
from backend.domain.service import get, scoped
from backend.identity import Identity, identity_scope, tenant_id
from backend.orders.service import new_order
from backend.storage.db import ROOT, Session
from backend.storage.models import Document, Order, Project


def seed(s, directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    s.execute(text("SELECT pg_advisory_xact_lock(777006)"))
    order = s.scalar(scoped(Order).where(Order.number == manifest["number"]))
    if order is None:
        order = new_order(
            s,
            {key: manifest[key] for key in ("title", "number", "customer", "category")}
            | {"synthetic": True},
        )
    if not get(s, Project, order.project_id).synthetic:
        raise RuntimeError("Refusing to seed an existing non-demo order.")
    for item in manifest["initial_documents"]:
        if s.scalar(
            scoped(Document).where(
                Document.project_id == order.project_id, Document.name == item["file"]
            )
        ):
            continue
        add_document(
            s,
            get(s, Project, order.project_id),
            item["file"],
            "auto",
            (directory / item["file"]).read_bytes(),
            {"order_role": item["role"], "revision_label": item["revision"]},
        )
    return order.id


def main():
    from backend.demo import demo_config

    demo = demo_config()
    if not demo or os.getenv("RIVET_LOCAL_ONLY", "true") != "true":
        raise SystemExit(
            "Run scripts.setup_demo first. This seeder is restricted to the local demo team."
        )
    with (
        identity_scope(
            Identity(
                tenant_id(demo["organization_id"]),
                demo["user_id"],
                "org:admin",
                demo["organization_id"],
            )
        ),
        Session.begin() as s,
    ):
        order_id = seed(s, ROOT / "examples/connected-review")
    print("Example queued for the worker: http://127.0.0.1:5178/#order/" + order_id)
    print(
        "Revision C is kept separate for the revision-review step. No responses, approvals, or notices were sent."
    )


if __name__ == "__main__":
    main()
