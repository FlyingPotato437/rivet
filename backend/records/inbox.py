"""Actionable work projected from records, without inferred customer activity."""

from fastapi import APIRouter

from backend.domain.service import scoped
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Order, RecordAgentProposal, RecordAgentRun

from . import service
from .agent_service import invalidate

router = APIRouter(prefix="/api", tags=["Work inbox"])


def record_items(w, updated_at):
    items = []
    tasks = [
        t
        for t in w.get("coordination", {}).get("tasks", [])
        if t.get("status") != "done"
    ]
    linked = {t.get("comment_id"): t for t in tasks if t.get("comment_id")}
    represented = set()
    for c in w["comments"]:
        if c.get("status") == "closed":
            continue
        if not c.get("reviewed"):
            kind = (
                "reference_review"
                if not c.get("target_source_id")
                else "comment_review"
            )
            detail = (
                "Confirm the referenced drawing and source details."
                if kind == "reference_review"
                else "Check the imported comment and its source details."
            )
        elif c.get("status") == "open" and not c.get("response"):
            kind, detail = "response", "No response has been recorded."
        else:
            continue
        task = linked.get(c["id"], {})
        if task:
            represented.add(task["id"])
        items.append(
            {
                "id": f"comment:{c['id']}",
                "kind": kind,
                "title": f"Comment {c['number']} · {c['text'][:180]}",
                "detail": detail,
                "comment_id": c["id"],
                "owner": task.get("owner", ""),
                "status": "needs_review",
                "updated_at": c.get("updated_at") or updated_at,
                "origin": "human"
                if c.get("origin") == "manual"
                else "automatic"
                if c.get("origin") == "unreadable area"
                else "imported",
            }
        )
    for t in tasks:
        if t["id"] in represented:
            continue
        items.append(
            {
                "id": f"task:{t['id']}",
                "task_id": t["id"],
                "kind": "task",
                "title": t["title"],
                "detail": t.get("note") or t.get("reason", ""),
                "comment_id": t.get("comment_id", ""),
                "owner": t.get("owner", ""),
                "status": "needs_review",
                "updated_at": t.get("updated_at") or t.get("created_at") or updated_at,
                "origin": "ai"
                if t.get("origin") == "ai"
                else "automatic"
                if t.get("origin") == "automatic" or t.get("managed")
                else "human",
            }
        )
    return items


@router.get("/records/inbox")
def inbox():
    output = []
    with Session.begin() as s:
        for candidate in s.scalars(
            scoped(Order).order_by(Order.updated_at.desc())
        ).all():
            order = lock_order(s, candidate.id)
            w = service.view(s, order)
            invalidate(s, order, w)
            s.flush()
            stamp = order.updated_at.isoformat()
            items = record_items(w, stamp)
            all_proposals = s.scalars(
                scoped(RecordAgentProposal).where(
                    RecordAgentProposal.order_id == order.id,
                )
            ).all()
            proposals = [p for p in all_proposals if p.status == "pending"]
            runs = s.scalars(
                scoped(RecordAgentRun).where(RecordAgentRun.order_id == order.id)
            ).all()
            run_by_id = {run.id: run for run in runs}
            proposal_runs = set()
            for p in proposals:
                # Stale proposals cannot be applied and must not appear ready.
                if p.base_version != w["version"]:
                    continue
                if p.run_id not in run_by_id or run_by_id[p.run_id].status in {
                    "queued",
                    "running",
                }:
                    continue
                proposal_runs.add(p.run_id)
                items.append(
                    {
                        "id": f"proposal:{p.id}",
                        "kind": "proposal",
                        "proposal_id": p.id,
                        "run_id": p.run_id,
                        "title": p.title,
                        "detail": p.summary,
                        "comment_id": next(
                            (
                                op.get("comment_id", "")
                                for op in p.operations
                                if op.get("comment_id")
                            ),
                            "",
                        ),
                        "status": "ready",
                        "updated_at": p.created_at.isoformat(),
                        "origin": "ai" if p.origin == "ai" else "automatic",
                    }
                )
            for run in runs:
                if run.status not in {
                    "queued",
                    "running",
                    "waiting_for_input",
                    "waiting_for_review",
                    "failed",
                    "paused",
                }:
                    continue
                if run.id in proposal_runs and run.status == "waiting_for_review":
                    continue
                needs_refresh = run.status == "waiting_for_review" and any(
                    p.run_id == run.id and p.status == "stale" for p in all_proposals
                )
                title = (
                    " ".join(
                        line
                        for line in run.goal.splitlines()
                        if not line.startswith(("Context:", "Source in view:"))
                    ).strip()
                    or "Review order evidence"
                )
                detail = {
                    "queued": "Queued for Rivet to work on.",
                    "running": "Rivet is investigating the order evidence.",
                    "waiting_for_input": "Answer Rivet's question to continue the saved task.",
                    "waiting_for_review": "Review Rivet's prepared work.",
                    "failed": "The task needs attention. Saved progress is retained.",
                    "paused": "The task is paused and needs your input.",
                }[run.status]
                items.append(
                    {
                        "id": f"run:{run.id}",
                        "kind": "run",
                        "run_id": run.id,
                        "title": title,
                        "detail": "The evidence changed. Refresh this review against the current record."
                        if needs_refresh
                        else detail,
                        "status": "needs_review"
                        if run.status
                        in {
                            "failed",
                            "waiting_for_input",
                            "waiting_for_review",
                            "paused",
                        }
                        else "waiting",
                        "updated_at": run.updated_at.isoformat(),
                        "origin": "ai" if run.mode == "ai" else "automatic",
                    }
                )
            output.extend(
                {
                    **item,
                    "id": f"{order.id}:{item['id']}",
                    "order_id": order.id,
                    "order_title": w["order"]["title"],
                    "order_number": order.number,
                }
                for item in items
            )
    output.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
    output.sort(
        key=lambda item: {"ready": 0, "needs_review": 1, "waiting": 2}[item["status"]]
    )
    return {"items": output}
