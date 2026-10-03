"""Human-owned clarification lifecycle and a queue derived from persisted work."""

from datetime import date

from backend.domain.service import (
    checks,
    docs,
    fail,
    get,
    invalidate,
    lines,
    scoped,
    validate_sources,
)
from backend.identity import current_actor
from backend.storage.models import (
    Clarification,
    Event,
    Job,
    Line,
    Project,
    Proposal,
    Quote,
    Run,
    now,
)


def clarification_view(c):
    result = {
        key: getattr(c, key)
        for key in (
            "id",
            "project_id",
            "title",
            "question",
            "recipient",
            "due_date",
            "line_ids",
            "source_ids",
            "run_id",
            "last_run_id",
            "status",
            "answer",
            "answer_source_ids",
            "resolution_note",
            "revision",
        )
    }
    result.update(
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
        answer_origin="human entered" if c.answer else None,
    )
    return result


def project_clarifications(s, p):
    return list(
        s.scalars(
            scoped(Clarification)
            .where(Clarification.project_id == p.id)
            .order_by(Clarification.updated_at.desc(), Clarification.id)
        )
    )


def validate_references(s, q, p, line_ids, source_ids, run_id=None):
    validate_sources(s, p, source_ids)
    for line_id in line_ids:
        if get(s, Line, line_id).quote_id != q.id:
            fail("Clarification lines must belong to this quote.", 403)
    if run_id and get(s, Run, run_id).quote_id != q.id:
        fail("Clarification run must belong to this quote.", 403)


def check_revision(c, expected):
    if c.revision != expected:
        fail("This clarification changed. Refresh before continuing.", 409)


def touch(c):
    c.revision += 1
    c.updated_at = now()


def clean_text(value, label):
    value = value.strip()
    if not value:
        fail(label + " is required.")
    return value


def validate_due_date(value):
    if value is not None:
        if len(value) != 10:
            fail("Use a due date in YYYY-MM-DD format.")
        date.fromisoformat(value)


def create_clarification(s, q, p, data):
    validate_due_date(data.get("due_date"))
    validate_references(
        s, q, p, data["line_ids"], data["source_ids"], data.get("run_id")
    )
    data = dict(data)
    data["title"] = clean_text(data["title"], "Title")
    data["question"] = clean_text(data["question"], "Question")
    data["recipient"] = data.get("recipient", "").strip()
    data["line_ids"] = list(dict.fromkeys(data["line_ids"]))
    data["source_ids"] = list(dict.fromkeys(data["source_ids"]))
    c = Clarification(project_id=p.id, **data)
    s.add(c)
    q.status = "draft"
    s.add(
        Event(
            project_id=p.id,
            summary="Prepared clarification: " + c.title,
            kind="coordination",
        )
    )
    s.flush()
    return c


def update_clarification(s, c, q, p, data):
    check_revision(c, data.pop("expected_revision"))
    if "due_date" in data:
        validate_due_date(data["due_date"])
    for key in ("title", "question", "recipient", "status", "resolution_note"):
        if key in data and data[key] is None:
            fail(key.replace("_", " ").capitalize() + " cannot be null.")
    if "question" in data and c.answer and data["question"].strip() != c.question:
        fail(
            "An answered question is part of the evidence trail. Create a follow-up clarification instead."
        )
    status = data.get("status", c.status)
    allowed = {
        "draft": {"draft", "awaiting_reply"},
        "awaiting_reply": {"draft", "awaiting_reply"},
        "answered": {"answered", "awaiting_reply", "resolved"},
        "resolved": {"resolved", "draft"},
    }
    if status not in allowed[c.status]:
        fail(
            "Record an answer before marking this clarification answered or resolved.",
            409,
        )
    if status == "resolved" and c.status != "resolved":
        if not c.answer.strip() or not str(data.get("resolution_note", "")).strip():
            fail(
                "Record the answer and describe your review before resolving this issue."
            )
        if q.reconciled_input != p.input_revision:
            fail(
                "Reconcile the current source revision before resolving the clarification."
            )
        if any(d.state not in ("ready", "mapped") for d in docs(s, p)):
            fail("Finish source processing before resolving the clarification.")
        current_lines = {line.id: line for line in lines(s, q)}
        if any(
            line_id in current_lines and current_lines[line_id].review != "approved"
            for line_id in c.line_ids
        ):
            fail("Review the affected quote lines before resolving this clarification.")
        if c.last_run_id:
            run = get(s, Run, c.last_run_id)
            if run.status in ("queued", "executing", "waiting_for_input"):
                fail("Finish or cancel the recheck before resolving the clarification.")
            proposal_id = run.result.get("proposal_id")
            if proposal_id and get(s, Proposal, proposal_id).status == "pending":
                fail(
                    "Review the proposed quote changes before resolving the clarification."
                )
    for key, value in data.items():
        if isinstance(value, str):
            value = value.strip()
        if key in ("title", "question"):
            value = clean_text(value, key.capitalize())
        setattr(c, key, value)
    if status != "resolved":
        q.status = "draft"
    if c.status == "draft":
        c.resolution_note = ""
    touch(c)
    s.add(
        Event(
            project_id=p.id,
            summary=f"Clarification {c.status.replace('_', ' ')}: {c.title}",
            kind="coordination",
        )
    )
    s.flush()
    return c


def record_answer(s, c, q, p, answer, source_ids):
    if c.status == "resolved":
        fail("Reopen this clarification before recording another answer.", 409)
    validate_sources(s, p, source_ids)
    c.answer = clean_text(answer, "Answer")
    c.answer_source_ids = list(dict.fromkeys(source_ids))
    c.status = "answered"
    c.resolution_note = ""
    touch(c)
    p.input_revision += 1
    invalidate(s, q)
    # Keep prior answers immutable in the audit trail when an answer is corrected.
    s.add(
        Event(
            project_id=p.id,
            summary="Recorded human-entered answer: " + c.title,
            kind="answer",
            meta={
                "clarification_id": c.id,
                "answer": c.answer,
                "source_ids": c.answer_source_ids,
                "origin": "human entered",
                "input_revision": p.input_revision,
            },
        )
    )
    s.flush()
    return c


def queue_recheck(s, c, q, p, model):
    if c.status != "answered" or not c.answer.strip():
        fail("Record the answer before rechecking the current quote.", 409)
    validate_references(s, q, p, [], [*c.source_ids, *c.answer_source_ids])
    if any(d.state in ("queued", "processing") for d in docs(s, p)):
        fail("Wait for the new documents to finish processing, then recheck.", 409)
    if c.last_run_id and get(s, Run, c.last_run_id).status in ("queued", "executing"):
        fail("This clarification already has a recheck in progress.", 409)
    human_answers = [
        {
            "answer": issue.answer,
            "question": issue.question,
            "actor": current_actor(),
            "at": issue.updated_at.isoformat(),
            "origin": "human entered",
            "source_ids": issue.answer_source_ids,
            "clarification_id": issue.id,
        }
        for issue in project_clarifications(s, p)
        if issue.answer and issue.status in ("answered", "resolved")
    ]
    r = Run(
        quote_id=q.id,
        goal=(
            "Recheck the current equipment quote after a clarification answer. "
            "Focus on the affected scope, supplier coverage, pricing basis, and delivery. "
            "Read current documents, including newly uploaded offers and addenda. "
            "Stage a new evidence-linked proposal if changes are needed; never reuse an older overlay. "
            "Do not resolve the issue or approve commercial commitments. "
            "Human-entered answers are not supplier confirmations.\n"
            f"Clarification {c.id}: {c.question}\n"
            f"Recorded answer: {c.answer}\n"
            f"Affected line IDs: {', '.join(c.line_ids)}\n"
            f"Evidence span IDs: {', '.join([*c.source_ids, *c.answer_source_ids])}"
        ),
        base_version=q.version,
        input_revision=p.input_revision,
        answers=human_answers,
        model=model,
    )
    s.add(r)
    s.flush()
    s.add(
        Job(
            project_id=p.id, kind="agent", payload={"run_id": r.id}, key="agent:" + r.id
        )
    )
    # A paused predecessor remains an audit record, but no longer clutters the
    # work queue once its answer has been carried into a current-baseline run.
    for old_id in dict.fromkeys([c.run_id, c.last_run_id]):
        if old_id:
            old = get(s, Run, old_id)
            if old.status == "waiting_for_input":
                old.status = "cancelled"
                old.result = {**old.result, "superseded_by": r.id}
    c.last_run_id = r.id
    touch(c)
    s.add(
        Event(
            project_id=p.id,
            summary="Started clarification recheck: " + c.title,
            kind="coordination",
            meta={
                "clarification_id": c.id,
                "run_id": r.id,
                "quote_version": q.version,
                "input_revision": p.input_revision,
            },
        )
    )
    s.flush()
    return r


def sync_agent_clarifications(s, r, q, p, questions):
    """Persist actual agent questions without multiplying the same open issue."""
    if (
        not isinstance(questions, list)
        or len(questions) > 30
        or not all(
            isinstance(question, str) and 1 <= len(question.strip()) <= 8000
            for question in questions
        )
    ):
        raise ValueError("Clarifications must be at most 30 specific question strings.")
    # Serialized per project even when independent agent runs discover one gap.
    from sqlalchemy import text

    s.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "clarifications:" + p.id},
    )
    existing = {
        " ".join(issue.question.split()).casefold(): issue
        for issue in project_clarifications(s, p)
        if issue.status != "resolved"
    }
    project_lines = lines(s, q)
    for question in questions:
        question = question.strip()
        key = " ".join(question.split()).casefold()
        if key in existing:
            continue
        related = [line.id for line in project_lines if line.tag.casefold() in key]
        spans = list(
            dict.fromkeys(
                source_id
                for op in r.overlay
                if not related or op.get("line_id") in related
                for source_id in [
                    *op.get("source_ids", []),
                    *(op.get("line") or {}).get("source_ids", []),
                ]
            )
        )[:100]
        issue = create_clarification(
            s,
            q,
            p,
            {
                "title": question[:197] + ("..." if len(question) > 197 else ""),
                "question": question,
                "recipient": "",
                "due_date": None,
                "line_ids": related,
                "source_ids": spans,
                "run_id": r.id,
            },
        )
        existing[key] = issue


def work_queue(s):
    from backend.storage.models import Order

    items = []
    for p in s.scalars(
        scoped(Project)
        .where(Project.id.not_in(scoped(Order).with_only_columns(Order.project_id)))
        .order_by(Project.created_at.desc())
    ):
        q = s.scalar(scoped(Quote).where(Quote.project_id == p.id))
        if not q:
            continue
        events = list(
            s.scalars(
                scoped(Event)
                .where(Event.project_id == p.id)
                .order_by(Event.created_at.desc())
                .limit(1)
            )
        )
        updated = events[0].created_at if events else p.created_at

        def add(
            id,
            kind,
            status,
            title,
            detail,
            target,
            at=None,
            due_date=None,
            *,
            project=p,
            quote=q,
            updated_at=updated,
            **extra,
        ):
            items.append(
                {
                    "id": id,
                    "project_id": project.id,
                    "project_title": project.title,
                    "customer": project.customer,
                    "quote_id": quote.id,
                    "quote_number": quote.number,
                    "kind": kind,
                    "status": status,
                    "title": title,
                    "detail": detail,
                    "updated_at": (at or updated_at).isoformat(),
                    "due_date": due_date,
                    "target": target,
                    **extra,
                }
            )

        issues = project_clarifications(s, p)
        represented_runs = {
            run_id
            for issue in issues
            for run_id in (issue.run_id, issue.last_run_id)
            if run_id
        }
        for issue in issues:
            if issue.status == "resolved":
                continue
            run = get(s, Run, issue.last_run_id) if issue.last_run_id else None
            processing = run and run.status in ("queued", "executing")
            needs_attention = run and run.status in ("waiting_for_input", "failed")
            status = (
                "processing"
                if processing
                else "attention"
                if needs_attention
                else {
                    "draft": "attention",
                    "awaiting_reply": "waiting",
                    "answered": "review",
                }[issue.status]
            )
            detail = (
                "Rechecking the current quote and evidence."
                if processing
                else " ".join(run.questions)
                or run.result.get(
                    "error",
                    "Review the saved recheck and clarify the missing information.",
                )
                if needs_attention
                else "Answer recorded; review the impact before resolving."
                if issue.status == "answered"
                else "Waiting for " + (issue.recipient or "an external reply") + "."
                if issue.status == "awaiting_reply"
                else issue.question
            )
            add(
                "clarification:" + issue.id,
                "clarification",
                status,
                issue.title,
                detail,
                "coordination",
                issue.updated_at,
                issue.due_date,
                clarification_id=issue.id,
            )
        proposals = list(
            s.scalars(
                scoped(Proposal).where(
                    Proposal.quote_id == q.id, Proposal.status == "pending"
                )
            )
        )
        for proposal in proposals:
            add(
                "proposal:" + proposal.id,
                "proposal",
                "review",
                proposal.title,
                proposal.summary
                or "Review the proposed scope, price, and delivery changes.",
                "changes",
                proposal.created_at,
            )
        runs = list(
            s.scalars(
                scoped(Run).where(Run.quote_id == q.id).order_by(Run.created_at.desc())
            )
        )
        for run in runs:
            if run.id in represented_runs:
                continue
            if run.status in ("queued", "executing"):
                add(
                    "run:" + run.id,
                    "run",
                    "processing",
                    "Agent is preparing the work",
                    run.goal[:240],
                    "coordination",
                    run.created_at,
                )
            elif run.status in ("waiting_for_input", "failed") and run == runs[0]:
                add(
                    "run:" + run.id,
                    "run",
                    "attention",
                    "Agent needs your input"
                    if run.status == "waiting_for_input"
                    else "Agent run needs attention",
                    " ".join(run.questions)
                    if run.questions
                    else run.result.get(
                        "error", "Review the saved run and start a follow-up."
                    ),
                    "coordination",
                    run.created_at,
                )
        project_docs = docs(s, p)
        for document in project_docs:
            coverage_gap = any(
                c.get("state") in ("scanned", "unsupported", "failed")
                for c in document.coverage
            ) and not document.meta.get("reviewed_gap")
            if document.state in ("ready", "mapped") and not coverage_gap:
                continue
            processing = document.state in ("queued", "processing")
            add(
                "document:" + document.id,
                "document",
                "processing" if processing else "attention",
                "Processing " + document.name
                if processing
                else "Review " + document.name,
                "Extracting the source evidence."
                if processing
                else "Confirm column mapping or resolve unreadable source sections.",
                "sources",
                document.created_at,
            )
        quote_lines = lines(s, q)
        if not quote_lines and not proposals:
            add(
                "setup:" + p.id,
                "setup",
                "attention",
                "Add the existing quote",
                "Import the quote and project change to start coordinating the revision.",
                "sources",
                due_date=p.due_date,
            )
        elif quote_lines:
            # Clarifications, documents, and proposals already have useful queue
            # entries. Aggregate remaining quote checks instead of flooding it.
            covered_lines = {
                line_id
                for issue in issues
                if issue.status != "resolved"
                for line_id in issue.line_ids
            }
            quote_checks = checks(s, q, p)
            remaining = [
                check
                for check in quote_checks
                if check["code"] not in ("clarification", "coverage", "empty")
                and not (
                    check["line_id"] in covered_lines and check["code"] != "review"
                )
                and not (proposals and check["code"] == "reconcile")
            ]
            if remaining:
                add(
                    "review:" + q.id,
                    "review",
                    "review",
                    "Review the quote"
                    if all(c["code"] == "review" for c in remaining)
                    else "Resolve quote checks",
                    f"{len(remaining)} item{'s' if len(remaining) != 1 else ''}: "
                    + remaining[0]["message"],
                    "quote",
                    due_date=p.due_date,
                )
            elif (
                not proposals
                and not quote_checks
                and not any(issue.status != "resolved" for issue in issues)
                and not any(d.state not in ("ready", "mapped") for d in project_docs)
            ):
                add(
                    "quote:" + q.id,
                    "review",
                    "complete" if q.status == "approved" else "review",
                    "Approved quote"
                    if q.status == "approved"
                    else "Quote ready for approval",
                    f"Revision {q.version} is approved."
                    if q.status == "approved"
                    else "All current checks are clear. Review and approve this revision.",
                    "quote",
                    due_date=p.due_date,
                )
    order = {"attention": 0, "review": 1, "waiting": 2, "processing": 3, "complete": 4}
    items.sort(key=lambda item: item["updated_at"], reverse=True)
    items.sort(key=lambda item: order[item["status"]])
    return {
        "items": items,
        "counts": {
            status: sum(item["status"] == status for item in items) for status in order
        },
    }
