"""Actionable work projected from records, without inferred customer activity."""

from decimal import Decimal

from fastapi import APIRouter

from backend.domain.service import scoped
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Order, RecordAgentProposal, RecordAgentRun

from . import service
from .agent_service import invalidate
from .automation import key as automation_key

router = APIRouter(prefix="/api", tags=["Work inbox"])


def lifecycle_item(view, updated_at):
    """One concrete order outcome, from hash-checked saved packages only."""
    active = [
        p for p in view["packages"] if p["status"] not in {"rejected", "superseded"}
    ]
    if not active:
        return None
    current = [p for p in active if p["evidence_current"] and p["status"] != "stale"]
    revision = next((p for p in current if p["operation"] == "revision_review"), None)
    parts = []
    if revision:
        counts = revision["payload"].get("counts", {})
        for key, singular, plural in [
            ("answer", "answer drafted", "answers drafted"),
            ("text_edit", "drawing/BOM field edited", "drawing/BOM fields edited"),
            ("layout_task", "located drafting task", "located drafting tasks"),
        ]:
            count = counts.get(key, 0)
            if count:
                parts.append(f"{count} {singular if count == 1 else plural}")
        change = revision["payload"].get("change_order", {})
        if change.get("lines"):
            total = Decimal(str(change["total"]))
            currency = change["lines"][0].get("currency", "USD")
            amount = (
                f"${total:,.2f}" if currency == "USD" else f"{currency} {total:,.2f}"
            )
            parts.append(f"{amount} proposed scope addition")
    if not parts:
        parts = [p["title"] for p in current[:2]]
    blockers = [message for p in active for message in p.get("blockers", [])]
    if len(current) != len(active):
        blockers.append(
            "Some prepared work has changed sources and needs to be refreshed."
        )
    gates = sorted(
        {
            g
            for p in current
            if p["status"] == "awaiting_decision"
            for g in p["pending_gates"]
        }
    )
    labels = {
        "engineering": "engineering",
        "money": "price",
        "scope": "scope",
        "customer_commitment": "customer response",
    }
    open_point = " ".join(dict.fromkeys(blockers))
    if gates:
        open_point += (
            " " if open_point else ""
        ) + f"Approval needed: {', '.join(labels[g] for g in gates)}."
    return {
        "id": "lifecycle-work",
        "kind": "lifecycle",
        "title": "Drawing review and supporting work prepared"
        if revision
        else "Order work prepared",
        "detail": " · ".join(parts) + "."
        if parts
        else "Previously prepared work needs refreshing before it can be used.",
        "status": "ready" if current else "needs_review",
        "review_required": not bool(current),
        "prepared_outputs": len(current),
        "prepared_automatically": True,
        "updated_at": updated_at,
        "origin": "automatic",
        "lifecycle": {
            "version": view["version"],
            "mode": view["mode"],
            "open_point": open_point,
            "packages": [
                {
                    "id": p["id"],
                    "status": p["status"],
                    "fingerprint": p["fingerprint"],
                    "evidence_current": p["evidence_current"],
                }
                for p in active
            ],
            "prepared_label": f"{len(current)} work {'package' if len(current) == 1 else 'packages'} · Local test adapters"
            if current
            else "Sources need refreshing",
        },
    }


def record_items(w, updated_at):
    items = []
    tasks = [
        t
        for t in w.get("coordination", {}).get("tasks", [])
        if t.get("status") != "done"
    ]
    linked = {t.get("comment_id"): t for t in tasks if t.get("comment_id")}
    represented = set()
    comments_by_id = {c["id"]: c for c in w["comments"]}

    def routine_for(task, comment_id):
        return bool(
            task.get("managed")
            and task.get("id") == automation_key("comment-task", comment_id)
            and not task.get("note")
            and not task.get("owner")
        )

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
        routine_task = routine_for(task, c["id"])
        items.append(
            {
                "id": f"comment:{c['id']}",
                "kind": kind,
                "title": f"Comment {c['number']} · {c['text'][:180]}",
                "detail": (task.get("note") or task.get("reason") or detail)
                if task
                else detail,
                "comment_id": c["id"],
                "task_id": task.get("id", ""),
                "owner": task.get("owner", ""),
                "status": "needs_review",
                "updated_at": c.get("updated_at") or updated_at,
                "origin": (
                    "ai"
                    if task.get("origin") == "ai"
                    else "automatic"
                    if task.get("origin") == "automatic" or task.get("managed")
                    else "human"
                )
                if task and not routine_task
                else (
                    "human"
                    if c.get("origin") == "manual"
                    else "automatic"
                    if c.get("origin") == "unreadable area"
                    else "imported"
                ),
            }
        )
        # Capturing a comment does not create a new PM approval step. Keep the
        # source record's reviewed flag intact; only the work projection changes.
        item = items[-1]
        item["review_required"] = item["origin"] != "imported"
        if not item["review_required"]:
            item["status"] = "ready"
    for t in tasks:
        if t["id"] in represented:
            continue
        c = comments_by_id.get(t.get("comment_id"), {})
        if (
            c
            and c.get("origin") not in {"manual", "unreadable area"}
            and routine_for(t, c["id"])
        ):
            continue  # Bookkeeping remains in the log, not a second PM approval.
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
            if order.state.get("lifecycle", {}).get("packages"):
                from backend.lifecycle.service import view as lifecycle_view

                prepared_lifecycle = lifecycle_item(lifecycle_view(s, order), stamp)
                if prepared_lifecycle:
                    items.append(prepared_lifecycle)
            from backend.reply_assistant.service import order_preparation

            prepared_view = order_preparation(s, order)
            reply = prepared_view.get("prepared_reply")
            state = prepared_view["preparation"]
            if state["status"] != "not_needed":
                coverage = reply.get("coverage", {}) if reply else {}
                is_ready = bool(reply and state["status"] in {"ready", "reviewed"})
                items.append(
                    {
                        "id": "prepared-reply",
                        "kind": "prepared_reply",
                        "title": "Response prepared"
                        if is_ready
                        else "Response preparation",
                        "detail": f"Response prepared from {coverage.get('documents_read', 0)} {'file' if coverage.get('documents_read') == 1 else 'files'} and {coverage.get('comments_considered', 0)} comments. Sources are linked with the draft."
                        if is_ready
                        else state["detail"],
                        "status": "ready"
                        if is_ready
                        else "waiting"
                        if state["status"] in {"queued", "running"}
                        else "needs_review",
                        "review_required": state["status"] in {"failed", "blocked"},
                        "prepared_outputs": 1 if is_ready else 0,
                        "updated_at": reply["created_at"] if reply else stamp,
                        "origin": "ai",
                    }
                )
            all_proposals = s.scalars(
                scoped(RecordAgentProposal).where(
                    RecordAgentProposal.order_id == order.id,
                )
            ).all()
            proposals = [
                p for p in all_proposals if p.status in {"pending", "prepared"}
            ]
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
                        "status": "ready" if p.status == "prepared" else "needs_review",
                        "review_required": p.status == "pending",
                        "prepared_automatically": p.status == "prepared",
                        "prepared_outputs": len(p.operations)
                        if p.status == "prepared"
                        else 0,
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
                # An outdated investigation is history, not a fresh PM action,
                # once a validated response exists for the current evidence.
                # Keep it in the run history; never mark it completed or apply it.
                if (
                    reply
                    and state["status"] in {"ready", "reviewed"}
                    and run.base_version < w["version"]
                    and run.status
                    in {"failed", "waiting_for_review", "waiting_for_input"}
                ):
                    continue
                needs_refresh = run.status == "waiting_for_review" and any(
                    p.run_id == run.id and p.status == "stale" for p in all_proposals
                )
                comment_ids = {
                    op["comment_id"]
                    for p in all_proposals
                    if p.run_id == run.id
                    for op in p.operations
                    if op.get("comment_id")
                }
                successors = [
                    newer
                    for newer in runs
                    if run.origin == newer.origin == "event"
                    and needs_refresh
                    and newer.created_at > run.created_at
                    and newer.base_version >= w["version"]
                    and newer.status in {"completed", "waiting_for_review"}
                ]
                successor = max(successors, key=lambda r: r.created_at, default=None)
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
                    "waiting_for_input": run.result.get("question")
                    or "Rivet needs a detail to finish this task.",
                    "waiting_for_review": "Review Rivet's prepared work.",
                    "failed": run.result.get("error")
                    or "Rivet could not finish. Saved progress is retained.",
                    "paused": "The task is paused and needs your input.",
                }[run.status]
                items.append(
                    {
                        "id": f"run:{run.id}",
                        "kind": "run",
                        "run_id": run.id,
                        "run_status": run.status,
                        "base_version": run.base_version,
                        "current_version": w["version"],
                        "run_origin": run.origin,
                        "needs_refresh": needs_refresh,
                        "superseded_by": successor.id if successor else "",
                        "comment_id": next(iter(comment_ids))
                        if len(comment_ids) == 1
                        else "",
                        "comment_ids": sorted(comment_ids),
                        "title": title,
                        "detail": "The evidence changed. Refresh this review against the current record."
                        if needs_refresh
                        else detail,
                        "review_required": run.status
                        in {"failed", "waiting_for_input", "paused"}
                        or needs_refresh,
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
