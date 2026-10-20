"""One labelled fictional manufacturer-rule run through the normal worker.

The company review below is explicitly a demo review, never a real customer's
engineering authorization. This setup sends no mail and preserves prior demos.
"""

import argparse
import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from backend.domain.service import get, scoped
from backend.equipment import profile_service, service, workflow
from backend.identity import Identity, identity_scope
from backend.ingestion.parser import parse
from backend.orders import service as orders
from backend.storage.db import Session
from backend.storage.models import Document, Job, Order, Project

NUMBER = "RVT-NATIVE-261018-16"
QUOTES = [
    "KA-1 and ALARM-RELAY-1 identify the same alarm relay.",
    "KA-1 center must be at or below 600 mm on the panel X axis.",
    "Wire W201 connects TB-1 terminal 4 to KA-1 terminal A1.",
]


def guide_bytes():
    stream = BytesIO()
    pdf = Canvas(stream, pagesize=(842, 595), invariant=1)
    pdf.setTitle("Fictional control panel conventions - demo review only")
    pdf.setFillColorRGB(0.08, 0.1, 0.13)
    pdf.rect(0, 0, 842, 595, fill=1, stroke=0)
    pdf.setFillColorRGB(0.77, 0.72, 0.86)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(45, 542, "RIVET / FICTIONAL MANUFACTURER REFERENCE / DEMO-1")
    pdf.setFillColorRGB(0.94, 0.95, 0.96)
    pdf.setFont("Helvetica-Bold", 26)
    pdf.drawString(45, 483, "Control cubicle conventions")
    pdf.setFont("Helvetica", 13)
    pdf.drawString(
        45, 448, "Fictional example. Not a customer guide or engineering approval."
    )
    for index, text in enumerate(QUOTES):
        y = 364 - index * 78
        pdf.setFillColorRGB(0.77, 0.72, 0.86)
        pdf.setFont("Helvetica", 11)
        pdf.drawString(45, y, f"0{index + 1}")
        pdf.setFillColorRGB(0.94, 0.95, 0.96)
        pdf.setFont("Helvetica", 15)
        pdf.drawString(83, y, text)
    pdf.setFont("Helvetica", 10)
    pdf.setFillColorRGB(0.65, 0.69, 0.74)
    pdf.drawString(
        45,
        48,
        "Demo company review validates only these explicit example rules. No production release.",
    )
    pdf.save()
    return stream.getvalue()


def seed(organization_id, root):
    from backend.api.main import add_document

    manifest = json.loads((root / "manifest.json").read_text())
    case = next(c for c in manifest["cases"] if c["id"].startswith("02-"))
    sources = {s["id"]: s for s in manifest["sources"]}
    with (
        identity_scope(
            Identity(organization_id, "Fictional demo company reviewer", "org:admin")
        ),
        Session.begin() as s,
    ):
        order = s.scalar(scoped(Order).where(Order.number == NUMBER))
        if order:
            return {
                "order_id": order.id,
                "number": NUMBER,
                "existing": True,
                "url": f"http://localhost:5178/#order/{order.id}",
            }
        order = orders.new_order(
            s,
            {
                "title": "Company rules · relay relocation and connection check",
                "number": NUMBER,
                "customer": "Fictional Northline Water Reclamation",
                "synthetic": True,
                "category": "Switchboard control cubicle",
            },
        )
        project = get(s, Project, order.project_id)
        for side in ("baseline", "target", "requirements"):
            for key in case[side]:
                item = sources[key]
                data = (root / item["path"]).read_bytes()
                role = item["role"]
                revision = "D" if role == "markup" else item["revision"]
                meta = {
                    "order_role": "markups"
                    if role == "markup"
                    else "approval_drawing"
                    if role == "drawing"
                    else role,
                    "revision_label": revision,
                    "synthetic": True,
                    "equipment_package": {"role": role, "revision": revision},
                }
                if item["name"].lower().endswith(".dxf"):
                    service.add_native_dxf(s, order, item["name"], data, meta)
                else:
                    created = add_document(
                        s, project, item["name"], meta["order_role"], data, meta
                    )
                    document = get(s, Document, created["id"])
                    if document.state == "queued":
                        parse(s, document)
                        orders.refresh(s, order)
        data = guide_bytes()
        reference = orders.new_order(s, {
            "title": "Company reference · fictional panel conventions",
            "number": "RVT-REFERENCE-261018-01", "customer": "Fictional demo manufacturer",
            "synthetic": True, "category": "Company reference library",
        })
        created = add_document(
            s,
            get(s, Project, reference.project_id),
            "Fictional-panel-conventions-DEMO-1.pdf",
            "specification",
            data,
            {
                "order_role": "specification",
                "revision_label": "DEMO-1",
                "synthetic": True,
                "equipment_package": {"role": "specification", "revision": "DEMO-1"},
            },
        )
        document = get(s, Document, created["id"])
        if document.state == "queued":
            parse(s, document)
            orders.refresh(s, reference)
        for job in s.scalars(
            scoped(Job).where(
                Job.project_id.in_([project.id, reference.project_id]),
                Job.kind == "parse",
                Job.status == "queued",
            )
        ):
            job.status = "succeeded"
        anchors = [
            {
                "document_id": document.id,
                "name": document.name,
                "revision": "DEMO-1",
                "role": "specification",
                "sha256": sha256(data).hexdigest(),
                "location": {"page": 1, "text": text},
            }
            for text in QUOTES
        ]
        proposed = profile_service.propose(
            s,
            {
                "profile_id": "fictional-control-cubicle",
                "manufacturer_name": "Fictional Rivet demo manufacturer",
                "name": "Fictional control cubicle conventions",
                "sources": anchors,
                "aliases": [
                    {
                        "alias_id": "alarm-relay",
                        "physical_device_id": "alarm-relay-1",
                        "tags": ["KA-1", "ALARM-RELAY-1"],
                        "source_ids": [document.id],
                    }
                ],
                "rules": [
                    {
                        "rule_id": "relay-position",
                        "label": "Alarm relay fits the company panel zone",
                        "device_tag": "ALARM-RELAY-1",
                        "source_ids": [document.id],
                        "constraint": {"op": "dimension_bounds", "x_max": 600},
                    },
                    {
                        "rule_id": "alarm-input",
                        "label": "Alarm input retains the specified terminals",
                        "device_tag": "ALARM-RELAY-1",
                        "source_ids": [document.id],
                        "constraint": {
                            "op": "terminal_pair",
                            "wire_id": "W201",
                            "from_terminal": "TB-1:4",
                            "to_terminal": "KA-1:A1",
                        },
                    },
                ],
            },
        )
        reviewed = profile_service.review(s, proposed["id"], proposed["version_hash"])
        profile_service.bind(
            s,
            order,
            order.version,
            reviewed["id"],
            "Explicit fictional demonstration of reviewed company conventions.",
        )
        service.save_check(s, order, "C", "D")
        view = workflow.view(s, order)
        if view["plan"]["status"] != "ready":
            raise RuntimeError(json.dumps(view["plan"]["blockers"]))
        return {
            "order_id": order.id,
            "number": NUMBER,
            "profile_id": reviewed["id"],
            "profile_version": reviewed["version_hash"],
            "automation": view["automation"],
            "url": f"http://localhost:5178/#order/{order.id}",
            "fictional": True,
            "sent": False,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument(
        "--root", type=Path, default=Path("output/native-revision-scenarios")
    )
    args = parser.parse_args()
    result = seed(args.organization_id, args.root)
    (args.root / "company-rules-demo.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result, indent=2))
