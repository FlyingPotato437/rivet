"""Create reproducible fictional sources; never modify the live Rivet database."""

import argparse
import json
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas


def pdf(path, title, subtitle, lines, drawing=False):
    c = canvas.Canvas(str(path), pagesize=(792, 612), pageCompression=0)
    c.setTitle(title)
    c.setFillColor(HexColor("#10141f"))
    c.rect(0, 518, 792, 94, fill=1, stroke=0)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont("Helvetica-Bold", 23)
    c.drawString(40, 568, title)
    c.setFont("Helvetica", 11)
    c.drawString(40, 542, subtitle)
    c.setFillColor(HexColor("#17202c"))
    for i, line in enumerate(lines):
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 13)
        c.drawString(40, 472 - i * 28, line)
    if drawing:
        c.setStrokeColor(HexColor("#33465d"))
        c.setLineWidth(2)
        c.line(610, 428, 610, 354)
        c.rect(562, 294, 96, 60)
        c.line(610, 294, 610, 206)
        c.line(580, 206, 640, 206)
        c.setFont("Helvetica", 11)
        c.drawCentredString(610, 322, "CB-12")
        c.drawCentredString(610, 180, "Illustrative symbol only")
    c.setStrokeColor(HexColor("#d1d5de"))
    c.line(40, 76, 752, 76)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(HexColor("#82491e"))
    c.drawString(
        40,
        53,
        "FICTIONAL INTEGRATION DEMO — NOT AN ENGINEERING DESIGN OR CUSTOMER ORDER",
    )
    c.showPage()
    c.save()


def build(destination, organization_id, job):
    destination.mkdir(parents=True, exist_ok=True)
    pdf(
        destination / "Specification-26-24-13-Rev-2.pdf",
        "Switchgear specification",
        f"Job {job} | Section 26 24 13 | Revision 2",
        [
            "Status: Approved project specification",
            "Equipment: CB-12",
            "Required trip unit: TU-3000-DEMO",
            "Accessory: remote indication module required",
            "Requirements must be reconciled with accepted order scope.",
        ],
    )
    for revision, unit, status in [
        ("B", "TU-2500-DEMO", "Released drawing"),
        ("C", "TU-3000-DEMO", "Draft - not released"),
    ]:
        pdf(
            destination / f"Drawing-E-10-Rev-{revision}.pdf",
            f"E-10 / {status}",
            f"Job {job} | Low-voltage switchboard | Revision {revision}",
            [
                f"Lifecycle: {status}",
                "Equipment tag: CB-12",
                f"Trip unit shown: {unit}",
                "Remote indication module: not shown"
                if revision == "B"
                else "Remote indication module: proposed",
                "Drawing release is not production release.",
                "Production release must be checked in the order record.",
            ],
            drawing=True,
        )
    (destination / "Accepted-order-scope.txt").write_text(
        f"FICTIONAL INTEGRATION DEMO - NOT A COMMERCIAL OFFER\nJob: {job}\nERP order: SO-24118\nStatus: Accepted order scope\nEquipment tag: CB-12\nTrip unit: TU-2500-DEMO\nRemote indication module: excluded from accepted scope\nProduction release: HOLD - engineering reconciliation outstanding\nNo price or delivery commitment for proposed changes is recorded.\n"
    )

    def item(id, file, rev, lifecycle, family, facts, port):
        return {
            "external_id": id,
            "file": file,
            "name": file,
            "revision": rev,
            "lifecycle": lifecycle,
            "family": family,
            "facts": facts,
            "source_url": f"http://127.0.0.1:{port}/originals/{file}",
        }

    records = {
        "erp": [
            item(
                "SO-24118",
                "Accepted-order-scope.txt",
                "1",
                "accepted",
                "SO-24118",
                {
                    "CB-12.trip_unit": "TU-2500-DEMO",
                    "CB-12.remote_indication": "excluded",
                },
                8791,
            )
        ],
        "sharepoint": [
            item(
                "spec-262413",
                "Specification-26-24-13-Rev-2.pdf",
                "2",
                "approved",
                "26-24-13",
                {
                    "CB-12.trip_unit": "TU-3000-DEMO",
                    "CB-12.remote_indication": "required",
                },
                8792,
            )
        ],
        "vault": [
            item(
                "E-10-B",
                "Drawing-E-10-Rev-B.pdf",
                "B",
                "released",
                "E-10",
                {"CB-12.trip_unit": "TU-2500-DEMO"},
                8793,
            ),
            item(
                "E-10-C",
                "Drawing-E-10-Rev-C.pdf",
                "C",
                "draft",
                "E-10",
                {"CB-12.trip_unit": "TU-3000-DEMO"},
                8793,
            ),
        ],
    }
    (destination / "catalog.json").write_text(
        json.dumps({"jobs": {job: records}}, indent=2)
    )
    connectors = [
        {
            "key": kind,
            "label": label,
            "kind": kind,
            "mode": "local_demo",
            "adapter": "rivet_readonly_v1",
            "base_url": f"http://127.0.0.1:{port}",
        }
        for kind, label, port in [
            ("erp", "ERP · accepted scope", 8791),
            ("sharepoint", "Specification library", 8792),
            ("vault", "Drawing vault", 8793),
        ]
    ]
    (destination / "connections.json").write_text(
        json.dumps(
            {
                "organizations": {
                    organization_id: {
                        "jobs": {
                            job: {"connectors": connectors, "auto_prepare_reply": True}
                        }
                    }
                }
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--job", default="DEMO-OUTLOOK-001")
    parser.add_argument("--output", type=Path, default=Path(".data/connected-demo"))
    args = parser.parse_args()
    build(args.output, args.organization_id, args.job)
