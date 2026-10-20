"""One timeline of order events, completed work and decisions."""

from datetime import timedelta

from fastapi import APIRouter

from backend.domain.service import get, scoped
from backend.storage.db import Session
from backend.storage.models import Document, Order, OrderEvent, RecordAgentRun, now

from .followups import FollowUp
from .models import ProjectSentMessage

router = APIRouter(prefix="/api", tags=["Order activity"])

# Plain values Today and the order story can turn into specific sentences.
# Hashes, receipts and raw payloads stay in the details drawer and Activity.
PACKAGE_FIELDS = (
    "total",
    "currency",
    "purchase_order_number",
    "part_number",
    "lead_time_days",
    "current_ship_date",
    "proposed_ship_date",
)


def package_facts(p):
    payload = p.get("payload") or {}
    facts = {"operation": p.get("operation", ""), "status": p.get("status", "")}
    counts = payload.get("counts")
    if isinstance(counts, dict):
        facts["counts"] = {
            k: v for k, v in counts.items() if isinstance(v, int) and v >= 0
        }
    for key in PACKAGE_FIELDS:
        if isinstance(payload.get(key), (str, int, float)):
            facts[key] = payload[key]
    change = payload.get("change_order")
    if isinstance(change, dict) and isinstance(change.get("total"), (str, int, float)):
        facts["change_order_total"] = change["total"]
    for key in ("mismatches", "scope_mismatches", "exceptions"):
        if isinstance(payload.get(key), list):
            facts[key + "_count"] = len(payload[key])
    return facts


def event_facts(e, order):
    data = e.data if isinstance(e.data, dict) else {}
    if e.kind == "workflow_action":
        return {
            k: data[k]
            for k in ("action", "status", "mode")
            if isinstance(data.get(k), str)
        }
    if e.kind == "enterprise_publication":
        receipt = data.get("receipt") if isinstance(data.get("receipt"), dict) else {}
        return {
            "target": str(data.get("target", "")),
            "status": str(receipt.get("status", "")),
            "name": str(receipt.get("name", "")),
        }
    package_id = data.get("package_id")
    if package_id:
        for p in order.state.get("lifecycle", {}).get("packages", []):
            if p.get("id") == package_id:
                return package_facts(p)
    return {}


def run_facts(run):
    coverage = run.result.get("coverage") or {}
    facts = {}
    if isinstance(coverage.get("comments_total"), int):
        failed = coverage.get("comments_failed") or []
        facts.update(
            comments_total=coverage["comments_total"],
            comments_prepared=int(coverage.get("comments_prepared") or 0),
            comments_failed=len(failed) if isinstance(failed, list) else 0,
        )
    sources = run.result.get("source_ids")
    if isinstance(sources, list):
        facts["sources"] = len(sources)
    prepared = [
        str(p.get("summary", ""))
        for p in run.result.get("lifecycle_preparations") or []
        if isinstance(p, dict) and p.get("summary")
    ]
    if prepared:
        facts["prepared"] = prepared[:4]
    return facts


def native_successor(s, order, run):
    """Relate a redundant event investigation to its actually checked files.

    Keep the run and its original error. Manual work, later ordinary email and
    different or changed source packages never lose their attention state.
    """
    if not (run.trigger_key or "").startswith(f"record-evidence:{order.id}:"):
        return None
    fingerprint = (run.state.get("equipment_check_hint") or {}).get("fingerprint")
    if not fingerprint:
        return None
    from fastapi import HTTPException

    from backend.equipment import ownership, workflow

    bundles = order.state.get("equipment_corrections", {}).get("bundles", [])
    for bundle in reversed(bundles):
        proof = bundle.get("native_verification") or {}
        checks = proof.get("checks") or []
        if (
            bundle.get("source_fingerprint") != fingerprint
            or proof.get("status") != "verified"
            or not checks
            or any(check.get("status") != "pass" for check in checks)
        ):
            continue
        try:
            # The stored independent check is relevant only while all of its
            # original source and output bytes still match their fingerprints.
            if not workflow._current(s, order, bundle):
                continue
            for artifact in bundle["artifacts"]:
                workflow.artifact(s, order, bundle["id"], artifact["name"])
            if not ownership.for_autorun(s, order):
                return None
        except HTTPException:
            return None
        return bundle["id"]
    return None


def working(s):
    """Live comment progress for runs that are queued or running right now."""
    from backend.records.agent_tasks import rows as comment_tasks

    items = []
    for run in s.scalars(
        scoped(RecordAgentRun).where(RecordAgentRun.status.in_(["queued", "running"]))
    ):
        order = s.get(Order, run.order_id)
        if not order:
            continue
        tasks = comment_tasks(s, run) if run.state.get("parallel_plan") else []
        items.append(
            {
                "order_id": order.id,
                "number": order.number,
                "status": run.status,
                "comments_total": len(tasks),
                "comments_prepared": sum(t.status == "prepared" for t in tasks),
            }
        )
    return items


def timeline(s, order_id=None, *, since=None, limit=100):
    result = []
    orders = {o.id: o for o in s.scalars(scoped(Order))}
    if order_id:
        orders = {order_id: get(s, Order, order_id)}
    if not orders:
        return []

    def query(model, time_field):
        q = scoped(model).where(model.order_id.in_(orders))
        if since:
            q = q.where(time_field >= since)
        return s.scalars(q.order_by(time_field.desc()).limit(limit))

    def append(
        id,
        order_id,
        at,
        kind,
        title,
        detail="",
        url="",
        *,
        actor="systems",
        event_type="",
        facts=None,
    ):
        result.append(
            {
                "id": id,
                "order_id": order_id,
                "number": orders[order_id].number,
                "at": at.isoformat(),
                "kind": kind,
                "title": title[:240],
                "detail": detail[:6000],
                "outlook_url": url,
                "actor": actor,
                "event_type": event_type,
                "facts": facts or {},
            }
        )

    for e in query(OrderEvent, OrderEvent.created_at):
        if e.summary == "Read current documents and reran order checks":
            continue
        append(
            e.id,
            e.order_id,
            e.created_at,
            "decision" if "decision" in e.kind or "approv" in e.kind else "event",
            e.summary,
            "Rivet"
            if "rivet" in e.actor.lower() or "worker" in e.actor.lower()
            else "Your team",
            actor="rivet"
            if "rivet" in e.actor.lower() or "worker" in e.actor.lower()
            else "people",
            event_type=e.kind,
            facts=event_facts(e, orders[e.order_id]),
        )
    for run in query(RecordAgentRun, RecordAgentRun.updated_at):
        if run.status in {
            "completed",
            "failed",
            "waiting_for_input",
            "waiting_for_review",
        }:
            incomplete = (
                run.result.get("incomplete")
                or run.result.get("error")
                or "reached" in run.result.get("summary", "").lower()
                and "budget" in run.result.get("summary", "").lower()
            )
            done = run.status == "completed" and not incomplete
            successor = None if done else native_successor(s, orders[run.order_id], run)
            facts = run_facts(run)
            if successor:
                facts["superseded_bundle_id"] = successor
            append(
                run.id,
                run.order_id,
                run.updated_at,
                "event" if successor else "work" if done else "attention",
                "The checked drawing package superseded an earlier background investigation."
                if successor
                else "Rivet prepared the order work"
                if done
                else "Preparation needs attention",
                run.result.get("summary") or run.result.get("error", ""),
                actor="rivet",
                facts=facts,
            )
    for m in query(ProjectSentMessage, ProjectSentMessage.sent_at):
        append(
            m.id,
            m.order_id,
            m.sent_at,
            "sent",
            "Reply sent · " + m.subject,
            "Confirmed in Outlook Sent Items.",
            m.outlook_url,
            actor="people",
        )
    # An upload is an event even when it has no associated email thread.
    project_orders = {o.project_id: o for o in orders.values()}
    q = scoped(Document).where(Document.project_id.in_(project_orders))
    if since:
        q = q.where(Document.created_at >= since)
    for d in s.scalars(q.order_by(Document.created_at.desc()).limit(limit)):
        append(
            d.id,
            project_orders[d.project_id].id,
            d.created_at,
            "received",
            "File received · " + d.name,
        )
    result.sort(key=lambda e: e["at"], reverse=True)
    return result[:limit]


def brief(s):
    items = timeline(s, since=now() - timedelta(days=1), limit=1000)
    decisions = []
    for o in s.scalars(scoped(Order)):
        for p in o.state.get("lifecycle", {}).get("packages", []):
            if p.get("status") == "awaiting_decision":
                decisions.append(
                    {
                        "order_id": o.id,
                        "number": o.number,
                        "package_id": p["id"],
                        "title": p.get("title", p.get("operation", "Decision")),
                    }
                )
    done = sum(e["kind"] == "work" for e in items)
    sent = sum(e["kind"] == "sent" for e in items)
    overdue = list(
        s.scalars(
            scoped(FollowUp).where(
                FollowUp.status.in_(["waiting", "needs_check"]),
                FollowUp.due_at <= now(),
            )
        )
    )
    # Presentation data for Today; the timer and drafting policy stay unchanged.
    waiting = []
    for row in s.scalars(
        scoped(FollowUp).where(
            FollowUp.status.in_(
                ["waiting", "needs_check", "drafted", "existing_draft"]
            ),
            FollowUp.due_at <= now(),
        )
    ):
        sent_message = s.get(ProjectSentMessage, row.sent_message_id)
        if sent_message:
            waiting.append(
                {
                    "id": row.id,
                    "order_id": row.order_id,
                    "due_at": row.due_at.isoformat(),
                    "sent_at": sent_message.sent_at.isoformat(),
                    "status": row.status,
                    "outlook_url": row.outlook_url,
                }
            )
    return {
        "period": "last_24_hours",
        "prepared_runs": done,
        "confirmed_sent_replies": sent,
        "decisions": decisions,
        "waiting_items_due": len(overdue),
        "waiting_items": waiting,
        "text": f"Prepared {done} order reviews · {sent} replies sent · {len(decisions)} decisions need you",
        "delivery": "workspace",
        "items": items[:20],
        "working": working(s),
    }


@router.get("/order-activity")
def activity():
    with Session() as s:
        return {"items": timeline(s)}


@router.get("/orders/{id}/timeline")
def order_timeline(id: str):
    with Session() as s:
        return {"items": timeline(s, id)}


@router.get("/daily-brief")
def daily_brief():
    with Session() as s:
        return brief(s)
