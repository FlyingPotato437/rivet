"""Seed isolated fictional native-revision orders for the real equipment worker."""

import argparse
import json
from pathlib import Path

from backend.domain.service import get, scoped
from backend.equipment import service, workflow
from backend.identity import Identity, identity_scope
from backend.ingestion.parser import parse
from backend.orders import service as orders
from backend.storage.db import Session
from backend.storage.models import Document, Job, Order, Project


def seed(organization_id, root, cases):
    from backend.api.main import add_document

    manifest = json.loads((root / "manifest.json").read_text())
    sources = {s["id"]: s for s in manifest["sources"]}
    outcomes = []
    for case in manifest["cases"]:
        if int(case["id"].split("-", 1)[0]) not in cases:
            continue
        number = "RVT-NATIVE-261018-" + case["id"].split("-", 1)[0]
        with (
            identity_scope(
                Identity(
                    organization_id,
                    "Rivet fictional native revision setup",
                    "org:admin",
                )
            ),
            Session.begin() as s,
        ):
            order = s.scalar(scoped(Order).where(Order.number == number))
            if order is None:
                order = orders.new_order(
                    s,
                    {
                        "title": case["title"],
                        "customer": "Fictional Northline Water Reclamation",
                        "number": number,
                        "synthetic": True,
                        "category": "Switchboard control cubicle",
                    },
                )
                for side in ("baseline", "target", "requirements"):
                    for key in case[side]:
                        item = sources[key]
                        data = (root / item["path"]).read_bytes()
                        role = item["role"]
                        # A returned change schedule belongs to drawing revision
                        # D, independently of its own printed CR issue number.
                        revision = "D" if role == "markup" else item["revision"]
                        meta = {
                            "order_role": "markups"
                            if role == "markup"
                            else "approval_drawing"
                            if role == "drawing"
                            else role,
                            "revision_label": revision,
                            "equipment_package": {"role": role, "revision": revision},
                            "synthetic": True,
                            "native_scenario": case["id"],
                            "source_issue_revision": item["revision"],
                        }
                        if item["name"].endswith(".dxf"):
                            service.add_native_dxf(s, order, item["name"], data, meta)
                        else:
                            doc = add_document(
                                s,
                                get(s, Project, order.project_id),
                                item["name"],
                                meta["order_role"],
                                data,
                                meta,
                            )
                            document = get(s, Document, doc["id"])
                            if document.state == "queued":
                                parse(s, document)
                                for job in s.scalars(
                                    scoped(Job).where(
                                        Job.project_id == order.project_id,
                                        Job.kind == "parse",
                                        Job.status == "queued",
                                    )
                                ):
                                    if job.payload.get("document_id") == document.id:
                                        job.status = "succeeded"
                                orders.refresh(s, order)
            service.save_check(s, order, "C", "D")
            view = workflow.view(s, order)
            outcomes.append(
                {
                    "case": case["id"],
                    "order_id": order.id,
                    "number": order.number,
                    "url": f"http://localhost:5178/#order/{order.id}",
                    "plan_status": view["plan"]["status"],
                    "planned_changes": len(view["plan"]["edits"]),
                    "automation": view["automation"],
                    "blockers": view["plan"]["blockers"],
                    "fictional": True,
                    "sent_mail": False,
                }
            )
    (root / "workspace-orders.json").write_text(json.dumps(outcomes, indent=2) + "\n")
    return outcomes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument(
        "--root", type=Path, default=Path("output/native-revision-scenarios")
    )
    parser.add_argument("--cases", default="1,2,3,4,5,13,14")
    args = parser.parse_args()
    print(
        json.dumps(
            seed(
                args.organization_id, args.root, {int(v) for v in args.cases.split(",")}
            ),
            indent=2,
        )
    )
