"""Create an isolated Clerk development account and import public PDFs.

Run from the repo: .venv/bin/python -m scripts.setup_demo
Run prepare_public_documents.py first. The normal worker processes the imports.
Reruns preserve user edits and never copy data from another tenant.
"""

import json
import os
import time

import httpx
from dotenv import set_key
from sqlalchemy import text

from backend.api.main import add_document
from backend.demo import DEMO_EMAIL
from backend.domain.service import get, scoped
from backend.identity import Identity, identity_scope, tenant_id
from backend.orders.service import lock_order, new_order
from backend.records import service as records
from backend.storage.db import ROOT, Session
from backend.storage.models import Document, Order, Project


def seed_public_documents(s, packages, directory):
    # One transaction per tenant prevents duplicate orders on concurrent reruns.
    s.execute(text("SELECT pg_advisory_xact_lock(777004)"))
    result = []
    for package in packages:
        short = package["name"].split("-")[0]
        number = "PUBLIC-" + short.upper()
        order = s.scalar(scoped(Order).where(Order.number == number))
        if order is None:
            order = new_order(
                s,
                {
                    "title": "Alachua · switchgear drawing review"
                    if short == "alachua"
                    else "AVTA · scanned review",
                    "customer": "City of Alachua"
                    if short == "alachua"
                    else "Antelope Valley Transit Authority",
                    "number": number,
                    "category": "Public documents · practice workspace",
                    "synthetic": True,
                },
            )
        path = directory / package["excerpt"]
        name = (
            short.upper()
            + " - original PDF pages "
            + ",".join(map(str, package["pages"]))
            + ".pdf"
        )
        doc = s.scalar(
            scoped(Document).where(
                Document.project_id == order.project_id, Document.name == name
            )
        )
        if doc is None:
            add_document(
                s,
                get(s, Project, order.project_id),
                name,
                "markups",
                path.read_bytes(),
                {
                    "order_role": "customer_markups",
                    "revision_label": "Public-source excerpt",
                    "public_source": {
                        "url": package["url"],
                        "pages": package["pages"],
                        "sha256": package["sha256"],
                    },
                },
            )
        result.append(order.id)
    return result


def main():
    key = os.getenv("CLERK_SECRET_KEY", "")
    if (
        not key.startswith("sk_test_")
        or os.getenv("RIVET_LOCAL_ONLY", "true") != "true"
    ):
        raise SystemExit(
            "Demo setup requires a local workspace and Clerk DEVELOPMENT keys."
        )
    directory = ROOT / ".data/validation"
    packages = json.loads((directory / "manifest.json").read_text())
    for p in packages:
        if not (directory / p["excerpt"]).is_file():
            raise SystemExit("Run scripts/prepare_public_documents.py first.")
    with httpx.Client(
        base_url="https://api.clerk.com/v1",
        headers={"Authorization": "Bearer " + key},
        timeout=30,
    ) as clerk:

        def call(method, path, **kwargs):
            response = clerk.request(method, path, **kwargs)
            if not response.is_success:
                # Deliberately avoid logging request headers or full provider responses.
                errors = response.json().get("errors", [])
                raise RuntimeError(
                    f"Clerk {path}: {response.status_code}; "
                    + ", ".join(
                        e.get("long_message", e.get("code", "unknown")) for e in errors
                    )
                )
            return response.json()

        users = call("GET", "/users", params={"external_id": "rivet-public-demo-v1"})
        user = next(
            (u for u in users if u.get("external_id") == "rivet-public-demo-v1"), None
        )
        if user is None:
            user = call(
                "POST",
                "/users",
                json={
                    "external_id": "rivet-public-demo-v1",
                    "email_address": [DEMO_EMAIL],
                    "first_name": "Demo",
                    "last_name": "PM",
                    "skip_password_requirement": True,
                    "private_metadata": {"rivet_demo": True},
                },
            )
        if not user.get("private_metadata", {}).get("rivet_demo"):
            raise RuntimeError("Existing user is not the designated demo account.")
        memberships = call("GET", f"/users/{user['id']}/organization_memberships")[
            "data"
        ]
        org = next(
            (
                m["organization"]
                for m in memberships
                if m["organization"].get("name") == "Rivet · public demo"
            ),
            None,
        )
        if org is None:
            org = call(
                "POST",
                "/organizations",
                json={
                    "name": "Rivet · public demo",
                    "created_by": user["id"],
                    "private_metadata": {"rivet_demo": True},
                },
            )
        org = call("GET", f"/organizations/{org['id']}")
        if not org.get("private_metadata", {}).get("rivet_demo"):
            raise RuntimeError(
                "Existing organization is not the designated demo workspace."
            )
    identity = Identity(tenant_id(org["id"]), user["id"], "org:admin", org["id"])
    with identity_scope(identity):
        with Session.begin() as s:
            ids = seed_public_documents(s, packages, directory)
        # Only observe these imports; the normal worker claims/parses jobs.
        deadline = time.monotonic() + 120
        while True:
            with Session.begin() as s:
                views = [records.view(s, lock_order(s, id)) for id in ids]
            states = [d["state"] for w in views for d in w["documents"]]
            if "failed" in states:
                raise RuntimeError(
                    "A demo document failed to parse; inspect Documents."
                )
            if not any(
                state in {"queued", "parsing", "processing"} for state in states
            ):
                break
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Demo imports queued. Start the worker, then rerun setup."
                )
            time.sleep(1)
    for key, value in {
        "RIVET_DEMO_ENABLED": "true",
        "RIVET_DEMO_USER_ID": user["id"],
        "RIVET_DEMO_ORG_ID": org["id"],
    }.items():
        set_key(ROOT / ".env", key, value)
    for w in views:
        print(
            f"{w['order']['title']}: {len(w['comments'])} comments · #order/{w['order']['id']}"
        )
    print(
        "Demo ready. Restart Rivet to show Try the demo. Existing records were preserved."
    )


if __name__ == "__main__":
    main()
