"""Run only the prepared fictional order's existing vision/reply/correction jobs.

The API meter fails closed before another call can exceed the user's $3 cap.
This is a rehearsal driver, not a new product workflow or model experiment.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.identity import Identity, identity_scope
from backend.storage import db
from scripts.pilot_demo.rehearse import CONNECTION, ORG, output

os.environ["RIVET_ENGINEERING_VISION_ENABLED"] = "false"
os.environ["RIVET_VISION_MODEL"] = "gpt-6.1-sol"
os.environ["OPENAI_MODEL"] = "gpt-6.1-sol"


def meter():
    import httpx

    ledger = ROOT / ".data/pilot-demo-20261019/api-usage.json"
    old = httpx.Client.send

    def send(client, request, *args, **kwargs):
        paid = request.url.host == "api.openai.com" and request.method == "POST"
        if not paid:
            return old(client, request, *args, **kwargs)
        payload = json.loads(request.content)
        if (
            payload.get("model") != "gpt-6.1-sol"
            or payload.get("max_output_tokens", 0) > 16000
        ):
            raise ValueError("Unapproved rehearsal model or output budget")
        data = (
            json.loads(ledger.read_text())
            if ledger.exists()
            else {"limit_usd": 3, "calls": []}
        )
        used = sum(c["upper_cost_usd"] for c in data["calls"])
        reserve = 0.4
        if used + reserve > 3:
            raise ValueError("Approved $3 API allowance exhausted")
        entry = {
            "model": payload["model"],
            "upper_cost_usd": reserve,
            "status": "reserved",
        }
        data["calls"].append(entry)
        ledger.write_text(json.dumps(data, indent=2) + "\n")
        response = old(client, request, *args, **kwargs)
        response.read()
        try:
            body = response.json()
            usage = body.get("usage", {})
            if usage:
                entry.update(
                    usage=usage,
                    upper_cost_usd=(
                        usage.get("input_tokens", 0) * 2.5
                        + usage.get("output_tokens", 0) * 10
                    )
                    / 1e6,
                    status="metered",
                )
            else:
                entry["status"] = "provider_error_reserved"
            ledger.write_text(json.dumps(data, indent=2) + "\n")
        except ValueError:
            pass
        return response

    httpx.Client.send = send


def vision(root, oid):
    from backend.domain.service import get, scoped
    from backend.ingestion import vision as v
    from backend.orders import service as orders
    from backend.records import service as records
    from backend.storage.models import Job, Order

    with db.Session() as s:
        o = get(s, Order, oid)
        jobs = [
            (j.id, j.payload)
            for j in s.scalars(
                scoped(Job).where(
                    Job.project_id == o.project_id,
                    Job.kind == "vision_page",
                    Job.status == "queued",
                )
            )
        ]
    for jid, payload in jobs:
        v.process(payload)
        with db.Session.begin() as s:
            j = get(s, Job, jid, True)
            j.status = "succeeded"
            orders.refresh(s, get(s, Order, oid))
    with db.Session() as s:
        w = records.view(s, get(s, Order, oid))
        output(
            root / "comment-log.json",
            {"comments": w["comments"], "documents": w["documents"]},
        )


def reply(root, oid, question=False):
    from backend.domain.service import get, scoped
    from backend.mailbox.models import MailboxReceipt
    from backend.reply_assistant import service as replies
    from backend.reply_assistant.schemas import GenerateReply
    from backend.storage.models import RecordIntake

    with db.Session() as s:
        receipt = next(
            (
                r
                for r in s.scalars(
                    scoped(MailboxReceipt).where(
                        MailboxReceipt.connection_id == CONNECTION
                    )
                )
                if r.intake_id and get(s, RecordIntake, r.intake_id).order_id == oid
            ),
            None,
        )
        message_id = receipt.internet_message_id if receipt else ""
    prompt = (
        "Post-sales question: what primary main breaker trip rating did we agree with the customer? Answer the value, equipment tag and exact source section. Separate the agreed requirement from the older Rev A value. Do not infer actual engineering approval or release."
        if question
        else "Prepare a concise response to the five returned comments C1-C5. Use the customer requirement specification as the governing fictional review target, cite the relevant source references, acknowledge uncertainty and do not claim engineering release. Do not claim any attachments were sent."
    )
    row = replies.generate(
        GenerateReply(order_id=oid, internet_message_id=message_id, instruction=prompt),
        f"pilot-{oid}-{'question' if question else 'reply'}-v3",
    )
    if row["status"] != "generating":
        output(
            root / ("post-sales-answer.json" if question else "cited-reply.json"), row
        )
        if row["status"] != "draft":
            raise ValueError(row.get("error") or "Reply is not ready")
        return
    result = replies.process_draft(row["id"])
    output(
        root / ("post-sales-answer.json" if question else "cited-reply.json"), result
    )


def correct(root, oid):
    from backend.domain.service import get
    from backend.equipment import outlook, workflow
    from backend.storage.models import Order

    with db.Session.begin() as s:
        o = get(s, Order, oid)
        report, _, _, _, _ = workflow.plan(s, o)
        if report["status"] != "ready":
            output(root / "correction-blocked.json", report)
            raise ValueError("Correction plan is blocked; inspect evidence")
        result = workflow.prepare(s, o, report["plan_id"])
        output(root / "corrected-package.json", result)
    with db.Session() as s:
        v = workflow.view(s, get(s, Order, oid))
        output(root / "corrected-recheck.json", v)
        # Retain independent copies of the actual saved, hash-verified artifacts.
        bundle = v.get("bundle") or v.get("latest_bundle")
        if not bundle:
            bundles = v.get("bundles", [])
            bundle = bundles[0] if bundles else None
        if not bundle:
            state = get(s, Order, oid).state.get("equipment_corrections", {})
            output(root / "bundle-state.json", state)
            raise ValueError("Inspect correction bundle contract")
        bid = bundle["id"]
        for a in bundle.get("artifacts", []):
            path, _ = workflow.artifact(s, get(s, Order, oid), bid, a["name"])
            dest = root / "corrected-files" / a["name"]
            dest.parent.mkdir(exist_ok=True)
            dest.write_bytes(path.read_bytes())
    placed = outlook.save(oid, bid, CONNECTION)
    output(root / "outlook-draft.json", placed)


def export(root, oid):
    from backend.domain.service import get
    from backend.records import exports
    from backend.records import service as records
    from backend.storage.models import Order

    with db.Session() as s:
        w = records.view(s, get(s, Order, oid))
    (root / "comment-log.xlsx").write_bytes(exports.workbook(w))
    (root / "comment-log.pdf").write_bytes(exports.pdf(w))
    output(root / "order-record.json", w)


def question(root, oid):
    from backend.records.conversations import ask_with_history
    from backend.records.schemas import Ask

    prompt = "What primary main breaker trip rating did we agree with the customer? Identify the breaker and cite the agreed source. Distinguish the older Rev A value and do not infer production release."
    result = ask_with_history(oid, Ask(question=prompt), key="pilot-post-sales-" + oid)
    output(root / "post-sales-answer.json", {"question": prompt, **result})


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "phase", choices=["vision", "reply", "question", "correct", "export"]
    )
    p.add_argument("--run", type=int, required=True)
    a = p.parse_args()
    root = ROOT / f"output/pilot-demo-20261019/rehearsal-{a.run}"
    oid = json.loads((root / "state.json").read_text())["order_id"]
    meter()
    with identity_scope(Identity(ORG, "Rivet pilot rehearsal", "org:admin")):
        if a.phase == "vision":
            vision(root, oid)
        elif a.phase == "reply":
            reply(root, oid)
        elif a.phase == "question":
            question(root, oid)
        elif a.phase == "correct":
            correct(root, oid)
        else:
            export(root, oid)
