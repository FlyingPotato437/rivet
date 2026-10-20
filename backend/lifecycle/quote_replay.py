"""Create a fresh fictional catalog/order and PDF mail inputs, without sending.

No commercial package is prepared here: the real captured email and background
agent must produce the quotation and purchase-order comparisons.
"""

import argparse
import json
from pathlib import Path
from uuid import UUID

from backend.identity import Identity, identity_scope
from backend.orders.service import new_order
from backend.records.service import ensure
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import now
from scripts.make_order_replay import write_pdf

from .replay import _source

CATALOG = {
    "equipment_category": "low_voltage_circuit_breaker",
    "tag": "CB-12",
    "model": "RIVET-DEMO-LV-1200-3P",
    "description": "Fictional 1200 A, three-pole circuit-breaker package",
    "unit_cost": "1200.00",
    "gross_margin_percent": "25.00",
    "currency": "USD",
    "trip_unit": "TU-2500",
    "remote_indication": "not included",
    "rated_current": "1200 A",
    "voltage": "480 V",
    "poles": "3",
    "exceptions": [
        "Tax and freight require a separate written quotation.",
        "Delivery date remains subject to written confirmation.",
    ],
}


def make_inputs(destination, job):
    destination.mkdir(parents=True, exist_ok=True)
    common = [
        "FICTIONAL PRODUCT DEMONSTRATION - not a customer commitment",
        "Job: " + job,
        "Equipment category: Low-voltage circuit breaker",
        "Equipment tag: CB-12",
        "Catalog model: RIVET-DEMO-LV-1200-3P",
        "Quantity: 2 each",
        "Currency: USD",
        "Trip unit: TU-2500",
        "Remote indication: required",
        "Rated current: 1200 A",
        "Voltage: 480 V",
        "Poles: 3",
    ]
    rfq = destination / f"{job}-RFQ.pdf"
    write_pdf(rfq, "Request for quotation", common)
    files = {"rfq": str(rfq.resolve())}
    for revision, price in [(1, "1500.00"), (2, "1600.00"), (3, "1600.00")]:
        path = destination / f"{job}-PO-Revision-{revision}.pdf"
        write_pdf(
            path,
            "Purchase order",
            [
                *[
                    "Remote indication: not included"
                    if revision == 3 and line == "Remote indication: required"
                    else line
                    for line in common
                ],
                "Purchase order: PO-RIVET-DEMO-442",
                f"PO revision: {revision}",
                f"Unit price: USD {price}",
                *(
                    [
                        "Fictional customer clarification: remote indication is not included in this purchase order.",
                        "Quotation exceptions acknowledged for this fictional test:",
                        "Tax and freight require a separate written quotation.",
                        "Delivery date remains subject to written confirmation.",
                    ]
                    if revision == 3
                    else []
                ),
            ],
        )
        key = {1: "po_mismatch", 2: "po_price_corrected", 3: "po_scope_corrected"}[
            revision
        ]
        files[key] = str(path.resolve())
        if revision == 2:
            files["po_corrected"] = str(path.resolve())  # Legacy key: price only.
    return files


def seed(organization_id, destination):
    number = "DEMO-QUOTE-" + now().strftime("%m%d-%H%M%S")
    with (
        identity_scope(Identity(organization_id, "Rivet quotation demo setup")),
        Session.begin() as s,
    ):
        order = new_order(
            s,
            {
                "title": "Incoming RFQ to purchase order - fictional breaker package",
                "customer": "Fictional demo customer",
                "number": number,
                "synthetic": True,
            },
        )
        _, source = _source(
            s, order, "Fictional server pricebook.json", CATALOG, kind="erp_price"
        )
        record = ensure(s, order)
        record.data = {**record.data, "agent_settings": {"enabled": True}}
        result = {
            "order_id": order.id,
            "number": number,
            "organization_id": organization_id,
            "mode": "local_demo",
            "trusted_catalog_source_id": source.id,
            "expected_quote_unit_price": "1600.00",
            "expected_quote_total": "3200.00",
            "expected_exceptions": "Remote indication requested but excluded by catalog; tax/freight and delivery need separate confirmation.",
            "commercial_packages_prepared": False,
            "messages_sent": False,
        }
    result["files"] = make_inputs(destination / number, number)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True, type=UUID)
    parser.add_argument(
        "--output", type=Path, default=db.DATA / "demo-readiness" / "quote-replay.json"
    )
    args = parser.parse_args()
    result = seed(str(args.organization_id), args.output.parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
