"""Controlled pilot rehearsal using existing Rivet services; no customer sends.

Seed A, send a fictional email from Outlook, capture, then load B and finish.
Only this order's jobs are executed. Originals and prior rehearsals are retained.
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.identity import Identity, identity_scope
from backend.storage import db

ORG = "6151b990-82f1-57d4-a559-df777cb047af"
CONNECTION = "1b23382a-47a9-4c7b-b7ec-10a6f5bb1a3f"
os.environ["RIVET_ENGINEERING_VISION_ENABLED"] = "false"
os.environ["RIVET_AGENT_ENABLED"] = "false"
os.environ["RIVET_VISION_MODEL"] = "gpt-6.1-sol"
os.environ["OPENAI_MODEL"] = "gpt-6.1-sol"

from backend.domain.service import get, scoped
from backend.equipment import service, workflow
from backend.ingestion.parser import parse
from backend.mailbox.models import MailboxFolder, MailboxReceipt
from backend.orders import service as orders
from backend.storage.models import Document, Job, Order, Project, now


def output(path, data):
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")
    print(json.dumps(data, default=str), flush=True)


def existing(s, job):
    return s.scalar(scoped(Order).where(Order.number == job))


def add(s, order, path, role, revision):
    from backend.api.main import add_document

    meta = {
        "order_role": role,
        "revision_label": revision,
        "equipment_package": {
            "role": "drawing" if role == "approval_drawing" else role,
            "revision": revision,
        },
        "synthetic": True,
    }
    from hashlib import sha256

    prior = s.scalar(
        scoped(Document).where(
            Document.project_id == order.project_id,
            Document.sha256 == sha256(path.read_bytes()).hexdigest(),
        )
    )
    if prior:
        service.register_existing(
            s, order, [prior.id], meta["equipment_package"]["role"], revision
        )
        return {"id": prior.id}
    if path.suffix == ".dxf":
        return service.add_native_dxf(s, order, path.name, path.read_bytes(), meta)
    return add_document(
        s, get(s, Project, order.project_id), path.name, role, path.read_bytes(), meta
    )


def parse_pending(order_id):
    with db.Session.begin() as s:
        order = get(s, Order, order_id)
        for d in s.scalars(
            scoped(Document).where(
                Document.project_id == order.project_id, Document.state == "queued"
            )
        ):
            parse(s, d)
        for j in s.scalars(
            scoped(Job).where(
                Job.project_id == order.project_id,
                Job.kind == "parse",
                Job.status == "queued",
            )
        ):
            j.status = "succeeded"
        orders.refresh(s, order)


def seed(root, job, folder):
    with db.Session.begin() as s:
        order = existing(s, job)
        if order:
            raise ValueError("Use a fresh rehearsal job; no old order is overwritten.")
        order = orders.new_order(
            s,
            {
                "title": f"Portable PDU · rehearsal {job[-1]}",
                "customer": "Fictional commissioning customer",
                "number": job,
                "synthetic": True,
                "category": "Portable power distribution",
            },
        )
        for name, role, revision in [
            ("PDU-75_Rev-A.pdf", "approval_drawing", "A"),
            ("PDU-75_Rev-A.dxf", "approval_drawing", "A"),
            ("PDU-75_BOM_Rev-A.xlsx", "bom", "A"),
            ("PDU-75_customer-requirements.pdf", "specification", "R1"),
        ]:
            add(s, order, folder / name, role, revision)
        oid = order.id
    parse_pending(oid)
    output(
        root / "state.json",
        {
            "order_id": oid,
            "job": job,
            "url": f"http://127.0.0.1:5178/#order/{oid}",
            "fictional": True,
        },
    )


def capture(root, job):
    from backend.mailbox.service import process_receipt, sync_folder

    with db.Session.begin() as s:
        f = s.scalar(
            scoped(MailboxFolder).where(MailboxFolder.connection_id == CONNECTION)
        )
        f.next_poll_at = now() - timedelta(seconds=1)
        fid = f.id
    synced = sync_folder(fid)
    with db.Session() as s:
        receipts = [
            r.id
            for r in s.scalars(
                scoped(MailboxReceipt).where(
                    MailboxReceipt.connection_id == CONNECTION,
                    MailboxReceipt.status.in_(["pending", "retry"]),
                )
            )
            if job in json.dumps(r.data)
        ]
    results = [process_receipt(rid) for rid in receipts]
    from backend.records.intake import reinspect_pending

    with db.Session.begin() as s:
        for r in s.scalars(
            scoped(MailboxReceipt).where(
                MailboxReceipt.connection_id == CONNECTION,
                MailboxReceipt.status == "needs_confirmation",
            )
        ):
            if job in json.dumps(r.data) and r.intake_id:
                reinspect_pending(s, r.intake_id)
    with db.Session() as s:
        order = existing(s, job)
        oid = order.id
    parse_pending(oid)
    # The returned review copy is its own review issue, not an issued native
    # drawing revision. CAD corrections use the customer's separate literal
    # requirement specification. Never promote unreviewed raster notes to CAD.
    with db.Session.begin() as s:
        o = get(s, Order, oid)
        returned = [
            d.id
            for d in s.scalars(
                scoped(Document).where(Document.project_id == o.project_id)
            )
            if "customer-markup" in d.name and not d.meta.get("equipment_package")
        ]
        if returned:
            service.register_existing(s, o, returned, "markup", "A-return")
    output(
        root / "capture.json",
        {"sync": synced, "receipts": receipts, "results": results, "order_id": oid},
    )


def revision(root, job, folder):
    with db.Session.begin() as s:
        order = existing(s, job)
        for name, role in [
            ("PDU-75_Rev-B.pdf", "approval_drawing"),
            ("PDU-75_Rev-B.dxf", "approval_drawing"),
            ("PDU-75_BOM_Rev-B.xlsx", "bom"),
        ]:
            add(s, order, folder / name, role, "B")
        oid = order.id
    parse_pending(oid)
    with db.Session.begin() as s:
        order = get(s, Order, oid)
        report = service.save_check(s, order, "A", "B")
        p = workflow.plan(s, order)[0]
        output(root / "revision-check.json", report)
        output(root / "correction-plan.json", p)


def message(root, job, folder, revb=False):
    from backend.autopilot import outlook as mail
    from backend.mailbox.models import MailboxConnection
    from backend.mailbox.service import client_for, connection_snapshot

    saved = root / ("revision-message.json" if revb else "customer-message.json")
    if saved.exists():
        print(saved.read_text(), flush=True)
        return

    with db.Session() as s:
        c = connection_snapshot(get(s, MailboxConnection, CONNECTION))
    graph = client_for(c)
    try:
        if revb:
            with db.Session() as s:
                o = existing(s, job)
                from backend.storage.models import RecordIntake

                receipt = next(
                    r
                    for r in s.scalars(
                        scoped(MailboxReceipt).where(
                            MailboxReceipt.connection_id == CONNECTION
                        )
                    )
                    if r.intake_id
                    and get(s, RecordIntake, r.intake_id).order_id == o.id
                )
                original_id = receipt.provider_message_id
            d = mail.create_reply(graph, original_id)
            mail._call(
                graph,
                "PATCH",
                mail._url(graph, "messages", d["id"]),
                {
                    "subject": f"Job {job} / Rev B updated drawings / FICTIONAL DEMO",
                    "body": {
                        "contentType": "Text",
                        "content": f"Fictional demo only. Job {job}, Rev B attached. Please check all five returned comments against the actual drawing and BOM. No production release is requested.",
                    },
                },
            )
            paths = [folder / "PDU-75_Rev-B.pdf", folder / "PDU-75_BOM_Rev-B.xlsx"]
        else:
            d = mail._call(
                graph,
                "POST",
                mail._url(graph, "messages"),
                {
                    "subject": f"Job {job} / Rev A returned comments / FICTIONAL DEMO",
                    "toRecipients": [{"emailAddress": {"address": c.mailbox}}],
                    "body": {
                        "contentType": "Text",
                        "content": f"Fictional demo only; not a customer engineering instruction.\n\nPlease review the five returned comments for job {job}, Rev A. Check the drawing and BOM against the customer requirements already filed to the order. Prepare a cited response. Do not release for production.\n\nFictional commissioning reviewer",
                    },
                },
            )
            paths = [folder / "PDU-75_Rev-A_customer-markup.pdf"]
        for path in paths:
            content_type = (
                "application/pdf"
                if path.suffix == ".pdf"
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            mail.attach(
                graph,
                d["id"],
                path.name,
                content_type,
                base64.b64encode(path.read_bytes()).decode(),
            )
        _, target = mail.open_target(graph, d["id"])
        output(
            root / ("revision-message.json" if revb else "customer-message.json"),
            {
                "id": d["id"],
                "url": target["outlook_url"],
                "fictional": True,
                "to": c.mailbox,
                "sent": False,
            },
        )
    finally:
        graph.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "phase", choices=["seed", "message", "message-b", "capture", "revision"]
    )
    p.add_argument("--run", type=int, required=True)
    a = p.parse_args()
    root = ROOT / f"output/pilot-demo-20261019/rehearsal-{a.run}"
    root.mkdir(parents=True, exist_ok=True)
    folder = root / "package"
    job = f"PDU-DEMO-261019-{a.run}"
    with identity_scope(Identity(ORG, "Rivet pilot rehearsal", "org:admin")):
        if a.phase == "seed":
            from scripts.pilot_demo.build_package import build

            build(folder, job)
            artifact = ROOT / "tmp/pilot-demo-20261019/build_boms.mjs"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "scripts/pilot_demo/build_boms.mjs", artifact)
            node = (
                Path.home()
                / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
            )
            modules = artifact.parent / "node_modules"
            if not modules.exists():
                modules.symlink_to(
                    node.parents[1] / "node_modules", target_is_directory=True
                )
            subprocess.run(
                [str(node), str(artifact), str(folder)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            seed(root, job, folder)
        elif a.phase in {"message", "message-b"}:
            message(root, job, folder, a.phase == "message-b")
        elif a.phase == "capture":
            capture(root, job)
        else:
            revision(root, job, folder)


if __name__ == "__main__":
    main()
