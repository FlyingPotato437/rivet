"""Add one isolated fictional equipment check to an explicitly selected team.

No existing order, Outlook thread, approval or external service is changed.
The original files are generated independently of the equipment extractor.
"""

import argparse
import json
from pathlib import Path

from backend.domain.service import get, scoped
from backend.equipment import service
from backend.equipment.demo_documents import build_demo_corpus
from backend.identity import Identity, identity_scope
from backend.ingestion.parser import parse
from backend.orders import service as orders
from backend.storage.db import Session
from backend.storage.models import Document, Job, Order, Project


def seed(organization_id, root):
    from backend.api.main import add_document

    manifest = build_demo_corpus(root)
    selected = [
        "requirements.pdf",
        *[
            f"{folder}.{kind}"
            for folder in ("01-baseline-C", "02-partial-D", "03-complete-E")
            for kind in ("drawing.pdf", "drawing.dxf", "bom.xlsx")
        ],
        "response.markup",
    ]
    originals = {item["id"]: item for item in manifest["sources"]}
    with (
        identity_scope(
            Identity(organization_id, "Rivet fictional equipment setup", "org:admin")
        ),
        Session.begin() as s,
    ):
        order = s.scalar(scoped(Order).where(Order.number == "RVT-EQUIPMENT-261017"))
        if order is None:
            order = orders.new_order(
                s,
                {
                    "title": "Switchboard revision check · drawing and BOM",
                    "customer": "Fictional Northline Water Reclamation",
                    "number": "RVT-EQUIPMENT-261017",
                    "synthetic": True,
                    "category": "Low-voltage switchboard",
                },
            )
            for key in selected:
                item = originals[key]
                data = (root / item["path"]).read_bytes()
                role = item["role"]
                kind = {"drawing": "approval_drawing", "markup": "markups"}.get(
                    role, role
                )
                metadata = {
                    "order_role": kind,
                    "revision_label": item["revision"],
                    "equipment_package": {"role": role, "revision": item["revision"]},
                    "synthetic": True,
                }
                if item["name"].endswith(".dxf"):
                    service.add_native_dxf(s, order, item["name"], data, metadata)
                else:
                    result = add_document(
                        s,
                        get(s, Project, order.project_id),
                        item["name"],
                        kind,
                        data,
                        metadata,
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
        partial = service.save_check(s, order, "C", "D")
        complete = service.view(s, order, "C", "E")
        output = {
            "order_id": order.id,
            "url": f"https://localhost:5179/#order/{order.id}",
            "fictional": True,
            "partial": partial["comparison"]["summary"],
            "complete": complete["comparison"]["summary"],
            "sent_mail": False,
            "production_release": False,
        }
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace-demo.json").write_text(json.dumps(output, indent=2) + "\n")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("output/equipment-revision-demo")
    )
    args = parser.parse_args()
    print(json.dumps(seed(args.organization_id, args.output), indent=2))
