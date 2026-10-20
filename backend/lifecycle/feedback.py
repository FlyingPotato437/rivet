"""Human-approved order decisions become unapproved, tenant-scoped history candidates."""

from backend.company_context import customers
from backend.company_context.models import CustomerMemory
from backend.company_context.schemas import CustomerNoteWrite
from backend.domain.service import get, scoped
from backend.storage.models import Order, Project


def candidate_current(s, row):
    """Historical approval must retain its signed package and native evidence."""
    from fastapi import HTTPException

    from .service import _draft_bundle

    try:
        order = get(s, Order, row.source_order_id)
        package = next(
            (
                p
                for p in order.state.get("lifecycle", {}).get("packages", [])
                if p["id"] == row.provenance.get("package_id")
            ),
            None,
        )
        if (
            not package
            or package["status"] == "rejected"
            or package["fingerprint"] != row.provenance.get("package_fingerprint")
        ):
            return False
        recorded = row.provenance.get("decisions", [])
        if not recorded or any(
            decision not in package["decisions"] for decision in recorded
        ):
            return False
        outcome = row.provenance.get("outcome", {})
        if outcome.get("kind") == "accepted_draft_edit":
            bundle = _draft_bundle(s, order, outcome["bundle_id"])
            return bundle["report_sha256"] == outcome[
                "report_sha256"
            ] and "engineering" in {
                d["gate"] for d in recorded if d["decision"] == "approve"
            }
        return outcome.get("kind") == "accepted_commercial_proposal" and {
            "money",
            "scope",
        }.issubset({d["gate"] for d in recorded if d["decision"] == "approve"})
    except (HTTPException, KeyError, OSError, ValueError):
        return False


def capture_candidates(s, order, package):
    binding = customers._binding(s, order.id)
    if not binding or not binding.customer_id:
        return {"status": "customer_not_linked", "candidates": []}
    approved = {
        decision["gate"]
        for decision in package["decisions"]
        if decision["decision"] == "approve"
    }
    if package["operation"] != "revision_review":
        return {"status": "not_applicable", "candidates": []}
    work = []
    if "engineering" in approved:
        for comment in package["payload"]["comments"]:
            if comment["kind"] != "text_edit":
                continue
            change = next(
                change
                for change in comment["draft_bundle"]["changes"]
                if change["id"] == comment["draft_change_id"]
            )
            work.append(
                (
                    comment["draft_change_id"],
                    f"Approved draft edit: {change['target']}",
                    f"On order {order.number}, the authorized engineering reviewer accepted the proposed draft edit from {change['before']} to {change['after']}. This is a historical decision, not a standing requirement or production release.",
                    comment["source_ids"],
                    {
                        "kind": "accepted_draft_edit",
                        "change": change,
                        "bundle_id": comment["draft_bundle_id"],
                        "report_sha256": comment["draft_bundle"]["report_sha256"],
                    },
                )
            )
    if {"money", "scope"}.issubset(approved):
        for index, line in enumerate(package["payload"]["change_order"]["lines"]):
            if line["billability"] != "contract_confirmed":
                continue
            work.append(
                (
                    f"commercial-{index}",
                    f"Approved proposed scope: {line['tag']}",
                    f"On order {order.number}, the authorized commercial reviewers accepted {line['quantity']} additional {line['tag']} at {line['currency']} {line['extended_price']} as proposed extra scope. This is a historical draft decision, not proof of customer acceptance, payment or a rule for another job.",
                    [*line["source_ids"], *line["billability_source_ids"]],
                    {"kind": "accepted_commercial_proposal", "line": line},
                )
            )
    rows = []
    for suffix, title, text, source_ids, outcome in work:
        key = f"lifecycle-{package['id'][:18]}-{suffix}"[:100]
        existing = s.scalar(
            scoped(CustomerMemory).where(
                CustomerMemory.customer_id == binding.customer_id,
                CustomerMemory.key == key,
            )
        )
        if existing:
            rows.append(
                {"id": existing.id, "status": existing.status, "title": existing.title}
            )
            continue
        note = customers._create_note(
            s,
            binding.customer_id,
            CustomerNoteWrite(
                key=key,
                title=title[:180],
                text=text,
                kind="process",
                topics=[
                    "approved-order-decisions",
                    "drawing-edit"
                    if outcome["kind"] == "accepted_draft_edit"
                    else "commercial-scope",
                ],
                category=get(s, Project, order.project_id).category,
                applicability="historical_reference",
                applicability_note="A customer-history reviewer must approve reuse. Confirm the current job's evidence before relying on this historical example.",
                source_order_id=order.id,
                source_ids=list(dict.fromkeys(source_ids))[:20],
                provenance_note="Captured from authenticated human approval of the exact lifecycle package. It has not been approved for reuse.",
            ),
            extra_provenance={
                "kind": "lifecycle_decision_candidate",
                "package_id": package["id"],
                "package_fingerprint": package["fingerprint"],
                "decisions": package["decisions"],
                "outcome": outcome,
                "reuse_policy": "human_customer_history_review_required",
                "not_training_data": True,
            },
        )
        rows.append({"id": note.id, "status": note.status, "title": note.title})
    return {
        "status": "candidates_need_review" if rows else "no_eligible_decision",
        "candidates": rows,
    }
