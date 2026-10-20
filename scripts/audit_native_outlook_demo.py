"""Read back the fictional demo's native outputs and existing Outlook drafts."""

import argparse
import json
import shutil
from pathlib import Path

from backend.domain.service import get, scoped
from backend.equipment import outlook, workflow
from backend.identity import Identity, identity_scope
from backend.storage.db import Session
from backend.storage.models import Order, now


def audit(organization_id, root):
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    with identity_scope(
        Identity(organization_id, "Rivet native demo verification", "org:admin")
    ):
        with Session() as s:
            orders = [
                (o.id, o.number)
                for o in s.scalars(
                    scoped(Order)
                    .where(Order.number.like("RVT-NATIVE-261018-%"))
                    .order_by(Order.number)
                )
            ]
        for oid, number in orders:
            with Session() as s:
                order = get(s, Order, oid)
                bundles = order.state.get("equipment_corrections", {}).get(
                    "bundles", []
                )
                if not bundles:
                    rows.append(
                        {"order_id": oid, "number": number, "status": "not_prepared"}
                    )
                    continue
                bid = bundles[-1]["id"]
            # GET-only reconciliation checks current Microsoft-stored bytes.
            delivery = outlook.sync_one(oid, bid)
            with Session() as s:
                order = get(s, Order, oid)
                raw = next(
                    b
                    for b in order.state["equipment_corrections"]["bundles"]
                    if b["id"] == bid
                )
                bundle = workflow.public_bundle(s, order, raw)
                if (
                    not bundle["current"]
                    or not bundle["attachable"]
                    or bundle["native_verification"]["status"] != "verified"
                ):
                    raise RuntimeError(
                        f"{number}: saved native package did not pass current recheck."
                    )
                destination = root / number
                destination.mkdir(exist_ok=True)
                for item in bundle["artifacts"]:
                    source, _ = workflow.artifact(s, order, bid, item["name"])
                    target = destination / item["name"]
                    if target.exists() and target.read_bytes() != source.read_bytes():
                        raise RuntimeError(
                            "An existing copied review file changed; preserve it for inspection."
                        )
                    shutil.copyfile(source, target)
                draft = delivery.get("draft", {})
                rows.append(
                    {
                        "order_id": oid,
                        "number": number,
                        "bundle_id": bid,
                        "url": f"http://localhost:5178/#order/{oid}",
                        "current": bundle["current"],
                        "attachable": bundle["attachable"],
                        "native_status": bundle["native_verification"]["status"],
                        "native_checks": len(bundle["native_verification"]["checks"]),
                        "company_rules": bundle.get("company_rules"),
                        "draft_status": draft.get("status"),
                        "outlook_url": draft.get("web_link"),
                        "attachments_verified": draft.get("attachments_verified"),
                        "attachments": draft.get("attachments", []),
                        "engineering_approved": bundle["engineering_approved"],
                        "production_released": bundle["production_released"],
                        "fictional": True,
                    }
                )
    result = {
        "at": now().isoformat(),
        "operation": "Read-only verification of existing drafts; no sends or new drafts.",
        "orders": rows,
    }
    (root / "live-outlook-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/native-revision-scenarios/review-ready"),
    )
    args = parser.parse_args()
    print(json.dumps(audit(args.organization_id, args.output), indent=2))
