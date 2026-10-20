"""Seed one fictional second revision from a real previously corrected bundle.

Run with ``python -m scripts.seed_native_followup_demo --organization-id ...``.
The seed only uploads reviewed source copies and queues the existing durable
correction worker. It never sends mail, edits the source order or releases work.
"""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

NUMBER = "RVT-NATIVE-261018-15"
TITLE = "Rev E follow-up · prior wiring retained"
SOURCE_ORDER_ID = "6fdb2727-b444-44f5-94cd-f56380dbf769"
BASELINE_REVISION = "D-R1"
TARGET_REVISION = "E"


def _write_identical(path, data):
    """Never overwrite a different local payload from an earlier setup."""
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise ValueError(
                f"A different local setup file already exists: {path.name}"
            )
        return
    with path.open("xb") as output:
        output.write(data)


def build_inputs(output_root, drawing_bytes, bom_bytes, provenance, *, resize=True):
    """Pure file preparation; no database, Microsoft or worker access."""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.pdfgen import canvas

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    drawing_hash, bom_hash = (
        sha256(drawing_bytes).hexdigest(),
        sha256(bom_bytes).hexdigest(),
    )
    inputs = {side: [] for side in ("baseline", "target", "requirements")}
    for side, revision in (
        ("baseline", BASELINE_REVISION),
        ("target", TARGET_REVISION),
    ):
        for role, suffix, data in (
            ("drawing", ".dxf", drawing_bytes),
            ("bom", ".xlsx", bom_bytes),
        ):
            path = root / f"{NUMBER}_{side}_{revision}_{role}{suffix}"
            _write_identical(path, data)
            inputs[side].append(
                {
                    "id": f"{NUMBER}:{side}:{role}",
                    "name": path.name,
                    "path": str(path),
                    "role": role,
                    "revision": revision,
                    "sha256": sha256(data).hexdigest(),
                    "lifecycle": "draft",
                }
            )
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(1191, 842), invariant=1)
    pdf.setTitle(f"{NUMBER} - fictional returned change schedule E")
    pdf.setFillColor(colors.HexColor("#18242F"))
    pdf.rect(0, 729, 1191, 113, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(40, 800, "RIVET / FICTIONAL FOLLOW-UP / RETURNED CHANGE SCHEDULE E")
    pdf.setFont("Helvetica", 23)
    pdf.drawString(40, 759, "Rev E follow-up - retain the prior corrected trip wiring")
    pdf.setFillColor(colors.HexColor("#18242F"))
    pdf.setFont("Helvetica", 12)
    lines = [
        f"Order {NUMBER} / Fictional Northline Water Reclamation / control cubicle E-21",
        f"Reviewed source: order {provenance['order_id']}, bundle {provenance['bundle_id']}.",
        "Baseline D-R1 and candidate E contain the exact same previously corrected native drawing and BOM bytes.",
        "Retain all previous trip rewiring and every other device, conductor identity and inventory row.",
        "Only the source-cited relay changes below are requested. Prepare a new draft, matching schedules and preview.",
    ]
    for index, line in enumerate(lines):
        pdf.drawString(40, 689 - index * 25, line)
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(40, 513, "EXPLICIT CHANGES")
    pdf.setFont("Courier", 12)
    pdf.drawString(45, 476, "CHANGE C01: Move KA-1 to (500, 235) mm.")
    if resize:
        pdf.drawString(45, 443, "CHANGE C02: Resize KA-1 to 60 x 45 mm.")
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(40, 357, "EXACT REVIEWED SOURCE BYTES")
    pdf.setFont("Courier", 10)
    pdf.drawString(40, 330, "DXF SHA-256: " + drawing_hash)
    pdf.drawString(40, 307, "BOM SHA-256: " + bom_hash)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(
        40,
        222,
        "Both location and size are fictional explicit test requests, not inferred engineering recommendations.",
    )
    pdf.drawString(
        40,
        198,
        "Preserve original files and previous schedule revisions. New schedules must identify the new saved DXF hash.",
    )
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(
        40,
        100,
        "FICTIONAL TEST DATA / ENGINEERING REVIEW REQUIRED / NOT FOR FABRICATION OR RELEASE",
    )
    pdf.save()
    path = root / f"{NUMBER}_returned-change_E.pdf"
    request_bytes = buffer.getvalue()
    _write_identical(path, request_bytes)
    inputs["requirements"].append(
        {
            "id": f"{NUMBER}:returned-change",
            "name": path.name,
            "path": str(path),
            "role": "markup",
            "revision": TARGET_REVISION,
            "sha256": sha256(request_bytes).hexdigest(),
            "lifecycle": "draft",
        }
    )
    return {
        "number": NUMBER,
        "title": TITLE,
        "fictional": True,
        "source_bundle": {
            **provenance,
            "drawing_sha256": drawing_hash,
            "bom_sha256": bom_hash,
        },
        "baseline_revision": BASELINE_REVISION,
        "target_revision": TARGET_REVISION,
        "requested_resize_mm": [60, 45] if resize else None,
        "inputs": inputs,
        "sent_mail": False,
        "production_release": False,
    }


def _existing_report(order):
    automation = order.state.get("equipment_corrections", {}).get("automation", {})
    return {
        "number": order.number,
        "order_id": order.id,
        "already_exists": True,
        "url": f"http://localhost:5178/#order/{order.id}",
        "automation": automation,
        "files_uploaded": 0,
        "fictional": True,
        "sent_mail": False,
        "production_release": False,
    }


def seed(organization_id, output_root, *, resize=True, source_order_id=SOURCE_ORDER_ID):
    from copy import deepcopy

    from backend.api.main import add_document
    from backend.domain.service import get, scoped
    from backend.equipment import service, workflow
    from backend.equipment.corrections import plan_corrections
    from backend.identity import Identity, identity_scope
    from backend.ingestion.parser import parse
    from backend.orders import service as orders
    from backend.storage.db import Session
    from backend.storage.models import Document, Job, Order, Project

    with (
        identity_scope(
            Identity(organization_id, "Rivet fictional follow-up setup", "org:admin")
        ),
        Session.begin() as session,
    ):
        existing = session.scalar(scoped(Order).where(Order.number == NUMBER))
        if existing is not None:
            return _existing_report(existing)
        source_order = get(session, Order, source_order_id)
        source_project = get(session, Project, source_order.project_id)
        if not source_project.synthetic:
            raise ValueError(
                "The follow-up setup requires an explicitly fictional source order."
            )
        source_state, source_version = (
            deepcopy(source_order.state),
            source_order.version,
        )
        bundles = source_order.state.get("equipment_corrections", {}).get("bundles", [])
        bundle = next(
            (
                b
                for b in bundles
                if b.get("target_revision") == BASELINE_REVISION
                and b.get("attachable") is True
                and b.get("native_verification", {}).get("status") == "verified"
            ),
            None,
        )
        if bundle is None:
            raise ValueError(
                "The source order has no verified native D-R1 correction bundle."
            )
        selected, source_paths = {}, []
        for role, extension in (("drawing", ".dxf"), ("bom", ".xlsx")):
            candidates = [
                a
                for a in bundle["artifacts"]
                if a["role"] == role and a["name"].lower().endswith(extension)
            ]
            if len(candidates) != 1:
                raise ValueError(
                    "The source bundle needs one unambiguous native drawing and XLSX BOM."
                )
            path, artifact = workflow.artifact(
                session, source_order, bundle["id"], candidates[0]["name"]
            )
            data = path.read_bytes()
            if sha256(data).hexdigest() != artifact["sha256"]:
                raise ValueError(
                    "The reviewed source artifact changed while being copied."
                )
            selected[role] = data
            source_paths.append((path, artifact["sha256"]))
        manifest = build_inputs(
            output_root,
            selected["drawing"],
            selected["bom"],
            {
                "order_id": source_order.id,
                "bundle_id": bundle["id"],
                "revision": BASELINE_REVISION,
            },
            resize=resize,
        )
        inputs = manifest["inputs"]
        pure_plan = plan_corrections(
            inputs["baseline"], inputs["target"], inputs["requirements"]
        )
        if pure_plan["status"] != "ready":
            raise ValueError(
                "The follow-up source requests are blocked: "
                + json.dumps(pure_plan["blockers"])
            )
        order = orders.new_order(
            session,
            {
                "title": TITLE,
                "customer": "Fictional Northline Water Reclamation",
                "number": NUMBER,
                "synthetic": True,
                "category": "Switchboard control cubicle",
            },
        )
        project = get(session, Project, order.project_id)
        uploaded = []
        for side in ("baseline", "target", "requirements"):
            for item in inputs[side]:
                role, revision = item["role"], item["revision"]
                order_role = (
                    "markups"
                    if role == "markup"
                    else "approval_drawing"
                    if role == "drawing"
                    else "bom"
                )
                meta = {
                    "order_role": order_role,
                    "revision_label": revision,
                    "equipment_package": {"role": role, "revision": revision},
                    "synthetic": True,
                    "native_scenario": "15-sequential-followup",
                    "source_issue_revision": TARGET_REVISION
                    if role == "markup"
                    else BASELINE_REVISION,
                    "native_followup": manifest["source_bundle"],
                }
                data = Path(item["path"]).read_bytes()
                if sha256(data).hexdigest() != item["sha256"]:
                    raise ValueError(
                        "A prepared follow-up upload changed before registration."
                    )
                if item["name"].endswith(".dxf"):
                    document = service.add_native_dxf(
                        session, order, item["name"], data, meta
                    )
                else:
                    result = add_document(
                        session, project, item["name"], order_role, data, meta
                    )
                    document = get(session, Document, result["id"])
                    if document.state == "queued":
                        parse(session, document)
                        for job in session.scalars(
                            scoped(Job).where(
                                Job.project_id == order.project_id,
                                Job.kind == "parse",
                                Job.status == "queued",
                            )
                        ):
                            if job.payload.get("document_id") == document.id:
                                job.status = "succeeded"
                        orders.refresh(session, order)
                uploaded.append(document.id)
        service.save_check(session, order, BASELINE_REVISION, TARGET_REVISION)
        job = workflow.schedule(session, order)
        if job is None:
            raise ValueError(
                "The follow-up did not schedule its durable correction job."
            )
        view = workflow.view(session, order)
        if view["plan"]["status"] != "ready":
            raise ValueError(
                "The stored follow-up sources did not retain their executable native plan."
            )
        if source_order.state != source_state or source_order.version != source_version:
            raise ValueError("The source order unexpectedly changed during setup.")
        if any(
            sha256(path.read_bytes()).hexdigest() != checksum
            for path, checksum in source_paths
        ):
            raise ValueError(
                "The original corrected source bytes changed during setup."
            )
        result = {
            "number": NUMBER,
            "title": TITLE,
            "order_id": order.id,
            "url": f"http://localhost:5178/#order/{order.id}",
            "already_exists": False,
            "files_uploaded": len(uploaded),
            "plan_status": view["plan"]["status"],
            "planned_changes": len(view["plan"]["edits"]),
            "automation": view["automation"],
            "job_id": job.id,
            "source_bundle": manifest["source_bundle"],
            "fictional": True,
            "sent_mail": False,
            "production_release": False,
        }
    root = Path(output_root).resolve()
    (root / "followup-inputs.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (root / "workspace-followup.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument(
        "--output-root", type=Path, default=Path("output/native-revision-followup")
    )
    parser.add_argument("--source-order-id", default=SOURCE_ORDER_ID)
    parser.add_argument(
        "--without-resize",
        action="store_true",
        help="Request only the relay move; omit the explicit 60 x 45 mm resize.",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            seed(
                args.organization_id,
                args.output_root,
                resize=not args.without_resize,
                source_order_id=args.source_order_id,
            ),
            indent=2,
        )
    )
