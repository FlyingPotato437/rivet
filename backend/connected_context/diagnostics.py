"""Tenant-scoped connection health from saved, successfully retrieved sources."""

from urllib.parse import urlsplit

from backend.company_context.service import require_admin, require_writer
from backend.domain.service import fail, get, scoped
from backend.identity import current_org
from backend.storage.models import Order

from . import providers, service


def integration_health(s, kind):
    """Summarize only this team's mapped sources, without exposing config secrets."""
    jobs = (
        providers.configuration()
        .get("organizations", {})
        .get(current_org(), {})
        .get("jobs", {})
    )
    sources = []
    for number in jobs:
        bindings = [
            b
            for b in providers.bindings(number)
            if b.get("kind") == kind
            and (kind != "erp" or b.get("adapter") == "sap_odata")
        ]
        if not bindings:
            continue
        order = s.scalar(scoped(Order).where(Order.number == number))
        summary = service.summary(s, order) if order else {}
        for binding in bindings:
            saved = next(
                (x for x in summary.get("systems", []) if x["key"] == binding["key"]),
                {},
            )
            sources.append(
                {
                    "mode": binding.get("mode"),
                    "sandbox": urlsplit(binding.get("base_url", "")).hostname
                    == "sandbox.api.sap.com",
                    "current": summary.get("status") == "current"
                    and saved.get("status") == "current",
                    "error": saved.get("status") == "unavailable",
                    "documents": saved.get("documents", 0),
                }
            )
    if not sources:
        return {
            "status": "not_connected",
            "detail": "No authorized source mapping is configured for this team.",
        }
    live = [x for x in sources if x["mode"] != "local_demo"]
    if not live:
        return {
            "status": "demo_only",
            "detail": "Local demonstration sources are configured. No customer system is connected.",
        }
    current = all(x["current"] for x in live)
    sandbox = all(x["sandbox"] for x in live)
    status = (
        "sandbox_verified"
        if current and sandbox
        else "current"
        if current
        else "attention_required"
        if any(x["error"] for x in live)
        else "configured"
    )
    count = sum(x["documents"] for x in live)
    detail = (
        f"Read-only {'SAP sandbox' if sandbox else 'connected source'} access: {count} retrieved document{'s' if count != 1 else ''}. "
        + (
            "The latest source check is current. "
            if current
            else "A fresh source check is needed. "
        )
        + (
            "Generic sample orders; customer SAP is not connected."
            if sandbox
            else "Rivet retrieves only the configured order/site records and preserves their source links."
        )
    )
    return {"status": status, "detail": detail}


def sap_status(s):
    require_admin()
    jobs = (
        providers.configuration()
        .get("organizations", {})
        .get(current_org(), {})
        .get("jobs", {})
    )
    profiles = []
    for number in sorted(jobs):
        connections = [
            b for b in providers.bindings(number) if b.get("adapter") == "sap_odata"
        ]
        if not connections:
            continue
        order = s.scalar(scoped(Order).where(Order.number == number))
        summary = service.summary(s, order) if order else {}
        saved = service.row_for(s, order) if order else None
        for binding in connections:
            system = next(
                (x for x in summary.get("systems", []) if x["key"] == binding["key"]),
                {},
            )
            sandbox = (
                urlsplit(binding.get("base_url", "")).hostname == "sandbox.api.sap.com"
            )
            profiles.append(
                {
                    "key": binding["key"],
                    "order_id": order.id if order else None,
                    "order_number": number,
                    "label": binding["label"],
                    "environment": "SAP sandbox" if sandbox else "Customer SAP",
                    "status": summary.get("status", "not_checked"),
                    "source_status": system.get("status", "pending"),
                    "checked_at": saved.checked_at.isoformat()
                    if saved and saved.checked_at
                    else None,
                    "documents": system.get("documents", 0),
                    "error": system.get("error", ""),
                    "resources": [
                        {k: r.get(k) for k in ["document_id", "name", "sha256"]}
                        for r in summary.get("resources", [])
                        if r["key"] == binding["key"]
                    ],
                }
            )
    return {"profiles": profiles, "read_only": True}


def check_sap(s, order_id):
    require_admin()
    require_writer()
    order = get(s, Order, order_id)
    if not any(
        b.get("adapter") == "sap_odata" for b in providers.bindings(order.number)
    ):
        fail("No authorized SAP mapping exists for this order.", 409)
    service.enqueue(s, order)
    return {"status": "queued", "order_id": order.id}
