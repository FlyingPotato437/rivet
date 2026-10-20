"""Create one isolated fictional correctable order; never send or release it."""

import argparse
import json
from pathlib import Path

from backend.domain.service import get, scoped
from backend.equipment import service, workflow
from backend.equipment.demo_documents import build_demo_corpus
from backend.identity import Identity, identity_scope
from backend.ingestion.parser import parse
from backend.orders import service as orders
from backend.storage.db import Session
from backend.storage.models import Document, Job, Order, Project


def seed(organization_id, root):
    from backend.api.main import add_document

    manifest = build_demo_corpus(root / "fixtures")
    originals = {item["id"]: item for item in manifest["sources"]}
    with (
        identity_scope(
            Identity(organization_id, "Rivet fictional correction setup", "org:admin")
        ),
        Session.begin() as s,
    ):
        order = s.scalar(scoped(Order).where(Order.number == "RVT-DEMO-261017"))
        if order is None:
            order = orders.new_order(
                s,
                {
                    "title": "Northline · drawing and BOM corrections",
                    "customer": "Fictional Northline Water Reclamation",
                    "number": "RVT-DEMO-261017",
                    "synthetic": True,
                    "category": "Low-voltage switchboard",
                },
            )
            for key in [
                "requirements.pdf",
                *[
                    f"{folder}.{kind}"
                    for folder in ("01-baseline-C", "04-titleblock-only")
                    for kind in ("drawing.pdf", "drawing.dxf", "bom.xlsx")
                ],
            ]:
                item = originals[key]
                data = (root / "fixtures" / item["path"]).read_bytes()
                kind = "approval_drawing" if item["role"] == "drawing" else item["role"]
                meta = {
                    "order_role": kind,
                    "revision_label": item["revision"],
                    "equipment_package": {
                        "role": item["role"],
                        "revision": item["revision"],
                    },
                    "synthetic": True,
                }
                if item["name"].endswith(".dxf"):
                    service.add_native_dxf(s, order, item["name"], data, meta)
                else:
                    result = add_document(
                        s,
                        get(s, Project, order.project_id),
                        item["name"],
                        kind,
                        data,
                        meta,
                    )
                    document = get(s, Document, result["id"])
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
        report = {
            "order_id": order.id,
            "url": f"http://localhost:5178/#order/{order.id}",
            "plan_status": view["plan"]["status"],
            "planned_changes": len(view["plan"]["edits"]),
            "fictional": True,
            "sent_mail": False,
            "production_release": False,
        }
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace-demo.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("output/equipment-correction-demo")
    )
    args = parser.parse_args()
    print(json.dumps(seed(args.organization_id, args.output), indent=2))
