import json, time, re
from copy import deepcopy
from datetime import datetime, timezone
from sqlalchemy import select
from backend.storage.db import Session
from backend.storage.models import (
    Run,
    Quote,
    Project,
    Document,
    Span,
    Catalog,
    Offer,
    Proposal,
    Requirement,
)
from backend.domain.service import (
    get,
    scoped,
    quote_view,
    document_dict,
    docs,
    apply_operation,
    validate_sources,
    digest,
    lock_quote,
)
from backend.domain.schemas import Operation
from backend.domain.pricing import gross_margin, markup, dec
from backend.domain.coordination import (
    sync_agent_clarifications,
    project_clarifications,
    clarification_view,
)
from .gateway import next_action

ALLOWED_OPS = {
    "add_line",
    "set_quantity",
    "update_description",
    "set_lead_time",
    "select_catalog_item",
    "set_gross_margin",
    "set_markup",
    "set_cost",
    "override_selling_price",
    "attach_evidence",
    "record_assumption",
}


def row_span(sp):
    return {
        "id": sp.id,
        "document_id": sp.document_id,
        "text": sp.text,
        "location": sp.location,
    }


def initial_context(s, r, q, p):
    return {
        "goal": r.goal,
        "quote": quote_view(s, q, p),
        "documents": [
            document_dict(d)
            | {"meta": {"sender": d.meta.get("sender"), "date": d.meta.get("date")}}
            for d in docs(s, p)
        ],
        "plan": r.plan,
        "steps": r.steps[-20:],
        "overlay": r.overlay,
        "answers": r.answers,
        "clarifications": [clarification_view(c) for c in project_clarifications(s, p)],
        "remaining_tool_calls": 24 - len(r.steps),
    }


def verify_overlay(s, r, q, p):
    errors = []
    with s.begin_nested() as tx:
        try:
            for raw in r.overlay:
                apply_operation(s, q, p, Operation.model_validate(raw))
            result = quote_view(s, q, p)
        except Exception as exc:
            errors.append(str(getattr(exc, "detail", str(exc))))
            result = None
        tx.rollback()
    return {"valid": not errors, "errors": errors, "draft": result}


def evidence_check(s, p, op, answers):
    ids = list(dict.fromkeys(op.source_ids + (op.line.source_ids if op.line else [])))
    validate_sources(s, p, ids)
    text = (
        " ".join(get(s, Span, id).text for id in ids)
        + " "
        + " ".join(a["answer"] for a in answers)
    )
    values = []
    if op.type == "add_line":
        if not op.line:
            raise ValueError("add_line requires line values.")
        values = [op.line.quantity, op.line.cost, op.line.price]
        if not ids and not answers:
            raise ValueError("New lines require evidence or an explicit human answer.")
        if op.line.model and not s.scalar(
            scoped(Catalog).where(Catalog.model == op.line.model)
        ):
            raise ValueError(
                "Product model is not present in the authorized catalog. Leave the selection blank and ask for a catalog import."
            )
    if op.type in ("set_quantity", "set_cost", "override_selling_price"):
        values = [op.value]
    numeric = {
        dec(n.replace(",", ""))
        for n in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w.])", text)
    }
    for value in values:
        if value is not None and dec(value) not in numeric:
            raise ValueError(
                f"Value {value} is not present in the cited evidence or human answers."
            )


def execute(s, r, q, p, name, args):
    if name == "answer_question":
        answer = args.get("answer", "")
        ids = args.get("source_ids", [])
        if not isinstance(answer, str) or not 1 <= len(answer) <= 10000:
            raise ValueError("Provide a concise answer.")
        if (
            not isinstance(ids, list)
            or not ids
            or not all(isinstance(x, str) for x in ids)
        ):
            raise ValueError("Cite existing source spans for the answer.")
        validate_sources(s, p, ids)
        r.status = "completed"
        r.result = {"summary": answer, "source_ids": ids, "read_only": True}
        return r.result
    if name == "read_project":
        return {
            "quote": quote_view(s, q, p),
            "documents": [document_dict(d) | {"meta": {}} for d in docs(s, p)],
        }
    if name == "update_plan":
        tasks = args.get("tasks", [])
        if (
            not isinstance(tasks, list)
            or not all(isinstance(t, str) for t in tasks)
            or len(tasks) > 20
        ):
            raise ValueError("Provide at most 20 task descriptions.")
        r.plan = tasks
        return {"plan": tasks}
    if name == "read_source_spans":
        d = get(s, Document, args.get("document_id"))
        if d.project_id != p.id:
            raise ValueError("Source is outside this project.")
        offset = max(0, int(args.get("offset", 0)))
        limit = min(200, max(1, int(args.get("limit", 100))))
        allsp = list(
            s.scalars(
                scoped(Span)
                .where(Span.document_id == d.id)
                .order_by(Span.created_at, Span.id)
            )
        )
        return {
            "document_id": d.id,
            "total": len(allsp),
            "offset": offset,
            "next_offset": offset + limit if offset + limit < len(allsp) else None,
            "spans": [row_span(x) for x in allsp[offset : offset + limit]],
        }
    if name == "search_sources":
        query = str(args.get("query", ""))[:200]
        ds = [d.id for d in docs(s, p)]
        found = s.scalars(
            scoped(Span)
            .where(
                Span.document_id.in_(ds),
                Span.text.ilike("%" + query.replace("%", "\\%") + "%"),
            )
            .limit(100)
        )
        return {"spans": [row_span(x) for x in found], "complete_inventory": False}
    if name == "find_catalog_candidates":
        query = str(args.get("query", ""))[:150]
        items = list(
            s.scalars(
                scoped(Catalog).where(Catalog.model.ilike("%" + query + "%")).limit(100)
            )
        )
        return {
            "items": [
                {
                    "id": c.id,
                    "model": c.model,
                    "description": c.description,
                    "attributes": c.attributes,
                    "offers": [
                        {
                            "id": o.id,
                            "cost": str(o.cost),
                            "unit": o.unit,
                            "currency": o.currency,
                            "max_quantity": str(o.max_quantity)
                            if o.max_quantity
                            else None,
                            "valid_until": o.valid_until,
                            "lead_time": o.lead_time,
                        }
                        for o in s.scalars(
                            scoped(Offer).where(Offer.catalog_id == c.id)
                        )
                    ],
                }
                for c in items
            ]
        }
    if name == "calculate_prices":
        if args.get("basis") not in ("gross_margin", "markup"):
            raise ValueError("Specify gross_margin or markup explicitly.")
        return {
            "price": str(
                (gross_margin if args["basis"] == "gross_margin" else markup)(
                    args.get("cost"), args.get("percent")
                )
            ),
            "rounding": "unit cents, half up",
        }
    if name == "stage_draft_changes":
        ops = args.get("operations", [])
        if not isinstance(ops, list) or not 1 <= len(ops) <= 500:
            raise ValueError("Stage between 1 and 500 operations.")
        parsed = [Operation.model_validate(o) for o in ops]
        for op in parsed:
            if op.type not in ALLOWED_OPS:
                raise ValueError("Agent cannot perform this operation.")
            evidence_check(s, p, op, r.answers)
        r.overlay = [o.model_dump(mode="json") for o in parsed]
        return {
            "staged": len(parsed),
            "summary": str(args.get("summary", ""))[:2000],
            "overlay_hash": digest(r.overlay),
        }
    if name == "inspect_draft":
        return verify_overlay(s, r, q, p) | {"overlay_hash": digest(r.overlay)}
    if name == "request_human_input":
        questions = args.get("questions", [])
        if (
            not isinstance(questions, list)
            or not questions
            or not all(isinstance(x, str) and 1 <= len(x) <= 2000 for x in questions)
        ):
            raise ValueError("Ask specific questions as strings.")
        r.questions = questions
        r.status = "waiting_for_input"
        sync_agent_clarifications(s, r, q, p, questions)
        return {"questions": questions}
    if name == "finalize_proposal":
        clarifications = args.get("clarifications", [])
        if not r.overlay:
            raise ValueError("No draft operations have been staged.")
        validations = [
            x
            for x in r.steps
            if x.get("tool") == "inspect_draft"
            and x.get("result", {}).get("valid")
            and x.get("result", {}).get("overlay_hash") == digest(r.overlay)
        ]
        if not validations:
            raise ValueError("Inspect and validate the current draft before finishing.")
        for d in docs(s, p):
            allids = {
                sp.id for sp in s.scalars(scoped(Span).where(Span.document_id == d.id))
            }
            readids = {
                sp["id"]
                for step in r.steps
                if step.get("tool") == "read_source_spans"
                for sp in step.get("result", {}).get("spans", [])
            }
            if allids - readids:
                raise ValueError(
                    f"Read all remaining source spans in {d.name} before finalizing."
                )
            if d.state not in ("ready", "mapped") or any(
                c["state"] in ("scanned", "unsupported", "failed") for c in d.coverage
            ):
                raise ValueError(
                    f"{d.name} has incomplete ingestion or coverage. Request human input."
                )
        source_ids = list(
            dict.fromkeys(x for op in r.overlay for x in op.get("source_ids", []))
        )
        pr = Proposal(
            quote_id=q.id,
            base_version=q.version,
            input_revision=p.input_revision,
            title=str(args.get("title", "Agent draft"))[:200],
            summary=str(args.get("summary", ""))[:8000],
            operations=r.overlay,
            sources=source_ids,
        )
        s.add(pr)
        s.flush()
        sync_agent_clarifications(s, r, q, p, clarifications)
        r.status = "ready_for_review"
        r.result = {
            "proposal_id": pr.id,
            "summary": pr.summary,
            "clarifications": clarifications,
        }
        return r.result
    raise ValueError("Unsupported tool.")


def run_agent(id, lease_check=lambda: True, provider=next_action):
    started = time.monotonic()
    for _ in range(24):
        if not lease_check():
            return
        with Session.begin() as s:
            initial = get(s, Run, id)
            q, p = lock_quote(s, initial.quote_id)
            r = get(s, Run, id, True)
            if r.status in (
                "cancelled",
                "ready_for_review",
                "completed",
                "waiting_for_input",
            ):
                return
            if q.version != r.base_version or p.input_revision != r.input_revision:
                r.status = "waiting_for_input"
                r.questions = [
                    "The quote or source revision changed. Start a new run against the current revision; your saved draft is preserved."
                ]
                return
            if len(r.steps) >= 24 or time.monotonic() - started > 480:
                r.status = "waiting_for_input"
                r.questions = [
                    "The run reached its work budget. Review the saved progress and start a focused follow-up."
                ]
                return
            r.status = "executing"
            context = initial_context(s, r, q, p)
        try:
            name, args, usage = provider(context)
        except Exception as exc:
            with Session.begin() as s:
                r = get(s, Run, id, True)
                if r.status != "cancelled":
                    r.status = "failed"
                    r.result = {"error": str(exc)[:500]}
            return
        if not lease_check():
            return
        with Session.begin() as s:
            initial = get(s, Run, id)
            q, p = lock_quote(s, initial.quote_id)
            r = get(s, Run, id, True)
            if r.status == "cancelled":
                return
            if q.version != r.base_version or p.input_revision != r.input_revision:
                r.status = "waiting_for_input"
                r.questions = [
                    "The baseline changed during analysis. Start a new run to reconcile it."
                ]
                return
            signature = digest([name, args])
            if len(r.steps) >= 3 and all(
                st.get("signature") == signature for st in r.steps[-3:]
            ):
                r.status = "waiting_for_input"
                r.questions = [
                    "The assistant repeated a step without progress. Review the saved observations and clarify the task."
                ]
                return
            try:
                with s.begin_nested():
                    result = execute(s, r, q, p, name, args)
            except Exception as exc:
                result = {"error": str(getattr(exc, "detail", str(exc)))[:2000]}
            r.steps = [
                *r.steps,
                {
                    "tool": name,
                    "arguments": args,
                    "result": result,
                    "signature": signature,
                    "usage": usage,
                    "at": datetime.now(timezone.utc).isoformat(),
                },
            ]
            if len(r.steps) >= 3 and all(
                "error" in step.get("result", {}) for step in r.steps[-3:]
            ):
                r.status = "waiting_for_input"
                r.questions = [
                    "The last three attempts could not be validated. Review the reported errors and clarify the missing inputs."
                ]
