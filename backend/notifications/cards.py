import json
import os
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from backend.domain.service import fail, get, scoped
from backend.identity import current_actor
from backend.lifecycle import service as lifecycle
from backend.lifecycle.schemas import DecisionWrite
from backend.orders.service import lock_order
from backend.storage.models import Order, now

from .models import ApprovalCard


def base_url():
    value = os.getenv("RIVET_PUBLIC_WORKSPACE_URL", "https://localhost:5179").rstrip(
        "/"
    )
    p = urlsplit(value)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.fragment
        or p.query
    ):
        fail("Configure the HTTPS workspace URL before publishing approval cards.", 503)
    return value


def actionable_enabled():
    try:
        identities = json.loads(os.getenv("RIVET_ACTIONABLE_IDENTITIES", "{}"))
        return bool(
            isinstance(identities, dict)
            and identities
            and os.getenv("RIVET_ACTIONABLE_ORIGINATOR")
            and os.getenv("RIVET_ACTIONABLE_AUDIENCE")
            and urlsplit(base_url()).hostname not in {"localhost", "127.0.0.1"}
        )
    except (ValueError, TypeError):
        return False


def create(s, order, package_id, gate):
    p = next(
        (
            p
            for p in order.state.get("lifecycle", {}).get("packages", [])
            if p["id"] == package_id
        ),
        None,
    )
    if not p or gate not in p["gates"]:
        fail("That approval is not part of this order.", 404)
    if p["blockers"] or p["status"] in {"approved", "rejected", "superseded"}:
        fail("This work is blocked or already decided.", 409)
    row = s.scalar(
        scoped(ApprovalCard).where(
            ApprovalCard.order_id == order.id,
            ApprovalCard.package_id == package_id,
            ApprovalCard.gate == gate,
            ApprovalCard.order_version == order.version,
        )
    )
    if not row:
        row = ApprovalCard(
            order_id=order.id,
            package_id=package_id,
            gate=gate,
            order_version=order.version,
            fingerprint=p["fingerprint"],
            source_ids=list(p["sources"]),
            expires_at=now() + timedelta(hours=24),
        )
        s.add(row)
        s.flush()
    return row


def render(s, row, channel="outlook"):
    order = get(s, Order, row.order_id)
    p = next(
        p for p in order.state["lifecycle"]["packages"] if p["id"] == row.package_id
    )
    url = base_url() + f"/#order/{order.id}?approval={row.id}"
    # Teams webhooks do not authenticate the clicking user. Until a registered
    # bot supplies delegated identity, open the signed-in approval surface.
    actionable = channel == "outlook" and actionable_enabled()
    actions = [{"type": "Action.OpenUrl", "title": "Evidence / edit", "url": url}]
    if actionable:
        for decision, title in [("approve", "Approve"), ("reject", "Reject")]:
            actions.append(
                {
                    "type": "Action.Http",
                    "title": title,
                    "method": "POST",
                    "url": base_url() + f"/api/actions/outlook/{row.id}",
                    "headers": [{"name": "Content-Type", "value": "application/json"}],
                    "body": json.dumps({"decision": decision}),
                }
            )
    titles = {
        "money": "Approve the change-order price",
        "scope": "Confirm the customer’s scope change",
        "engineering": "Check the proposed drawing changes",
        "customer_commitment": "Approve the proposed customer response",
    }
    title = titles[row.gate]
    if row.gate == "money":
        amount = p.get("payload", {}).get("change_order", {}).get("total")
        if amount is None:
            amount = p.get("payload", {}).get("total")
        try:
            value = Decimal(str(amount))
            if value.is_finite():
                currency = p.get("payload", {}).get("currency", "USD")
                price = ("$" if currency == "USD" else currency + " ") + f"{value:,.0f}"
                title = f"Approve {price} change order"
        except (InvalidOperation, ValueError):
            pass
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.0" if channel == "outlook" else "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": order.number,
                "size": "Small",
                "isSubtle": True,
            },
            {
                "type": "TextBlock",
                "text": title,
                "size": "Large",
                "weight": "Bolder",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": p["title"],
                "wrap": True,
            },
        ],
        "actions": actions,
    }
    if actionable:
        card["originator"] = os.environ["RIVET_ACTIONABLE_ORIGINATOR"]
    return {
        "id": row.id,
        "status": row.status,
        "expires_at": row.expires_at.isoformat(),
        "channel": channel,
        "mode": "actionable" if actionable else "signed_in_review_link",
        # Bind the UI's visible work to this exact existing approval card.
        "order_version": row.order_version,
        "fingerprint": row.fingerprint,
        "card": card,
    }


def decide(s, card_id, decision):
    row = get(s, ApprovalCard, card_id, True)
    if row.status != "pending":
        if (
            row.result.get("actor") != current_actor()
            or row.result.get("decision") != decision
        ):
            fail("This card already has a different decision.", 409)
        return row.result
    if row.expires_at < now():
        fail("This decision card expired. Open the current order.", 409)
    order = lock_order(s, row.order_id)
    p = next(
        (
            p
            for p in order.state.get("lifecycle", {}).get("packages", [])
            if p["id"] == row.package_id
        ),
        None,
    )
    if (
        not p
        or p["fingerprint"] != row.fingerprint
        or order.version != row.order_version
    ):
        fail(
            "The order changed after this card was prepared. Open the current evidence.",
            409,
        )
    lifecycle.decide(
        s,
        order,
        row.package_id,
        DecisionWrite(
            expected_version=row.order_version,
            gate=row.gate,
            decision=decision,
            reason="Reviewed this exact evidence-bound approval card.",
            source_ids=row.source_ids,
        ),
    )
    row.status = "decided"
    row.result = {
        "decision": decision,
        "actor": current_actor(),
        "at": now().isoformat(),
        "order_id": order.id,
        "package_id": row.package_id,
        "gate": row.gate,
    }
    return row.result
